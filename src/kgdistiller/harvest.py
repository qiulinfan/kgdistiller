"""Harvest checked Markdown review tasks through one ingest transaction.

``prepare`` appends one unchecked task per reviewed capture to a sheet and
writes a readable draft beside a review manifest. ``apply`` ingests only the
checked tasks whose draft, entry file and cited source text are unchanged since
review, then turns their links into links to the committed entries.
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from .capture import CaptureError, _inside, capture_record, prepare_captures
from .entries import SECTION_TITLES, cited_text, entry_relative, slug_id, split_lines
from .home import atomic_write_text, knowledge_root
from .ingest import IngestPaths, apply_ingest, load_receipt, load_request
from .knowledge_store import entries_root, load_state

SCHEMA = "kgdistiller-checkbox-review-v1"
_HEADER = re.compile(r"^<!-- kgdistiller-harvest-review: (\S+) -->$")
_MARKER = re.compile(r"<!-- kgdistiller-harvest: ([a-z0-9-]+) -->")
_TASK = re.compile(r"^(\s*[-*+] \[)([ xX])(\] )(.+)$")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_DRAFT_SECTIONS = ("summary", "context", "role", "prerequisites", "pending_prerequisites",
                   "common_confusions", "open_questions")


class HarvestError(CaptureError):
    """A sheet selection needs a bounded correction or renewed review."""


def _write(path: Path, content: str) -> None:
    atomic_write_text(path, content)


def _sheet_path(paths: IngestPaths, value: Path) -> Path:
    root = paths.base.root
    sheet = _inside(root, value, "sheet")
    if sheet.suffix.lower() != ".md":
        raise HarvestError("a harvest sheet must be a Markdown file")
    entries = entries_root(root)
    if sheet == entries or sheet.is_relative_to(entries):
        raise HarvestError("sheet must be outside the committed entries")
    return sheet


def _read_exact(path: Path) -> str:
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _entry_text(root: Path, entry_id: str) -> str | None:
    path = root / entry_relative(entry_id)
    return _read_exact(path) if path.is_file() else None


def _cited_source(root: Path, payload: dict[str, Any]) -> str | None:
    path = root / payload["source"]
    try:
        with path.open("r", encoding="utf-8", newline=None) as handle:
            lines = split_lines(handle.read())
    except (OSError, UnicodeError):
        return None
    if payload["line_end"] > len(lines):
        return None
    return cited_text(lines, payload["line_start"], payload["line_end"])


def _link(sheet: Path, path: Path, label: str) -> str:
    escaped = label.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
    relative = Path(os.path.relpath(path, sheet.parent)).as_posix()
    return f"[{escaped}]({quote(relative, safe='/.-_~')})"


def _visible_lines(text: str):
    fence: str | None = None
    length = 0
    for number, line in enumerate(text.splitlines(keepends=True)):
        match = _FENCE.match(line)
        if match:
            run = match[1]
            if fence is None:
                fence, length = run[0], len(run)
            elif run[0] == fence and len(run) >= length and not line[match.end():].strip():
                fence = None
            continue
        if fence is None and not line.startswith(("    ", "\t")):
            yield number, line.rstrip("\r\n")


def _manifest_path(root: Path, sheet: Path, text: str) -> Path | None:
    headers = [match for _, line in _visible_lines(text) if (match := _HEADER.fullmatch(line))]
    if not headers:
        return None
    if len(headers) != 1:
        raise HarvestError("sheet must have exactly one harvest review binding")
    return _inside(root, (sheet.parent / unquote(headers[0][1])).resolve(), "review manifest")


def _load_manifest(path: Path, root: Path, sheet: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise HarvestError(f"cannot load bound harvest review: {path}") from error
    if (
        not isinstance(value, dict)
        or value.get("schema") != SCHEMA
        or value.get("sheet") != sheet.relative_to(root).as_posix()
        or not isinstance(value.get("items"), dict)
    ):
        raise HarvestError("harvest review does not belong to this sheet")
    return value


def _save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    _write(path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _value(value: Any) -> str:
    if value is None or value == []:
        return "（无）"
    if isinstance(value, list):
        return ", ".join(json.dumps(item, ensure_ascii=False) for item in value)
    return json.dumps(value, ensure_ascii=False)


def _sections(record: dict[str, Any] | None) -> list[str]:
    if record is None:
        return ["（尚无词条）", ""]
    lines: list[str] = []
    for field in _DRAFT_SECTIONS:
        if field not in record:
            continue
        lines.extend([f"### {SECTION_TITLES[field]}", ""])
        value = record[field]
        lines.extend([f"- {item}" for item in value] if isinstance(value, list) else [value])
        lines.append("")
    return lines


def _fence(text: str) -> str:
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def _draft(capture: dict[str, Any], root: Path, draft: Path) -> str:
    record, before = capture["record"], capture["existing"]
    entry = root / entry_relative(record["id"])
    rows = [
        f"- {name}: {_value((before or {}).get(field))} → {_value(record.get(field))}"
        for name, field in (("Label", "label"), ("Kind", "kind"), ("Aliases", "aliases"),
                            ("Understanding", "understanding"))
    ]
    evidence_fence = _fence(record["evidence"])
    lines = [
        f"# {record['label']} · REVIEW DRAFT", "",
        "> 这是待写入的审阅草稿。勾选后请求 harvest，才会写入知识库。", "",
        f"目标知识库：{_link(draft, knowledge_root(root), root.name + '/' + knowledge_root(root).name + '/')}", "",
        f"- Action: {capture['action']}",
        f"- Entry: {_link(draft, entry, entry_relative(record['id']).as_posix())}",
        f"- Source: `{record['source']}` lines {record['line_start']}–{record['line_end']}", "",
        "勾选只表示选择这次写入，不会改变 Understanding。", "",
        "## Fields", "", *rows, "",
        "## Entry before", "", *_sections(before),
        "## Entry after", "", *_sections(record),
        "## Identity review", "",
        f"- Reviewer: {capture['reviewer']}", "", capture["evidence"], "",
        "## Evidence", "", evidence_fence, record["evidence"], evidence_fence,
    ]
    return "\n".join(lines) + "\n"


def _tasks(sheet: Path, root: Path, text: str, manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    tasks: dict[str, dict[str, Any]] = {}
    active = set((manifest.get("active_run") or {}).get("selected", []))
    for number, line in _visible_lines(text):
        markers = list(_MARKER.finditer(line))
        if not markers:
            continue
        match = _TASK.fullmatch(line)
        if len(markers) != 1 or match is None:
            raise HarvestError("harvest bindings must remain on a Markdown task line")
        token = markers[0][1]
        if token in tasks or token not in manifest["items"]:
            raise HarvestError("duplicate or unknown harvest task binding")
        item = manifest["items"][token]
        label = item["payload"]["label"]
        allowed = [_link(sheet, _inside(root, item["draft"], "draft"), label + " (draft)")]
        if item.get("committed") or token in active:
            allowed.append(_link(sheet, root / entry_relative(item["entry_id"]), label))
        content = match[4]
        valid = next((link for link in allowed
                      if content.startswith(link) and content[len(link):len(link) + 1] in {"", " ", "\t"}),
                     None)
        if valid is None:
            raise HarvestError("harvest label or link changed; review this task again")
        tasks[token] = {"line": number, "checked": match[2].lower() == "x", "link": valid}
    return tasks


def prepare_harvest(
    paths: IngestPaths, payload: dict[str, Any], sheet_path: Path, output_dir: Path
) -> dict[str, Any]:
    """Append unchecked reviewed captures and readable drafts; never ingest."""
    if (
        not isinstance(payload, dict) or set(payload) != {"captures"}
        or not isinstance(payload["captures"], list) or not payload["captures"]
    ):
        raise HarvestError("harvest input must contain a non-empty captures array")
    root = paths.base.root
    sheet = _sheet_path(paths, sheet_path)
    output = _inside(root, output_dir, "output_dir")
    text = _read_exact(sheet) if sheet.exists() else "# Definition sheet\n\nCoverage: partial\n"
    manifest_path = _manifest_path(root, sheet, text)
    if manifest_path:
        manifest = _load_manifest(manifest_path, root, sheet)
        if manifest.get("active_run"):
            raise HarvestError("resume the active harvest before preparing more entries")
        if manifest_path.parent != output:
            raise HarvestError("use the sheet's existing review directory when adding captures")
    else:
        manifest_path = output / "review.json"
        if manifest_path.exists():
            raise HarvestError("review directory already belongs to another sheet")
        manifest = {"schema": SCHEMA, "sheet": sheet.relative_to(root).as_posix(),
                    "next_item": 1, "runs": 0, "items": {}}
    existing_tasks = _tasks(sheet, root, text, manifest) if manifest["items"] else {}
    lines = text.splitlines(keepends=True)
    state = load_state(root)
    additions: list[tuple[Path, str, str]] = []
    seen: set[str] = set()
    for raw in payload["captures"]:
        capture = capture_record(paths, raw, state)
        record = capture["record"]
        if root / record["source"] == sheet:
            raise HarvestError("the review sheet must be separate from its source")
        entry_id = record["id"]
        if entry_id in seen:
            raise HarvestError("prepare input names the same entry more than once")
        seen.add(entry_id)
        pending = [token for token, item in manifest["items"].items()
                   if item["entry_id"] == entry_id and not item.get("committed")]
        if len(pending) > 1:
            raise HarvestError("review contains several pending drafts for one entry")
        if pending:
            token = pending[0]
            revision = manifest["items"][token]["revision"] + 1
        else:
            token = f"{manifest['next_item']}-{slug_id(record['label']) or entry_id}"
            manifest["next_item"] += 1
            revision = 1
        frozen = copy.deepcopy(raw)
        frozen["source"] = record["source"]
        frozen["label"] = record["label"]
        draft = output / f"{token}.r{revision}.md"
        draft_text = _draft(capture, root, draft)
        manifest["items"][token] = {
            "payload": frozen, "entry_id": entry_id, "revision": revision,
            "draft": draft.relative_to(root).as_posix(), "draft_text": draft_text,
            "entry_text": _entry_text(root, entry_id), "source_text": record["evidence"],
        }
        link = _link(sheet, draft, record["label"] + " (draft)")
        row = f"- [ ] {link} <!-- kgdistiller-harvest: {token} -->"
        if token in existing_tasks:
            task = existing_tasks[token]
            refreshed = lines[task["line"]].replace(task["link"], link, 1)
            lines[task["line"]] = re.sub(r"^(\s*[-*+] \[)[ xX](\])", r"\1 \2", refreshed, count=1)
            row = ""
        additions.append((draft, draft_text, row))
    text = "".join(lines)
    output.mkdir(parents=True, exist_ok=True)
    for draft, draft_text, _ in additions:
        with draft.open("x", encoding="utf-8", newline="") as handle:
            handle.write(draft_text)
    _save_manifest(manifest_path, manifest)
    newline = "\r\n" if "\r\n" in text else "\n"
    end = "" if text.endswith("\n") else newline
    if not _manifest_path(root, sheet, text):
        relative = quote(Path(os.path.relpath(manifest_path, sheet.parent)).as_posix(), safe="/.-_~")
        text += f"{end}{newline}<!-- kgdistiller-harvest-review: {relative} -->{newline}"
        end = ""
    new_rows = [row for _, _, row in additions if row]
    if new_rows:
        text += end + newline + newline.join(new_rows) + newline
    _write(sheet, text)
    return {"status": "prepared", "sheet": str(sheet), "review": str(manifest_path),
            "counts": {"prepared": len(additions), "selected": 0},
            "artifacts": {"drafts": [str(draft) for draft, _, _ in additions]}}


def _checked_payload(root: Path, item: dict[str, Any]) -> dict[str, Any]:
    """Return a checked item's frozen capture if nothing it was reviewed against changed."""
    payload = copy.deepcopy(item["payload"])
    name = payload["label"]
    draft = _inside(root, item["draft"], "draft")
    if not draft.is_file() or _read_exact(draft) != item["draft_text"]:
        raise HarvestError(f"{name}: review draft changed; prepare this item again")
    if _entry_text(root, item["entry_id"]) != item["entry_text"]:
        raise HarvestError(f"{name}: entry changed after review; prepare this item again")
    if _cited_source(root, payload) != item["source_text"]:
        raise HarvestError(f"{name}: source changed after review; prepare this item again")
    return payload


def _refresh_sheet(sheet: Path, root: Path, manifest: dict[str, Any], selected: list[str]) -> None:
    text = _read_exact(sheet)
    tasks = _tasks(sheet, root, text, manifest)
    lines = text.splitlines(keepends=True)
    for token in selected:
        if token not in tasks:
            raise HarvestError("committed harvest task was removed; restore its binding to refresh the sheet")
        item = manifest["items"][token]
        entry = root / entry_relative(item["entry_id"])
        if not entry.is_file():
            raise HarvestError("committed entry is unavailable; keep the ingest receipt")
        task = tasks[token]
        lines[task["line"]] = lines[task["line"]].replace(
            task["link"], _link(sheet, entry, item["payload"]["label"]), 1)
        item["committed"] = entry_relative(item["entry_id"]).as_posix()
    _write(sheet, "".join(lines))


def apply_harvest(paths: IngestPaths, sheet_path: Path, output_dir: Path) -> dict[str, Any]:
    """Apply only checked reviewed tasks, then link the committed entries."""
    root = paths.base.root
    sheet = _sheet_path(paths, sheet_path)
    text = _read_exact(sheet)
    manifest_path = _manifest_path(root, sheet, text)
    if manifest_path is None:
        raise HarvestError("sheet has no prepared harvest review")
    manifest = _load_manifest(manifest_path, root, sheet)
    tasks = _tasks(sheet, root, text, manifest)
    run = manifest.get("active_run")
    if run:
        selected = run["selected"]
        receipt = load_receipt(paths, run["request_id"])
        if receipt is None:
            if any(token not in tasks or not tasks[token]["checked"] for token in selected):
                raise HarvestError("active harvest selection changed before commit; restore it before retry")
            for token in selected:
                _checked_payload(root, manifest["items"][token])
            receipt = apply_ingest(paths, load_request(_inside(root, run["request"], "request"), mode="apply"))
    else:
        selected = [token for token, task in tasks.items()
                    if task["checked"] and not manifest["items"][token].get("committed")]
        if not selected:
            return {"status": "nothing-selected", "sheet": str(sheet), "counts": {"selected": 0, "committed": 0}}
        captures = [_checked_payload(root, manifest["items"][token]) for token in selected]
        manifest["runs"] += 1
        _save_manifest(manifest_path, manifest)
        review_name = (slug_id(manifest_path.parent.name) or "review")[:80].rstrip("-")
        request_id = f"harvest-{review_name}-{manifest['runs']}"
        prepared = prepare_captures(paths, captures, output_dir, request_id=request_id)
        request_path = Path(prepared["artifacts"]["apply"])
        run = {"selected": selected, "request_id": request_id,
               "request": request_path.relative_to(root).as_posix()}
        manifest["active_run"] = run
        _save_manifest(manifest_path, manifest)
        receipt = apply_ingest(paths, load_request(request_path, mode="apply"))
    # Keep active_run until both the sheet and the review have been refreshed;
    # a retry then finds the committed receipt by request_id.
    try:
        _refresh_sheet(sheet, root, manifest, selected)
        manifest["last_request_id"] = run["request_id"]
        manifest.pop("active_run", None)
        _save_manifest(manifest_path, manifest)
    except (OSError, HarvestError) as error:
        return {
            "status": "committed-sheet-pending", "sheet": str(sheet),
            "counts": {"selected": len(selected), "committed": len(selected)},
            "receipt": receipt, "refresh_error": str(error),
        }
    return {"status": "committed", "sheet": str(sheet),
            "counts": {"selected": len(selected), "committed": len(selected)}, "receipt": receipt}
