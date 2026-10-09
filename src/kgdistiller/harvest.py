"""Harvest explicitly bound Markdown checkboxes through the ingest transaction."""

from __future__ import annotations

from .knowledge_paths import knowledge_root, knowledge_relative

import copy
import difflib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from .capture import CaptureError, _inside, _merge_source_content, prepare_capture, prepare_captures
from .cli import (KnowledgeError, atomic_write, build_identity_index, load_identity_registry, load_sources, load_state,
                  mark_definition_projection, pretty_json, scan_source, sha256_authority_file, unique_source_for_path)
from .contracts import sha256_json
from .entry_markdown import DERIVED_SOURCE_ROOT, ENTRY_ROOT, _SECTIONS, entry_relative
from .ingest import IngestPaths, _receipt_path, apply_ingest, load_request

SCHEMA = "kgdistiller-checkbox-review-v1"
_HEADER = re.compile(r"^<!-- kgdistiller-harvest-review: (\S+) -->$")
_MARKER = re.compile(r"<!-- kgdistiller-harvest: ([a-z0-9-]+) -->")
_TASK = re.compile(r"^(\s*[-*+] \[)([ xX])(\] )(.+)$")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")


class HarvestError(CaptureError):
    """A sheet selection needs a bounded correction or renewed review."""


def _sheet_path(paths: IngestPaths, value: Path) -> Path:
    root = paths.repo_root.resolve()
    sheet = _inside(root, value, "sheet")
    if sheet.suffix.lower() != ".md":
        raise HarvestError("a harvest sheet must be a Markdown file")
    for protected in (paths.graph_dir.resolve(), knowledge_root(root) / "entries", knowledge_root(root) / "derived/by-source"):
        if sheet == protected or sheet.is_relative_to(protected):
            raise HarvestError("sheet must be outside committed knowledge and derived evidence")
    return sheet


def _read_sheet(path: Path) -> str:
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


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
    headers = [_HEADER.fullmatch(line) for _, line in _visible_lines(text)]
    headers = [match for match in headers if match]
    if not headers:
        return None
    if len(headers) != 1:
        raise HarvestError("sheet must have exactly one harvest review binding")
    path = (sheet.parent / unquote(headers[0][1])).resolve()
    return _inside(root, path, "review manifest")


def _load_manifest(path: Path, root: Path, sheet: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise HarvestError(f"cannot load bound harvest review: {path}") from error
    if not isinstance(value, dict) or value.get("schema") != SCHEMA or value.get("sheet") != sheet.relative_to(root).as_posix():
        raise HarvestError("harvest review does not belong to this sheet")
    if not isinstance(value.get("items"), dict):
        raise HarvestError("invalid harvest review items")
    return value


def _node_state(paths: IngestPaths, node_id: str) -> dict[str, Any]:
    node = load_state(paths.graph_dir, repo_root=paths.repo_root).nodes.get(node_id)
    authority = (node or {}).get("provenance") or {}
    properties = (node or {}).get("properties") or {}
    entry_path = paths.repo_root / entry_relative(node_id, paths.repo_root)
    return {
        "node": None if node is None else {
            "id": node["id"], "label": node.get("label"), "type": node.get("type"),
            "text": node.get("text"), "entry": node.get("entry"),
            "authority": authority.get("authority"), "active": authority.get("active"),
            "definition": authority.get("definition_sha256"),
            "kind": properties.get("kind"), "kind_origin": properties.get("kind_origin"),
            "entry_file": properties.get("entry_sha256"),
        },
        "entry_file": sha256_authority_file(entry_path) if entry_path.is_file() else None,
    }


def _entry_sections(entry: dict[str, Any]) -> list[str]:
    if not entry:
        return ["（尚无词条）"]
    lines = []
    for field, value in entry.items():
        lines.extend([f"### {_SECTIONS.get(field, field.replace('_', ' ').title())}", ""])
        if isinstance(value, list):
            lines.extend([f"- {item}" for item in value] if value else ["（清空）"])
        else:
            lines.append(str(value))
        lines.append("")
    return lines


def _draft(payload: dict[str, Any], before: dict[str, Any], base_source: str, root: Path, draft: Path) -> str:
    previous = before.get("node") or {}
    old_kind = json.dumps(previous["kind"], ensure_ascii=False) if previous.get("kind") else "（尚无类型）"
    if "kind" in payload:
        new_kind = json.dumps(payload["kind"], ensure_ascii=False)
    elif previous.get("kind"):
        new_kind = old_kind + "（保持现有类型）"
    else:
        new_kind = "未指定审定类型；原文语法类型仅供参考。"
    lines = [f"# {payload['name']} · REVIEW DRAFT", "",
             "> 这是待写入的审阅草稿。勾选后请求 harvest，才会写入知识库。", "",
             f"目标知识库：{_link(draft, knowledge_root(root), root.name + '/' + knowledge_root(root).name + '/')}", "",
             f"Source: `{payload['source']}`", "",
             "勾选只表示选择这次写入，不会改变 Understanding。", "",
             "## Knowledge type", "", f"- Before: {old_kind}", f"- After: {new_kind}", "",
             "## Reviewed text", "", payload["text"], "", "## Entry before", "",
             *_entry_sections((before.get("node") or {}).get("entry") or {}), "",
             "## Entry after", "", *_entry_sections(payload.get("entry") or {}), "",
             "## Identity review", "", f"- Action: {payload['review']['action']}",
             f"- Reviewer: {payload['review']['reviewer']}", "",
             "### Evidence", "", payload["review"]["evidence"], "",
             "## Native source changes", ""]
    content = payload["source_content"]
    changes = "".join(difflib.unified_diff(base_source.splitlines(keepends=True), content.splitlines(keepends=True),
                                        fromfile=payload["source"], tofile=payload["source"], n=3))
    if changes:
        # A longer fence keeps arbitrary source content readable as literal text.
        fence = "`" * max(3, max((len(run) + 1 for run in re.findall(r"`+", changes)), default=3))
        lines.extend([fence + "diff", changes.rstrip("\n"), fence])
    else:
        lines.append("No native source changes.")
    return "\n".join(lines).rstrip() + "\n"


def prepare_harvest(
    paths: IngestPaths, payload: dict[str, Any], sheet_path: Path, output_dir: Path
) -> dict[str, Any]:
    """Append unchecked reviewed captures and readable drafts; never ingest."""
    if not isinstance(payload, dict) or set(payload) != {"captures"} or not isinstance(payload["captures"], list) or not payload["captures"]:
        raise HarvestError("harvest input must contain a non-empty captures array")
    root = paths.repo_root.resolve()
    sheet = _sheet_path(paths, sheet_path)
    output = _inside(root, output_dir, "output_dir")
    text = _read_sheet(sheet) if sheet.exists() else "# Definition sheet\n\nCoverage: partial\n"
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
        manifest = {"schema": SCHEMA, "sheet": sheet.relative_to(root).as_posix(), "items": {}, "source_versions": {}}
        text = mark_definition_projection(text)
    if not manifest["items"] and sheet.is_file():
        specs = load_sources(root, paths.registry)
        owners = [spec for spec in specs if sheet.is_relative_to(spec.root)]
        if owners:
            owner = unique_source_for_path(specs, sheet)
            identities = build_identity_index(load_state(paths.graph_dir, repo_root=paths.repo_root), load_identity_registry(paths.identities))
            scanned = scan_source(root, owner, sheet, identities)
            if scanned.definitions or scanned.references or scanned.errors:
                raise HarvestError("an existing native authority cannot become a harvest projection")
    existing_tasks = _tasks(paths, sheet, text, manifest) if manifest["items"] else {}
    lines = text.splitlines(keepends=True)
    additions = []
    seen = set()
    for capture in payload["captures"]:
        result = prepare_capture(paths, capture, output)
        request = load_request(Path(result["artifacts"]["apply"]))
        source_patch = request["authority_patches"][0]
        if root / source_patch["path"] == sheet:
            raise HarvestError("the review sheet must be separate from its native source")
        node_id = request["decisions"][0]["target_id"]
        if node_id in seen:
            raise HarvestError("prepare input contains duplicate native identities")
        seen.add(node_id)
        pending = [token for token, item in manifest["items"].items()
                   if item["node_id"] == node_id and not item.get("committed")]
        if len(pending) > 1:
            raise HarvestError("review contains multiple pending drafts for one native identity")
        frozen = copy.deepcopy(capture)
        frozen.pop("source_content_file", None)
        frozen["source"] = source_patch["path"]
        frozen["source_content"] = source_patch["content"]
        frozen["name"] = frozen["name"].strip()
        frozen["entry"] = copy.deepcopy(request["delta"]["nodes"][0]["entry"])
        before = _node_state(paths, node_id)
        if before["entry_file"] != (before.get("node") or {}).get("entry_file"):
            raise HarvestError("selected entry file differs from the graph; synchronize it before review")
        source = root / source_patch["path"]
        base_source = source.read_text(encoding="utf-8") if source.is_file() else ""
        expected = sha256_authority_file(source) if source.is_file() else None
        if expected != source_patch["expected_sha256"]:
            raise HarvestError("source changed while preparing review; retry")
        token = pending[0] if pending else uuid.uuid4().hex
        draft = output / f"{uuid.uuid4().hex}.md"
        draft_text = _draft(frozen, before, base_source, root, draft)
        item = {
            "payload": frozen, "node_id": node_id, "before": before,
            "source_expected": expected, "source_base": base_source,
            "draft": draft.relative_to(root).as_posix(), "draft_content": draft_text,
        }
        item["binding"] = sha256_json(item)
        manifest["items"][token] = item
        link = _link(sheet, draft, frozen["name"] + " (draft)")
        row = f"- [ ] {link} <!-- kgdistiller-harvest: {token} -->"
        if token in existing_tasks:
            task = existing_tasks[token]
            old_line = lines[task["line"]]
            refreshed = old_line.replace(task["link"], link, 1)
            lines[task["line"]] = re.sub(r"^(\s*[-*+] \[)[ xX](\])", r"\1 \2", refreshed, count=1)
            row = ""
        additions.append((draft, draft_text, row))
    text = "".join(lines)
    sheet.parent.mkdir(parents=True, exist_ok=True)
    for draft, draft_text, _ in additions:
        with draft.open("x", encoding="utf-8") as handle:
            handle.write(draft_text)
    atomic_write(manifest_path, pretty_json(manifest))
    if not _manifest_path(root, sheet, text):
        relative = quote(Path(os.path.relpath(manifest_path, sheet.parent)).as_posix(), safe="/.-_~")
        text += ("" if text.endswith("\n") else "\n") + f"\n<!-- kgdistiller-harvest-review: {relative} -->\n"
    new_rows = [row for _, _, row in additions if row]
    if new_rows:
        text += ("" if text.endswith("\n") else "\n") + "\n" + "\n".join(new_rows) + "\n"
    atomic_write(sheet, text)
    return {"status": "prepared", "sheet": str(sheet), "review": str(manifest_path),
            "counts": {"prepared": len(additions), "selected": 0},
            "artifacts": {"drafts": [str(draft) for draft, _, _ in additions]}}


def _tasks(paths: IngestPaths, sheet: Path, text: str, manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    root = paths.repo_root.resolve()
    tasks = {}
    state = None
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
        expected = _link(sheet, _inside(root, item["draft"], "draft"), item["payload"]["name"] + " (draft)")
        allowed = [expected]
        if item.get("committed"):
            allowed.append(_link(sheet, _inside(root, item["committed"], "entry"), item["payload"]["name"]))
        if token in active:
            if state is None:
                state = load_state(paths.graph_dir, repo_root=paths.repo_root)
            entry = (state.nodes.get(item["node_id"], {}).get("properties") or {}).get("entry_authority")
            if entry:
                allowed.append(_link(sheet, _inside(root, entry, "entry"), item["payload"]["name"]))
        content = match[4]
        valid = next((link for link in allowed if content.startswith(link) and content[len(link):len(link)+1] in {"", " ", "\t"}), None)
        if valid is None:
            raise HarvestError("harvest label or link changed; review this task again")
        tasks[token] = {"line": number, "checked": match[2].lower() == "x", "link": valid}
    return tasks


def _checked_payload(paths: IngestPaths, item: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    root = paths.repo_root.resolve()
    frozen = {key: value for key, value in item.items() if key not in {"binding", "committed"}}
    if sha256_json(frozen) != item["binding"]:
        raise HarvestError("review metadata changed; prepare this item again")
    draft = _inside(root, item["draft"], "draft")
    if not draft.is_file() or draft.read_text(encoding="utf-8") != item["draft_content"]:
        raise HarvestError(f"{item['payload']['name']}: review draft changed; ask for a targeted re-review before harvest")
    try:
        current = _node_state(paths, item["node_id"])
    except KnowledgeError as error:
        raise HarvestError(f"{item['payload']['name']}: selected knowledge entry changed after review; {error}") from error
    if current != item["before"]:
        raise HarvestError(f"{item['payload']['name']}: selected knowledge entry changed after review; review it again")
    payload = copy.deepcopy(item["payload"])
    source = _inside(root, payload["source"], "source")
    actual = sha256_authority_file(source) if source.is_file() else None
    if actual != item["source_expected"]:
        if payload["source"] not in manifest["source_versions"] or actual != manifest["source_versions"][payload["source"]]:
            raise HarvestError(f"{item['payload']['name']}: source changed after review; review the selected item again")
        current = source.read_text(encoding="utf-8") if source.is_file() else ""
        payload["source_content"] = _merge_source_content(item["source_base"], [current, payload["source_content"]])
    return payload


def _refresh_sheet(paths: IngestPaths, sheet: Path, manifest: dict[str, Any], selected: list[str]) -> None:
    root = paths.repo_root.resolve()
    text = _read_sheet(sheet)
    tasks = _tasks(paths, sheet, text, manifest)
    lines = text.splitlines(keepends=True)
    state = load_state(paths.graph_dir, repo_root=paths.repo_root)
    for token in selected:
        if token not in tasks:
            raise HarvestError("committed harvest task was removed; restore its binding to refresh the sheet")
        item = manifest["items"][token]
        entry = (state.nodes.get(item["node_id"], {}).get("properties") or {}).get("entry_authority")
        if not entry or not _inside(root, entry, "entry").is_file():
            raise HarvestError("committed entry link is unavailable; retain the ingest receipt")
        task = tasks[token]
        line = lines[task["line"]]
        line = line.replace(task["link"], _link(sheet, root / entry, item["payload"]["name"]), 1)
        lines[task["line"]] = line
        item["committed"] = entry
    atomic_write(sheet, "".join(lines))


def apply_harvest(paths: IngestPaths, sheet_path: Path, output_dir: Path) -> dict[str, Any]:
    """Apply only checked reviewed tasks, then project real committed entry links."""
    root = paths.repo_root.resolve()
    sheet = _sheet_path(paths, sheet_path)
    text = _read_sheet(sheet)
    manifest_path = _manifest_path(root, sheet, text)
    if manifest_path is None:
        raise HarvestError("sheet has no prepared harvest review")
    manifest = _load_manifest(manifest_path, root, sheet)
    tasks = _tasks(paths, sheet, text, manifest)
    run = manifest.get("active_run")
    if run:
        selected = run["selected"]
        request = load_request(_inside(root, run["request"], "request"), mode="apply")
        # A stored receipt makes apply_ingest idempotent even when sheet refresh
        # was interrupted or a retry names a different output directory.
        if not _receipt_path(paths, request["request_sha256"]).is_file():
            if any(token not in tasks or not tasks[token]["checked"] for token in selected):
                raise HarvestError("active harvest selection changed before commit; restore it before retry")
            for token in selected:
                _checked_payload(paths, manifest["items"][token], manifest)
    else:
        selected = [token for token, task in tasks.items() if task["checked"] and not manifest["items"][token].get("committed")]
        if not selected:
            return {"status": "nothing-selected", "sheet": str(sheet), "counts": {"selected": 0, "committed": 0}}
        captures = [_checked_payload(paths, manifest["items"][token], manifest) for token in selected]
        prepared = prepare_captures(paths, captures, output_dir)
        request_path = Path(prepared["artifacts"]["apply"])
        request = load_request(request_path, mode="apply")
        run = {"selected": selected, "request": request_path.relative_to(root).as_posix()}
        manifest["active_run"] = run
        atomic_write(manifest_path, pretty_json(manifest))
    receipt = apply_ingest(paths, request)
    # Keep active_run until both the sheet and review have been refreshed.
    # Retrying a failed refresh uses exactly the already committed request.
    try:
        _refresh_sheet(paths, sheet, manifest, selected)
        for source_patch in request["authority_patches"]:
            manifest["source_versions"][source_patch["path"]] = source_patch["content_sha256"]
        manifest["last_receipt"] = receipt
        manifest.pop("active_run", None)
        atomic_write(manifest_path, pretty_json(manifest))
    except (OSError, HarvestError) as error:
        return {
            "status": "committed-sheet-pending", "sheet": str(sheet),
            "counts": {"selected": len(selected), "committed": len(selected)},
            "receipt": receipt, "refresh_error": str(error),
        }
    return {"status": "committed", "sheet": str(sheet), "counts": {"selected": len(selected), "committed": len(selected), "comparisons": 1}, "receipt": receipt}
