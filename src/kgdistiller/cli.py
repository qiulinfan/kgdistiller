"""Distill source documents into a deterministic, source-backed knowledge graph."""

from __future__ import annotations

import argparse
import codecs
import copy
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kgdistiller.knowledge_paths import KNOWLEDGE_DIRECTORY, knowledge_root

GRAPH_SCHEMA = "kgdistiller-graph-v2"
SOURCE_SCHEMA = "kgdistiller-sources-v1"
SOURCE_REGISTRY_KEYS = {"schema", "sources", "document_types"}
SOURCE_KEYS = {"id", "root", "files", "document_type"}
DELTA_SCHEMA = "kgdistiller-agent-delta-v1"
IDENTITY_SCHEMA = "kgdistiller-identities-v1"
ENTRY_AUTHORITY_SCHEMA = "kgdistiller-entry-index-v1"
ENTRY_SOURCE_INDEX_SCHEMA = "kgdistiller-entry-source-index-v1"
AGENT_SNAPSHOT_SCHEMA = "kgdistiller-agent-snapshot-v1"
MAX_NODE_ID_LENGTH = 256
MAX_NODE_LABEL_LENGTH = 1024
MAX_NAMESPACE_LENGTH = 256
ID_RE = re.compile(
    rf"(?=.{{1,{MAX_NODE_ID_LENGTH}}}\Z)[a-z0-9]+(?:-[a-z0-9]+)*"
)
NAMESPACE_RE = re.compile(
    rf"(?=.{{1,{MAX_NAMESPACE_LENGTH}}}\Z)[a-z0-9][a-z0-9._-]*"
    r"(?::[a-z0-9][a-z0-9._-]*)*"
)
KN_RE = re.compile(r"#kn\s*\[")
REF_RE = re.compile(r"#ref\s*\[")
LATEX_KN_RE = re.compile(r"\\kn\s*\{")
LATEX_REF_RE = re.compile(r"\\knref\s*\{")
MARKDOWN_WIKILINK_RE = re.compile(
    r"(?P<definition>(?<![!\\])--\[\[(?P<definition_body>[^\]\n]+)\]\]--)"
    r"|(?P<reference>(?<![!\-\\])\[\[(?P<reference_body>[^\]\n]+)\]\](?!--))"
)
STATEMENT_RE = re.compile(
    r"#(?P<kind>definition|theorem|lemma|corollary|proposition|axiom|example)\s*\("
)
SEMANTIC_RELATIONS = {
    "prerequisite-for",
    "implies",
    "generalizes",
    "contrasts-with",
    "derived-from",
}
ACYCLIC_RELATIONS = {"prerequisite-for"}
# Source markers rebuild every other property; these record reviewed curation.
RETAINED_SOURCE_NODE_PROPERTIES = frozenset({
    "aliases",
    "kind",
    "kind_origin",
    "source_kind",
    "display_name",
    "conditions",
    "curation_status",
    "curated_definition_sha256",
    "entry_authority",
    "entry_origin",
    "entry_sha256",
    "entry_source",
    "entry_source_sha256",
    "entry_source_current_sha256",
})
CURATION_STATUSES = {"current", "pending", "needs-review"}
CROSS_FILE_REF_ENDPOINTS = {
    "prerequisite-for": ("target", "source"),
    "generalizes": ("source", "target"),
    "derived-from": ("source", "target"),
}


class KnowledgeError(RuntimeError):
    """Raised when the graph contract cannot be satisfied."""


@dataclass(frozen=True)
class SourceSpec:
    id: str
    root: Path
    patterns: tuple[str, ...]
    document_type: str = ""


@dataclass(frozen=True)
class StatementRange:
    start: int
    end: int
    kind: str


@dataclass(frozen=True)
class DefinitionOccurrence:
    id: str
    label: str
    label_markup: str
    source_format: str
    kind: str
    authority: str
    line: int
    source_id: str
    position: int
    statement: StatementRange | None
    definition_sha256: str
    definition_start_line: int
    definition_end_line: int
    document_type: str = ""


@dataclass(frozen=True)
class ReferenceOccurrence:
    id: str
    target: str
    label: str
    authority: str
    line: int
    context: str | None
    source_format: str
    source_name: str


@dataclass
class ScanResult:
    definitions: list[DefinitionOccurrence]
    references: list[ReferenceOccurrence]
    errors: list[dict[str, Any]]


@dataclass
class GraphState:
    nodes: dict[str, dict[str, Any]]
    edges: dict[tuple[str, str, str], dict[str, Any]]
    references: list[dict[str, Any]]
    manifest: dict[str, Any]


def diagnostic(
    code: str,
    message: str,
    *,
    source: str | None = None,
    node: str | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {"code": code, "message": message}
    if source:
        value["source"] = source
    if node:
        value["node"] = node
    return value


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _json_backslash_replace(error: UnicodeError) -> tuple[str, int]:
    if not isinstance(error, UnicodeEncodeError):
        raise error
    escaped: list[str] = []
    for character in error.object[error.start : error.end]:
        codepoint = ord(character)
        if codepoint <= 0xFFFF:
            escaped.append(f"\\u{codepoint:04x}")
            continue
        codepoint -= 0x10000
        escaped.append(
            f"\\u{0xD800 + (codepoint >> 10):04x}"
            f"\\u{0xDC00 + (codepoint & 0x3FF):04x}"
        )
    return "".join(escaped), error.end


def configure_console_streams() -> None:
    """Escape unencodable console text as valid JSON Unicode escapes."""
    error_handler = "kgdistiller_json_backslashreplace"
    codecs.register_error(error_handler, _json_backslash_replace)
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors=error_handler)


def jsonl(values: Iterable[dict[str, Any]]) -> str:
    rendered = [json_text(value) for value in values]
    return "\n".join(rendered) + ("\n" if rendered else "")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    """Hash the exact bytes of a binary or byte-stable artifact."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_authority_file(path: Path) -> str:
    """Hash one UTF-8 authority after universal-newline normalization.

    Git may materialize a tracked Markdown, Typst, or LaTeX authority with LF,
    CRLF, or CR line endings.  The scanners consume those files in text mode,
    so ``source_hashes`` bind the same normalized text rather than a platform-
    specific checkout representation.  Keep byte-artifact hashing on
    :func:`sha256_file`.
    """

    with path.open("r", encoding="utf-8", newline=None) as handle:
        return sha256_text(handle.read())


def relative_path(repo_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError as error:
        raise KnowledgeError(f"source lies outside repository: {path}") from error


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return copy.deepcopy(default)
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def load_identity_registry(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.is_file():
        return {}
    payload = read_json(path, {})
    if payload.get("schema") != IDENTITY_SCHEMA:
        raise KnowledgeError(f"expected {IDENTITY_SCHEMA} identity registry: {path}")
    result: dict[str, dict[str, Any]] = {}
    names: dict[str, str] = {}
    for raw in payload.get("identities", []):
        node_id = str(raw.get("id", ""))
        if not ID_RE.fullmatch(node_id) or node_id in result:
            raise KnowledgeError(f"duplicate or invalid identity id: {node_id!r}")
        canonical_name = str(raw.get("canonical_name", "")).strip()
        aliases = list(dict.fromkeys(str(item).strip() for item in raw.get("aliases", []) if str(item).strip()))
        if not canonical_name:
            raise KnowledgeError(f"identity {node_id} has no canonical_name")
        for name in (canonical_name, *aliases):
            key = identity_key(name)
            existing = names.get(key)
            if existing and existing != node_id:
                raise KnowledgeError(
                    f"ambiguous registered knowledge name {name!r}: {existing!r} and {node_id!r}"
                )
            names[key] = node_id
        result[node_id] = {
            "id": node_id,
            "canonical_name": canonical_name,
            "aliases": aliases,
        }
    return result


def identity_registry_sha256(path: Path | None) -> str | None:
    return (
        sha256_text(json_text(read_json(path, {})))
        if path is not None and path.is_file()
        else None
    )


def source_registry_sha256(path: Path) -> str:
    """Hash a source registry's canonical JSON, independent of checkout newlines."""

    return sha256_text(json_text(read_json(path, {})))


def load_sources(repo_root: Path, registry: Path) -> list[SourceSpec]:
    from kgdistiller.document_types import parse_document_types, validate_document_type

    payload = read_json(registry, {})
    if not isinstance(payload, dict) or payload.get("schema") != SOURCE_SCHEMA:
        raise KnowledgeError(f"expected {SOURCE_SCHEMA} source registry: {registry}")
    unknown = sorted(set(payload) - SOURCE_REGISTRY_KEYS)
    if unknown:
        raise KnowledgeError(f"unknown source registry key {unknown[0]!r}: {registry}")
    profiles = parse_document_types(payload)
    repository = repo_root.resolve()
    result: list[SourceSpec] = []
    seen: set[str] = set()
    raw_sources = payload.get("sources", [])
    if not isinstance(raw_sources, list):
        raise KnowledgeError(f"source registry sources must be a list: {registry}")
    for raw in raw_sources:
        if not isinstance(raw, dict):
            raise KnowledgeError(f"source registry entries must be objects: {registry}")
        unknown = sorted(set(raw) - SOURCE_KEYS)
        if unknown:
            raise KnowledgeError(f"unknown key {unknown[0]!r} in source {raw.get('id', '')!r}")
        source_id = str(raw.get("id", ""))
        if not source_id or source_id in seen:
            raise KnowledgeError(f"duplicate or empty source id: {source_id!r}")
        seen.add(source_id)
        root_value = raw.get("root")
        if not isinstance(root_value, str) or not root_value:
            raise KnowledgeError(f"source {source_id} has no portable relative root")
        relative_root = Path(root_value)
        if relative_root.is_absolute() or ".." in relative_root.parts:
            raise KnowledgeError(
                f"source root must be a portable relative path for {source_id}: {root_value}"
            )
        lexical_root = repository / relative_root
        root = lexical_root.resolve()
        try:
            root.relative_to(repository)
        except ValueError as error:
            raise KnowledgeError(
                f"source root escapes repository for {source_id}: {root}"
            ) from error
        if lexical_root != root:
            raise KnowledgeError(
                f"source root must not traverse a symlink for {source_id}: {root_value}"
            )
        if not root.is_dir():
            raise KnowledgeError(f"missing source root for {source_id}: {root}")
        files = raw.get("files", [])
        if not isinstance(files, list) or not files or not all(
            isinstance(item, str) and item for item in files
        ):
            raise KnowledgeError(f"source {source_id} has no bounded file patterns")
        patterns = tuple(files)
        result.append(
            SourceSpec(
                id=source_id,
                root=root,
                patterns=patterns,
                document_type=(validate_document_type(raw["document_type"], profiles)
                               if "document_type" in raw else ""),
            )
        )
    return result


def _is_managed_build_path(path: Path) -> bool:
    """Keep disposable projections out of authority discovery unconditionally."""
    parts = tuple(part.casefold() for part in path.resolve().parts)
    return any(
        parts[index] == KNOWLEDGE_DIRECTORY and parts[index + 1] == "build"
        for index in range(max(0, len(parts) - 1))
    )


DEFINITION_SHEET_MARKER = "<!-- kgdistiller-projection: definition-sheet -->"


def _projection_header_offset(text: str) -> int:
    """Find the first content line after optional Markdown frontmatter."""
    lines = text.splitlines(keepends=True)
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index < len(lines) and lines[index].strip() == "---":
        end = next((i for i in range(index + 1, len(lines)) if lines[i].strip() in {"---", "..."}), None)
        if end is not None:
            index = end + 1
            while index < len(lines) and not lines[index].strip():
                index += 1
    return sum(len(line) for line in lines[:index])


def mark_definition_projection(text: str) -> str:
    """Declare a link view without replacing authored source markers."""
    if MARKDOWN_WIKILINK_RE.search(text):
        raise KnowledgeError("a definition sheet projection cannot contain native authority/reference markers")
    offset = _projection_header_offset(text)
    if text[offset:].splitlines()[:1] == [DEFINITION_SHEET_MARKER]:
        return text
    newline = "\r\n" if "\r\n" in text else "\n"
    return text[:offset] + DEFINITION_SHEET_MARKER + newline + text[offset:]


def is_source_projection(path: Path) -> bool:
    if path.suffix.lower() != ".md" or not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    offset = _projection_header_offset(text)
    if text[offset:].splitlines()[:1] != [DEFINITION_SHEET_MARKER]:
        return False
    if MARKDOWN_WIKILINK_RE.search(text):
        raise KnowledgeError(f"projection contains native knowledge markers: {path}")
    return True


def expand_source(spec: SourceSpec) -> list[Path]:
    files: set[Path] = set()
    for pattern in spec.patterns:
        files.update(
            path.resolve()
            for path in spec.root.glob(pattern)
            if path.is_file() and not _is_managed_build_path(path) and not is_source_projection(path)
        )
    return sorted(
        {preferred_source_path(spec, path) for path in files},
        key=lambda item: item.as_posix(),
    )


def glob_matches_path(relative: Path, pattern: str) -> bool:
    """Match one relative path with ``Path.glob`` segment semantics."""
    path_parts = relative.as_posix().split("/")
    pattern_parts = Path(pattern).as_posix().split("/")

    def matches(path_index: int, pattern_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        segment = pattern_parts[pattern_index]
        if segment == "**":
            return matches(path_index, pattern_index + 1) or (
                path_index < len(path_parts)
                and matches(path_index + 1, pattern_index)
            )
        return (
            path_index < len(path_parts)
            and fnmatch.fnmatchcase(path_parts[path_index], segment)
            and matches(path_index + 1, pattern_index + 1)
        )

    return matches(0, 0)


def _source_admits_path(spec: SourceSpec, path: Path) -> bool:
    """Check registry bounds independently of paired source preference."""
    if _is_managed_build_path(path) or is_source_projection(path):
        return False
    try:
        relative = path.resolve().relative_to(spec.root)
    except ValueError:
        return False
    return any(glob_matches_path(relative, pattern) for pattern in spec.patterns)


def preferred_source_path(spec: SourceSpec, path: Path) -> Path:
    """Choose an admitted TeX sibling over Typst in the same directory."""
    path = path.resolve()
    if path.suffix.lower() == ".typ":
        sibling = path.with_suffix(".tex")
    elif path.suffix.lower() == ".tex" and not path.is_file():
        # A deleted TeX authority exposes its still-registered Typst sibling.
        sibling = path.with_suffix(".typ")
    else:
        return path
    if sibling.is_file() and _source_admits_path(spec, sibling):
        return sibling.resolve()
    return path


def source_matches_path(spec: SourceSpec, path: Path) -> bool:
    """Return whether a path is an admitted, currently selected authority."""
    return (
        _source_admits_path(spec, path)
        and preferred_source_path(spec, path) == path.resolve()
    )


def matching_sources(specs: list[SourceSpec], path: Path) -> list[SourceSpec]:
    return [spec for spec in specs if source_matches_path(spec, path)]


def unique_source_for_path(
    specs: list[SourceSpec], path: Path, *, include_shadowed: bool = False
) -> SourceSpec:
    owners = (
        [spec for spec in specs if _source_admits_path(spec, path)]
        if include_shadowed
        else matching_sources(specs, path)
    )
    if len(owners) == 1:
        return owners[0]
    if len(owners) > 1:
        raise KnowledgeError(
            f"source file matches multiple registry sources: {path} "
            f"({', '.join(sorted(spec.id for spec in owners))})"
        )
    admitted = [spec for spec in specs if _source_admits_path(spec, path)]
    if admitted:
        siblings = sorted(
            {preferred_source_path(spec, path).as_posix() for spec in admitted}
        )
        raise KnowledgeError(
            f"source file is superseded by its registered sibling: {path}; "
            f"use {', '.join(siblings)}"
        )
    roots = [spec.id for spec in specs if path == spec.root or spec.root in path.parents]
    if roots:
        raise KnowledgeError(
            f"file is inside a configured source root but is not admitted by its "
            f"file patterns: {path} ({', '.join(sorted(roots))})"
        )
    raise KnowledgeError(f"file is outside configured source roots: {path}")


def source_format(path: Path) -> str:
    formats = {
        ".typ": "typst",
        ".md": "markdown",
        ".tex": "latex",
    }
    try:
        return formats[path.suffix.lower()]
    except KeyError as error:
        raise KnowledgeError(f"unsupported knowledge source format: {path}") from error


def find_matching(text: str, start: int, opening: str, closing: str) -> int:
    if start >= len(text) or text[start] != opening:
        raise KnowledgeError(f"expected {opening!r} at offset {start}")
    depth = 0
    content_depth = 0
    quote = False
    math = False
    escaped = False
    for index in range(start, len(text)):
        character = text[index]
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quote = False
            continue
        if character == '"' and (index == 0 or text[index - 1] != "\\"):
            quote = True
            continue
        # Typst math uses ``$...$`` and square brackets inside it are ordinary
        # interval/set delimiters, not nested content blocks. In particular,
        # half-open intervals such as ``$[0,1)$`` are intentionally unbalanced
        # as square brackets and must not consume the surrounding statement's
        # closing content delimiter.
        if character == "$" and (index == 0 or text[index - 1] != "\\"):
            math = not math
            continue
        if math:
            continue
        if opening == "(" and character == "[" and (index == 0 or text[index - 1] != "\\"):
            content_depth += 1
            continue
        if opening == "(" and character == "]" and (index == 0 or text[index - 1] != "\\"):
            content_depth = max(0, content_depth - 1)
            continue
        if content_depth:
            continue
        if character == opening and (index == 0 or text[index - 1] != "\\"):
            depth += 1
        elif character == closing and (index == 0 or text[index - 1] != "\\"):
            depth -= 1
            if depth == 0:
                return index
    raise KnowledgeError(f"unclosed {opening!r} at offset {start}")


def statement_ranges(text: str) -> list[StatementRange]:
    result: list[StatementRange] = []
    for match in STATEMENT_RE.finditer(text):
        close = find_matching(text, match.end() - 1, "(", ")")
        cursor = close + 1
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        end = close + 1
        if cursor < len(text) and text[cursor] == "[":
            end = find_matching(text, cursor, "[", "]") + 1
        result.append(StatementRange(match.start(), end, match.group("kind")))
    return result


def strip_typst(value: str) -> str:
    text = unicodedata.normalize("NFKC", value)
    text = re.sub(r"#(?:strong|emph|text)\[", "", text)
    text = text.replace("$", "").replace("\\", "")
    text = re.sub(r"[#\[\]{}]", " ", text)
    text = re.sub(r"\bsigma\b", "σ", text)
    text = re.sub(r"\bpi\b", "π", text)
    text = re.sub(r"\s+", " ", text).strip(" ,:;")
    return text


def strip_latex(value: str) -> str:
    text = unicodedata.normalize("NFKC", value)
    replacements = {
        r"\sigma": "σ",
        r"\pi": "π",
        r"\lambda": "λ",
        r"\mu": "μ",
        r"\rho": "ρ",
        r"\Omega": "Ω",
    }
    for source, replacement in replacements.items():
        text = text.replace(source, replacement)
    text = re.sub(r"\\(?:text|mathrm|mathbf|mathbb|mathcal|operatorname)\s*\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\[A-Za-z]+\*?", " ", text)
    text = text.replace("$", "").replace("\\", "")
    text = re.sub(r"[{}]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" ,:;")
    return text


def latex_name_key(value: str) -> str:
    # An unchanged explicit native marker keeps its existing ID even when the
    # plain-text label normalizer learns another TeX spelling.
    return "\0latex-source-name\0" + value


def strip_latex_name(value: str) -> str:
    # An empty circumflex accent typesets a literal caret. Preserve its
    # adjacency so a plain Typst name such as "R^n" keeps the same identity.
    value = value.replace(r"\^{}", "^")
    formatting = {
        "text", "mathrm", "mathbf", "mathbb", "mathcal", "operatorname",
        "textbf", "textit", "textup", "textsf", "texttt", "emph", "boldsymbol",
        "mathsf", "mathtt", "mathit", "mathfrak", "displaystyle", "textstyle",
        "scriptstyle", "scriptscriptstyle", "left", "right", "big", "Big",
        "bigl", "bigr", "Bigl", "Bigr", "quad", "qquad", "enspace", "thinspace",
    }
    recognized = {"sigma", "pi", "lambda", "mu", "rho", "Omega"}

    def command(match: re.Match[str]) -> str:
        name = match.group(1)
        return "\\" + name if name in formatting or name in recognized else name

    # Opaque macros remain named tokens instead of disappearing and collapsing
    # unrelated explicit mathematical names onto the same generic suffix.
    return strip_latex(re.sub(r"\\([A-Za-z]+)\*?", command, value))


def strip_markdown(value: str) -> str:
    text = re.sub(r"^\s*<|>\s*$", "", value.strip())
    text = re.sub(r"[*_`~]", "", text)
    return strip_latex(text)


def wikilink_parts(value: str) -> tuple[str, str]:
    target, separator, alias = value.partition("|")
    target = target.strip()
    display = alias.strip() if separator and alias.strip() else target
    return target, display


def latex_statement_ranges(text: str) -> list[StatementRange]:
    from kgdistiller.latex_syntax import statement_ranges as latex_ranges

    try:
        return [StatementRange(item.start, item.end, item.kind) for item in latex_ranges(text)]
    except ValueError as error:
        raise KnowledgeError(str(error)) from error


def identity_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    for symbol, name in {
        "σ": " sigma ",
        "π": " pi ",
        "λ": " lambda ",
        "μ": " mu ",
        "ρ": " rho ",
        "ω": " omega ",
    }.items():
        normalized = normalized.replace(symbol, name)
    return re.sub(r"\s+", " ", normalized).strip()


def generated_id(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = normalized.replace("σ", " sigma ").replace("π", " pi ").replace("λ", " lambda ")
    candidate = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")[:120].rstrip("-")
    return candidate or f"knowledge-{sha256_text(identity_key(value))[:16]}"


def build_identity_index(
    state: GraphState,
    registered: dict[str, dict[str, Any]] | None = None,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for node in state.nodes.values():
        if node.get("type") != "knowledge":
            continue
        properties = node.get("properties") or {}
        if properties.get("source_format") == "latex" and properties.get("source_name"):
            key = latex_name_key(str(properties["source_name"]))
            existing = result.get(key)
            if existing and existing != node["id"]:
                raise KnowledgeError("ambiguous explicit LaTeX marker spelling")
            result[key] = node["id"]
        names = [node.get("label", ""), *properties.get("aliases", [])]
        for raw in names:
            key = identity_key(str(raw))
            if not key:
                continue
            existing = result.get(key)
            if existing and existing != node["id"]:
                raise KnowledgeError(
                    f"ambiguous knowledge name {raw!r}: {existing!r} and {node['id']!r}"
                )
            result[key] = node["id"]
    for reference in state.references:
        if reference.get("origin") != "authored" or reference.get("source_format") != "latex":
            continue
        name = reference.get("source_name")
        target = reference.get("target")
        target_node = state.nodes.get(target) if isinstance(target, str) else None
        if (
            not isinstance(name, str) or not name.strip()
            or not target_node or target_node.get("type") != "knowledge"
            or (target_node.get("properties") or {}).get("source_status") != "active"
            or (target_node.get("provenance") or {}).get("active") is False
        ):
            continue
        key = latex_name_key(name)
        existing = result.get(key)
        if existing and existing != target:
            raise KnowledgeError("ambiguous explicit LaTeX marker spelling")
        result[key] = target
    for node_id, record in sorted((registered or {}).items()):
        for raw in (record.get("canonical_name", ""), *record.get("aliases", [])):
            key = identity_key(str(raw))
            if not key:
                continue
            existing = result.get(key)
            if existing and existing != node_id:
                raise KnowledgeError(
                    f"registered knowledge name {raw!r} conflicts with {existing!r} and {node_id!r}"
                )
            result[key] = node_id
    return result


def containing_statement(ranges: list[StatementRange], position: int) -> StatementRange | None:
    candidates = [item for item in ranges if item.start <= position < item.end]
    return min(candidates, key=lambda item: item.end - item.start) if candidates else None


def definition_fingerprint(
    text: str,
    position: int,
    statement: StatementRange | None,
) -> tuple[str, int, int]:
    start = statement.start if statement else text.rfind("\n", 0, position) + 1
    if statement:
        end = statement.end
    else:
        line_end = text.find("\n", position)
        end = len(text) if line_end < 0 else line_end
    content = text[start:end].replace("\r\n", "\n").replace("\r", "\n")
    start_line = text.count("\n", 0, start) + 1
    end_line = start_line + content.count("\n")
    return sha256_text(content), start_line, end_line


def scan_typst(
    repo_root: Path,
    spec: SourceSpec,
    path: Path,
    identities: dict[str, str],
) -> ScanResult:
    authority = relative_path(repo_root, path)
    text = path.read_text(encoding="utf-8")
    errors: list[dict[str, Any]] = []
    try:
        ranges = statement_ranges(text)
    except KnowledgeError as error:
        return ScanResult([], [], [diagnostic("typst-parse", str(error), source=authority)])
    definitions: list[DefinitionOccurrence] = []
    for match in KN_RE.finditer(text):
        try:
            close = find_matching(text, match.end() - 1, "[", "]")
        except KnowledgeError as error:
            errors.append(diagnostic("typst-parse", str(error), source=authority))
            continue
        label_typst = text[match.end() : close]
        label = strip_typst(label_typst)
        if not label:
            errors.append(
                diagnostic(
                    "empty-knowledge-name",
                    "#kn must contain a non-empty semantic name",
                    source=authority,
                )
            )
            continue
        key = identity_key(label)
        node_id = identities.get(key) or generated_id(label)
        identities.setdefault(key, node_id)
        statement = containing_statement(ranges, match.start())
        fingerprint, definition_start_line, definition_end_line = definition_fingerprint(
            text, match.start(), statement
        )
        line = text.count("\n", 0, match.start()) + 1
        definitions.append(
            DefinitionOccurrence(
                id=node_id,
                label=label,
                label_markup=label_typst,
                source_format="typst",
                kind=statement.kind if statement else "concept",
                authority=authority,
                line=line,
                source_id=spec.id,
                document_type=spec.document_type,
                position=match.start(),
                statement=statement,
                definition_sha256=fingerprint,
                definition_start_line=definition_start_line,
                definition_end_line=definition_end_line,
            )
        )
    statement_nodes: dict[tuple[int, int], list[str]] = defaultdict(list)
    for item in definitions:
        if item.statement:
            statement_nodes[(item.statement.start, item.statement.end)].append(item.id)
    references: list[ReferenceOccurrence] = []
    for match in REF_RE.finditer(text):
        try:
            close = find_matching(text, match.end() - 1, "[", "]")
        except KnowledgeError as error:
            errors.append(diagnostic("typst-parse", str(error), source=authority))
            continue
        label = strip_typst(text[match.end() : close])
        if not label:
            errors.append(
                diagnostic(
                    "empty-reference-name",
                    "#ref must contain a non-empty semantic name",
                    source=authority,
                )
            )
            continue
        target = identities.get(identity_key(label)) or generated_id(label)
        statement = containing_statement(ranges, match.start())
        context = None
        if statement:
            candidates = statement_nodes.get((statement.start, statement.end), [])
            if len(candidates) == 1:
                context = candidates[0]
        line = text.count("\n", 0, match.start()) + 1
        references.append(
            ReferenceOccurrence(
                id=sha256_text(f"{authority}:{line}:{target}:{context or ''}")[:20],
                target=target,
                label=label,
                authority=authority,
                line=line,
                context=context,
                source_format="typst",
                source_name=text[match.end() : close],
            )
        )
    return ScanResult(definitions, references, errors)


def markdown_kind_at(text: str, position: int) -> str:
    start = text.rfind("\n", 0, position) + 1
    end = text.find("\n", position)
    line = text[start : len(text) if end < 0 else end]
    line = re.sub(r"^\s*(?:>\s*)*#{0,6}\s*", "", line)
    line = re.sub(r"^[*_`\s]+", "", line)
    match = re.match(
        r"(?i)(definition|theorem|lemma|corollary|proposition|axiom|example)\b",
        line,
    )
    return match.group(1).lower() if match else "concept"


def markdown_definition_range(text: str, position: int) -> StatementRange:
    """Return the smallest conservative Markdown block containing a marker."""
    line_start = text.rfind("\n", 0, position) + 1
    line_end = text.find("\n", position)
    line_end = len(text) if line_end < 0 else line_end + 1
    line = text[line_start:line_end]
    if re.match(r"\s*>", line):
        start = line_start
        while start > 0:
            previous_end = start - 1
            previous_start = text.rfind("\n", 0, previous_end) + 1
            if not re.match(r"\s*>", text[previous_start:previous_end]):
                break
            start = previous_start
        end = line_end
        while end < len(text):
            next_end = text.find("\n", end)
            next_end = len(text) if next_end < 0 else next_end + 1
            if not re.match(r"\s*>", text[end:next_end]):
                break
            end = next_end
        return StatementRange(start, end, markdown_kind_at(text, position))

    start_break = text.rfind("\n\n", 0, position)
    end_break = text.find("\n\n", position)
    start = 0 if start_break < 0 else start_break + 2
    end = len(text) if end_break < 0 else end_break
    return StatementRange(start, end, markdown_kind_at(text, position))


def scan_markdown(
    repo_root: Path,
    spec: SourceSpec,
    path: Path,
    identities: dict[str, str],
) -> ScanResult:
    authority = relative_path(repo_root, path)
    text = path.read_text(encoding="utf-8")
    definitions: list[DefinitionOccurrence] = []
    references: list[ReferenceOccurrence] = []
    errors: list[dict[str, Any]] = []
    matches = list(MARKDOWN_WIKILINK_RE.finditer(text))

    for match in matches:
        body = match.group("definition_body")
        if body is None:
            continue
        target_markup, _ = wikilink_parts(body)
        label = strip_markdown(target_markup)
        if not label:
            errors.append(
                diagnostic(
                    "empty-knowledge-name",
                    "--[[...]]-- must contain a non-empty semantic name",
                    source=authority,
                )
            )
            continue
        key = identity_key(label)
        node_id = identities.get(key) or generated_id(label)
        identities.setdefault(key, node_id)
        statement = markdown_definition_range(text, match.start())
        fingerprint, definition_start_line, definition_end_line = definition_fingerprint(
            text, match.start(), statement
        )
        line = text.count("\n", 0, match.start()) + 1
        definitions.append(
            DefinitionOccurrence(
                id=node_id,
                label=label,
                label_markup=target_markup,
                source_format="markdown",
                kind=statement.kind,
                authority=authority,
                line=line,
                source_id=spec.id,
                document_type=spec.document_type,
                position=match.start(),
                statement=statement,
                definition_sha256=fingerprint,
                definition_start_line=definition_start_line,
                definition_end_line=definition_end_line,
            )
        )

    definitions_by_line: dict[int, list[str]] = defaultdict(list)
    for item in definitions:
        definitions_by_line[item.line].append(item.id)
    for match in matches:
        body = match.group("reference_body")
        if body is None:
            continue
        target_markup, _ = wikilink_parts(body)
        label = strip_markdown(target_markup)
        if not label:
            errors.append(
                diagnostic(
                    "empty-reference-name",
                    "[[...]] must contain a non-empty semantic name",
                    source=authority,
                )
            )
            continue
        target = identities.get(identity_key(label)) or generated_id(label)
        line = text.count("\n", 0, match.start()) + 1
        contexts = definitions_by_line.get(line, [])
        context = contexts[0] if len(contexts) == 1 else None
        references.append(
            ReferenceOccurrence(
                id=sha256_text(
                    f"{authority}:{line}:{match.start()}:{target}:{context or ''}"
                )[:20],
                target=target,
                label=label,
                authority=authority,
                line=line,
                context=context,
                source_format="markdown",
                source_name=target_markup,
            )
        )
    return ScanResult(definitions, references, errors)


def scan_latex(
    repo_root: Path,
    spec: SourceSpec,
    path: Path,
    identities: dict[str, str],
) -> ScanResult:
    from kgdistiller.latex_syntax import find_group_end, mask_latex

    authority = relative_path(repo_root, path)
    text = path.read_text(encoding="utf-8")
    try:
        ranges = latex_statement_ranges(text)
        active = mask_latex(text)
    except (KnowledgeError, ValueError) as error:
        return ScanResult([], [], [diagnostic("latex-parse", str(error), source=authority)])
    definitions: list[DefinitionOccurrence] = []
    references: list[ReferenceOccurrence] = []
    errors: list[dict[str, Any]] = []

    def marker_is_active(position: int) -> bool:
        cursor = position - 1
        while cursor >= 0 and active[cursor] == "\\":
            cursor -= 1
        return (position - cursor - 1) % 2 == 0

    for match in LATEX_KN_RE.finditer(active):
        if not marker_is_active(match.start()):
            continue
        try:
            close = find_group_end(text, match.end() - 1)
        except ValueError as error:
            errors.append(diagnostic("latex-parse", str(error), source=authority))
            continue
        label_markup = text[match.end() : close]
        label = strip_latex_name(active[match.end() : close])
        if not label:
            errors.append(
                diagnostic(
                    "empty-knowledge-name",
                    r"\kn{...} must contain a non-empty semantic name",
                    source=authority,
                )
            )
            continue
        key = identity_key(label)
        native_key = latex_name_key(label_markup)
        node_id = identities.get(native_key) or identities.get(key) or generated_id(label)
        identities.setdefault(key, node_id)
        identities.setdefault(native_key, node_id)
        statement = containing_statement(ranges, match.start())
        fingerprint, definition_start_line, definition_end_line = definition_fingerprint(
            text, match.start(), statement
        )
        line = text.count("\n", 0, match.start()) + 1
        definitions.append(
            DefinitionOccurrence(
                id=node_id,
                label=label,
                label_markup=label_markup,
                source_format="latex",
                kind=statement.kind if statement else "concept",
                authority=authority,
                line=line,
                source_id=spec.id,
                document_type=spec.document_type,
                position=match.start(),
                statement=statement,
                definition_sha256=fingerprint,
                definition_start_line=definition_start_line,
                definition_end_line=definition_end_line,
            )
        )

    statement_nodes: dict[tuple[int, int], list[str]] = defaultdict(list)
    for item in definitions:
        if item.statement:
            statement_nodes[(item.statement.start, item.statement.end)].append(item.id)
    for match in LATEX_REF_RE.finditer(active):
        if not marker_is_active(match.start()):
            continue
        try:
            close = find_group_end(text, match.end() - 1)
        except ValueError as error:
            errors.append(diagnostic("latex-parse", str(error), source=authority))
            continue
        label = strip_latex_name(active[match.end() : close])
        if not label:
            errors.append(
                diagnostic(
                    "empty-reference-name",
                    r"\knref{...} must contain a non-empty semantic name",
                    source=authority,
                )
            )
            continue
        target = identities.get(latex_name_key(text[match.end() : close])) or identities.get(identity_key(label)) or generated_id(label)
        statement = containing_statement(ranges, match.start())
        context = None
        if statement:
            candidates = statement_nodes.get((statement.start, statement.end), [])
            if len(candidates) == 1:
                context = candidates[0]
        line = text.count("\n", 0, match.start()) + 1
        references.append(
            ReferenceOccurrence(
                id=sha256_text(f"{authority}:{line}:{target}:{context or ''}")[:20],
                target=target,
                label=label,
                authority=authority,
                line=line,
                context=context,
                source_format="latex",
                source_name=text[match.end() : close],
            )
        )
    return ScanResult(definitions, references, errors)


def scan_source(
    repo_root: Path,
    spec: SourceSpec,
    path: Path,
    identities: dict[str, str],
) -> ScanResult:
    scanner = {
        "typst": scan_typst,
        "markdown": scan_markdown,
        "latex": scan_latex,
    }[source_format(path)]
    return scanner(repo_root, spec, path, identities)


def _entry_repo_root(graph_dir: Path, repo_root: Path | None) -> Path:
    if repo_root is not None:
        return repo_root.resolve()
    graph = graph_dir.resolve()
    if graph.name == "graph" and graph.parent.name == KNOWLEDGE_DIRECTORY:
        return graph.parent.parent
    raise KnowledgeError("entry-backed custom graph directories require explicit repo_root")


def _load_compact_entries(
    graph_dir: Path,
    nodes: dict[str, dict[str, Any]],
    manifest: dict[str, Any],
    *,
    repo_root: Path | None,
    verify_entries: bool,
) -> None:
    from kgdistiller.entry_markdown import (
        EntryMarkdownError,
        authority_sha256,
        parse_entry,
    )

    inventory = manifest.get("entry_authorities") or {}
    if inventory and inventory.get("schema") != ENTRY_AUTHORITY_SCHEMA:
        raise KnowledgeError("unsupported entry authority inventory")
    records = inventory.get("entries", [])
    if not isinstance(records, list):
        raise KnowledgeError("entry authority inventory must be an array")
    expected: dict[str, str] = {}
    for record in records:
        if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
            raise KnowledgeError("invalid entry authority inventory record")
        path, digest = record["path"], record["sha256"]
        if not isinstance(path, str) or not path or path in expected:
            raise KnowledgeError("duplicate or invalid entry authority path")
        relative = Path(path)
        if relative.is_absolute() or ".." in relative.parts or relative.suffix != ".md":
            raise KnowledgeError(f"unsafe entry authority path: {path}")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise KnowledgeError(f"invalid entry authority digest: {path}")
        expected[path] = digest
    linked: dict[str, dict[str, Any]] = {}
    for node in nodes.values():
        path = (node.get("properties") or {}).get("entry_authority")
        if path:
            if not isinstance(path, str) or path in linked:
                raise KnowledgeError("duplicate or invalid node entry authority")
            if "text" in node or "entry" in node:
                raise KnowledgeError(f"entry-backed node duplicates Markdown content: {path}")
            linked[path] = node
    if set(linked) != set(expected):
        raise KnowledgeError("entry authority inventory does not match graph nodes")
    if not expected:
        return
    root = _entry_repo_root(graph_dir, repo_root)
    for relative, node in linked.items():
        path = root / relative
        try:
            path.resolve().relative_to(root)
        except ValueError as error:
            raise KnowledgeError(f"entry authority escapes repository: {relative}") from error
        if any(part.is_symlink() for part in (path, *path.parents) if part != root):
            raise KnowledgeError(f"entry authority must not use symlinks: {relative}")
        if not path.is_file():
            if not verify_entries:
                # Explicit synchronization can accept deletion of an entry.
                continue
            raise KnowledgeError(f"missing entry authority: {relative}; run kgdistiller sync")
        try:
            content_digest = authority_sha256(path)
            if verify_entries and content_digest != expected[relative]:
                raise KnowledgeError(f"entry authority is out of sync: {relative}; run kgdistiller sync")
            parsed = parse_entry(path)
        except (EntryMarkdownError, OSError, UnicodeError) as error:
            raise KnowledgeError(str(error)) from error
        if parsed["metadata"]["kgd_id"] != node["id"]:
            raise KnowledgeError(f"entry authority identity mismatch: {relative}")
        # Read only the accepted body. Curation status and evidence freshness are
        # updated by synchronization, never as a side effect of a query.
        node["entry"] = parsed["entry"]
        node["text"] = str(parsed["entry"].get("summary", ""))


def load_state(
    graph_dir: Path,
    *,
    repo_root: Path | None = None,
    verify_entries: bool = True,
) -> GraphState:
    """Load accepted graph data; mutation paths alone may accept edited entries."""
    manifest_path = graph_dir / "manifest.json"
    if not manifest_path.exists():
        if manifest_path.is_symlink():
            raise KnowledgeError(f"graph manifest path is a broken symlink: {manifest_path}")
        return GraphState({}, {}, [], {})
    if not manifest_path.is_file():
        raise KnowledgeError(f"graph manifest is not a file: {manifest_path}")
    try:
        manifest = read_json(manifest_path, {})
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise KnowledgeError(f"invalid graph manifest JSON: {manifest_path}") from error
    if not isinstance(manifest, dict):
        raise KnowledgeError(f"graph manifest must be a JSON object: {manifest_path}")
    if manifest.get("schema") != GRAPH_SCHEMA:
        raise KnowledgeError(
            f"expected {GRAPH_SCHEMA} graph manifest: {manifest_path}; "
            f"got {manifest.get('schema')!r}"
        )
    nodes = {item["id"]: item for item in read_jsonl(graph_dir / "nodes.jsonl")}
    _load_compact_entries(graph_dir, nodes, manifest, repo_root=repo_root, verify_entries=verify_entries)
    for node in nodes.values():
        node.setdefault("text", str((node.get("entry") or {}).get("summary", "")))
    edges = {
        (item["source"], item["relation"], item["target"]): item
        for item in read_jsonl(graph_dir / "edges.jsonl")
    }
    references = read_jsonl(graph_dir / "references.jsonl")
    state = GraphState(nodes, edges, references, manifest)
    refresh_node_curation_defaults(state)
    return state


def select_scope(
    repo_root: Path,
    specs: list[SourceSpec],
    files: list[Path],
) -> tuple[list[tuple[SourceSpec, Path]], set[str], bool]:
    full = not files
    pairs: list[tuple[SourceSpec, Path]] = []
    if files:
        for raw in files:
            path = (repo_root / raw).resolve() if not raw.is_absolute() else raw.resolve()
            if path.is_file():
                owner = unique_source_for_path(specs, path, include_shadowed=True)
                preferred = preferred_source_path(owner, path)
                pairs.append((unique_source_for_path(specs, preferred), preferred))
            elif path.is_dir():
                selected: list[tuple[SourceSpec, Path]] = []
                for spec in specs:
                    selected.extend(
                        (spec, candidate)
                        for candidate in expand_source(spec)
                        if path == candidate.parent or path in candidate.parents
                    )
                if not selected and not any(
                    path == spec.root
                    or path in spec.root.parents
                    or spec.root in path.parents
                    for spec in specs
                ):
                    raise KnowledgeError(f"directory is outside configured source roots: {raw}")
                pairs.extend(selected)
            elif path.exists():
                raise KnowledgeError(f"scope path is not a file or directory: {raw}")
            else:
                owner = unique_source_for_path(specs, path, include_shadowed=True)
                preferred = preferred_source_path(owner, path)
                pairs.append((unique_source_for_path(specs, preferred), preferred))
    else:
        for spec in specs:
            pairs.extend((spec, path) for path in expand_source(spec))
    unique: dict[str, tuple[SourceSpec, Path]] = {}
    for spec, path in pairs:
        key = relative_path(repo_root, path)
        existing = unique.get(key)
        if existing is not None and existing[0].id != spec.id:
            raise KnowledgeError(
                f"source file matches multiple registry sources: {key} "
                f"({existing[0].id}, {spec.id})"
            )
        unique[key] = (spec, path)
    return list(unique.values()), set(unique), full


def _decode_machine_output(value: object, label: str) -> str:
    if not isinstance(value, bytes):
        raise KnowledgeError(f"{label} machine output is not bytes")
    try:
        return value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise KnowledgeError(f"{label} machine output is not valid UTF-8") from error


def _decode_git_machine_output(value: object, label: str) -> str:
    return _decode_machine_output(value, f"Git {label}")


def _parse_git_name_status(raw: str) -> list[dict[str, str]]:
    if not raw:
        return []
    if not raw.endswith("\0"):
        raise KnowledgeError("Git diff machine output is not NUL-terminated")
    parts = raw[:-1].split("\0")
    changes: list[dict[str, str]] = []
    cursor = 0
    while cursor < len(parts):
        code = parts[cursor]
        cursor += 1
        if not code:
            raise KnowledgeError("Git diff machine output has an empty status")
        path_count = 2 if code.startswith(("R", "C")) else 1
        if cursor + path_count > len(parts):
            raise KnowledgeError(f"Git diff machine output is truncated after {code}")
        old_path = parts[cursor]
        new_path = parts[cursor + 1] if path_count == 2 else old_path
        cursor += path_count
        if not old_path or not new_path:
            raise KnowledgeError(f"Git diff machine output has an empty path after {code}")
        changes.append({"status": code, "old_path": old_path, "new_path": new_path})
    return changes


def git_source_context(
    repo_root: Path,
    base_revision: str | None,
    specs: list[SourceSpec],
) -> dict[str, Any]:
    """Describe source changes relative to the last synchronized Git revision."""
    try:
        head_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=False,
        )
    except (OSError, subprocess.CalledProcessError):
        return {"head": None, "dirty": False, "changes": []}
    head = _decode_git_machine_output(head_result.stdout, "HEAD").strip()

    roots = [relative_path(repo_root, spec.root) for spec in specs]
    try:
        status_result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all", "--", *roots],
            cwd=repo_root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=False,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise KnowledgeError("cannot read Git source status") from error
    status = _decode_git_machine_output(status_result.stdout, "status")
    changes: list[dict[str, str]] = []
    if base_revision:
        try:
            diff_result = subprocess.run(
                [
                    "git",
                    "diff",
                    "--name-status",
                    "-z",
                    "-M",
                    base_revision,
                    "--",
                    *roots,
                ],
                cwd=repo_root,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=False,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            raise KnowledgeError("cannot read Git source diff") from error
        raw = _decode_git_machine_output(diff_result.stdout, "diff")
        changes = _parse_git_name_status(raw)
    return {"head": head, "dirty": bool(status.strip()), "changes": changes}


def source_owner(
    repo_root: Path,
    specs: list[SourceSpec],
    authority: str,
) -> SourceSpec | None:
    path = (repo_root / authority).resolve()
    return next(
        (spec for spec in specs if path == spec.root or spec.root in path.parents),
        None,
    )


def include_previous_authorities(
    repo_root: Path,
    specs: list[SourceSpec],
    pairs: list[tuple[SourceSpec, Path]],
    selected_keys: set[str],
    previous: GraphState,
    git_context: dict[str, Any],
    *,
    files: list[Path],
    full: bool,
) -> tuple[list[tuple[SourceSpec, Path]], set[str]]:
    """Include deleted/renamed old paths so their occurrences can be retired."""
    unique = {relative_path(repo_root, path): (spec, path) for spec, path in pairs}
    previous_paths = set((previous.manifest.get("source_hashes") or {}).keys())
    requested = [
        (repo_root / raw).resolve() if not raw.is_absolute() else raw.resolve()
        for raw in files
    ]

    def requested_path(authority: str) -> bool:
        candidate = (repo_root / authority).resolve()
        return any(root == candidate or root in candidate.parents for root in requested)

    candidates: set[str] = set()
    for authority in previous_paths:
        owner = source_owner(repo_root, specs, authority)
        if owner is None:
            continue
        preferred = relative_path(
            repo_root, preferred_source_path(owner, repo_root / authority)
        )
        if (
            full
            or requested_path(authority)
            or preferred in selected_keys
        ):
            candidates.add(authority)

    if files:
        for change in git_context.get("changes", []):
            old_path = str(change.get("old_path", ""))
            new_path = str(change.get("new_path", ""))
            if new_path in selected_keys or requested_path(new_path):
                candidates.add(old_path)
        previous_hashes = previous.manifest.get("source_hashes") or {}
        for _, path in pairs:
            if not path.is_file():
                continue
            current_key = relative_path(repo_root, path)
            current_hash = sha256_authority_file(path)
            exact_old_paths = [
                authority
                for authority, digest in previous_hashes.items()
                if authority != current_key
                and digest == current_hash
                and not (repo_root / authority).is_file()
            ]
            if len(exact_old_paths) == 1:
                candidates.add(exact_old_paths[0])

    for authority in sorted(candidates):
        owner = source_owner(repo_root, specs, authority)
        if owner is None:
            continue
        path = (repo_root / authority).resolve()
        if is_source_projection(path):
            continue
        if preferred_source_path(owner, path) == path:
            unique.setdefault(authority, (owner, path))
        selected_keys.add(authority)
    return list(unique.values()), selected_keys


def scan_scope(
    repo_root: Path,
    pairs: list[tuple[SourceSpec, Path]],
    identities: dict[str, str],
) -> ScanResult:
    definitions: list[DefinitionOccurrence] = []
    references: list[ReferenceOccurrence] = []
    errors: list[dict[str, Any]] = []
    for spec, path in pairs:
        if not path.is_file():
            continue
        result = scan_source(repo_root, spec, path, identities)
        definitions.extend(result.definitions)
        references.extend(result.references)
        errors.extend(result.errors)
    by_id: dict[str, list[DefinitionOccurrence]] = defaultdict(list)
    for item in definitions:
        by_id[item.id].append(item)
    for node_id, items in by_id.items():
        if len(items) > 1:
            locations = ", ".join(f"{item.authority}:{item.line}" for item in items)
            errors.append(
                diagnostic(
                    "duplicate-kn",
                    f"global knowledge name {items[0].label!r} occurs more than once: {locations}",
                    node=node_id,
                )
            )
    return ScanResult(definitions, references, errors)


def edge_key(edge: dict[str, Any]) -> tuple[str, str, str]:
    return str(edge["source"]), str(edge["relation"]), str(edge["target"])


def source_node(definition: DefinitionOccurrence, existing: dict[str, Any] | None) -> dict[str, Any]:
    previous = copy.deepcopy(existing) if existing else {}
    properties = {
        key: value
        for key, value in (previous.get("properties") or {}).items()
        if key in RETAINED_SOURCE_NODE_PROPERTIES
    }
    aliases = list(dict.fromkeys(str(item) for item in properties.get("aliases", [])))
    old_label = str(previous.get("label", ""))
    if old_label and old_label != definition.label and old_label not in aliases:
        aliases.append(old_label)
    if properties.get("kind_origin") == "reviewed":
        properties["source_kind"] = definition.kind
    else:
        properties["kind"] = definition.kind
        properties.pop("source_kind", None)
    properties.update(
        {
            "aliases": aliases,
            "origin": "authored",
            "source_status": "active",
            "source_format": definition.source_format,
            "source_name": definition.label_markup,
        }
    )
    if definition.document_type:
        properties["document_type"] = definition.document_type
    previous_provenance = previous.get("provenance") or {}
    previous_fingerprint = str(previous_provenance.get("definition_sha256", ""))
    curated_fingerprint = str(properties.get("curated_definition_sha256", ""))
    has_entry = bool(str(previous.get("text", "")).strip() or previous.get("entry"))
    if not has_entry:
        properties["curation_status"] = "pending"
        properties.pop("curated_definition_sha256", None)
    elif previous_fingerprint and previous_fingerprint != definition.definition_sha256:
        properties["curation_status"] = "needs-review"
        if not curated_fingerprint:
            properties["curated_definition_sha256"] = previous_fingerprint
    elif properties.get("curation_status") == "needs-review" and curated_fingerprint != definition.definition_sha256:
        properties["curation_status"] = "needs-review"
    else:
        properties["curation_status"] = "current"
        properties["curated_definition_sha256"] = definition.definition_sha256
    node = {
        "id": definition.id,
        "type": "knowledge",
        "label": definition.label,
        "text": str(previous.get("text", "")),
        "properties": properties,
        "provenance": {
            "authority": definition.authority,
            "line": definition.line,
            "definition_start_line": definition.definition_start_line,
            "definition_end_line": definition.definition_end_line,
            "definition_sha256": definition.definition_sha256,
            "active": True,
        },
    }
    if previous.get("entry"):
        node["entry"] = copy.deepcopy(previous["entry"])
    return node


def orphan_node(node: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(node)
    properties = dict(result.get("properties") or {})
    provenance = dict(result.get("provenance") or {})
    if provenance.get("authority"):
        properties["orphaned_from"] = provenance["authority"]
    properties["source_status"] = "orphaned"
    provenance["active"] = False
    result["properties"] = properties
    if provenance:
        result["provenance"] = provenance
    return result


def reference_record(item: ReferenceOccurrence) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": item.id,
        "target": item.target,
        "label": item.label,
        "authority": item.authority,
        "line": item.line,
        "origin": "authored",
        "source_format": item.source_format,
        "source_name": item.source_name,
    }
    if item.context:
        value["context"] = item.context
    return value


def refresh_node_curation_defaults(state: GraphState) -> None:
    for node in state.nodes.values():
        if node.get("type") != "knowledge":
            continue
        properties = dict(node.get("properties") or {})
        if properties.get("curation_status") not in CURATION_STATUSES:
            has_entry = bool(str(node.get("text", "")).strip() or node.get("entry"))
            properties["curation_status"] = "current" if has_entry else "pending"
            fingerprint = str((node.get("provenance") or {}).get("definition_sha256", ""))
            if has_entry and fingerprint:
                properties["curated_definition_sha256"] = fingerprint
        node["properties"] = properties


def refresh_semantic_edge_curation(state: GraphState) -> None:
    """Keep reviewed edges, but make source changes visible instead of silently trusting them."""
    for edge in state.edges.values():
        current: dict[str, str] = {}
        inactive: list[str] = []
        for endpoint in (str(edge.get("source", "")), str(edge.get("target", ""))):
            node = state.nodes.get(endpoint) or {}
            if node.get("type") != "knowledge":
                continue
            provenance = node.get("provenance") or {}
            fingerprint = str(provenance.get("definition_sha256", ""))
            if fingerprint:
                current[endpoint] = fingerprint
            if provenance and provenance.get("active") is False:
                inactive.append(endpoint)
        recorded = {
            str(key): str(value)
            for key, value in (edge.get("evidence_fingerprints") or {}).items()
        }
        if not recorded:
            recorded = dict(current)
            edge["evidence_fingerprints"] = recorded
        stale = sorted(
            set(inactive)
            | {
                node_id
                for node_id, fingerprint in recorded.items()
                if current.get(node_id) != fingerprint
            }
        )
        if stale:
            edge["curation_status"] = "needs-review"
            edge["stale_endpoints"] = stale
        else:
            edge["curation_status"] = "current"
            edge.pop("stale_endpoints", None)

def graph_cycles(nodes: set[str], edges: Iterable[dict[str, Any]], relation: str) -> list[list[str]]:
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge.get("relation") == relation:
            adjacency[str(edge["source"])].append(str(edge["target"]))
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []
    cycles: list[list[str]] = []

    def visit(node: str) -> None:
        if node in visiting:
            try:
                start = stack.index(node)
            except ValueError:
                start = 0
            cycles.append(stack[start:] + [node])
            return
        if node in visited:
            return
        visiting.add(node)
        stack.append(node)
        for target in adjacency.get(node, []):
            visit(target)
        stack.pop()
        visiting.remove(node)
        visited.add(node)

    for node in sorted(nodes):
        visit(node)
    return cycles


def validate_state(state: GraphState) -> dict[str, list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    node_ids = set(state.nodes)
    for node in state.nodes.values():
        raw_node_id = node.get("id")
        raw_label = node.get("label")
        node_id = str(raw_node_id or "")
        if not isinstance(raw_node_id, str) or not ID_RE.fullmatch(raw_node_id):
            errors.append(
                diagnostic(
                    "invalid-node-id",
                    f"node ID must be lowercase ASCII kebab-case and at most {MAX_NODE_ID_LENGTH} characters",
                    node=node_id,
                )
            )
        if (
            not isinstance(raw_label, str)
            or not raw_label
            or len(raw_label) > MAX_NODE_LABEL_LENGTH
        ):
            errors.append(
                diagnostic(
                    "invalid-node-label",
                    "node label must be a non-empty string of at most "
                    f"{MAX_NODE_LABEL_LENGTH} characters",
                    node=node_id,
                )
            )
        if node.get("type") != "knowledge":
            errors.append(
                diagnostic(
                    "unknown-node-type",
                    f"unsupported node type: {node.get('type')}",
                    node=str(node.get("id", "")),
                )
            )
    for edge in state.edges.values():
        if edge.get("relation") not in SEMANTIC_RELATIONS:
            errors.append(diagnostic("unknown-relation", f"unknown relation: {edge.get('relation')}"))
        for endpoint in ("source", "target"):
            if edge.get(endpoint) not in node_ids:
                errors.append(
                    diagnostic(
                        "dangling-edge",
                        f"edge endpoint does not exist: {edge.get(endpoint)}",
                        node=str(edge.get(endpoint, "")),
                    )
                )
        if edge.get("curation_status") == "needs-review":
            warnings.append(
                diagnostic(
                    "stale-semantic-edge",
                    "semantic edge evidence predates a changed or orphaned authority",
                    node=str(edge.get("source", "")),
                )
            )
    for relation in ACYCLIC_RELATIONS:
        for cycle in graph_cycles(node_ids, state.edges.values(), relation):
            errors.append(
                diagnostic(
                    "graph-cycle",
                    f"{relation} cycle: {' -> '.join(cycle)}",
                    node=cycle[0],
                )
            )
    for node in state.nodes.values():
        properties = node.get("properties") or {}
        curation_status = properties.get("curation_status")
        if node.get("type") == "knowledge" and curation_status not in CURATION_STATUSES:
            errors.append(
                diagnostic(
                    "invalid-curation-status",
                    f"unsupported knowledge curation status: {curation_status!r}",
                    node=str(node.get("id", "")),
                )
            )
        if properties.get("source_status") == "orphaned":
            warnings.append(
                diagnostic(
                    "orphaned-node",
                    "knowledge metadata and semantic edges are retained, but no active source marker defines this node",
                    source=(node.get("provenance") or {}).get("authority"),
                    node=node["id"],
                )
            )
        elif curation_status == "needs-review":
            warnings.append(
                diagnostic(
                    "stale-node-entry",
                    "knowledge entry predates the current authoritative definition",
                    source=(node.get("provenance") or {}).get("authority"),
                    node=node["id"],
                )
            )
    for reference in state.references:
        if reference.get("target") not in node_ids:
            warnings.append(
                diagnostic(
                    "dangling-ref",
                    f"knowledge reference target does not exist: {reference.get('target')}",
                    source=reference.get("authority"),
                    node=reference.get("target"),
                )
            )
    return {
        "errors": sorted(errors, key=json_text),
        "warnings": sorted(warnings, key=json_text),
    }


def make_agent_snapshot(
    state: GraphState,
    namespace: str = "personal",
) -> dict[str, Any]:
    """Create the deterministic, self-contained Agent snapshot contract."""
    if not NAMESPACE_RE.fullmatch(namespace):
        raise KnowledgeError(f"invalid Agent snapshot namespace: {namespace!r}")
    if state.manifest.get("schema") != GRAPH_SCHEMA:
        raise KnowledgeError(f"expected a {GRAPH_SCHEMA} graph before snapshot export")
    graph_sha256 = str(state.manifest.get("graph_sha256", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", graph_sha256):
        raise KnowledgeError("authority graph has no valid graph_sha256")

    diagnostics = validate_state(state)
    if diagnostics["errors"]:
        codes = ", ".join(item["code"] for item in diagnostics["errors"])
        raise KnowledgeError(f"cannot export invalid authority graph: {codes}")

    nodes = sorted(
        (copy.deepcopy(node) for node in state.nodes.values()),
        key=lambda item: item["id"],
    )
    edges = sorted(
        (copy.deepcopy(edge) for edge in state.edges.values()),
        key=lambda item: (item["source"], item["relation"], item["target"]),
    )
    references = sorted(
        (copy.deepcopy(reference) for reference in state.references),
        key=lambda item: (
            str(item.get("authority", "")),
            int(item.get("line", 0)),
            str(item.get("target", "")),
            str(item.get("id", "")),
        ),
    )
    counts = {
        "nodes": len(nodes),
        "edges": len(edges),
        "references": len(references),
    }
    manifest_counts = state.manifest.get("counts") or {}
    if any(int(manifest_counts.get(key, -1)) != value for key, value in counts.items()):
        raise KnowledgeError(
            "authority graph manifest counts do not match the exported snapshot"
        )
    if _compact_graph_digest(state) != graph_sha256:
        raise KnowledgeError(
            "authority graph digest does not match its hydrated graph content"
        )

    snapshot: dict[str, Any] = {
        "schema": AGENT_SNAPSHOT_SCHEMA,
        "namespace": namespace,
        "graph": {
            "schema": state.manifest["schema"],
            "sha256": graph_sha256,
            "counts": counts,
        },
        "nodes": nodes,
        "edges": edges,
        "references": references,
        "diagnostics": diagnostics,
    }
    snapshot["snapshot_sha256"] = sha256_text(json_text(snapshot))
    return snapshot


def _entry_inventory_hashes(manifest: dict[str, Any], key: str) -> dict[str, str]:
    return {
        str(item["path"]): str(item["sha256"])
        for item in ((manifest.get(key) or {}).get("entries", []))
    }


def _graph_digest_suffix(manifest: dict[str, Any]) -> str:
    return "".join(
        path + digest
        for key in ("entry_authorities", "entry_sources")
        for path, digest in sorted(_entry_inventory_hashes(manifest, key).items())
    )


def _compact_graph_digest(state: GraphState) -> str:
    # Bind the hydrated scientific content, although its only disk authority is
    # Markdown. In-memory edits cannot hide behind an unchanged file hash.
    nodes = []
    for node in sorted(state.nodes.values(), key=lambda item: item["id"]):
        normalized = copy.deepcopy(node)
        normalized.setdefault("text", str((normalized.get("entry") or {}).get("summary", "")))
        nodes.append(normalized)
    edges = sorted(state.edges.values(), key=lambda item: (item["source"], item["relation"], item["target"]))
    references = sorted(state.references, key=lambda item: (item.get("authority", ""), item.get("line", 0), item["target"]))
    return sha256_text(jsonl(nodes) + jsonl(edges) + jsonl(references) + _graph_digest_suffix(state.manifest))


def make_artifacts(
    state: GraphState,
    source_hashes: dict[str, str],
    *,
    entry_hashes: dict[str, str] | None = None,
    registry_sha256: str | None = None,
    identity_sha256: str | None = None,
    git_revision: str | None = None,
) -> dict[str, str]:
    if registry_sha256 is None:
        registry_sha256 = str(state.manifest.get("registry_sha256", "")) or None
    if entry_hashes is None:
        entry_hashes = _entry_inventory_hashes(state.manifest, "entry_authorities")
    refresh_node_curation_defaults(state)
    nodes = sorted(state.nodes.values(), key=lambda item: item["id"])
    edges = sorted(state.edges.values(), key=lambda item: (item["source"], item["relation"], item["target"]))
    references = sorted(state.references, key=lambda item: (item.get("authority", ""), item.get("line", 0), item["target"]))
    entry_source_hashes: dict[str, str] = {}
    serialized_nodes = []
    for node in nodes:
        properties = node.get("properties") or {}
        path = str(properties.get("entry_source", ""))
        digest = str(properties.get("entry_source_current_sha256", ""))
        if path and digest:
            previous = entry_source_hashes.setdefault(path, digest)
            if previous != digest:
                raise KnowledgeError(f"inconsistent entry source digest: {path}")
        serialized = copy.deepcopy(node)
        if properties.get("entry_authority"):
            serialized.pop("text", None)
            serialized.pop("entry", None)
        elif serialized.get("text", "") == (serialized.get("entry") or {}).get("summary", ""):
            serialized.pop("text", None)
        serialized_nodes.append(serialized)
    manifest = {
        "schema": GRAPH_SCHEMA,
        "counts": {"nodes": len(nodes), "edges": len(edges), "references": len(references)},
        "source_hashes": dict(sorted(source_hashes.items())),
        "entry_authorities": {
            "schema": ENTRY_AUTHORITY_SCHEMA,
            "entries": [{"path": path, "sha256": digest} for path, digest in sorted(entry_hashes.items())],
        },
        "entry_sources": {
            "schema": ENTRY_SOURCE_INDEX_SCHEMA,
            "entries": [{"path": path, "sha256": digest} for path, digest in sorted(entry_source_hashes.items())],
        },
    }
    if registry_sha256:
        manifest["registry_sha256"] = registry_sha256
    if identity_sha256:
        manifest["identity_sha256"] = identity_sha256
    if git_revision:
        manifest["git_revision"] = git_revision
    manifest["graph_sha256"] = _compact_graph_digest(GraphState(state.nodes, state.edges, state.references, manifest))
    return {
        "manifest.json": pretty_json(manifest),
        "nodes.jsonl": jsonl(serialized_nodes),
        "edges.jsonl": jsonl(edges),
        "references.jsonl": jsonl(references),
    }


def write_artifacts(graph_dir: Path, artifacts: dict[str, str]) -> None:
    graph_dir.mkdir(parents=True, exist_ok=True)
    for name, content in artifacts.items():
        atomic_write(graph_dir / name, content)


def synchronize(
    repo_root: Path,
    registry: Path,
    graph_dir: Path,
    *,
    identities: Path | None = None,
    alignments: Path | None = None,
    files: list[Path],
    write: bool,
) -> tuple[GraphState, dict[str, str], dict[str, Any]]:
    specs = load_sources(repo_root, registry)
    previous = load_state(graph_dir, repo_root=repo_root, verify_entries=False)
    registered_identities = load_identity_registry(identities)
    git_context = git_source_context(
        repo_root,
        str(previous.manifest.get("git_revision", "")) or None,
        specs,
    )
    pairs, selected_keys, full = select_scope(repo_root, specs, files)
    pairs, selected_keys = include_previous_authorities(
        repo_root,
        specs,
        pairs,
        selected_keys,
        previous,
        git_context,
        files=files,
        full=full,
    )
    state = copy.deepcopy(previous)
    identity_index = build_identity_index(previous, registered_identities)
    scan = scan_scope(
        repo_root,
        pairs,
        dict(identity_index),
    )
    if scan.errors:
        raise KnowledgeError("\n".join(item["message"] for item in scan.errors))
    definitions_by_authority: dict[str, list[DefinitionOccurrence]] = defaultdict(list)
    for definition in scan.definitions:
        definitions_by_authority[definition.authority].append(definition)
    for node_id, node in previous.nodes.items():
        provenance = node.get("provenance") or {}
        authority = str(provenance.get("authority", ""))
        if (
            node.get("type") != "knowledge"
            or not provenance.get("active")
            or authority not in selected_keys
            or Path(authority).suffix.lower() != ".typ"
        ):
            continue
        owner = source_owner(repo_root, specs, authority)
        if owner is None:
            continue
        preferred = preferred_source_path(owner, repo_root / authority)
        if preferred.suffix.lower() != ".tex" or not preferred.is_file():
            continue
        preferred_key = relative_path(repo_root, preferred)
        if not any(
            definition.id == node_id
            and (
                identity_index.get(latex_name_key(definition.label_markup))
                or identity_index.get(identity_key(definition.label))
            ) == node_id
            for definition in definitions_by_authority[preferred_key]
        ):
            raise KnowledgeError(
                f"paired source identity needs an explicit alias: {authority} "
                f"defines {node.get('label')!r} ({node_id}), but {preferred_key} "
                "does not resolve a definition to that ID; register the converted "
                "marker name in the identity registry before switching authorities"
            )
    outside = {
        node_id: node
        for node_id, node in state.nodes.items()
        if (node.get("provenance") or {}).get("active")
        and (node.get("provenance") or {}).get("authority") not in selected_keys
    }
    for definition in scan.definitions:
        if definition.id in outside:
            old = outside[definition.id].get("provenance") or {}
            raise KnowledgeError(
                f"global knowledge name {definition.label!r} occurs more than once: "
                f"{old.get('authority')} and {definition.authority}"
            )
    found_ids = {item.id for item in scan.definitions}
    orphaned: list[str] = []
    for node_id, node in list(state.nodes.items()):
        provenance = node.get("provenance") or {}
        if provenance.get("active") and provenance.get("authority") in selected_keys and node_id not in found_ids:
            state.nodes[node_id] = orphan_node(node)
            orphaned.append(node_id)
    for definition in scan.definitions:
        state.nodes[definition.id] = source_node(definition, state.nodes.get(definition.id))
    state.references = [
        item for item in state.references if item.get("authority") not in selected_keys
    ] + [reference_record(item) for item in scan.references]
    previous_source_hashes = dict(previous.manifest.get("source_hashes") or {})
    source_hashes = dict(previous_source_hashes)
    for authority in previous_source_hashes:
        if is_source_projection(repo_root / authority):
            if any(
                (node.get("provenance") or {}).get("authority") == authority
                and (node.get("provenance") or {}).get("active")
                for node in previous.nodes.values()
            ) or any(ref.get("authority") == authority for ref in previous.references):
                raise KnowledgeError("cannot replace an existing knowledge authority with a sheet projection")
            source_hashes.pop(authority, None)
    if full:
        source_hashes = {}
    for key in selected_keys:
        source_hashes.pop(key, None)
    for _, path in pairs:
        key = relative_path(repo_root, path)
        if path.is_file():
            source_hashes[key] = sha256_authority_file(path)
        else:
            source_hashes.pop(key, None)
    from kgdistiller.entry_markdown import (
        EntryMarkdownError,
        load_entry_authorities,
        write_entry,
    )

    source_relocations = {}
    for definition in scan.definitions:
        old_authority = str(((previous.nodes.get(definition.id) or {}).get("provenance") or {}).get("authority", ""))
        if (old_authority and old_authority != definition.authority
                and Path(old_authority).suffix.lower() in {".md", ".typ", ".tex"}):
            source_relocations[definition.id] = (old_authority, definition.authority)
    entry_updates: dict[Path, str] = {}
    try:
        entry_hashes = load_entry_authorities(
            repo_root, state.nodes, source_relocations=source_relocations, entry_updates=entry_updates
        )
    except EntryMarkdownError as error:
        raise KnowledgeError(str(error)) from error
    refresh_semantic_edge_curation(state)
    previous_git_revision = str(previous.manifest.get("git_revision", "")) or None
    git_revision = previous_git_revision
    if git_context.get("head") and (
        not previous_git_revision
        or (not git_context.get("dirty") and source_hashes != previous_source_hashes)
    ):
        git_revision = str(git_context["head"])
    artifacts = make_artifacts(
        state,
        source_hashes,
        entry_hashes=entry_hashes,
        registry_sha256=source_registry_sha256(registry),
        identity_sha256=identity_registry_sha256(identities),
        git_revision=git_revision,
    )
    diagnostics = validate_state(state)
    if diagnostics["errors"]:
        raise KnowledgeError("\n".join(item["message"] for item in diagnostics["errors"]))
    old_counts = previous.manifest.get("counts") or {"nodes": 0, "edges": 0, "references": 0}
    new_manifest = json.loads(artifacts["manifest.json"])
    state.manifest = new_manifest
    new_counts = new_manifest["counts"]
    report = {
        "scope": "repository" if full else "incremental",
        "files": len(pairs),
        "definitions": len(scan.definitions),
        "references": len(scan.references),
        "orphaned": sorted(orphaned),
        "delta": {
            key: int(new_counts.get(key, 0)) - int(old_counts.get(key, 0))
            for key in ("nodes", "edges", "references")
        },
        "counts": new_counts,
        "warnings": len(diagnostics["warnings"]),
        "needs_review": {
            "nodes": sum(
                (node.get("properties") or {}).get("curation_status") == "needs-review"
                for node in state.nodes.values()
                if node.get("type") == "knowledge"
            ),
            "edges": sum(
                edge.get("curation_status") == "needs-review"
                for edge in state.edges.values()
            ),
        },
        "source_changes": {
            "added": sorted(set(source_hashes) - set(previous_source_hashes)),
            "deleted": sorted(set(previous_source_hashes) - set(source_hashes)),
            "modified": sorted(
                path
                for path in set(source_hashes) & set(previous_source_hashes)
                if source_hashes[path] != previous_source_hashes[path]
            ),
        },
    }
    if write:
        for path, content in sorted(entry_updates.items()):
            write_entry(path, content)
        write_artifacts(graph_dir, artifacts)
        # All readers load and validate this committed JSON generation
        # directly. There is no second runtime generation to publish.
        state = load_state(graph_dir, repo_root=repo_root)
    return state, artifacts, report


def apply_delta(
    graph_dir: Path,
    delta_path: Path,
    *,
    repo_root: Path | None = None,
    registry: Path | None = None,
) -> dict[str, Any]:
    delta = read_json(delta_path, {})
    if delta.get("schema") != DELTA_SCHEMA:
        raise KnowledgeError(f"expected {DELTA_SCHEMA} delta: {delta_path}")
    repo_root = _entry_repo_root(graph_dir, repo_root)
    state = load_state(graph_dir, repo_root=repo_root, verify_entries=False)
    from kgdistiller.entry_markdown import (
        EntryMarkdownError,
        authority_sha256,
        entry_relative,
        entry_with_kind,
        load_entry_authorities,
        normalize_entry,
        render_entry,
        resolve_entry_source,
        write_entry,
    )

    entry_deletes: set[Path] = set()
    entry_writes: dict[Path, str] = {}
    before = dict(state.manifest.get("counts") or {})
    removed_nodes = 0
    kind_profiles: dict[str, dict[str, Any]] | None = None
    kind_specs: list[SourceSpec] = []
    for raw_id in delta.get("remove_nodes", []):
        node_id = str(raw_id)
        existing = state.nodes.get(node_id)
        if existing is None:
            continue
        if existing.get("type") == "knowledge" and (existing.get("provenance") or {}).get("active"):
            raise KnowledgeError(f"cannot remove active authored knowledge node: {node_id}")
        state.nodes.pop(node_id)
        entry_deletes.add(repo_root / entry_relative(node_id))
        removed_nodes += 1
        state.edges = {
            key: edge
            for key, edge in state.edges.items()
            if edge.get("source") != node_id and edge.get("target") != node_id
        }
        state.references = [
            reference for reference in state.references if reference.get("target") != node_id
        ]
    for raw in delta.get("nodes", []):
        node_id = str(raw.get("id", ""))
        if not ID_RE.fullmatch(node_id):
            raise KnowledgeError(f"invalid delta node id: {node_id!r}")
        existing = copy.deepcopy(state.nodes.get(node_id) or {})
        properties = dict(existing.get("properties") or {})
        properties.update(raw.get("properties") or {})
        node_type = str(raw.get("type") or existing.get("type") or "knowledge")
        if node_type == "knowledge" and "kind" in (raw.get("properties") or {}):
            from kgdistiller.document_types import (
                load_document_types,
                validate_node_kind,
            )

            if kind_profiles is None:
                kind_registry = registry or knowledge_root(repo_root) / "sources.json"
                has_registry = registry is not None or kind_registry.is_file()
                kind_profiles = load_document_types(kind_registry) if has_registry else {}
                kind_specs = load_sources(repo_root, kind_registry) if has_registry else []
            authority = str((existing.get("provenance") or {}).get("authority", ""))
            source = authority or str(raw.get("entry_source", "") or properties.get("entry_source", ""))
            source_path = (repo_root / source).resolve() if source else None
            owner = (unique_source_for_path(kind_specs, source_path)
                     if source_path and matching_sources(kind_specs, source_path) else None)
            document_type = owner.document_type if owner else ""
            original_properties = existing.get("properties") or {}
            if original_properties.get("kind_origin") != "reviewed" and "kind" in original_properties:
                properties["source_kind"] = original_properties["kind"]
            properties["kind"] = validate_node_kind(properties["kind"], document_type, kind_profiles)
            properties["kind_origin"] = "reviewed"
        properties.setdefault("aliases", [])
        properties.setdefault("origin", "agent")
        properties.setdefault("source_status", "meta")
        raw_entry = raw.get("entry") if "entry" in raw else existing.get("entry")
        if raw_entry is not None and not isinstance(raw_entry, dict):
            raise KnowledgeError(f"structured entry must be an object: {node_id}")
        if isinstance(raw_entry, dict):
            raw_entry = copy.deepcopy(raw_entry)
            if "entry" in raw:
                for field in ("understanding", "pending_prerequisites"):
                    if field not in raw_entry and field in (existing.get("entry") or {}):
                        raw_entry[field] = copy.deepcopy(existing["entry"][field])
            elif "text" in raw:
                raw_entry["summary"] = str(raw["text"])
        text_value = str(
            raw.get("text")
            if "text" in raw
            else (raw_entry or {}).get("summary", existing.get("text", ""))
        )
        node = {
            "id": node_id,
            "type": node_type,
            "label": str(raw.get("label") or existing.get("label") or node_id.replace("-", " ")),
            "text": text_value,
            "properties": properties,
        }
        if raw_entry:
            node["entry"] = copy.deepcopy(raw_entry)
        if existing.get("provenance"):
            node["provenance"] = existing["provenance"]
        if node_type == "knowledge":
            reviewed_content = "text" in raw or "entry" in raw
            if reviewed_content and (text_value.strip() or raw_entry):
                fingerprint = str(
                    (node.get("provenance") or {}).get("definition_sha256", "")
                )
                properties["curation_status"] = "current"
                if fingerprint:
                    properties["curated_definition_sha256"] = fingerprint
            elif reviewed_content:
                properties["curation_status"] = "pending"
                properties.pop("curated_definition_sha256", None)
            else:
                properties.setdefault(
                    "curation_status", "current" if text_value.strip() or raw_entry else "pending"
                )
        state.nodes[node_id] = node
        reviewed_kind = "kind" in (raw.get("properties") or {})
        if node_type == "knowledge" and reviewed_kind and not (text_value.strip() or raw_entry):
            raise KnowledgeError(
                f"reviewed kind requires an existing knowledge entry or reviewed content: {node_id}"
            )
        if node_type == "knowledge" and reviewed_kind and not reviewed_content:
            entry_path = repo_root / entry_relative(node_id)
            if entry_path not in entry_writes and not entry_path.is_file():
                raise KnowledgeError(
                    f"reviewed kind requires an existing knowledge entry or reviewed content: {node_id}"
                )
            try:
                entry_writes[entry_path] = entry_with_kind(
                    entry_path, str(properties["kind"]), content=entry_writes.get(entry_path)
                )
            except EntryMarkdownError as error:
                raise KnowledgeError(str(error)) from error
            entry_deletes.discard(entry_path)
            continue
        if node_type == "knowledge" and reviewed_content:
            entry_path = repo_root / entry_relative(node_id)
            if not text_value.strip() and not raw_entry:
                entry_deletes.add(entry_path)
                entry_writes.pop(entry_path, None)
            else:
                try:
                    source, source_path = resolve_entry_source(
                        repo_root,
                        node,
                        str(raw.get("entry_source", "")) or None,
                    )
                    normalized_entry = normalize_entry(raw_entry, text_value)
                    source_sha = authority_sha256(source_path)
                    definition_sha = str(
                        (node.get("provenance") or {}).get("definition_sha256", "")
                    )
                    content = render_entry(
                        node_id=node_id,
                        label=str(node["label"]),
                        entry=normalized_entry,
                        source=source,
                        source_sha256=source_sha,
                        definition_sha256=definition_sha,
                        origin=str(properties.get("entry_origin", "agent-extracted")),
                        kind=(str(properties["kind"])
                              if properties.get("kind_origin") == "reviewed" else None),
                    )
                except EntryMarkdownError as error:
                    raise KnowledgeError(str(error)) from error
                entry_writes[entry_path] = content
                entry_deletes.discard(entry_path)
    for raw in delta.get("remove_edges", []):
        state.edges.pop(
            (str(raw["source"]), str(raw["relation"]), str(raw["target"])),
            None,
        )
    for raw in delta.get("edges", []):
        edge = {
            "source": str(raw["source"]),
            "relation": str(raw["relation"]),
            "target": str(raw["target"]),
            "origin": str(raw.get("origin", "agent")),
            "confidence": str(raw.get("confidence", "high")),
            "evidence": str(raw.get("evidence", "agent semantic extraction")),
        }
        state.edges[edge_key(edge)] = edge
    refresh_semantic_edge_curation(state)
    # Entry Markdown is the authority. Install reviewed entry changes before
    # hydrating the graph projection from those files.
    for path in sorted(entry_deletes):
        if path.is_symlink():
            raise KnowledgeError(f"entry authority is a symlink: {path}")
        if path.is_file():
            path.unlink()
    for path, content in sorted(entry_writes.items(), key=lambda item: str(item[0])):
        write_entry(path, content)
    try:
        entry_hashes = load_entry_authorities(repo_root, state.nodes)
    except EntryMarkdownError as error:
        raise KnowledgeError(str(error)) from error
    artifacts = make_artifacts(
        state,
        dict(state.manifest.get("source_hashes") or {}),
        entry_hashes=entry_hashes,
        registry_sha256=state.manifest.get("registry_sha256"),
        identity_sha256=state.manifest.get("identity_sha256"),
        git_revision=state.manifest.get("git_revision"),
    )
    diagnostics = validate_state(state)
    if diagnostics["errors"]:
        raise KnowledgeError("\n".join(item["message"] for item in diagnostics["errors"]))
    state.manifest = json.loads(artifacts["manifest.json"])
    write_artifacts(graph_dir, artifacts)
    state = load_state(graph_dir, repo_root=repo_root)
    after = state.manifest["counts"]
    return {
        "nodes_removed": removed_nodes,
        "nodes_upserted": len(delta.get("nodes", [])),
        "edges_upserted": len(delta.get("edges", [])),
        "edges_removed": len(delta.get("remove_edges", [])),
        "delta": {
            key: int(after.get(key, 0)) - int(before.get(key, 0))
            for key in ("nodes", "edges", "references")
        },
        "counts": after,
        "warnings": len(diagnostics["warnings"]),
    }


def reconcile_node_name(
    state: GraphState,
    identity_path: Path,
    node_id_or_name: str,
    new_name: str,
) -> dict[str, Any]:
    """Record an explicit authored-name change without changing the stable node ID."""
    registered = load_identity_registry(identity_path)
    graph_index = build_identity_index(state, registered)
    node_id = node_id_or_name if node_id_or_name in state.nodes else graph_index.get(
        identity_key(node_id_or_name), ""
    )
    node = state.nodes.get(node_id) if node_id else None
    if node is None or node.get("type") != "knowledge":
        raise KnowledgeError(f"unknown knowledge node for reconciliation: {node_id_or_name}")
    canonical_name = unicodedata.normalize("NFKC", new_name).strip()
    if not canonical_name:
        raise KnowledgeError("new knowledge name must not be empty")
    existing = graph_index.get(identity_key(canonical_name))
    if existing and existing != node_id:
        raise KnowledgeError(
            f"new knowledge name {canonical_name!r} already resolves to {existing!r}"
        )

    previous = registered.get(node_id) or {}
    names = [
        str(previous.get("canonical_name", "")),
        *previous.get("aliases", []),
        str(node.get("label", "")),
        *((node.get("properties") or {}).get("aliases", [])),
    ]
    aliases = list(
        dict.fromkeys(
            name.strip()
            for name in names
            if name.strip() and identity_key(name) != identity_key(canonical_name)
        )
    )
    registered[node_id] = {
        "id": node_id,
        "canonical_name": canonical_name,
        "aliases": aliases,
    }
    payload = {
        "schema": IDENTITY_SCHEMA,
        "identities": [registered[key] for key in sorted(registered)],
    }
    identity_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(identity_path, pretty_json(payload))
    return {
        "id": node_id,
        "old_name": str(node.get("label", "")),
        "new_name": canonical_name,
        "identity_registry": str(identity_path),
        "next": "run kgdistiller sync to apply the reconciled source marker",
    }


def reconcile_alignment_mapping(
    state: GraphState,
    graph_dir: Path,
    alignment_path: Path,
    candidate_snapshot: dict[str, Any],
    candidate_id: str,
    target_id: str,
    *,
    predicate: str,
    status: str,
    justification: str,
    evidence: str,
    target_namespace: str,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Persist one reviewed cross-namespace decision with content fingerprints."""
    from kgdistiller.alignment import (
        load_alignment_set,
        make_reviewed_mapping,
        upsert_mapping,
    )
    from kgdistiller.query import align, get, load_graph_view

    candidate_namespace = str(candidate_snapshot.get("namespace", ""))
    view = load_graph_view(graph_dir, alignment_path, repo_root=repo_root)
    align(
        view,
        candidate_snapshot,
        alignments=alignment_path,
        target_namespace=target_namespace,
        limit_per_node=1,
    )
    candidate = next(
        (
            node
            for node in candidate_snapshot.get("nodes") or []
            if str(node.get("id", "")) == candidate_id
        ),
        None,
    )
    if candidate is None:
        raise KnowledgeError(
            f"candidate snapshot has no node {candidate_namespace}:{candidate_id}"
        )
    target = get(
        view,
        target_id,
        alignments=alignment_path,
        namespace=target_namespace,
    )["node"]
    mapping = make_reviewed_mapping(
        subject_namespace=candidate_namespace,
        subject_node=candidate,
        predicate=predicate,
        object_namespace=target_namespace,
        object_node=target,
        status=status,
        justification=justification,
        evidence=evidence,
    )
    alignment_set = upsert_mapping(load_alignment_set(alignment_path), mapping)
    alignment_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(alignment_path, pretty_json(alignment_set))
    return {
        "schema": alignment_set["schema"],
        "mapping": mapping,
        "alignment_registry": str(alignment_path),
        "mappings": len(alignment_set["mappings"]),
        "query_view": "reloads from the committed graph and alignment registry",
    }


def search_graph(state: GraphState, query: str, limit: int) -> list[dict[str, Any]]:
    terms = [item for item in unicodedata.normalize("NFKC", query).lower().split() if item]
    scored: list[tuple[int, dict[str, Any]]] = []
    for node in state.nodes.values():
        properties = node.get("properties") or {}
        aliases = " ".join(str(item) for item in properties.get("aliases", []))
        label = str(node.get("label", ""))
        haystack = " ".join((node["id"], label, str(node.get("text", "")), aliases)).lower()
        if not all(term in haystack for term in terms):
            continue
        score = 20 if all(term in label.lower() for term in terms) else 0
        scored.append((score, node))
    return [item for _, item in sorted(scored, key=lambda pair: (-pair[0], pair[1]["label"]))[:limit]]


def show_node(state: GraphState, node_id_or_name: str) -> dict[str, Any]:
    node_id = node_id_or_name
    if node_id not in state.nodes:
        node_id = build_identity_index(state).get(identity_key(node_id_or_name), "")
    if not node_id or node_id not in state.nodes:
        raise KnowledgeError(f"unknown knowledge node: {node_id_or_name}")
    return {
        "node": state.nodes[node_id],
        "incoming": sorted(
            [item for item in state.edges.values() if item["target"] == node_id],
            key=json_text,
        ),
        "outgoing": sorted(
            [item for item in state.edges.values() if item["source"] == node_id],
            key=json_text,
        ),
        "backlinks": sorted(
            [item for item in state.references if item["target"] == node_id],
            key=json_text,
        ),
    }


def curation_report(
    state: GraphState,
    authorities: set[str],
    *,
    node_ids: set[str] | None = None,
    relation_node_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Check relations across the source while allowing partial entry coverage."""
    active = {
        node_id: node
        for node_id, node in state.nodes.items()
        if node.get("type") == "knowledge"
        and (node.get("provenance") or {}).get("active")
    }
    authority_node_ids = {
        node_id for node_id, node in active.items()
        if (node.get("provenance") or {}).get("authority") in authorities
    }
    selected = {
        node_id: node for node_id, node in active.items()
        if node_id in (authority_node_ids if node_ids is None else node_ids)
    }
    # A partial write can remove a reference required by an unchanged sibling,
    # or add an edge without touching its consumer's entry or source file.
    # Neither case requires full entry coverage, but both require valid relations.
    relation_scope = (
        authority_node_ids | set(selected) | (relation_node_ids or set())
    ) & set(active)
    errors: list[dict[str, Any]] = []
    entries = 0
    for node_id, node in selected.items():
        if (node.get("properties") or {}).get("curation_status") == "needs-review":
            errors.append(
                diagnostic(
                    "stale-node-entry",
                    "active knowledge node changed after its entry was curated",
                    source=(node.get("provenance") or {}).get("authority"),
                    node=node_id,
                )
            )
        if str(node.get("text", "")).strip():
            entries += 1
            continue
        errors.append(
            diagnostic(
                "missing-node-entry",
                "active knowledge node has no source-grounded text entry",
                source=(node.get("provenance") or {}).get("authority"),
                node=node_id,
            )
        )

    reference_pairs = {
        (str(item.get("authority", "")), str(item.get("target", "")))
        for item in state.references
    }
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for edge in state.edges.values():
        relation = str(edge.get("relation", ""))
        endpoints = CROSS_FILE_REF_ENDPOINTS.get(relation)
        if endpoints is None:
            continue
        consumer_id = str(edge.get(endpoints[0], ""))
        dependency_id = str(edge.get(endpoints[1], ""))
        consumer = state.nodes.get(consumer_id) or {}
        dependency = state.nodes.get(dependency_id) or {}
        consumer_provenance = consumer.get("provenance") or {}
        dependency_provenance = dependency.get("provenance") or {}
        consumer_authority = str(consumer_provenance.get("authority", ""))
        dependency_authority = str(dependency_provenance.get("authority", ""))
        if (
            consumer_id not in relation_scope
            or not dependency_authority
            or not dependency_provenance.get("active")
            or consumer_authority == dependency_authority
        ):
            continue
        key = (consumer_authority, dependency_id)
        requirement = grouped.setdefault(
            key,
            {
                "authority": consumer_authority,
                "target": dependency_id,
                "target_authority": dependency_authority,
                "consumer_nodes": set(),
                "relations": set(),
                "evidence": set(),
            },
        )
        requirement["consumer_nodes"].add(consumer_id)
        requirement["relations"].add(relation)
        if edge.get("evidence"):
            requirement["evidence"].add(str(edge["evidence"]))

    requirements: list[dict[str, Any]] = []
    for key, raw in sorted(grouped.items()):
        covered = key in reference_pairs
        requirement = {
            "authority": raw["authority"],
            "target": raw["target"],
            "target_authority": raw["target_authority"],
            "consumer_nodes": sorted(raw["consumer_nodes"]),
            "relations": sorted(raw["relations"]),
            "evidence": sorted(raw["evidence"]),
            "covered": covered,
        }
        requirements.append(requirement)
        if not covered:
            errors.append(
                diagnostic(
                    "missing-cross-file-ref",
                    f"direct external dependency has no file-level #ref: {raw['target']}",
                    source=raw["authority"],
                    node=raw["target"],
                )
            )

    for edge in state.edges.values():
        if (
            edge.get("curation_status") == "needs-review"
            and ({str(edge.get("source", "")), str(edge.get("target", ""))} & relation_scope)
        ):
            errors.append(
                diagnostic(
                    "stale-semantic-edge",
                    "semantic edge must be reviewed against the current authority",
                    node=str(edge.get("source", "")),
                )
            )

    return {
        "schema": "kgdistiller-curation-check-v1",
        "files": sorted(authorities),
        "nodes": len(selected),
        "entries": entries,
        "required_refs": requirements,
        "errors": sorted(errors, key=json_text),
    }


def audit_report(state: GraphState) -> dict[str, Any]:
    """Summarize deterministic graph and curation coverage without inferring semantics."""
    active = {
        node_id: node
        for node_id, node in state.nodes.items()
        if node.get("type") == "knowledge"
        and (node.get("provenance") or {}).get("active")
    }
    semantic_edges = list(state.edges.values())
    adjacency: dict[str, set[str]] = defaultdict(set)
    semantic_degree: Counter[str] = Counter()
    for edge in semantic_edges:
        source = str(edge.get("source", ""))
        target = str(edge.get("target", ""))
        if source in active and target in active:
            adjacency[source].add(target)
            adjacency[target].add(source)
            semantic_degree[source] += 1
            semantic_degree[target] += 1

    unseen = set(active)
    component_sizes: list[int] = []
    while unseen:
        start = min(unseen)
        unseen.remove(start)
        stack = [start]
        size = 0
        while stack:
            current = stack.pop()
            size += 1
            for neighbor in sorted(adjacency[current]):
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    stack.append(neighbor)
        component_sizes.append(size)
    component_sizes.sort(reverse=True)

    authorities: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in active.values():
        authority = str((node.get("provenance") or {}).get("authority", ""))
        authorities[authority].append(node)
    complete_authorities: list[str] = []
    partial_authorities: list[str] = []
    pending_authorities: list[str] = []
    for authority, nodes in sorted(authorities.items()):
        entries = sum(bool(str(node.get("text", "")).strip()) for node in nodes)
        if entries == len(nodes):
            complete_authorities.append(authority)
        elif entries:
            partial_authorities.append(authority)
        else:
            pending_authorities.append(authority)

    entry_count = sum(bool(str(node.get("text", "")).strip()) for node in active.values())
    relation_counts = Counter(str(edge.get("relation", "")) for edge in state.edges.values())
    return {
        "schema": "kgdistiller-audit-v1",
        "counts": {
            "nodes": len(state.nodes),
            "active_knowledge": len(active),
            "entries": entry_count,
            "edges": len(state.edges),
            "semantic_edges": len(semantic_edges),
            "references": len(state.references),
        },
        "curation": {
            "entry_ratio": round(entry_count / len(active), 6) if active else 1.0,
            "node_statuses": dict(
                sorted(
                    Counter(
                        str((node.get("properties") or {}).get("curation_status", "pending"))
                        for node in active.values()
                    ).items()
                )
            ),
            "stale_semantic_edges": sum(
                edge.get("curation_status") == "needs-review" for edge in semantic_edges
            ),
            "authorities": len(authorities),
            "complete_authorities": complete_authorities,
            "partial_authorities": partial_authorities,
            "pending_authorities": pending_authorities,
        },
        "topology": {
            "semantic_components": len(component_sizes),
            "largest_component": component_sizes[0] if component_sizes else 0,
            "isolated_nodes": sum(size == 1 for size in component_sizes),
            "component_size_histogram": {
                str(size): count
                for size, count in sorted(Counter(component_sizes).items())
            },
            "top_hubs": [
                {"id": node_id, "degree": degree}
                for node_id, degree in sorted(
                    semantic_degree.items(),
                    key=lambda item: (-item[1], item[0]),
                )[:12]
            ],
        },
        "relations": {
            relation: relation_counts[relation]
            for relation in sorted(relation_counts)
        },
        "quality": {
            "semantic_edges_missing_evidence": sum(
                not str(edge.get("evidence", "")).strip() for edge in semantic_edges
            ),
            "semantic_edges_missing_confidence": sum(
                not str(edge.get("confidence", "")).strip() for edge in semantic_edges
            ),
        },
    }


def defaults(repo_root: Path, value: str) -> Path:
    return (repo_root / value).resolve()


def add_scope_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--file", action="append", default=[], type=Path)


def add_model_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--embedding", action="store_true",
        help="explicitly enable local embedding candidates (requires the retrieval extra)",
    )
    parser.add_argument("--model-device", choices=("cpu", "mps", "cuda"), default="cpu")
    parser.add_argument("--model-batch-size", type=int, default=4)
    parser.add_argument("--model-max-length", type=int, default=8192)
    parser.add_argument("--embedding-model", help="embedding model; defaults to the adapter's pinned BAAI/bge-m3")
    parser.add_argument("--embedding-revision", help="immutable revision; defaults to the adapter's pinned revision")
    parser.add_argument("--rerank", action="store_true", help="rerank embedding and lexical candidates with the pinned local cross-encoder; requires --embedding")
    parser.add_argument("--rerank-candidates", type=int, default=50, help="maximum candidates sent to the reranker (1 to 500)")
    parser.add_argument("--reranker-model", help="reranker model; defaults to the adapter's pinned BAAI/bge-reranker-v2-m3")
    parser.add_argument("--reranker-revision", help="immutable reranker revision; defaults to the adapter's pinned revision")
    parser.add_argument("--model-cache-dir", type=Path, help=f"derived vector cache; defaults to {KNOWLEDGE_DIRECTORY}/build/retrieval")
    parser.add_argument("--models-offline", action="store_true", help="load only already downloaded local model files")


def add_graph_retrieval_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--graph-retrieval", action="store_true",
        help="explicitly explore source-backed graph edges from ranked candidate roots",
    )
    parser.add_argument("--graph-seed-candidates", type=int, default=5, help="maximum ranked candidate roots (1 to 32)")
    parser.add_argument("--graph-edge-policy", choices=("high-confidence", "current"), default="high-confidence", help="edge gate; high-confidence uses declared confidence and evidence, not independent review")


def make_graph_retrieval_policy(args: argparse.Namespace):
    """Keep graph exploration opt-in independently of optional model inference."""
    if not args.graph_retrieval:
        return None
    from .graph_retrieval import GraphRetrievalPolicy
    from .retrieval import RetrievalError

    try:
        return GraphRetrievalPolicy(candidate_limit=args.graph_seed_candidates, edge_policy=args.graph_edge_policy)
    except ValueError as error:
        raise RetrievalError("invalid-graph-settings", str(error)) from error


def load_support_selection(path: Path, repo_root: Path) -> dict[str, Any]:
    from .contracts import ContractError, parse_contract_json, validate_contract
    from .retrieval import RetrievalError, _read_bounded_regular_file

    resolved = path.resolve() if path.is_absolute() else (repo_root / path).resolve()
    try:
        value = parse_contract_json(_read_bounded_regular_file(resolved).decode("utf-8"))
        if not isinstance(value, dict) or value.get("schema") != "kgdistiller-support-selection-v1":
            raise ContractError("expected support-selection-v1")
        return validate_contract(value)
    except (OSError, UnicodeError, ContractError, RetrievalError) as error:
        raise RetrievalError("invalid-support-selection", "support selection file is invalid or unreadable") from error


def make_ranking_service(args: argparse.Namespace, *, graph_dir: Path, repo_root: Path):
    """Keep optional model dependencies out of every non-model operation."""
    if args.rerank and not args.embedding:
        from .retrieval import RetrievalError
        raise RetrievalError("invalid-model-settings", "--rerank requires --embedding")
    if not args.embedding:
        return None
    from .adapters.sentence_transformers import (
        DEFAULT_EMBEDDING_MODEL,
        DEFAULT_EMBEDDING_REVISION,
        SentenceTransformersAdapter,
    )
    from .retrieval import RetrievalError
    from .semantic_retrieval import SemanticRankingService, SemanticRetrievalError

    cache_dir = args.model_cache_dir or graph_dir.parent / "build" / "retrieval"
    if not cache_dir.is_absolute():
        cache_dir = repo_root / cache_dir
    try:
        adapter_options = {
            "model": args.embedding_model if args.embedding_model is not None else DEFAULT_EMBEDDING_MODEL,
            "revision": args.embedding_revision if args.embedding_revision is not None else DEFAULT_EMBEDDING_REVISION,
            "device": args.model_device,
            "batch_size": args.model_batch_size,
            "max_length": args.model_max_length,
            "local_files_only": args.models_offline,
        }
        service_options = {}
        if args.rerank:
            from .adapters.sentence_transformers import (
                DEFAULT_RERANKER_MODEL,
                DEFAULT_RERANKER_REVISION,
            )
            adapter_options.update({
                "reranker_model": args.reranker_model if args.reranker_model is not None else DEFAULT_RERANKER_MODEL,
                "reranker_revision": args.reranker_revision if args.reranker_revision is not None else DEFAULT_RERANKER_REVISION,
            })
            service_options = {"rerank": True, "candidate_limit": args.rerank_candidates}
        adapter = SentenceTransformersAdapter(**adapter_options)
        return SemanticRankingService(adapter, cache_dir=cache_dir.resolve(), **service_options)
    except SemanticRetrievalError as error:
        raise RetrievalError(error.code, error.message) from error


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--repo-root",
        type=Path,
        help="use an explicit vault path without consulting the user registry",
    )
    target.add_argument(
        "--vault",
        help="select a registered vault by machine-local name or stable ID",
    )
    parser.add_argument(
        "--kgdistiller-home",
        type=Path,
        help="override the user-level registry directory (or use KGDISTILLER_HOME)",
    )
    parser.add_argument("--registry", default=f"{KNOWLEDGE_DIRECTORY}/sources.json")
    parser.add_argument("--graph", default=f"{KNOWLEDGE_DIRECTORY}/graph")
    parser.add_argument("--identities", default=f"{KNOWLEDGE_DIRECTORY}/identities.json")
    parser.add_argument("--alignments", default=f"{KNOWLEDGE_DIRECTORY}/alignments.json")
    commands = parser.add_subparsers(dest="command", required=True)
    vault_command = commands.add_parser(
        "vault",
        help="manage machine-local vault registrations",
        description="Manage machine-local vault names, paths, and the default target.",
    )
    vault_commands = vault_command.add_subparsers(
        dest="vault_command", required=True
    )
    vault_register = vault_commands.add_parser(
        "register", help="register or relocate a vault"
    )
    vault_register.add_argument("path", type=Path)
    vault_register.add_argument("--name")
    vault_register.add_argument(
        "--replace",
        action="store_true",
        help="relocate an existing vault identity even when its old path still exists",
    )
    vault_commands.add_parser("list", help="list registered vaults")
    vault_show = vault_commands.add_parser("show", help="show one registered vault")
    vault_show.add_argument("selector")
    vault_default = vault_commands.add_parser(
        "default", help="set or clear the default vault"
    )
    vault_default.add_argument("selector", nargs="?")
    vault_default.add_argument(
        "--clear",
        action="store_true",
        help="clear the default vault",
    )
    vault_unregister = vault_commands.add_parser(
        "unregister", help="remove a machine-local registration"
    )
    vault_unregister.add_argument("selector")
    vault_doctor = vault_commands.add_parser(
        "doctor", help="validate registered paths and portable identities"
    )
    vault_doctor.add_argument("selector", nargs="?")
    init_command = commands.add_parser("init")
    init_command.add_argument("--source-root", type=Path, default=Path("notes"))
    init_command.add_argument("--force", action="store_true")
    for name in ("sync", "build", "scan"):
        command = commands.add_parser(name)
        add_scope_arguments(command)
    apply_command = commands.add_parser("apply")
    apply_command.add_argument("delta", type=Path)
    derive_command = commands.add_parser(
        "derive",
        help=f"place converted Markdown under the owning vault's {KNOWLEDGE_DIRECTORY}/derived tree",
    )
    derive_commands = derive_command.add_subparsers(
        dest="derive_command", required=True
    )
    derive_locate = derive_commands.add_parser("locate")
    derive_locate.add_argument("source", type=Path)
    derive_locate.add_argument("--output", type=Path)
    derive_install = derive_commands.add_parser("install")
    derive_install.add_argument("source", type=Path)
    derive_install.add_argument("--input", type=Path, required=True)
    derive_install.add_argument("--output", type=Path)
    derive_install.add_argument("--replace", action="store_true")
    obsidian_command = commands.add_parser(
        "obsidian",
        help="manage kgdistiller's integration with an Obsidian vault",
    )
    obsidian_commands = obsidian_command.add_subparsers(
        dest="obsidian_command", required=True
    )
    obsidian_install = obsidian_commands.add_parser(
        "install", help="install the bundled kgdistiller plugin into the selected vault"
    )
    obsidian_install.add_argument(
        "--replace",
        action="store_true",
        help="atomically update an existing kgdistiller plugin bundle",
    )
    obsidian_install.add_argument(
        "--no-enable",
        action="store_false",
        dest="enable",
        help="install the plugin files without adding kgdistiller to community-plugins.json",
    )
    reconcile_command = commands.add_parser("reconcile")
    reconcile_commands = reconcile_command.add_subparsers(dest="reconcile_command", required=True)
    rename_command = reconcile_commands.add_parser("rename-node")
    rename_command.add_argument("id")
    rename_command.add_argument("new_name")
    alignment_command = reconcile_commands.add_parser("alignment")
    alignment_command.add_argument("candidate", type=Path)
    alignment_command.add_argument("candidate_id")
    alignment_command.add_argument("target_id")
    alignment_command.add_argument(
        "--predicate",
        choices=(
            "exact-match",
            "close-match",
            "broad-match",
            "narrow-match",
            "related-match",
            "different-from",
        ),
        default="exact-match",
    )
    alignment_command.add_argument(
        "--status", choices=("reviewed", "rejected"), default="reviewed"
    )
    alignment_command.add_argument(
        "--justification", default="manual-mapping-curation"
    )
    alignment_command.add_argument("--evidence", required=True)
    alignment_command.add_argument("--target-namespace", default="personal")
    commands.add_parser("check")
    search_command = commands.add_parser("search")
    search_command.add_argument("query")
    search_command.add_argument("--limit", type=int, default=20)
    show_command = commands.add_parser("show")
    show_command.add_argument("id")
    curate_command = commands.add_parser("curate-check")
    curate_command.add_argument("--file", action="append", required=True, type=Path)
    commands.add_parser("audit")
    commands.add_parser("stats")
    snapshot_command = commands.add_parser("snapshot")
    snapshot_command.add_argument("--namespace", default="personal")
    snapshot_command.add_argument("--output", type=Path)
    candidate_command = commands.add_parser("candidate")
    candidate_commands = candidate_command.add_subparsers(
        dest="candidate_command", required=True
    )
    candidate_build = candidate_commands.add_parser("build")
    candidate_build.add_argument("source", type=Path)
    candidate_build.add_argument("--output", type=Path)
    candidate_validate = candidate_commands.add_parser("validate")
    candidate_validate.add_argument("snapshot", type=Path)
    agent_command = commands.add_parser("agent")
    agent_commands = agent_command.add_subparsers(dest="agent_command", required=True)
    agent_commands.add_parser("status")
    compiled_command = agent_commands.add_parser(
        "compiled", help="search, navigate and read an explicitly supplied compiled knowledge library"
    )
    compiled_command.add_argument("--library", type=Path, required=True)
    compiled_operations = compiled_command.add_subparsers(dest="compiled_operation", required=True)
    compiled_search = compiled_operations.add_parser("search")
    compiled_search.add_argument("query")
    compiled_search.add_argument("--limit", type=int, default=40)
    compiled_browse = compiled_operations.add_parser("browse")
    compiled_browse.add_argument("reference", nargs="?")
    compiled_get = compiled_operations.add_parser("get")
    compiled_get.add_argument("reference", nargs="+")
    compiled_inventory = compiled_operations.add_parser("inventory", help="list all exact authored term declarations")
    compiled_inventory.add_argument("term")
    compiled_pack = compiled_operations.add_parser("pack")
    compiled_pack.add_argument("reference", nargs="+")
    compiled_pack.add_argument("--budget", type=int, default=24000, help="complete UTF-8 response byte budget")
    evidence_command = agent_commands.add_parser("evidence", help="retrieve exact raw-source evidence spans without creating graph identities")
    evidence_command.add_argument("query")
    evidence_command.add_argument("--manifest", type=Path, required=True)
    evidence_command.add_argument("--doc-id", action="append", dest="doc_ids")
    evidence_command.add_argument("--limit", type=int, default=10)
    evidence_command.add_argument("--budget", type=int, default=12000, help="canonical UTF-8 response byte budget")
    evidence_command.add_argument("--context-projection", choices=("full", "compact"), default="full", help="compact shares source and heading records while preserving exact fragment text")
    evidence_resolve = agent_commands.add_parser("evidence-resolve", help="resolve registered source document references, including exact declared versions")
    evidence_resolve.add_argument("reference", nargs="+")
    evidence_resolve.add_argument("--manifest", type=Path, required=True)
    resolve_command = agent_commands.add_parser("resolve")
    resolve_command.add_argument("concept", nargs="+")
    resolve_command.add_argument("--namespace", default="personal")
    agent_search_command = agent_commands.add_parser("search")
    agent_search_command.add_argument("query", nargs="?")
    agent_search_command.add_argument(
        "--plan",
        type=Path,
        help="execute a kgdistiller-retrieval-plan-v1 JSON file instead of a legacy query",
    )
    agent_search_command.add_argument("--namespace")
    agent_search_command.add_argument("--limit", type=int)
    agent_search_command.add_argument("--depth", type=int)
    agent_search_command.add_argument("--include-stale", action="store_true", default=None)
    agent_search_command.add_argument(
        "--include-orphaned", action="store_true", default=None
    )
    agent_search_command.add_argument(
        "--graph-strategy", choices=("bfs", "ppr", "hybrid")
    )
    add_model_arguments(agent_search_command)
    add_graph_retrieval_arguments(agent_search_command)
    get_command = agent_commands.add_parser("get")
    get_command.add_argument("id")
    get_command.add_argument("--namespace", default="personal")
    expand_command = agent_commands.add_parser("expand")
    expand_command.add_argument("id", nargs="+")
    expand_command.add_argument("--namespace", default="personal")
    expand_command.add_argument(
        "--direction",
        choices=("incoming", "outgoing", "both"),
        default="both",
    )
    expand_command.add_argument("--relation", action="append", dest="edge_types")
    expand_command.add_argument("--depth", type=int, default=1)
    expand_command.add_argument("--limit", type=int, default=50)
    expand_command.add_argument("--include-stale", action="store_true")
    expand_command.add_argument("--include-orphaned", action="store_true")
    ppr_command = agent_commands.add_parser("ppr")
    ppr_command.add_argument("id", nargs="+")
    ppr_command.add_argument("--namespace", default="personal")
    ppr_command.add_argument("--relation", action="append", dest="edge_types")
    ppr_command.add_argument(
        "--direction",
        choices=("incoming", "outgoing", "both"),
        default="outgoing",
    )
    ppr_command.add_argument("--limit", type=int, default=50)
    ppr_command.add_argument("--include-stale", action="store_true")
    ppr_command.add_argument("--include-orphaned", action="store_true")
    context_command = agent_commands.add_parser("context")
    context_command.add_argument("query", nargs="?")
    context_command.add_argument(
        "--plan",
        type=Path,
        help="execute a kgdistiller-retrieval-plan-v1 JSON file instead of a legacy query",
    )
    context_command.add_argument("--namespace")
    context_command.add_argument("--budget", type=int, default=6000)
    context_command.add_argument("--context-projection", choices=("full", "compact"), default="full", help="source context projection; compact retains definitions and conditions and stores path evidence once")
    context_command.add_argument("--support-selection", type=Path, help="source-bound caller-selected evidence support manifest; does not change answer ranking or identity")
    context_command.add_argument("--limit", type=int)
    context_command.add_argument("--depth", type=int)
    context_command.add_argument("--include-stale", action="store_true", default=None)
    context_command.add_argument(
        "--include-orphaned", action="store_true", default=None
    )
    context_command.add_argument(
        "--graph-strategy", choices=("bfs", "ppr", "hybrid")
    )
    add_model_arguments(context_command)
    add_graph_retrieval_arguments(context_command)
    align_command = agent_commands.add_parser("align")
    align_command.add_argument("candidate", type=Path)
    align_command.add_argument("--target-namespace", default="personal")
    align_command.add_argument("--limit", type=int, default=10)
    align_command.add_argument("--output", type=Path)
    compare_command = agent_commands.add_parser("compare")
    compare_command.add_argument("candidate", type=Path)
    compare_command.add_argument("--target-namespace", default="personal")
    propose_command = agent_commands.add_parser("propose")
    propose_command.add_argument("candidate", type=Path)
    propose_command.add_argument("--target-namespace", default="personal")
    propose_command.add_argument("--target-authority")
    propose_command.add_argument("--output", type=Path)
    propose_command.add_argument("--delta-output", type=Path)
    harvest_command = commands.add_parser("harvest")
    harvest_commands = harvest_command.add_subparsers(
        dest="harvest_command", required=True
    )
    harvest_prepare = harvest_commands.add_parser("prepare")
    harvest_prepare.add_argument("input", type=Path)
    harvest_prepare.add_argument("--sheet", type=Path, required=True)
    harvest_prepare.add_argument("--output", type=Path, required=True)
    harvest_apply = harvest_commands.add_parser("apply")
    harvest_apply.add_argument("sheet", type=Path)
    harvest_apply.add_argument("--output", type=Path, required=True)
    capture_command = commands.add_parser("capture")
    capture_commands = capture_command.add_subparsers(
        dest="capture_command", required=True
    )
    capture_prepare = capture_commands.add_parser("prepare")
    capture_prepare.add_argument("input", type=Path)
    capture_prepare.add_argument("--output", type=Path, required=True)
    ingest_command = commands.add_parser("ingest")
    ingest_commands = ingest_command.add_subparsers(
        dest="ingest_command", required=True
    )
    ingest_plan = ingest_commands.add_parser("plan")
    ingest_plan.add_argument("request", type=Path)
    ingest_plan.add_argument("--output", type=Path)
    ingest_apply = ingest_commands.add_parser("apply")
    ingest_apply.add_argument("request", type=Path)
    ingest_apply.add_argument("--receipt", type=Path)
    store_command = commands.add_parser("store")
    store_commands = store_command.add_subparsers(
        dest="store_command", required=True
    )
    store_snapshot = store_commands.add_parser("snapshot")
    store_snapshot.add_argument(
        "--output",
        type=Path,
        help="write a self-contained copy instead of refreshing this repository",
    )
    store_commands.add_parser("verify")
    export_command = commands.add_parser("export")
    export_commands = export_command.add_subparsers(
        dest="export_command", required=True
    )
    export_obsidian = export_commands.add_parser(
        "obsidian", help="write the Obsidian plugin's typed graph feed"
    )
    export_obsidian.add_argument(
        "--output",
        type=Path,
        default=Path(KNOWLEDGE_DIRECTORY, "build", "obsidian", "semantic-graph.json"),
    )
    codex_command = commands.add_parser("codex")
    codex_commands = codex_command.add_subparsers(dest="codex_command", required=True)
    codex_link = codex_commands.add_parser("link")
    codex_link.add_argument("--codex-home", type=Path)
    codex_link.add_argument(
        "--mode",
        choices=("auto", "symlink", "copy"),
        default="auto",
        help="auto requires live links; copy is an explicit non-live snapshot",
    )
    codex_doctor = codex_commands.add_parser("doctor")
    codex_doctor.add_argument("--codex-home", type=Path)
    codex_doctor.add_argument("--source-only", action="store_true")
    claude_command = commands.add_parser("claude")
    claude_commands = claude_command.add_subparsers(
        dest="claude_command", required=True
    )
    claude_link = claude_commands.add_parser("link")
    claude_link.add_argument("--claude-home", type=Path)
    claude_link.add_argument(
        "--mode",
        choices=("auto", "symlink", "copy"),
        default="auto",
        help="auto requires live links; copy is an explicit non-live snapshot",
    )
    claude_doctor = claude_commands.add_parser("doctor")
    claude_doctor.add_argument("--claude-home", type=Path)
    claude_doctor.add_argument("--source-only", action="store_true")
    mcp_command = commands.add_parser("mcp")
    add_model_arguments(mcp_command)
    args = parser.parse_args()
    if hasattr(args, "graph_retrieval"):
        graph_options = {"--graph-seed-candidates", "--graph-edge-policy"}
        supplied_graph_options = set()
        for argument in sys.argv[1:]:
            if argument == "--":
                break
            if not argument.startswith("--"):
                continue
            name = argument.split("=", 1)[0]
            if name in graph_options:
                supplied_graph_options.add(name)
            else:
                matches = [option for option in graph_options if option.startswith(name)]
                if len(matches) == 1:
                    supplied_graph_options.add(matches[0])
        if supplied_graph_options and not args.graph_retrieval:
            parser.error("graph options require --graph-retrieval: " + ", ".join(sorted(supplied_graph_options)))
        if not 1 <= args.graph_seed_candidates <= 32:
            parser.error("--graph-seed-candidates must be between 1 and 32")
    if hasattr(args, "embedding"):
        model_options = {
            "--model-device", "--model-batch-size", "--model-max-length",
            "--embedding-model", "--embedding-revision", "--model-cache-dir",
            "--models-offline", "--rerank", "--rerank-candidates",
            "--reranker-model", "--reranker-revision",
        }
        supplied_options = set()
        for argument in sys.argv[1:]:
            if argument == "--":
                break
            if not argument.startswith("--"):
                continue
            name = argument.split("=", 1)[0]
            if name in model_options:
                supplied_options.add(name)
            else:
                # argparse accepts unambiguous option abbreviations too.
                matches = [option for option in model_options if option.startswith(name)]
                if len(matches) == 1:
                    supplied_options.add(matches[0])
        if args.rerank and not args.embedding:
            parser.error("--rerank requires --embedding")
        if supplied_options and not args.embedding:
            parser.error("model options require --embedding: " + ", ".join(sorted(supplied_options)))
        if not 1 <= args.model_batch_size <= 64:
            parser.error("--model-batch-size must be between 1 and 64")
        if not 1 <= args.model_max_length <= 8192:
            parser.error("--model-max-length must be between 1 and 8192")
        if args.embedding_model is not None and args.embedding_revision is None:
            parser.error("--embedding-model requires an explicit immutable --embedding-revision")
        if not 1 <= args.rerank_candidates <= 500:
            parser.error("--rerank-candidates must be between 1 and 500")
        if args.reranker_model is not None and args.reranker_revision is None:
            parser.error("--reranker-model requires an explicit immutable --reranker-revision")
        if not args.rerank and supplied_options.intersection({"--rerank-candidates", "--reranker-model", "--reranker-revision"}):
            parser.error("reranker settings require --rerank")
    if args.command == "vault" and (args.repo_root is not None or args.vault is not None):
        parser.error("vault registry commands cannot be combined with --repo-root or --vault")
    if (
        args.command == "vault"
        and args.vault_command == "default"
        and ((args.selector is None) == (not args.clear))
    ):
        parser.error("vault default requires one selector or --clear")
    if (
        args.command == "agent"
        and args.agent_command in {"search", "context"}
        and ((args.query is None) == (args.plan is None))
    ):
        parser.error("agent search/context requires exactly one of query or --plan")
    if (
        args.command == "agent"
        and args.agent_command in {"search", "context"}
        and args.plan is not None
        and any(
            getattr(args, name) is not None
            for name in (
                "namespace",
                "limit",
                "depth",
                "include_stale",
                "include_orphaned",
                "graph_strategy",
            )
        )
    ):
        parser.error("--plan cannot be combined with legacy retrieval controls")
    return args


def main() -> int:
    configure_console_streams()
    args = parse_args()
    try:
        if args.command == "agent" and args.agent_command == "compiled":
            from .compiled_retrieval import CompiledLibrary, CompiledRetrievalError

            try:
                library = CompiledLibrary.from_path(args.library)
                if args.compiled_operation == "search":
                    result = {"candidates": library.search(args.query, limit=args.limit)}
                elif args.compiled_operation == "browse":
                    result = library.browse(args.reference)
                elif args.compiled_operation == "inventory":
                    result = library.inventory(args.term)
                elif args.compiled_operation == "get":
                    result = {"entries": [library.get(reference) for reference in args.reference]}
                else:
                    result = library.pack(args.reference, byte_budget=args.budget)
            except (CompiledRetrievalError, OSError) as error:
                print(pretty_json({"error": str(error)}), end="", file=sys.stderr)
                return 1
            if args.compiled_operation == "pack":
                print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False), end="")
            else:
                print(pretty_json(result), end="")
            return 0
        if args.command == "vault":
            from kgdistiller.vault_registry import (
                doctor_vaults,
                list_vaults,
                register_vault,
                set_default_vault,
                show_vault,
                unregister_vault,
            )

            if args.vault_command == "register":
                result = register_vault(
                    args.path,
                    name=args.name,
                    home=args.kgdistiller_home,
                    replace=args.replace,
                )
            elif args.vault_command == "list":
                result = list_vaults(args.kgdistiller_home)
            elif args.vault_command == "show":
                result = show_vault(args.selector, args.kgdistiller_home)
            elif args.vault_command == "default":
                result = set_default_vault(
                    None if args.clear else args.selector,
                    args.kgdistiller_home,
                )
            elif args.vault_command == "unregister":
                result = unregister_vault(args.selector, args.kgdistiller_home)
            else:
                result = doctor_vaults(args.selector, args.kgdistiller_home)
            print(pretty_json(result), end="")
            return 1 if result.get("status") == "error" else 0
        if args.command == "codex":
            from .codex_product import CodexProductError, doctor_product, link_product

            try:
                if args.codex_command == "link":
                    result = link_product(codex_home=args.codex_home, mode=args.mode)
                else:
                    result = doctor_product(
                        codex_home=args.codex_home,
                        source_only=args.source_only,
                    )
            except (CodexProductError, OSError) as error:
                print(
                    pretty_json(
                        {
                            "kind": "kgdistiller-codex-product-error",
                            "code": "codex-product-failed",
                            "message": str(error),
                        }
                    ),
                    end="",
                    file=sys.stderr,
                )
                return 1
            print(pretty_json(result), end="")
            return 0
        if args.command == "claude":
            from .claude_product import (
                ClaudeProductError,
                doctor_claude_product,
                link_claude_product,
            )

            try:
                if args.claude_command == "link":
                    result = link_claude_product(
                        claude_home=args.claude_home, mode=args.mode
                    )
                else:
                    result = doctor_claude_product(
                        claude_home=args.claude_home,
                        source_only=args.source_only,
                    )
            except (ClaudeProductError, OSError) as error:
                print(
                    pretty_json(
                        {
                            "kind": "kgdistiller-claude-product-error",
                            "code": "claude-product-failed",
                            "message": str(error),
                        }
                    ),
                    end="",
                    file=sys.stderr,
                )
                return 1
            print(pretty_json(result), end="")
            return 0
        from kgdistiller.vault_registry import resolve_repo_root

        if args.command == "derive":
            from kgdistiller.derivation import (
                DerivationError,
                install_derivation,
                plan_derivation,
            )

            explicit_target = None
            if args.repo_root is not None or args.vault is not None:
                explicit_target = resolve_repo_root(
                    explicit_repo_root=args.repo_root,
                    explicit_vault=args.vault,
                    home=args.kgdistiller_home,
                    use_default=False,
                )
            try:
                if args.derive_command == "locate":
                    result = plan_derivation(
                        args.source,
                        target_vault=explicit_target,
                        output=args.output,
                    )
                else:
                    result = install_derivation(
                        args.source,
                        args.input,
                        target_vault=explicit_target,
                        output=args.output,
                        replace=args.replace,
                    )
            except DerivationError as error:
                raise KnowledgeError(str(error)) from error
            print(pretty_json(result), end="")
            return 0

        repo_root = resolve_repo_root(
            explicit_repo_root=args.repo_root,
            explicit_vault=args.vault,
            home=args.kgdistiller_home,
            use_default=args.command != "init",
        )
        # Every repo-relative default lives under the knowledge tree; reject a
        # symlinked tree before any command reads or writes through it.
        knowledge_root(repo_root)
        if args.command == "obsidian":
            from .obsidian_plugin import ObsidianPluginError, install_obsidian_plugin

            try:
                result = install_obsidian_plugin(
                    repo_root,
                    replace=args.replace,
                    enable=args.enable,
                )
            except ObsidianPluginError as error:
                print(
                    pretty_json(
                        {
                            "kind": "kgdistiller-obsidian-plugin-error",
                            "code": "obsidian-plugin-install-failed",
                            "message": str(error),
                        }
                    ),
                    end="",
                    file=sys.stderr,
                )
                return 1
            print(pretty_json(result), end="")
            return 0
        if args.command == "export":
            from .obsidian_export import ObsidianExportError, export_obsidian_graph

            output = (
                Path(os.path.abspath(args.output))
                if args.output.is_absolute()
                else Path(os.path.abspath(repo_root / args.output))
            )
            try:
                result = export_obsidian_graph(
                    repo_root,
                    output,
                    registry=defaults(repo_root, args.registry),
                    graph_dir=defaults(repo_root, args.graph),
                    identities=defaults(repo_root, args.identities),
                )
            except ObsidianExportError as error:
                print(
                    pretty_json(
                        {
                            "kind": "kgdistiller-obsidian-export-error",
                            "code": "obsidian-export-failed",
                            "message": str(error),
                        }
                    ),
                    end="",
                    file=sys.stderr,
                )
                return 1
            print(pretty_json(result), end="")
            return 0
        registry = defaults(repo_root, args.registry)
        graph_dir = defaults(repo_root, args.graph)
        identities = defaults(repo_root, args.identities)
        alignments = defaults(repo_root, args.alignments)
        if args.command == "candidate":
            from .candidate import build_candidate_snapshot
            from .query import validate_agent_snapshot

            source_argument = (
                args.source
                if args.candidate_command == "build"
                else args.snapshot
            )
            source_path = (
                source_argument.resolve()
                if source_argument.is_absolute()
                else (repo_root / source_argument).resolve()
            )
            payload = read_json(source_path, {})
            if args.candidate_command == "build":
                result = build_candidate_snapshot(payload)
                output_argument = args.output
                content = pretty_json(result)
                if output_argument is None:
                    print(content, end="")
                else:
                    output = (
                        output_argument.resolve()
                        if output_argument.is_absolute()
                        else (repo_root / output_argument).resolve()
                    )
                    atomic_write(output, content)
                    print(
                        pretty_json(
                            {
                                "schema": result["schema"],
                                "namespace": result["namespace"],
                                "snapshot_sha256": result["snapshot_sha256"],
                                "counts": result["graph"]["counts"],
                                "output": str(output),
                            }
                        ),
                        end="",
                    )
            else:
                print(pretty_json(validate_agent_snapshot(payload)), end="")
            return 0
        if args.command == "harvest":
            from .harvest import apply_harvest, prepare_harvest
            from .ingest import IngestPaths

            paths = IngestPaths(
                repo_root=repo_root,
                registry=registry,
                graph_dir=graph_dir,
                identities=identities,
                alignments=alignments,
            )
            sheet_path = defaults(repo_root, args.sheet)
            output_path = defaults(repo_root, args.output)
            if args.harvest_command == "prepare":
                payload = read_json(defaults(repo_root, args.input), {})
                result = prepare_harvest(paths, payload, sheet_path, output_path)
            else:
                result = apply_harvest(paths, sheet_path, output_path)
            print(pretty_json(result), end="")
            return 0
        if args.command == "capture":
            from .capture import prepare_capture
            from .ingest import IngestPaths

            input_path = defaults(repo_root, args.input)
            output_path = defaults(repo_root, args.output)
            paths = IngestPaths(
                repo_root=repo_root,
                registry=registry,
                graph_dir=graph_dir,
                identities=identities,
                alignments=alignments,
            )
            result = prepare_capture(paths, read_json(input_path, {}), output_path)
            print(pretty_json(result), end="")
            return 0
        if args.command == "ingest":
            from .ingest import (
                IngestError,
                IngestPaths,
                apply_ingest,
                load_request,
                plan_ingest,
            )

            request_path = (
                args.request.resolve()
                if args.request.is_absolute()
                else (repo_root / args.request).resolve()
            )
            paths = IngestPaths(
                repo_root=repo_root,
                registry=registry,
                graph_dir=graph_dir,
                identities=identities,
                alignments=alignments,
            )
            fail_stage = os.environ.get("KGDISTILLER_INGEST_FAIL_STAGE", "")
            crash_stage = os.environ.get("KGDISTILLER_INGEST_CRASH_STAGE", "")

            def inject(stage: str) -> None:
                if crash_stage and stage == crash_stage:
                    os._exit(86)
                if fail_stage and stage == fail_stage:
                    raise IngestError(
                        "injected-failure",
                        f"failure injected at {stage}",
                        stage=stage,
                    )

            try:
                request = load_request(request_path, mode=args.ingest_command)
                if args.ingest_command == "plan":
                    result = plan_ingest(
                        paths,
                        request,
                        failure_injector=inject if fail_stage or crash_stage else None,
                    )
                    destination = args.output
                else:
                    result = apply_ingest(
                        paths,
                        request,
                        failure_injector=inject if fail_stage or crash_stage else None,
                    )
                    destination = args.receipt
                content = pretty_json(result)
                if destination is None:
                    print(content, end="")
                else:
                    output = (
                        destination.resolve()
                        if destination.is_absolute()
                        else (repo_root / destination).resolve()
                    )
                    atomic_write(output, content)
                    print(
                        pretty_json(
                            {
                                "schema": result["schema"],
                                "request_sha256": result["request_sha256"],
                                "status": result["status"],
                                "output": str(output),
                            }
                        ),
                        end="",
                    )
                return 0
            except IngestError as error:
                print(pretty_json(error.payload()), end="", file=sys.stderr)
                return 1
        if args.command == "store":
            from .store import snapshot_store, verify_store

            if args.store_command == "snapshot":
                _, artifacts, _ = synchronize(
                    repo_root,
                    registry,
                    graph_dir,
                    identities=identities,
                    alignments=alignments,
                    files=[],
                    write=False,
                )
                stale = [
                    name
                    for name, content in artifacts.items()
                    if not (graph_dir / name).is_file()
                    or (graph_dir / name).read_text(encoding="utf-8") != content
                ]
                if stale:
                    raise KnowledgeError(
                        f"stale graph artifacts: {', '.join(stale)}; run kgdistiller sync"
                    )
                output = args.output or repo_root
                output_root = (
                    output.resolve()
                    if output.is_absolute()
                    else (repo_root / output).resolve()
                )
                result = snapshot_store(
                    repo_root,
                    output_root,
                    registry=registry,
                    graph_dir=graph_dir,
                    identities=identities,
                    alignments=alignments,
                )
            else:
                result = verify_store(repo_root)
            print(pretty_json(result), end="")
            return 0
        if args.command == "init":
            from .project import initialize_project

            initialize_project(
                repo_root,
                registry,
                source_root=args.source_root,
                alignments=alignments,
                force=args.force,
            )
            _, _, report = synchronize(
                repo_root,
                registry,
                graph_dir,
                identities=identities,
                alignments=alignments,
                files=[],
                write=True,
            )
            print(pretty_json({"initialized": str(repo_root), **report}), end="")
            return 0
        if args.command in {"sync", "build", "scan"}:
            pairs_files = list(args.file)
            if args.command == "scan":
                from kgdistiller.document_types import load_document_types

                specs = load_sources(repo_root, registry)
                profiles = load_document_types(registry)
                state = load_state(graph_dir, repo_root=repo_root)
                pairs, selected, full = select_scope(repo_root, specs, pairs_files)
                pairs, selected = include_previous_authorities(
                    repo_root,
                    specs,
                    pairs,
                    selected,
                    state,
                    git_source_context(
                        repo_root,
                        str(state.manifest.get("git_revision", "")) or None,
                        specs,
                    ),
                    files=pairs_files,
                    full=full,
                )
                result = scan_scope(
                    repo_root,
                    pairs,
                    build_identity_index(state, load_identity_registry(identities)),
                )
                found = {item.id for item in result.definitions}
                orphaned = sorted(
                    node_id
                    for node_id, node in state.nodes.items()
                    if (node.get("provenance") or {}).get("active")
                    and (node.get("provenance") or {}).get("authority") in selected
                    and node_id not in found
                )
                print(
                    pretty_json(
                        {
                            "scope": "repository" if full else "incremental",
                            "files": [relative_path(repo_root, path) for _, path in pairs],
                            "sources": [
                                {"path": relative_path(repo_root, path), "source_id": spec.id,
                                 "source_format": source_format(path), "document_type": spec.document_type}
                                for spec, path in pairs
                            ],
                            "document_types": {
                                name: profiles[name] for name in sorted({
                                    spec.document_type for spec, _ in pairs if spec.document_type
                                })
                            },
                            "definitions": [item.__dict__ | {"statement": None} for item in result.definitions],
                            "references": [item.__dict__ for item in result.references],
                            "would_orphan": orphaned,
                            "errors": result.errors,
                        }
                    ),
                    end="",
                )
                return 1 if result.errors else 0
            _, _, report = synchronize(
                repo_root,
                registry,
                graph_dir,
                identities=identities,
                alignments=alignments,
                files=pairs_files,
                write=True,
            )
            print(pretty_json(report), end="")
            return 0
        if args.command == "apply":
            delta = args.delta if args.delta.is_absolute() else (repo_root / args.delta)
            print(
                pretty_json(
                    apply_delta(
                        graph_dir,
                        delta,
                        repo_root=repo_root,
                        registry=registry,
                    )
                ),
                end="",
            )
            return 0
        if args.command == "reconcile":
            state = load_state(graph_dir, repo_root=repo_root)
            if args.reconcile_command == "rename-node":
                print(
                    pretty_json(
                        reconcile_node_name(state, identities, args.id, args.new_name)
                    ),
                    end="",
                )
            else:
                candidate_path = (
                    args.candidate.resolve()
                    if args.candidate.is_absolute()
                    else (repo_root / args.candidate).resolve()
                )
                print(
                    pretty_json(
                        reconcile_alignment_mapping(
                            state,
                            graph_dir,
                            alignments,
                            read_json(candidate_path, {}),
                            args.candidate_id,
                            args.target_id,
                            predicate=args.predicate,
                            status=args.status,
                            justification=args.justification,
                            evidence=args.evidence,
                            target_namespace=args.target_namespace,
                            repo_root=repo_root,
                        )
                    ),
                    end="",
                )
            return 0
        if args.command == "check":
            _, artifacts, report = synchronize(
                repo_root,
                registry,
                graph_dir,
                identities=identities,
                alignments=alignments,
                files=[],
                write=False,
            )
            stale = [
                name
                for name, content in artifacts.items()
                if not (graph_dir / name).is_file()
                or (graph_dir / name).read_text(encoding="utf-8") != content
            ]
            if stale:
                raise KnowledgeError(f"stale graph artifacts: {', '.join(stale)}")
            print(f"OK: {GRAPH_SCHEMA}; {json_text(report['counts'])}; warnings={report['warnings']}")
            return 0
        if args.command == "mcp":
            from kgdistiller.mcp import serve_stdio
            from kgdistiller.retrieval import RetrievalError

            try:
                ranking_service = make_ranking_service(args, graph_dir=graph_dir, repo_root=repo_root)
            except RetrievalError as error:
                print(pretty_json(error.to_payload()), end="", file=sys.stderr)
                return 1
            serve_stdio(graph_dir, alignments=alignments, ranking_service=ranking_service, repo_root=repo_root)
            return 0
        if args.command == "agent":
            from kgdistiller.query import (
                PROPOSAL_SCHEMA,
                align,
                compare,
                expand,
                get,
                load_graph_view,
                personalized_pagerank,
                propose,
                query_status,
                resolve_concepts,
            )
            from kgdistiller.retrieval import (
                RetrievalError,
                build_context_from_execution,
                execute_retrieval_plan,
                legacy_retrieval_plan,
                load_retrieval_plan,
            )

            def current_authority_graph_sha256() -> str:
                authority_state = load_state(graph_dir, repo_root=repo_root)
                digest = str(authority_state.manifest.get("graph_sha256", ""))
                if not re.fullmatch(r"[0-9a-f]{64}", digest):
                    raise KnowledgeError("authority graph has no valid graph_sha256")
                return digest

            if args.agent_command == "evidence":
                from .source_evidence import SourceEvidenceError, SourceEvidenceIndex
                manifest_path = args.manifest if args.manifest.is_absolute() else repo_root / args.manifest
                try:
                    if not 1 <= args.budget <= 200000:
                        raise SourceEvidenceError("invalid-source-query", "source result byte budget must be between 1 and 200000")
                    index = SourceEvidenceIndex.from_manifest(manifest_path)
                    result = index.search(args.query, doc_ids=args.doc_ids, limit=args.limit, byte_budget=200000 if args.context_projection == "compact" else args.budget)
                    if args.context_projection == "compact":
                        from .source_context import build_source_context
                        result = build_source_context([result], byte_budget=args.budget)
                        index._check_sources()
                except SourceEvidenceError as error:
                    print(pretty_json(error.to_payload()), end="", file=sys.stderr)
                    return 1
            elif args.agent_command == "evidence-resolve":
                from .source_evidence import SourceEvidenceError, SourceEvidenceIndex
                from .source_references import resolve_source_references
                manifest_path = args.manifest if args.manifest.is_absolute() else repo_root / args.manifest
                try:
                    result = resolve_source_references(SourceEvidenceIndex.from_manifest(manifest_path), list(args.reference))
                except SourceEvidenceError as error:
                    print(pretty_json(error.to_payload()), end="", file=sys.stderr)
                    return 1
            elif args.agent_command == "status":
                result = query_status(load_graph_view(graph_dir, alignments, repo_root=repo_root))
            elif args.agent_command == "resolve":
                result = resolve_concepts(
                    load_graph_view(graph_dir, alignments, repo_root=repo_root),
                    list(args.concept),
                    alignments=alignments,
                    namespace=args.namespace,
                )
            elif args.agent_command == "search":
                try:
                    expected_graph_sha256 = current_authority_graph_sha256()
                    if args.plan is not None:
                        plan_path = (
                            args.plan.resolve()
                            if args.plan.is_absolute()
                            else (repo_root / args.plan).resolve()
                        )
                        plan = load_retrieval_plan(plan_path)
                        plan_mode = "planned"
                        execution_namespace_argument = None
                    else:
                        execution_namespace_argument = args.namespace or "personal"
                        plan = legacy_retrieval_plan(
                            str(args.query),
                            namespace=execution_namespace_argument,
                            limit=args.limit if args.limit is not None else 20,
                            max_depth=args.depth if args.depth is not None else 1,
                            include_stale=bool(args.include_stale),
                            include_orphaned=bool(args.include_orphaned),
                            graph_strategy=args.graph_strategy or "hybrid",
                        )
                        plan_mode = "legacy"
                    result = execute_retrieval_plan(
                        graph_dir,
                        plan,
                        alignments=alignments,
                        plan_mode=plan_mode,
                        namespace=execution_namespace_argument,
                        expected_graph_sha256=expected_graph_sha256,
                        ranking_service=make_ranking_service(args, graph_dir=graph_dir, repo_root=repo_root),
                        graph_policy=make_graph_retrieval_policy(args),
                        repo_root=repo_root,
                    )
                except RetrievalError as error:
                    print(pretty_json(error.to_payload()), end="", file=sys.stderr)
                    return 1
            elif args.agent_command == "get":
                result = get(
                    load_graph_view(graph_dir, alignments, repo_root=repo_root),
                    args.id,
                    alignments=alignments,
                    namespace=args.namespace,
                )
            elif args.agent_command == "expand":
                result = expand(
                    load_graph_view(graph_dir, alignments, repo_root=repo_root),
                    list(args.id),
                    alignments=alignments,
                    namespace=args.namespace,
                    direction=args.direction,
                    edge_types=args.edge_types,
                    max_depth=args.depth,
                    limit=args.limit,
                    include_stale=args.include_stale,
                    include_orphaned=args.include_orphaned,
                )
            elif args.agent_command == "ppr":
                result = personalized_pagerank(
                    load_graph_view(graph_dir, alignments, repo_root=repo_root),
                    {str(node_id): 1.0 for node_id in args.id},
                    alignments=alignments,
                    namespace=args.namespace,
                    edge_types=args.edge_types,
                    direction=args.direction,
                    limit=args.limit,
                    include_stale=args.include_stale,
                    include_orphaned=args.include_orphaned,
                )
            elif args.agent_command == "context":
                try:
                    expected_graph_sha256 = current_authority_graph_sha256()
                    if args.plan is not None:
                        plan_path = (
                            args.plan.resolve()
                            if args.plan.is_absolute()
                            else (repo_root / args.plan).resolve()
                        )
                        plan = load_retrieval_plan(plan_path)
                        plan_mode = "planned"
                        execution_namespace = str(plan["namespace"])
                        execution_namespace_argument = None
                    else:
                        execution_namespace = args.namespace or "personal"
                        plan = legacy_retrieval_plan(
                            str(args.query),
                            namespace=execution_namespace,
                            limit=args.limit if args.limit is not None else 50,
                            max_depth=args.depth if args.depth is not None else 1,
                            include_stale=bool(args.include_stale),
                            include_orphaned=bool(args.include_orphaned),
                            graph_strategy=args.graph_strategy or "hybrid",
                        )
                        plan_mode = "legacy"
                        execution_namespace_argument = execution_namespace
                    execution = execute_retrieval_plan(
                        graph_dir,
                        plan,
                        alignments=alignments,
                        plan_mode=plan_mode,
                        namespace=execution_namespace_argument,
                        expected_graph_sha256=expected_graph_sha256,
                        ranking_service=make_ranking_service(args, graph_dir=graph_dir, repo_root=repo_root),
                        graph_policy=make_graph_retrieval_policy(args),
                        repo_root=repo_root,
                    )
                    result = build_context_from_execution(
                        graph_dir,
                        execution,
                        alignments=alignments,
                        plan=plan,
                        token_budget=args.budget,
                        namespace=execution_namespace,
                        context_projection=args.context_projection,
                        repo_root=repo_root,
                        support_selection=load_support_selection(args.support_selection, repo_root) if args.support_selection else None,
                    )
                except RetrievalError as error:
                    print(pretty_json(error.to_payload()), end="", file=sys.stderr)
                    return 1
            elif args.agent_command == "align":
                candidate_path = (
                    args.candidate.resolve()
                    if args.candidate.is_absolute()
                    else (repo_root / args.candidate).resolve()
                )
                alignment_report = align(
                    load_graph_view(graph_dir, alignments, repo_root=repo_root),
                    read_json(candidate_path, {}),
                    alignments=alignments,
                    target_namespace=args.target_namespace,
                    limit_per_node=args.limit,
                )
                if args.output:
                    output = (
                        args.output.resolve()
                        if args.output.is_absolute()
                        else (repo_root / args.output).resolve()
                    )
                    output.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write(output, pretty_json(alignment_report))
                    result = {
                        "schema": alignment_report["schema"],
                        "report_sha256": alignment_report["report_sha256"],
                        "summary": alignment_report["summary"],
                        "proposals": len(alignment_report["proposals"]),
                        "output": str(output),
                    }
                else:
                    result = alignment_report
            elif args.agent_command == "compare":
                candidate_path = (
                    args.candidate.resolve()
                    if args.candidate.is_absolute()
                    else (repo_root / args.candidate).resolve()
                )
                result = compare(
                    load_graph_view(graph_dir, alignments, repo_root=repo_root),
                    read_json(candidate_path, {}),
                    alignments=alignments,
                    target_namespace=args.target_namespace,
                )
            else:
                candidate_path = (
                    args.candidate.resolve()
                    if args.candidate.is_absolute()
                    else (repo_root / args.candidate).resolve()
                )
                proposal = propose(
                    load_graph_view(graph_dir, alignments, repo_root=repo_root),
                    read_json(candidate_path, {}),
                    alignments=alignments,
                    target_namespace=args.target_namespace,
                    target_authority=args.target_authority,
                )
                written: dict[str, str] = {}
                if args.output:
                    output = (
                        args.output.resolve()
                        if args.output.is_absolute()
                        else (repo_root / args.output).resolve()
                    )
                    output.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write(output, pretty_json(proposal))
                    written["proposal"] = str(output)
                if args.delta_output:
                    delta_output = (
                        args.delta_output.resolve()
                        if args.delta_output.is_absolute()
                        else (repo_root / args.delta_output).resolve()
                    )
                    delta_output.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write(delta_output, pretty_json(proposal["delta_preview"]))
                    written["delta"] = str(delta_output)
                result = (
                    {
                        "schema": PROPOSAL_SCHEMA,
                        "proposal_sha256": proposal["proposal_sha256"],
                        "comparison_summary": proposal["comparison_summary"],
                        "delta_ready": proposal["delta_ready"],
                        "fully_resolved": proposal["fully_resolved"],
                        "written": written,
                    }
                    if written
                    else proposal
                )
            print(pretty_json(result), end="")
            return 0
        state = load_state(graph_dir, repo_root=repo_root)
        if args.command == "search":
            print(pretty_json(search_graph(state, args.query, args.limit)), end="")
        elif args.command == "show":
            print(pretty_json(show_node(state, args.id)), end="")
        elif args.command == "snapshot":
            snapshot = make_agent_snapshot(state, args.namespace)
            content = pretty_json(snapshot)
            if args.output is None:
                print(content, end="")
            else:
                output = (
                    args.output.resolve()
                    if args.output.is_absolute()
                    else (repo_root / args.output).resolve()
                )
                output.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(output, content)
                print(
                    pretty_json(
                        {
                            "schema": AGENT_SNAPSHOT_SCHEMA,
                            "namespace": args.namespace,
                            "snapshot_sha256": snapshot["snapshot_sha256"],
                            "counts": snapshot["graph"]["counts"],
                            "output": str(output),
                        }
                    ),
                    end="",
                )
        elif args.command == "curate-check":
            specs = load_sources(repo_root, registry)
            pairs, authorities, _ = select_scope(repo_root, specs, list(args.file))
            missing = [
                relative_path(repo_root, path)
                for _, path in pairs
                if not path.is_file()
            ]
            if missing:
                raise KnowledgeError(f"curation source does not exist: {', '.join(missing)}")
            report = curation_report(state, authorities)
            print(pretty_json(report), end="")
            return 1 if report["errors"] else 0
        elif args.command == "audit":
            print(pretty_json(audit_report(state)), end="")
        elif args.command == "stats":
            print(pretty_json(state.manifest), end="")
        return 0
    except (KnowledgeError, OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        print(f"knowledge command failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
