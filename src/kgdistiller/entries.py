"""The reviewed entry file: one knowledge node per ``.knowledge/entries/<id>.md``.

An entry is ordinary Markdown with YAML frontmatter that Obsidian renders as
properties. The frontmatter holds the node's identity and source binding; the
body holds the human sections and a verbatim Evidence quote of the cited
source lines. The reader accepts a restricted YAML subset and rejects anything
it does not understand.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any

from .home import KNOWLEDGE_DIRECTORY

ENTRY_SCHEMA = "kgdistiller-entry-v1"
ENTRIES_DIR = Path(KNOWLEDGE_DIRECTORY, "entries")
UNDERSTANDING_STATES = ("unknown", "not-yet-understood", "understood")
FRONTMATTER_KEYS = (
    "schema", "id", "label", "kind", "aliases", "source", "line_start", "line_end",
    "understanding",
)
TEXT_SECTIONS = ("summary", "context", "role")
LIST_SECTIONS = (
    "prerequisites", "pending_prerequisites", "common_confusions", "open_questions",
)
SECTION_TITLES = {
    "summary": "Summary",
    "context": "Context",
    "role": "Role",
    "prerequisites": "Prerequisites",
    "pending_prerequisites": "Pending prerequisites",
    "common_confusions": "Common confusions",
    "open_questions": "Open questions",
    "evidence": "Evidence",
}
RECORD_KEYS = (*(key for key in FRONTMATTER_KEYS if key != "schema"),
               *TEXT_SECTIONS, *LIST_SECTIONS, "evidence")
REQUIRED_KEYS = frozenset({*FRONTMATTER_KEYS, "summary", "evidence"} - {"schema"})
ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MAX_ID_LENGTH = 200
MAX_LABEL_LENGTH = 1024
WINDOWS_RESERVED = frozenset({
    "con", "prn", "aux", "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
})
_TITLE_KEYS = {title.casefold(): key for key, title in SECTION_TITLES.items()}
_SECTION_HEADING = re.compile(r"##[ \t]+(.+?)[ \t]*")
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")
_INTEGER = re.compile(r"(?:0|[1-9][0-9]*)")
_PLAIN_UNSAFE_START = set("-?:,[]{}#&*!|>'\"%@` \t")
_PLAIN_RESERVED = frozenset({
    "", "~", "null", "true", "false", "yes", "no", "on", "off", "y", "n",
})


class EntryError(ValueError):
    """An entry file or record violates the entry contract."""


def validate_id(value: Any) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise EntryError(f"entry id must be a lowercase ASCII slug like 'measure-space': {value!r}")
    if len(value) > MAX_ID_LENGTH:
        raise EntryError(f"entry id exceeds {MAX_ID_LENGTH} characters: {value[:40]}...")
    if value in WINDOWS_RESERVED:
        raise EntryError(f"entry id is a reserved file name: {value!r}")
    return value


def slug_id(label: str) -> str | None:
    """Derive the readable default id of a label, or None when it has none."""
    ascii_text = "".join(
        character if character.isascii() else ("" if unicodedata.combining(character) else " ")
        for character in unicodedata.normalize("NFKD", label)
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    slug = slug[:MAX_ID_LENGTH].rstrip("-")
    if not slug or slug in WINDOWS_RESERVED:
        return None
    return slug


def identity_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def entry_relative(entry_id: str) -> Path:
    return ENTRIES_DIR / f"{validate_id(entry_id)}.md"


def normalize_evidence(text: str) -> str:
    return " ".join(text.split())


def split_lines(text: str) -> list[str]:
    """Split LF-normalized text into its 1-based lines (index 0 is line 1)."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def cited_text(lines: list[str], start: int, end: int) -> str:
    return "\n".join(lines[start - 1:end])


def locate_evidence(source_text: str, evidence: str) -> list[tuple[int, int]]:
    """Find every whole-line range whose whitespace tokens equal the evidence's tokens.

    Evidence always quotes whole cited lines, so a candidate range starts at the
    first token of a nonblank line and ends at the last token of a nonblank line.
    """
    wanted = evidence.split()
    if not wanted:
        return []
    line_tokens = [line.split() for line in split_lines(source_text)]
    size = len(wanted)
    ranges: list[tuple[int, int]] = []
    for start, tokens in enumerate(line_tokens):
        if not tokens or tokens[0] != wanted[0]:
            continue
        collected: list[str] = []
        for end in range(start, len(line_tokens)):
            collected.extend(line_tokens[end])
            if len(collected) >= size:
                if collected == wanted:
                    ranges.append((start + 1, end + 1))
                break
    return ranges


def _single_line(value: Any, field: str, *, limit: int = MAX_LABEL_LENGTH) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or any(ord(character) < 32 or character in "\x7f  " for character in value)
    ):
        raise EntryError(f"{field} must be nonempty single-line text without surrounding whitespace")
    if len(value) > limit:
        raise EntryError(f"{field} exceeds {limit} characters")
    return value


def validate_source_path(value: Any) -> str:
    """Check a base-relative POSIX source path lexically."""
    if not isinstance(value, str) or not value or "\\" in value or any(
        ord(character) < 32 for character in value
    ):
        raise EntryError(f"source must be a base-relative POSIX path: {value!r}")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or re.match(r"^[A-Za-z]:", value)
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise EntryError(f"source must be a safe base-relative path without '..': {value!r}")
    if path.parts[0] == KNOWLEDGE_DIRECTORY:
        raise EntryError(f"source must be outside the {KNOWLEDGE_DIRECTORY}/ tree: {value!r}")
    return value


def _line_number(value: Any, field: str) -> int:
    if type(value) is not int or value < 1:
        raise EntryError(f"{field} must be a positive integer")
    return value


def _text_section(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise EntryError(f"{field} must be text")
    if "\r" in value:
        raise EntryError(f"{field} must use LF line endings")
    return re.sub(r"\A(?:[ \t]*\n)+", "", value).rstrip()


def _list_section(value: Any, field: str, *, limit: int = 16384) -> list[str]:
    if not isinstance(value, list):
        raise EntryError(f"{field} must be a list of single-line items")
    return [_single_line(item, f"{field} item", limit=limit) for item in value]


def normalize_record(raw: Any) -> dict[str, Any]:
    """Validate an entry record and return its canonical form.

    Optional sections are omitted when empty; required fields are always present.
    """
    if not isinstance(raw, dict):
        raise EntryError("entry must be an object")
    unknown = sorted(set(raw) - set(RECORD_KEYS))
    if unknown:
        raise EntryError(f"unknown entry fields: {', '.join(unknown)}")
    missing = sorted(REQUIRED_KEYS - set(raw))
    if missing:
        raise EntryError(f"entry is missing: {', '.join(missing)}")
    label = _single_line(raw["label"], "label")
    record: dict[str, Any] = {
        "id": validate_id(raw["id"]),
        "label": label,
        "kind": _single_line(raw["kind"], "kind"),
    }
    aliases = _list_section(raw["aliases"], "aliases", limit=MAX_LABEL_LENGTH)
    keys = [identity_key(alias) for alias in aliases]
    if len(set(keys)) != len(keys):
        raise EntryError(f"aliases of {record['id']} must be unique")
    if identity_key(label) in keys:
        raise EntryError(f"aliases of {record['id']} must not repeat its label")
    record["aliases"] = aliases
    record["source"] = validate_source_path(raw["source"])
    start = _line_number(raw["line_start"], "line_start")
    end = _line_number(raw["line_end"], "line_end")
    if start > end:
        raise EntryError("line_start must not exceed line_end")
    record["line_start"], record["line_end"] = start, end
    if raw["understanding"] not in UNDERSTANDING_STATES:
        raise EntryError(f"understanding must be one of {', '.join(UNDERSTANDING_STATES)}")
    record["understanding"] = raw["understanding"]
    for field in TEXT_SECTIONS:
        if field in raw:
            value = _text_section(raw[field], field)
            if value:
                record[field] = value
    if "summary" not in record:
        raise EntryError("summary must not be empty")
    for field in LIST_SECTIONS:
        if field in raw:
            items = _list_section(raw[field], field)
            if items:
                record[field] = items
    evidence = raw["evidence"]
    if not isinstance(evidence, str) or not evidence.strip():
        raise EntryError("evidence must quote nonempty source text")
    if "\r" in evidence:
        raise EntryError("evidence must use LF line endings")
    record["evidence"] = evidence
    return {key: record[key] for key in RECORD_KEYS if key in record}


def _scalar(value: str) -> str:
    plain = (
        value[:1] not in _PLAIN_UNSAFE_START
        and value == value.strip()
        and value.casefold() not in _PLAIN_RESERVED
        and not value[:1].isdigit()
        and value[:1] not in {"+", "."}
        and ": " not in value
        and " #" not in value
        and not value.endswith(":")
        and all(ord(character) >= 32 and character not in "\x7f  ﻿"
                for character in value)
    )
    return value if plain else json.dumps(value, ensure_ascii=False)


def _fence(text: str) -> str:
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def render_entry(raw: dict[str, Any]) -> str:
    record = normalize_record(raw)
    lines = ["---", f"schema: {ENTRY_SCHEMA}"]
    for key in FRONTMATTER_KEYS[1:]:
        value = record[key]
        if key == "aliases":
            lines.extend(["aliases: []"] if not value else
                         ["aliases:", *(f"  - {_scalar(alias)}" for alias in value)])
        elif isinstance(value, int):
            lines.append(f"{key}: {value}")
        else:
            lines.append(f"{key}: {_scalar(value)}")
    lines.extend(["---", "", f"# {record['label']}"])
    for field in (*TEXT_SECTIONS, *LIST_SECTIONS):
        if field not in record:
            continue
        lines.extend(["", f"## {SECTION_TITLES[field]}", ""])
        if field in LIST_SECTIONS:
            lines.extend(f"- {item}" for item in record[field])
        else:
            lines.append(record[field])
    fence = _fence(record["evidence"])
    lines.extend(["", "## Evidence", "", fence, record["evidence"], fence])
    text = "\n".join(lines) + "\n"
    try:
        represented = parse_entry(text, Path(f"{record['id']}.md")) == record
    except EntryError:
        represented = False
    if not represented:
        raise EntryError(f"entry {record['id']} cannot be represented as Markdown sections")
    return text


def _parse_scalar(raw: str, field: str, path: Path) -> str:
    value = raw.strip()
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise EntryError(f"{path}: {field} has an unsupported double-quoted scalar") from error
        if not isinstance(parsed, str):
            raise EntryError(f"{path}: {field} must be a string")
        return parsed
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'") or re.search(r"(?<!')'(?!')", value[1:-1]):
            raise EntryError(f"{path}: {field} has an unsupported single-quoted scalar")
        return value[1:-1].replace("''", "'")
    if not value:
        raise EntryError(f"{path}: {field} must not be empty")
    if value[:1] in set("[]{}&*!|>%@`#") or " #" in value or ": " in value:
        raise EntryError(f"{path}: {field} uses YAML syntax outside the entry subset")
    return value


def _parse_frontmatter(lines: list[str], path: Path) -> tuple[dict[str, Any], int]:
    if not lines or lines[0] != "---":
        raise EntryError(f"{path}: entry must start with YAML frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise EntryError(f"{path}: unterminated YAML frontmatter") from error
    values: dict[str, Any] = {}
    current_list: str | None = None
    for line in lines[1:end]:
        if not line.strip():
            continue
        item = re.fullmatch(r"\s*- (.*)", line) or re.fullmatch(r"\s*-()", line)
        if item is not None:
            if current_list is None:
                raise EntryError(f"{path}: list item outside a list property: {line!r}")
            values[current_list].append(_parse_scalar(item[1], current_list, path))
            continue
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*):(?:[ \t]+(.*))?", line)
        if match is None:
            raise EntryError(f"{path}: unsupported frontmatter line: {line!r}")
        key, raw = match[1], (match[2] or "").strip()
        if key not in FRONTMATTER_KEYS:
            raise EntryError(f"{path}: unknown frontmatter key {key!r}")
        if key in values:
            raise EntryError(f"{path}: duplicate frontmatter key {key!r}")
        current_list = None
        if key == "aliases":
            if raw == "[]" or not raw:
                values[key] = []
                current_list = None if raw else key
                continue
            raise EntryError(f"{path}: aliases must be a block list or []")
        if key in {"line_start", "line_end"}:
            if not _INTEGER.fullmatch(raw):
                raise EntryError(f"{path}: {key} must be an integer")
            values[key] = int(raw)
            continue
        values[key] = _parse_scalar(raw, key, path)
    missing = [key for key in FRONTMATTER_KEYS if key not in values]
    if missing:
        raise EntryError(f"{path}: frontmatter is missing {', '.join(missing)}")
    if values["schema"] != ENTRY_SCHEMA:
        raise EntryError(f"{path}: expected schema {ENTRY_SCHEMA}")
    return values, end + 1


def _fence_state(line: str, fence: str | None) -> str | None:
    match = _FENCE.fullmatch(line)
    if fence is None:
        return match[1] if match else None
    if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
        return None
    return fence


def _parse_evidence(lines: list[str], path: Path) -> str:
    while lines and not lines[-1].strip():
        lines.pop()
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    if len(lines) - start < 3:
        raise EntryError(f"{path}: Evidence must be one fenced quote")
    opening = lines[start]
    if not re.fullmatch(r"`{3,}", opening) or lines[-1] != opening:
        raise EntryError(f"{path}: Evidence must be one backtick fence without an info string")
    return "\n".join(lines[start + 1:-1])


def parse_entry(text: str, path: Path) -> dict[str, Any]:
    """Parse one entry file into its canonical record."""
    lines = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    metadata, index = _parse_frontmatter(lines, path)
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index >= len(lines) or not lines[index].startswith("# "):
        raise EntryError(f"{path}: entry body must start with '# <label>'")
    title = lines[index][2:].strip()
    if title != metadata["label"]:
        raise EntryError(f"{path}: heading {title!r} differs from label {metadata['label']!r}")
    sections: dict[str, list[str]] = {}
    current: str | None = None
    fence: str | None = None
    for line in lines[index + 1:]:
        heading = _SECTION_HEADING.fullmatch(line) if fence is None else None
        if heading:
            key = _TITLE_KEYS.get(heading[1].casefold())
            if key is None:
                raise EntryError(f"{path}: unknown entry section {heading[1]!r}")
            if key in sections:
                raise EntryError(f"{path}: duplicate entry section {heading[1]!r}")
            current = key
            sections[key] = []
            continue
        fence = _fence_state(line, fence)
        if current is None:
            if line.strip():
                raise EntryError(f"{path}: text outside an entry section: {line!r}")
            continue
        sections[current].append(line)
    if fence is not None:
        raise EntryError(f"{path}: unterminated code fence")
    record: dict[str, Any] = {key: value for key, value in metadata.items() if key != "schema"}
    for key, body in sections.items():
        if key == "evidence":
            record[key] = _parse_evidence(body, path)
        elif key in LIST_SECTIONS:
            items = []
            for line in body:
                if not line.strip():
                    continue
                if not line.startswith("- "):
                    raise EntryError(f"{path}: {SECTION_TITLES[key]} items must be '- ' lines")
                items.append(line[2:].strip())
            record[key] = items
        else:
            record[key] = "\n".join(body)
    if "evidence" not in record:
        raise EntryError(f"{path}: entry has no Evidence section")
    try:
        return normalize_record(record)
    except EntryError as error:
        raise EntryError(f"{path}: {error}") from error
