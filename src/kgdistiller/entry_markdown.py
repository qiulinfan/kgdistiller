"""Markdown authorities for curated atomic knowledge entries."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


ENTRY_SCHEMA = "kgdistiller-entry-v1"
ENTRY_INDEX_SCHEMA = "kgdistiller-entry-index-v1"
ENTRY_ROOT = Path("knowledge/entries")
DERIVED_ROOT = Path("knowledge/derived")
DERIVED_SOURCE_ROOT = DERIVED_ROOT / "by-source"

_SCALAR_FIELDS = ("summary", "context", "role")
_LIST_FIELDS = (
    "prerequisites",
    "common_confusions",
    "open_questions",
    "sources",
)
_SECTIONS = {
    "summary": "Summary",
    "context": "Context",
    "role": "Role",
    "prerequisites": "Prerequisites",
    "common_confusions": "Common confusions",
    "open_questions": "Open questions",
    "sources": "Sources",
}
_SECTION_KEYS = {value.casefold(): key for key, value in _SECTIONS.items()}
_FRONTMATTER_KEYS = {
    "kgd_schema",
    "kgd_id",
    "kgd_label",
    "kgd_entry_origin",
    "kgd_source",
    "kgd_source_sha256",
    "kgd_definition_sha256",
}
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


class EntryMarkdownError(ValueError):
    """Raised when an entry Markdown authority violates its contract."""


def _normalized_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline=None) as handle:
        return handle.read()


def authority_sha256(path: Path) -> str:
    return hashlib.sha256(_normalized_text(path).encode("utf-8")).hexdigest()


def entry_relative(node_id: str) -> Path:
    filename = f"{node_id}.md"
    if len(filename.encode("utf-8")) > 255 or node_id.casefold() in _WINDOWS_RESERVED:
        digest = hashlib.sha256(node_id.encode("utf-8")).hexdigest()
        filename = f"_kgd-{node_id[:48]}-{digest}.md"
    return ENTRY_ROOT / filename


def default_derived_relative(authority: str) -> Path:
    source = Path(authority)
    if source.is_absolute() or ".." in source.parts:
        raise EntryMarkdownError(f"unsafe source authority path: {authority}")
    # Preserve the original suffix in the derived name. ``same.typ`` and
    # ``same.tex`` are distinct authorities and must never collide.
    return DERIVED_SOURCE_ROOT / Path(f"{source.as_posix()}.md")


def _safe_markdown_source(repo_root: Path, value: str) -> tuple[str, Path]:
    relative = Path(value)
    if (
        not value
        or relative.is_absolute()
        or ".." in relative.parts
        or relative.suffix.casefold() != ".md"
    ):
        raise EntryMarkdownError(
            f"entry source must be a vault-relative Markdown path: {value!r}"
        )
    root = repo_root.resolve()
    path = (root / relative).resolve(strict=False)
    try:
        canonical = path.relative_to(root).as_posix()
    except ValueError as error:
        raise EntryMarkdownError(f"entry source escapes the vault: {value}") from error
    if path.is_symlink() or not path.is_file():
        raise EntryMarkdownError(f"entry source Markdown does not exist: {canonical}")
    return canonical, path


def resolve_entry_source(
    repo_root: Path,
    node: dict[str, Any],
    requested: str | None = None,
) -> tuple[str, Path]:
    properties = node.get("properties") or {}
    candidate = requested or str(properties.get("entry_source", ""))
    if not candidate:
        authority = str((node.get("provenance") or {}).get("authority", ""))
        suffix = Path(authority).suffix.casefold()
        if suffix == ".md":
            candidate = authority
        elif suffix in {".typ", ".tex", ".pdf"}:
            candidate = default_derived_relative(authority).as_posix()
        else:
            raise EntryMarkdownError(
                f"knowledge entry {node.get('id')} needs an explicit Markdown entry_source"
            )
    return _safe_markdown_source(repo_root, candidate)


def _frontmatter_line(key: str, value: str) -> str:
    return f"{key}: {json.dumps(value, ensure_ascii=False)}"


def normalize_entry(entry: Any, text: str = "") -> dict[str, Any]:
    if entry is None:
        entry = {}
    if not isinstance(entry, dict):
        raise EntryMarkdownError("structured entry must be an object")
    unsupported = set(entry) - set(_SCALAR_FIELDS) - set(_LIST_FIELDS)
    if unsupported:
        raise EntryMarkdownError(
            f"unsupported structured entry fields: {', '.join(sorted(unsupported))}"
        )
    result: dict[str, Any] = {}
    for field in _SCALAR_FIELDS:
        value = entry.get(field, "")
        if value is not None and not isinstance(value, str):
            raise EntryMarkdownError(f"entry field {field} must be a string")
        if str(value).strip():
            result[field] = str(value).strip()
    for field in _LIST_FIELDS:
        value = entry.get(field, [])
        if value is None:
            value = []
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise EntryMarkdownError(f"entry field {field} must be an array of strings")
        items = [item.strip() for item in value if item.strip()]
        if items:
            result[field] = items
    if not result.get("summary") and text.strip():
        result["summary"] = text.strip()
    return result


def render_entry(
    *,
    node_id: str,
    label: str,
    entry: dict[str, Any],
    source: str,
    source_sha256: str,
    definition_sha256: str,
    origin: str = "agent-extracted",
) -> str:
    normalized = normalize_entry(entry)
    if not normalized:
        raise EntryMarkdownError(f"knowledge entry {node_id} has no content")
    lines = [
        "---",
        _frontmatter_line("kgd_schema", ENTRY_SCHEMA),
        _frontmatter_line("kgd_id", node_id),
        _frontmatter_line("kgd_label", label),
        _frontmatter_line("kgd_entry_origin", origin),
        _frontmatter_line("kgd_source", source),
        _frontmatter_line("kgd_source_sha256", source_sha256),
        _frontmatter_line("kgd_definition_sha256", definition_sha256),
        "---",
        "",
        f"# {label}",
    ]
    for field in (*_SCALAR_FIELDS, *_LIST_FIELDS):
        value = normalized.get(field)
        if not value:
            continue
        lines.extend(["", f"## {_SECTIONS[field]}", ""])
        if field in _LIST_FIELDS:
            lines.extend(f"- {item}" for item in value)
        else:
            lines.extend(str(value).splitlines())
    return "\n".join(lines).rstrip() + "\n"


def _parse_frontmatter(text: str, path: Path) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise EntryMarkdownError(f"entry has no YAML frontmatter: {path}")
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise EntryMarkdownError(f"entry has unterminated YAML frontmatter: {path}") from error
    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        key, separator, raw_value = line.partition(":")
        if not separator or key not in _FRONTMATTER_KEYS or key in metadata:
            raise EntryMarkdownError(f"invalid entry frontmatter line in {path}: {line!r}")
        try:
            value = json.loads(raw_value.strip())
        except json.JSONDecodeError as error:
            raise EntryMarkdownError(
                f"entry frontmatter values must be JSON strings in {path}: {key}"
            ) from error
        if not isinstance(value, str):
            raise EntryMarkdownError(f"entry frontmatter value must be a string: {key}")
        metadata[key] = value
    if set(metadata) != _FRONTMATTER_KEYS:
        missing = ", ".join(sorted(_FRONTMATTER_KEYS - set(metadata)))
        raise EntryMarkdownError(f"entry frontmatter is missing fields in {path}: {missing}")
    if metadata["kgd_schema"] != ENTRY_SCHEMA:
        raise EntryMarkdownError(f"expected {ENTRY_SCHEMA} entry: {path}")
    return metadata, "\n".join(lines[end + 1 :]).strip()


def _parse_sections(body: str, path: Path) -> dict[str, Any]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in body.splitlines():
        match = re.fullmatch(r"##\s+(.+?)\s*", line)
        if match:
            key = _SECTION_KEYS.get(match.group(1).casefold())
            if key is None or key in sections:
                raise EntryMarkdownError(f"unsupported or duplicate entry section in {path}: {line}")
            current = key
            sections[current] = []
            continue
        if current is not None:
            sections[current].append(line)
    result: dict[str, Any] = {}
    for field, lines in sections.items():
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if field in _LIST_FIELDS:
            items: list[str] = []
            for line in lines:
                if not line.strip():
                    continue
                if not line.startswith("- "):
                    raise EntryMarkdownError(f"entry list section must use '- ' items: {path}")
                items.append(line[2:].strip())
            if items:
                result[field] = items
        else:
            value = "\n".join(lines).strip()
            if value:
                result[field] = value
    return result


def parse_entry(path: Path) -> dict[str, Any]:
    metadata, body = _parse_frontmatter(_normalized_text(path), path)
    entry = _parse_sections(body, path)
    if not entry:
        raise EntryMarkdownError(f"entry has no recognized content sections: {path}")
    return {"metadata": metadata, "entry": entry}


def write_entry(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            descriptor = -1
            handle.write(content)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def load_entry_authorities(
    repo_root: Path,
    nodes: dict[str, dict[str, Any]],
) -> dict[str, str]:
    root = repo_root / ENTRY_ROOT
    if root.exists() and (root.is_symlink() or not root.is_dir()):
        raise EntryMarkdownError(f"entry authority root is not an ordinary directory: {root}")
    entry_hashes: dict[str, str] = {}
    parsed_by_id: dict[str, tuple[Path, dict[str, Any]]] = {}
    if root.is_dir():
        for path in sorted(root.glob("*.md")):
            if path.is_symlink() or not path.is_file():
                raise EntryMarkdownError(f"entry authority is not an ordinary file: {path}")
            parsed = parse_entry(path)
            node_id = parsed["metadata"]["kgd_id"]
            if path.name != entry_relative(node_id).name or node_id in parsed_by_id:
                raise EntryMarkdownError(f"entry filename/id mismatch or duplicate: {path}")
            if node_id not in nodes:
                raise EntryMarkdownError(f"entry authority references unknown node: {node_id}")
            parsed_by_id[node_id] = (path, parsed)
            entry_hashes[path.relative_to(repo_root).as_posix()] = authority_sha256(path)
    for node_id, node in nodes.items():
        if node.get("type") != "knowledge":
            continue
        node.pop("entry", None)
        node["text"] = ""
        properties = dict(node.get("properties") or {})
        node["properties"] = properties
        for key in (
            "entry_authority",
            "entry_sha256",
            "entry_source",
            "entry_source_sha256",
            "entry_source_current_sha256",
            "entry_origin",
            "curated_definition_sha256",
        ):
            properties.pop(key, None)
        found = parsed_by_id.get(node_id)
        if found is None:
            properties["curation_status"] = "pending"
            continue
        path, parsed = found
        metadata = parsed["metadata"]
        label_matches = metadata["kgd_label"] == str(node.get("label", ""))
        source, source_path = _safe_markdown_source(repo_root, metadata["kgd_source"])
        current_source_sha = authority_sha256(source_path)
        current_definition_sha = str(
            (node.get("provenance") or {}).get("definition_sha256", "")
        )
        provenance_authority = str(
            (node.get("provenance") or {}).get("authority", "")
        )
        direct_markdown_source = (
            source == provenance_authority
            and Path(provenance_authority).suffix.casefold() == ".md"
            and not source.startswith(f"{DERIVED_SOURCE_ROOT.as_posix()}/")
        )
        entry = parsed["entry"]
        node["entry"] = entry
        node["text"] = str(entry.get("summary", ""))
        entry_path = path.relative_to(repo_root).as_posix()
        properties.update(
            {
                "entry_authority": entry_path,
                "entry_sha256": entry_hashes[entry_path],
                "entry_source": source,
                "entry_source_sha256": metadata["kgd_source_sha256"],
                "entry_source_current_sha256": current_source_sha,
                "entry_origin": metadata["kgd_entry_origin"],
                "curated_definition_sha256": metadata["kgd_definition_sha256"],
            }
        )
        properties["curation_status"] = (
            "current"
            if (
                direct_markdown_source
                or current_source_sha == metadata["kgd_source_sha256"]
            )
            and current_definition_sha == metadata["kgd_definition_sha256"]
            and label_matches
            else "needs-review"
        )
    return dict(sorted(entry_hashes.items()))
