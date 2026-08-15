"""Strict Obsidian-native concept and taxonomy note contracts."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Union

import yaml
from yaml.constructor import ConstructorError
from yaml.events import AliasEvent
from yaml.nodes import MappingNode

from .cli import ID_RE, MAX_NODE_LABEL_LENGTH


CONCEPT_SCHEMA = "qlkg-concept-v1"
TAXONOMY_SCHEMA = "qlkg-taxonomy-v1"
MAX_NOTE_BYTES = 8 * 1024 * 1024
MAX_FRONTMATTER_BYTES = 64 * 1024
MAX_FRONTMATTER_KEYS = 256
MAX_LIST_ITEMS = 4096
MAX_YAML_DEPTH = 64
_H1_RE = re.compile(r"^ {0,3}#[ \t]+(?P<label>.*?)(?:[ \t]+#+)?[ \t]*$")
_FENCE_RE = re.compile(r"^ {0,3}(?P<marker>`{3,}|~{3,})(?P<info>.*)$")
_HTML_BLOCK_TAG_RE = re.compile(
    r"^</?(?:address|article|aside|base|basefont|blockquote|body|caption|center|"
    r"col|colgroup|dd|details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|"
    r"footer|form|frame|frameset|h[1-6]|head|header|hr|html|iframe|legend|li|"
    r"link|main|menu|menuitem|nav|noframes|ol|optgroup|option|p|param|search|"
    r"section|summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul)"
    r"(?:[\s/>]|$)",
    re.IGNORECASE,
)
_HTML_COMPLETE_TAG_RE = re.compile(
    r"^</?[A-Za-z][A-Za-z0-9-]*(?:\s+[^<>]*)?/?>\s*$"
)
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_LINK_SEMANTICS_RE = re.compile(r"[\[\]#^\x00-\x1f\x7f]")
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
_CONCEPT_KEYS = {
    "kgd_schema",
    "kgd_id",
    "aliases",
    "tags",
    "kgd_fields",
    "kgd_topics",
    "kgd_prerequisites",
    "kgd_implies",
    "kgd_generalizes",
    "kgd_contrasts_with",
    "kgd_derived_from",
}
_TAXONOMY_KEYS = {"kgd_schema", "kgd_id", "kgd_kind", "aliases", "kgd_parents"}


class NativeNoteError(ValueError):
    """A stable native-note validation failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        authority: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.authority = authority

    def payload(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "kind": "kgdistiller-knowledge-error",
            "code": self.code,
            "message": self.message,
        }
        if self.authority:
            result["details"] = {"authority": self.authority}
        return result


class _KgdSafeLoader(yaml.SafeLoader):
    """SafeLoader with identity-amplifying YAML features disabled."""

    yaml_implicit_resolvers = {
        key: [
            resolver
            for resolver in value
            if resolver[0] != "tag:yaml.org,2002:timestamp"
        ]
        for key, value in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }

    def compose_node(self, parent: Any, index: Any) -> Any:
        depth = getattr(self, "_kgd_depth", 0) + 1
        if depth > MAX_YAML_DEPTH:
            event = self.peek_event()
            raise ConstructorError(
                None,
                None,
                f"YAML nesting exceeds {MAX_YAML_DEPTH}",
                event.start_mark,
            )
        self._kgd_depth = depth
        event = self.peek_event()
        try:
            if isinstance(event, AliasEvent):
                raise ConstructorError(None, None, "YAML aliases are forbidden", event.start_mark)
            if getattr(event, "anchor", None) is not None:
                raise ConstructorError(None, None, "YAML anchors are forbidden", event.start_mark)
            if getattr(event, "tag", None) is not None:
                raise ConstructorError(
                    None, None, "explicit YAML tags are forbidden", event.start_mark
                )
            return super().compose_node(parent, index)
        finally:
            self._kgd_depth = depth - 1

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
        if not isinstance(node, MappingNode):
            raise ConstructorError(None, None, "expected a YAML mapping", node.start_mark)
        result: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in result
            except TypeError as error:
                raise ConstructorError(
                    None, None, "YAML mapping keys must be scalar", key_node.start_mark
                ) from error
            if duplicate:
                raise ConstructorError(
                    None, None, f"duplicate YAML key: {key!r}", key_node.start_mark
                )
            result[key] = self.construct_object(value_node, deep=deep)
        return result


@dataclass(frozen=True)
class NoteLink:
    target: str
    display: str | None = None

    def render(self) -> str:
        suffix = f"|{self.display}" if self.display else ""
        return f"[[{self.target}{suffix}]]"


@dataclass(frozen=True)
class ConceptNote:
    path: Path
    authority: str
    id: str
    label: str
    aliases: tuple[str, ...]
    tags: tuple[str, ...]
    fields: tuple[NoteLink, ...]
    topics: tuple[NoteLink, ...]
    prerequisites: tuple[NoteLink, ...]
    implies: tuple[NoteLink, ...]
    generalizes: tuple[NoteLink, ...]
    contrasts_with: tuple[NoteLink, ...]
    derived_from: tuple[NoteLink, ...]
    body: str
    definition_text: str
    definition_sha256: str
    h1_line: int
    end_line: int
    normalized_text: str
    frontmatter: Mapping[str, Any]
    raw_frontmatter: bytes


@dataclass(frozen=True)
class TaxonomyNote:
    path: Path
    authority: str
    id: str
    kind: str
    label: str
    aliases: tuple[str, ...]
    parents: tuple[NoteLink, ...]
    body: str
    definition_text: str
    definition_sha256: str
    h1_line: int
    end_line: int
    normalized_text: str
    frontmatter: Mapping[str, Any]
    raw_frontmatter: bytes


NativeNote = Union[ConceptNote, TaxonomyNote]


def _line_content(line: bytes) -> bytes:
    if line.endswith(b"\r\n"):
        return line[:-2]
    if line.endswith((b"\n", b"\r")):
        return line[:-1]
    return line


def _frontmatter_parts(data: bytes, authority: str) -> tuple[str, str, bytes, int]:
    if len(data) > MAX_NOTE_BYTES:
        raise NativeNoteError(
            "note-too-large", f"native note exceeds {MAX_NOTE_BYTES} bytes", authority=authority
        )
    raw_lines = data.splitlines(keepends=True)
    if not raw_lines or _line_content(raw_lines[0]) != b"---":
        raise NativeNoteError(
            "missing-frontmatter",
            "native note must begin with YAML frontmatter",
            authority=authority,
        )
    closing = next(
        (
            index
            for index, line in enumerate(raw_lines[1:], start=1)
            if _line_content(line) == b"---"
        ),
        None,
    )
    if closing is None:
        raise NativeNoteError(
            "missing-frontmatter-end", "native note frontmatter is not closed", authority=authority
        )
    raw_frontmatter = b"".join(raw_lines[1:closing])
    if len(raw_frontmatter) > MAX_FRONTMATTER_BYTES:
        raise NativeNoteError(
            "frontmatter-too-large",
            f"native note frontmatter exceeds {MAX_FRONTMATTER_BYTES} bytes",
            authority=authority,
        )
    try:
        full_text = data.decode("utf-8", errors="strict")
        frontmatter_text = raw_frontmatter.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise NativeNoteError(
            "invalid-note-utf8", "native note must be strict UTF-8", authority=authority
        ) from error
    normalized = full_text.replace("\r\n", "\n").replace("\r", "\n")
    normalized_frontmatter = frontmatter_text.replace("\r\n", "\n").replace("\r", "\n")
    normalized_lines = normalized.split("\n")
    if closing >= len(normalized_lines) or normalized_lines[closing] != "---":
        raise NativeNoteError(
            "invalid-frontmatter", "frontmatter line structure is inconsistent", authority=authority
        )
    content = "\n".join(normalized_lines[closing + 1 :])
    return normalized_frontmatter, content, raw_frontmatter, closing + 2


def _simple_frontmatter(text: str, authority: str) -> dict[str, Any]:
    try:
        payload = yaml.load(text, Loader=_KgdSafeLoader)
    except (yaml.YAMLError, RecursionError) as error:
        raise NativeNoteError(
            "invalid-frontmatter", f"invalid native-note YAML: {error}", authority=authority
        ) from error
    if not isinstance(payload, dict):
        raise NativeNoteError(
            "invalid-frontmatter", "native-note frontmatter must be a mapping", authority=authority
        )
    if len(payload) > MAX_FRONTMATTER_KEYS:
        raise NativeNoteError(
            "frontmatter-too-large",
            f"native-note frontmatter exceeds {MAX_FRONTMATTER_KEYS} keys",
            authority=authority,
        )
    for key, value in payload.items():
        if not isinstance(key, str) or not key:
            raise NativeNoteError(
                "invalid-frontmatter",
                "frontmatter keys must be non-empty strings",
                authority=authority,
            )
        values = value if isinstance(value, list) else [value]
        if isinstance(value, (dict, tuple, set)) or len(values) > MAX_LIST_ITEMS:
            raise NativeNoteError(
                "invalid-frontmatter",
                "frontmatter values must be bounded simple scalars or scalar lists",
                authority=authority,
            )
        for item in values:
            if isinstance(item, (dict, list, tuple, set)) or not isinstance(
                item, (str, int, float, bool, type(None))
            ):
                raise NativeNoteError(
                    "invalid-frontmatter",
                    "frontmatter lists may contain only simple scalars",
                    authority=authority,
                )
            if isinstance(item, float) and not math.isfinite(item):
                raise NativeNoteError(
                    "invalid-frontmatter", "frontmatter numbers must be finite", authority=authority
                )
    return payload


def _required_string(payload: Mapping[str, Any], key: str, authority: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise NativeNoteError(
            "invalid-frontmatter", f"{key} must be a non-empty string", authority=authority
        )
    normalized = value.strip()
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise NativeNoteError(
            "invalid-frontmatter",
            f"{key} must not contain control characters",
            authority=authority,
        )
    return normalized


def _string_list(payload: Mapping[str, Any], key: str, authority: str) -> tuple[str, ...]:
    value = payload.get(key, [])
    if not isinstance(value, list) or len(value) > MAX_LIST_ITEMS:
        raise NativeNoteError(
            "invalid-frontmatter", f"{key} must be a bounded string list", authority=authority
        )
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise NativeNoteError(
                "invalid-frontmatter",
                f"{key} entries must be non-empty strings",
                authority=authority,
            )
        normalized = item.strip()
        if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
            raise NativeNoteError(
                "invalid-frontmatter",
                f"{key} entries must not contain control characters",
                authority=authority,
            )
        if normalized in result:
            raise NativeNoteError(
                "duplicate-frontmatter-value",
                f"{key} contains a duplicate value",
                authority=authority,
            )
        result.append(normalized)
    return tuple(result)


def parse_note_link(value: str, *, authority: str | None = None) -> NoteLink:
    """Parse one exact, qualified, vault-relative Obsidian Wikilink."""

    if (
        not isinstance(value, str)
        or not value.startswith("[[")
        or not value.endswith("]]")
    ):
        raise NativeNoteError(
            "invalid-note-link",
            "native relation values must be Obsidian Wikilinks",
            authority=authority,
        )
    inner = value[2:-2]
    target, separator, display = inner.partition("|")
    if target != target.strip():
        raise NativeNoteError(
            "invalid-note-link",
            "Obsidian link target must not have padding",
            authority=authority,
        )
    display_value = display.strip() if separator else None
    if separator and (
        not display_value
        or "|" in display
        or any(ord(character) < 32 or ord(character) == 127 for character in display)
        or any(character in "[]" for character in display)
    ):
        raise NativeNoteError(
            "invalid-note-link",
            "Obsidian link display text is invalid",
            authority=authority,
        )
    if (
        not target
        or "\\" in target
        or _DRIVE_RE.match(target)
        or _LINK_SEMANTICS_RE.search(target)
        or len(target.encode("utf-8")) > 4096
    ):
        raise NativeNoteError(
            "invalid-note-link",
            "Obsidian link target is not a safe vault-relative path",
            authority=authority,
        )
    if target.endswith(".md"):
        target = target[:-3]
    elif target.casefold().endswith(".md"):
        raise NativeNoteError(
            "invalid-note-link",
            "Obsidian link suffix must use exact .md case",
            authority=authority,
        )
    if unicodedata.normalize("NFC", target) != target:
        raise NativeNoteError(
            "invalid-note-link",
            "Obsidian link targets must use Unicode NFC normalization",
            authority=authority,
        )
    path = PurePosixPath(target)
    if (
        path.is_absolute()
        or len(path.parts) < 2
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(
            part.endswith((" ", "."))
            or any(character in '<>:"|?*' for character in part)
            or part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED
            for part in path.parts
        )
        or path.as_posix() != target
    ):
        raise NativeNoteError(
            "invalid-note-link",
            "Obsidian links must use a qualified canonical vault-relative path",
            authority=authority,
        )
    return NoteLink(target=target, display=display_value)


def _link_list(payload: Mapping[str, Any], key: str, authority: str) -> tuple[NoteLink, ...]:
    values = _string_list(payload, key, authority)
    links = tuple(parse_note_link(value, authority=authority) for value in values)
    targets = [item.target for item in links]
    if len(targets) != len(set(targets)):
        raise NativeNoteError(
            "duplicate-note-link",
            f"{key} resolves the same path more than once",
            authority=authority,
        )
    return links


def _entry(
    content: str, first_content_line: int, authority: str
) -> tuple[str, str, str, int, int, str]:
    lines = content.split("\n")
    fence_character: str | None = None
    fence_length = 0
    html_end: str | None = None
    html_until_blank = False
    for index, line in enumerate(lines):
        if fence_character is not None:
            stripped = line.lstrip(" ")
            indent = len(line) - len(stripped)
            candidate = stripped.rstrip(" \t")
            if (
                indent <= 3
                and len(candidate) >= fence_length
                and set(candidate) == {fence_character}
            ):
                fence_character = None
                fence_length = 0
            continue
        if html_end is not None:
            if html_end in line.casefold():
                html_end = None
            continue
        if html_until_blank:
            if not line.strip():
                html_until_blank = False
            continue
        stripped_html = line.lstrip(" ")
        html_indent = len(line) - len(stripped_html)
        lowered_html = stripped_html.casefold()
        if html_indent <= 3 and lowered_html.startswith("<!--"):
            if "-->" not in lowered_html[4:]:
                html_end = "-->"
            continue
        if html_indent <= 3 and lowered_html.startswith("<?"):
            if "?>" not in lowered_html[2:]:
                html_end = "?>"
            continue
        if html_indent <= 3 and lowered_html.startswith("<![cdata["):
            if "]]>" not in lowered_html[9:]:
                html_end = ']]>'
            continue
        if (
            html_indent <= 3
            and re.match(r"^<![A-Z]", stripped_html)
        ):
            if ">" not in stripped_html[2:]:
                html_end = ">"
            continue
        raw_html = next(
            (
                tag
                for tag in ("script", "pre", "style", "textarea")
                if re.match(rf"<{tag}(?:[\s>]|$)", lowered_html)
            ),
            None,
        )
        if html_indent <= 3 and raw_html is not None:
            closing = f"</{raw_html}>"
            if closing not in lowered_html:
                html_end = closing
            continue
        if html_indent <= 3 and (
            _HTML_BLOCK_TAG_RE.match(stripped_html)
            or _HTML_COMPLETE_TAG_RE.fullmatch(stripped_html)
        ):
            html_until_blank = True
            continue
        fence = _FENCE_RE.fullmatch(line)
        if fence is not None:
            marker = fence.group("marker")
            info = fence.group("info")
            if marker[0] != "`" or "`" not in info:
                fence_character = marker[0]
                fence_length = len(marker)
                continue
        if line.startswith("\t") or line.startswith("    "):
            continue
        match = _H1_RE.fullmatch(line)
        if match is None:
            continue
        label = match.group("label").strip()
        if (
            not label
            or len(label) > MAX_NODE_LABEL_LENGTH
            or any(ord(character) < 32 or ord(character) == 127 for character in label)
        ):
            raise NativeNoteError(
                "invalid-note-label",
                f"first H1 must contain at most {MAX_NODE_LABEL_LENGTH} characters",
                authority=authority,
            )
        definition_text = "\n".join(lines[index:])
        body = "\n".join(lines[index + 1 :])
        if body.startswith("\n"):
            body = body[1:]
        body = body.rstrip("\n")
        h1_line = first_content_line + index
        end_line = first_content_line + max(index, len(lines) - 1)
        digest = hashlib.sha256(definition_text.encode("utf-8")).hexdigest()
        return label, body, definition_text, h1_line, max(h1_line, end_line), digest
    raise NativeNoteError(
        "missing-note-label",
        "native note must contain an H1 display label",
        authority=authority,
    )


def _reject_unknown_kgd(payload: Mapping[str, Any], allowed: set[str], authority: str) -> None:
    unknown = sorted(key for key in payload if key.startswith("kgd_") and key not in allowed)
    if unknown:
        raise NativeNoteError(
            "unknown-kgd-property",
            f"unknown kgdistiller frontmatter properties: {', '.join(unknown)}",
            authority=authority,
        )


def parse_native_markdown(
    data: bytes,
    *,
    authority: str,
    path: Path | None = None,
) -> NativeNote:
    """Parse one bounded UTF-8 native note without inferring its identity."""

    if (
        unicodedata.normalize("NFC", authority) != authority
        or any(ord(character) < 32 or ord(character) == 127 for character in authority)
    ):
        raise NativeNoteError(
            "invalid-note-authority",
            "native-note authority must be control-free Unicode NFC",
            authority=authority,
        )
    frontmatter_text, content, raw_frontmatter, first_content_line = _frontmatter_parts(
        data, authority
    )
    payload = _simple_frontmatter(frontmatter_text, authority)
    schema = _required_string(payload, "kgd_schema", authority)
    node_id = _required_string(payload, "kgd_id", authority)
    if not ID_RE.fullmatch(node_id):
        raise NativeNoteError(
            "invalid-note-id",
            "kgd_id must be bounded lowercase ASCII kebab-case",
            authority=authority,
        )
    label, body, definition_text, h1_line, end_line, definition_sha256 = _entry(
        content, first_content_line, authority
    )
    try:
        normalized_text = (
            data.decode("utf-8", errors="strict")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
        )
    except UnicodeDecodeError as error:
        raise NativeNoteError(
            "invalid-note-utf8", "native note must be strict UTF-8", authority=authority
        ) from error
    note_path = path or Path(*PurePosixPath(authority).parts)
    aliases = _string_list(payload, "aliases", authority)
    if schema == CONCEPT_SCHEMA:
        _reject_unknown_kgd(payload, _CONCEPT_KEYS, authority)
        return ConceptNote(
            path=note_path,
            authority=authority,
            id=node_id,
            label=label,
            aliases=aliases,
            tags=_string_list(payload, "tags", authority),
            fields=_link_list(payload, "kgd_fields", authority),
            topics=_link_list(payload, "kgd_topics", authority),
            prerequisites=_link_list(payload, "kgd_prerequisites", authority),
            implies=_link_list(payload, "kgd_implies", authority),
            generalizes=_link_list(payload, "kgd_generalizes", authority),
            contrasts_with=_link_list(payload, "kgd_contrasts_with", authority),
            derived_from=_link_list(payload, "kgd_derived_from", authority),
            body=body,
            definition_text=definition_text,
            definition_sha256=definition_sha256,
            h1_line=h1_line,
            end_line=end_line,
            normalized_text=normalized_text,
            frontmatter=dict(payload),
            raw_frontmatter=raw_frontmatter,
        )
    if schema == TAXONOMY_SCHEMA:
        _reject_unknown_kgd(payload, _TAXONOMY_KEYS, authority)
        kind = _required_string(payload, "kgd_kind", authority)
        if kind not in {"field", "topic"}:
            raise NativeNoteError(
                "invalid-taxonomy-kind", "kgd_kind must be field or topic", authority=authority
            )
        parents = _link_list(payload, "kgd_parents", authority)
        if (kind == "field" and parents) or (kind == "topic" and not parents):
            raise NativeNoteError(
                "invalid-taxonomy-parents",
                "fields have no parents and topics require at least one field parent",
                authority=authority,
            )
        return TaxonomyNote(
            path=note_path,
            authority=authority,
            id=node_id,
            kind=kind,
            label=label,
            aliases=aliases,
            parents=parents,
            body=body,
            definition_text=definition_text,
            definition_sha256=definition_sha256,
            h1_line=h1_line,
            end_line=end_line,
            normalized_text=normalized_text,
            frontmatter=dict(payload),
            raw_frontmatter=raw_frontmatter,
        )
    raise NativeNoteError(
        "invalid-note-schema",
        f"kgd_schema must be {CONCEPT_SCHEMA} or {TAXONOMY_SCHEMA}",
        authority=authority,
    )


def _yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _render_list(key: str, values: tuple[str, ...]) -> list[str]:
    if not values:
        return [f"{key}: []"]
    return [f"{key}:", *(f"  - {_yaml_string(value)}" for value in values)]


def render_concept_note(note: ConceptNote) -> str:
    """Render a canonical new concept note; transactional edits preserve raw user YAML."""

    lines = [
        "---",
        f"kgd_schema: {_yaml_string(CONCEPT_SCHEMA)}",
        f"kgd_id: {_yaml_string(note.id)}",
        *_render_list("aliases", note.aliases),
        *_render_list("tags", note.tags),
        *_render_list("kgd_fields", tuple(link.render() for link in note.fields)),
        *_render_list("kgd_topics", tuple(link.render() for link in note.topics)),
        *_render_list("kgd_prerequisites", tuple(link.render() for link in note.prerequisites)),
        *_render_list("kgd_implies", tuple(link.render() for link in note.implies)),
        *_render_list("kgd_generalizes", tuple(link.render() for link in note.generalizes)),
        *_render_list("kgd_contrasts_with", tuple(link.render() for link in note.contrasts_with)),
        *_render_list("kgd_derived_from", tuple(link.render() for link in note.derived_from)),
        "---",
        "",
        f"# {note.label}",
    ]
    if note.body:
        lines.extend(["", note.body.rstrip("\n")])
    return "\n".join(lines) + "\n"


def render_taxonomy_note(note: TaxonomyNote) -> str:
    """Render a canonical new field/topic note."""

    lines = [
        "---",
        f"kgd_schema: {_yaml_string(TAXONOMY_SCHEMA)}",
        f"kgd_id: {_yaml_string(note.id)}",
        f"kgd_kind: {_yaml_string(note.kind)}",
        *_render_list("aliases", note.aliases),
        *_render_list("kgd_parents", tuple(link.render() for link in note.parents)),
        "---",
        "",
        f"# {note.label}",
    ]
    if note.body:
        lines.extend(["", note.body.rstrip("\n")])
    return "\n".join(lines) + "\n"


__all__ = [
    "CONCEPT_SCHEMA",
    "TAXONOMY_SCHEMA",
    "ConceptNote",
    "NativeNote",
    "NativeNoteError",
    "NoteLink",
    "TaxonomyNote",
    "parse_native_markdown",
    "parse_note_link",
    "render_concept_note",
    "render_taxonomy_note",
]
