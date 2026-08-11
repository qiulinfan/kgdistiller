#!/usr/bin/env python3
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
import sqlite3
import subprocess
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

try:
    from .profile import ProfileError, resolve_runtime_config
except ImportError:  # Direct execution of this file during compatibility tests.
    from kgdistiller.profile import ProfileError, resolve_runtime_config


GRAPH_SCHEMA = "qlkg-v2"
SOURCE_SCHEMA = "qlkg-sources-v2"
DELTA_SCHEMA = "qlkg-agent-delta-v2"
IDENTITY_SCHEMA = "qlkg-identities-v1"
ENTRY_STORE_SCHEMA = "qlkg-entry-shards-v1"
AGENT_SNAPSHOT_SCHEMA = "qlkg-agent-snapshot-v1"
ENTRY_SHARD_LIMIT = 48 * 1024 * 1024
KNOWLEDGE_ORIGINS = {"personal-note", "research"}
ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
NAMESPACE_RE = re.compile(
    r"^[a-z0-9][a-z0-9._-]*(?::[a-z0-9][a-z0-9._-]*)*$"
)
KN_RE = re.compile(r"#kn\s*\[")
REF_RE = re.compile(r"#ref\s*\[")
LATEX_KN_RE = re.compile(r"\\kn\s*\{")
LATEX_REF_RE = re.compile(r"\\knref\s*\{")
MARKDOWN_WIKILINK_RE = re.compile(
    r"(?P<definition>(?<![!\\])--\[\[(?P<definition_body>[^\]\n]+)\]\]--)"
    r"|(?P<reference>(?<![!\-\\])\[\[(?P<reference_body>[^\]\n]+)\]\](?!--))"
)
LATEX_STATEMENT_RE = re.compile(
    r"\\begin\{(?P<kind>definition|theorem|lemma|corollary|proposition|axiom|example)\}"
)
LABEL_HTML_RE = re.compile(
    r'<ql-label data-node-id="(?P<id>[a-z0-9-]+)">(?P<html>.*?)</ql-label>',
    re.DOTALL,
)
UNSAFE_LABEL_HTML_RE = re.compile(
    r"<(?:script|style|iframe|object|embed|link|meta|img|svg|form|input|button|a)\b"
    r"|\son[a-z]+\s*=|javascript:",
    re.IGNORECASE,
)
STATEMENT_RE = re.compile(
    r"#(?P<kind>definition|theorem|lemma|corollary|proposition|axiom|example)\s*\("
)
SEMANTIC_RELATIONS = {
    "contains",
    "prerequisite-for",
    "implies",
    "generalizes",
    "contrasts-with",
    "derived-from",
}
ACYCLIC_RELATIONS = {"contains", "prerequisite-for"}
CURATION_STATUSES = {"current", "pending", "needs-review"}
CROSS_FILE_REF_ENDPOINTS = {
    "prerequisite-for": ("target", "source"),
    "generalizes": ("source", "target"),
    "derived-from": ("source", "target"),
}


class KnowledgeError(RuntimeError):
    """Raised when the graph contract cannot be satisfied."""


@dataclass(frozen=True)
class FieldSpec:
    id: str
    label: str
    text: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class SourceSpec:
    id: str
    subject: str
    course: str
    root: Path
    patterns: tuple[str, ...]
    web: str
    knowledge_origin: str
    fields: tuple[str, ...]
    topic_patterns: tuple[tuple[str, str, str, tuple[str, ...]], ...]


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
    anchor: str
    web: str
    source_id: str
    subject: str
    course: str
    knowledge_origin: str
    topic: str | None
    fields: tuple[str, ...]
    position: int
    statement: StatementRange | None
    definition_sha256: str
    definition_start_line: int
    definition_end_line: int


@dataclass(frozen=True)
class ReferenceOccurrence:
    id: str
    target: str
    label: str
    authority: str
    line: int
    web: str
    context: str | None
    source_format: str
    source_name: str
    display_markup: str


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
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    return sha256_file(path) if path is not None and path.is_file() else None


def load_fields(registry: Path) -> list[FieldSpec]:
    payload = read_json(registry, {})
    if payload.get("schema") != SOURCE_SCHEMA:
        raise KnowledgeError(f"expected {SOURCE_SCHEMA} source registry: {registry}")
    result: list[FieldSpec] = []
    seen: set[str] = set()
    for raw in payload.get("fields", []):
        field_id = str(raw.get("id", ""))
        if not ID_RE.fullmatch(field_id) or field_id in seen:
            raise KnowledgeError(f"duplicate or invalid field id: {field_id!r}")
        seen.add(field_id)
        label = str(raw.get("label", "")).strip()
        if not label:
            raise KnowledgeError(f"field {field_id} has no label")
        result.append(
            FieldSpec(
                id=field_id,
                label=label,
                text=str(raw.get("text", "")).strip(),
                aliases=tuple(str(item) for item in raw.get("aliases", [])),
            )
        )
    return result


def load_sources(repo_root: Path, registry: Path) -> list[SourceSpec]:
    payload = read_json(registry, {})
    if payload.get("schema") != SOURCE_SCHEMA:
        raise KnowledgeError(f"expected {SOURCE_SCHEMA} source registry: {registry}")
    result: list[SourceSpec] = []
    seen: set[str] = set()
    for raw in payload.get("sources", []):
        source_id = str(raw.get("id", ""))
        if not source_id or source_id in seen:
            raise KnowledgeError(f"duplicate or empty source id: {source_id!r}")
        seen.add(source_id)
        root = (repo_root / str(raw.get("root", ""))).resolve()
        if not root.is_dir():
            raise KnowledgeError(f"missing source root for {source_id}: {root}")
        patterns = tuple(str(item) for item in raw.get("files", []))
        if not patterns:
            raise KnowledgeError(f"source {source_id} has no bounded file patterns")
        source_fields = tuple(dict.fromkeys(str(item) for item in raw.get("fields", [])))
        topics = tuple(
            (
                str(item["glob"]),
                str(item["id"]),
                str(item["label"]),
                tuple(dict.fromkeys(str(field) for field in item.get("fields", []))),
            )
            for item in raw.get("topics", [])
        )
        knowledge_origin = str(raw.get("knowledge_origin", "personal-note"))
        if knowledge_origin not in KNOWLEDGE_ORIGINS:
            raise KnowledgeError(
                f"source {source_id} has invalid knowledge_origin: {knowledge_origin!r}"
            )
        result.append(
            SourceSpec(
                id=source_id,
                subject=str(raw.get("subject", "")),
                course=str(raw.get("course", "")),
                root=root,
                patterns=patterns,
                web=str(raw.get("web", "")).rstrip("/"),
                knowledge_origin=knowledge_origin,
                fields=source_fields,
                topic_patterns=topics,
            )
        )
    return result


def expand_source(spec: SourceSpec) -> list[Path]:
    files: set[Path] = set()
    for pattern in spec.patterns:
        files.update(path.resolve() for path in spec.root.glob(pattern) if path.is_file())
    return sorted(files, key=lambda item: item.as_posix())


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


def source_matches_path(spec: SourceSpec, path: Path) -> bool:
    """Return whether a file path is admitted by a source's bounded patterns."""
    try:
        relative = path.resolve().relative_to(spec.root)
    except ValueError:
        return False
    return any(glob_matches_path(relative, pattern) for pattern in spec.patterns)


def matching_sources(specs: list[SourceSpec], path: Path) -> list[SourceSpec]:
    return [spec for spec in specs if source_matches_path(spec, path)]


def unique_source_for_path(specs: list[SourceSpec], path: Path) -> SourceSpec:
    owners = matching_sources(specs, path)
    if len(owners) == 1:
        return owners[0]
    if len(owners) > 1:
        raise KnowledgeError(
            f"source file matches multiple registry sources: {path} "
            f"({', '.join(sorted(spec.id for spec in owners))})"
        )
    roots = [spec.id for spec in specs if path == spec.root or spec.root in path.parents]
    if roots:
        raise KnowledgeError(
            f"file is inside a configured source root but is not admitted by its "
            f"file patterns: {path} ({', '.join(sorted(roots))})"
        )
    raise KnowledgeError(f"file is outside configured source roots: {path}")


def topic_for(spec: SourceSpec, path: Path) -> tuple[str, str, tuple[str, ...]] | None:
    relative = path.resolve().relative_to(spec.root).as_posix()
    for pattern, topic_id, label, topic_fields in spec.topic_patterns:
        if path.match(str(spec.root / pattern)) or Path(relative).match(pattern):
            return topic_id, label, tuple(dict.fromkeys((*spec.fields, *topic_fields)))
    return None


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


def markdown_web_path(spec: SourceSpec, path: Path) -> str:
    """Map one Markdown authority to its static note route."""
    relative = path.resolve().relative_to(spec.root).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1].casefold() in {"index", "readme"}:
        parts.pop()
    suffix = "/".join(quote(part, safe="-._~") for part in parts)
    return f"{spec.web}/{suffix}".rstrip("/") if suffix else spec.web


def definition_web(spec: SourceSpec, path: Path, node_id: str) -> str:
    base = markdown_web_path(spec, path) if path.suffix.lower() == ".md" else spec.web
    if not base:
        return f"/knowledge/#node={node_id}"
    return f"{base}/#kn-{node_id}"


def find_matching(text: str, start: int, opening: str, closing: str) -> int:
    if start >= len(text) or text[start] != opening:
        raise KnowledgeError(f"expected {opening!r} at offset {start}")
    depth = 0
    content_depth = 0
    quote = False
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
    result: list[StatementRange] = []
    for match in LATEX_STATEMENT_RE.finditer(text):
        closing = re.compile(rf"\\end\{{{re.escape(match.group('kind'))}\}}")
        end_match = closing.search(text, match.end())
        end = end_match.end() if end_match else len(text)
        result.append(StatementRange(match.start(), end, match.group("kind")))
    return result


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
    topic = topic_for(spec, path)
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
        anchor = f"kn-{node_id}"
        definitions.append(
            DefinitionOccurrence(
                id=node_id,
                label=label,
                label_markup=label_typst,
                source_format="typst",
                kind=statement.kind if statement else "concept",
                authority=authority,
                line=line,
                anchor=anchor,
                web=definition_web(spec, path, node_id),
                source_id=spec.id,
                subject=spec.subject,
                course=spec.course,
                knowledge_origin=spec.knowledge_origin,
                topic=topic[0] if topic else None,
                fields=topic[2] if topic else spec.fields,
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
                web=spec.web,
                context=context,
                source_format="typst",
                source_name=text[match.end() : close],
                display_markup=text[match.end() : close],
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
    topic = topic_for(spec, path)
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
                anchor=f"kn-{node_id}",
                web=definition_web(spec, path, node_id),
                source_id=spec.id,
                subject=spec.subject,
                course=spec.course,
                knowledge_origin=spec.knowledge_origin,
                topic=topic[0] if topic else None,
                fields=topic[2] if topic else spec.fields,
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
                web=markdown_web_path(spec, path),
                context=context,
                source_format="markdown",
                source_name=target_markup,
                display_markup=wikilink_parts(body)[1],
            )
        )
    return ScanResult(definitions, references, errors)


def scan_latex(
    repo_root: Path,
    spec: SourceSpec,
    path: Path,
    identities: dict[str, str],
) -> ScanResult:
    authority = relative_path(repo_root, path)
    text = path.read_text(encoding="utf-8")
    ranges = latex_statement_ranges(text)
    topic = topic_for(spec, path)
    definitions: list[DefinitionOccurrence] = []
    references: list[ReferenceOccurrence] = []
    errors: list[dict[str, Any]] = []

    for match in LATEX_KN_RE.finditer(text):
        try:
            close = find_matching(text, match.end() - 1, "{", "}")
        except KnowledgeError as error:
            errors.append(diagnostic("latex-parse", str(error), source=authority))
            continue
        label_markup = text[match.end() : close]
        label = strip_latex(label_markup)
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
                label_markup=label_markup,
                source_format="latex",
                kind=statement.kind if statement else "concept",
                authority=authority,
                line=line,
                anchor=f"kn-{node_id}",
                web=definition_web(spec, path, node_id),
                source_id=spec.id,
                subject=spec.subject,
                course=spec.course,
                knowledge_origin=spec.knowledge_origin,
                topic=topic[0] if topic else None,
                fields=topic[2] if topic else spec.fields,
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
    for match in LATEX_REF_RE.finditer(text):
        try:
            close = find_matching(text, match.end() - 1, "{", "}")
        except KnowledgeError as error:
            errors.append(diagnostic("latex-parse", str(error), source=authority))
            continue
        label = strip_latex(text[match.end() : close])
        if not label:
            errors.append(
                diagnostic(
                    "empty-reference-name",
                    r"\knref{...} must contain a non-empty semantic name",
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
                web=spec.web,
                context=context,
                source_format="latex",
                source_name=text[match.end() : close],
                display_markup=text[match.end() : close],
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


def load_state(graph_dir: Path) -> GraphState:
    manifest = read_json(graph_dir / "manifest.json", {})
    if manifest.get("schema") != GRAPH_SCHEMA:
        return GraphState({}, {}, [], {})
    nodes = {item["id"]: item for item in read_jsonl(graph_dir / "nodes.jsonl")}
    entry_store = manifest.get("entry_store") or {}
    if entry_store:
        if entry_store.get("schema") != ENTRY_STORE_SCHEMA:
            raise KnowledgeError("unsupported knowledge entry store schema")
        entries: dict[str, dict[str, Any]] = {}
        for shard in entry_store.get("shards", []):
            relative = Path(str(shard.get("path", "")))
            if relative.is_absolute() or ".." in relative.parts:
                raise KnowledgeError(f"unsafe knowledge entry shard path: {relative}")
            path = graph_dir / relative
            if not path.is_file():
                raise KnowledgeError(f"missing knowledge entry shard: {relative}")
            content = path.read_text(encoding="utf-8")
            size = len(content.encode("utf-8"))
            if size > ENTRY_SHARD_LIMIT:
                raise KnowledgeError(f"knowledge entry shard exceeds 48 MiB: {relative}")
            if shard.get("bytes") is not None and int(shard["bytes"]) != size:
                raise KnowledgeError(f"knowledge entry shard size mismatch: {relative}")
            if shard.get("sha256") and sha256_text(content) != shard["sha256"]:
                raise KnowledgeError(f"stale knowledge entry shard: {relative}")
            records = [json.loads(line) for line in content.splitlines() if line]
            if int(shard.get("count", len(records))) != len(records):
                raise KnowledgeError(f"knowledge entry shard count mismatch: {relative}")
            for record in records:
                node_id = str(record.get("id", ""))
                if not node_id or node_id in entries:
                    raise KnowledgeError(f"duplicate or empty sharded entry id: {node_id!r}")
                entries[node_id] = record
        for node_id, record in entries.items():
            node = nodes.get(node_id)
            if node is None:
                raise KnowledgeError(f"entry shard references unknown node: {node_id}")
            node["text"] = str(record.get("text", ""))
            if isinstance(record.get("entry"), dict) and record["entry"]:
                node["entry"] = record["entry"]
    # In-memory source nodes always carry a text field, including pending
    # authorities whose entry is still empty. The committed JSONL projection
    # omits that empty value. Rehydrate it here so snapshots built directly
    # during sync and snapshots rebuilt from committed artifacts are identical;
    # otherwise the next nominally read-only Agent query rebuilds SQLite.
    for node in nodes.values():
        node.setdefault("text", "")
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
    course: str | None,
    subject: str | None,
) -> tuple[list[tuple[SourceSpec, Path]], set[str], bool]:
    full = not files and not course and not subject
    selected_specs = [
        spec
        for spec in specs
        if (course is None or spec.course == course)
        and (subject is None or spec.subject == subject)
    ]
    if (course or subject) and not selected_specs:
        raise KnowledgeError(f"no source matched course={course!r} subject={subject!r}")
    if full:
        selected_specs = specs
    pairs: list[tuple[SourceSpec, Path]] = []
    if files:
        for raw in files:
            path = (repo_root / raw).resolve() if not raw.is_absolute() else raw.resolve()
            if path.is_file():
                pairs.append((unique_source_for_path(specs, path), path))
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
                pairs.append((unique_source_for_path(specs, path), path))
    else:
        for spec in selected_specs:
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


def git_source_context(
    repo_root: Path,
    base_revision: str | None,
    specs: list[SourceSpec],
) -> dict[str, Any]:
    """Describe source changes relative to the last synchronized Git revision."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return {"head": None, "dirty": False, "changes": []}

    roots = [relative_path(repo_root, spec.root) for spec in specs]
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all", "--", *roots],
        cwd=repo_root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    ).stdout
    changes: list[dict[str, str]] = []
    if base_revision:
        try:
            raw = subprocess.run(
                ["git", "diff", "--name-status", "-z", "-M", base_revision, "--", *roots],
                cwd=repo_root,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            ).stdout.decode("utf-8", errors="strict")
            parts = raw.split("\0")
            cursor = 0
            while cursor < len(parts) and parts[cursor]:
                code = parts[cursor]
                cursor += 1
                if code.startswith(("R", "C")):
                    old_path, new_path = parts[cursor], parts[cursor + 1]
                    cursor += 2
                else:
                    old_path = parts[cursor]
                    new_path = old_path
                    cursor += 1
                changes.append(
                    {"status": code, "old_path": old_path, "new_path": new_path}
                )
        except (OSError, subprocess.CalledProcessError, UnicodeError):
            changes = []
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
    course: str | None,
    subject: str | None,
    full: bool,
) -> tuple[list[tuple[SourceSpec, Path]], set[str]]:
    """Include deleted/renamed old paths so their occurrences can be retired."""
    unique = {relative_path(repo_root, path): (spec, path) for spec, path in pairs}
    previous_paths = set((previous.manifest.get("source_hashes") or {}).keys())
    selected_specs = {
        spec.id
        for spec in specs
        if (course is None or spec.course == course)
        and (subject is None or spec.subject == subject)
    }
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
        if full or ((course or subject) and owner.id in selected_specs) or requested_path(authority):
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
            current_hash = sha256_file(path)
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
    properties = dict(previous.get("properties") or {})
    aliases = list(dict.fromkeys(str(item) for item in properties.get("aliases", [])))
    additional_fields = list(
        dict.fromkeys(str(item) for item in properties.get("additional_fields", []))
    )
    old_label = str(previous.get("label", ""))
    if old_label and old_label != definition.label and old_label not in aliases:
        aliases.append(old_label)
    properties.update(
        {
            "kind": definition.kind,
            "aliases": aliases,
            "origin": "authored",
            "source_status": "active",
            "subject": definition.subject,
            "course": definition.course,
            "fields": list(dict.fromkeys((*definition.fields, *additional_fields))),
            "additional_fields": additional_fields,
            "knowledge_origin": definition.knowledge_origin,
            "source_format": definition.source_format,
            "source_name": definition.label_markup,
        }
    )
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
    if definition.source_format == "typst":
        properties["typst_name"] = definition.label_markup
    else:
        properties.pop("typst_name", None)
        properties.pop("label_html", None)
    if definition.topic:
        properties["topic"] = definition.topic
    properties.pop("orphaned_from", None)
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
            "anchor": definition.anchor,
            "web": definition.web,
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
        "display_markup": item.display_markup,
    }
    if item.web:
        value["web"] = item.web
    if item.context:
        value["context"] = item.context
    return value


def ensure_taxonomy_nodes_and_edges(
    state: GraphState,
    fields: list[FieldSpec],
    specs: list[SourceSpec],
    definitions: list[DefinitionOccurrence],
    selected_knowledge_ids: set[str],
    *,
    prune: bool,
) -> None:
    field_index = {field.id: field for field in fields}
    referenced_fields = {
        field_id
        for spec in specs
        for field_id in (
            *spec.fields,
            *(
                field_id
                for _, _, _, topic_fields in spec.topic_patterns
                for field_id in topic_fields
            ),
        )
    }
    missing_fields = sorted(referenced_fields - set(field_index))
    if missing_fields:
        raise KnowledgeError(
            f"source registry references undefined fields: {', '.join(missing_fields)}"
        )

    for field in fields:
        previous = copy.deepcopy(state.nodes.get(field.id) or {})
        properties = dict(previous.get("properties") or {})
        properties.update(
            {
                "kind": "field",
                "aliases": list(field.aliases),
                "origin": "registry-taxonomy",
                "source_status": "meta",
            }
        )
        for source_key in ("subject", "course", "fields", "knowledge_origin"):
            properties.pop(source_key, None)
        state.nodes[field.id] = {
            "id": field.id,
            "type": "field",
            "label": field.label,
            "text": field.text or str(previous.get("text", "")),
            "properties": properties,
        }

    configured_topics: dict[str, tuple[str, tuple[str, ...], SourceSpec]] = {}
    for spec in specs:
        for _, topic_id, label, topic_fields in spec.topic_patterns:
            effective_fields = tuple(dict.fromkeys((*spec.fields, *topic_fields)))
            previous_topic = configured_topics.get(topic_id)
            if previous_topic and previous_topic[:2] != (label, effective_fields):
                raise KnowledgeError(f"conflicting configured topic: {topic_id}")
            configured_topics[topic_id] = (label, effective_fields, spec)

    taxonomy_collisions = sorted(set(field_index) & set(configured_topics))
    if taxonomy_collisions:
        raise KnowledgeError(
            f"field and topic ids must be distinct: {', '.join(taxonomy_collisions)}"
        )
    knowledge_collisions = sorted(
        {definition.id for definition in definitions}
        & (set(field_index) | set(configured_topics))
    )
    if knowledge_collisions:
        raise KnowledgeError(
            f"knowledge ids collide with configured taxonomy: {', '.join(knowledge_collisions)}"
        )
    if prune:
        configured_ids = set(field_index) | set(configured_topics)
        stale_ids = {
            node_id
            for node_id, node in state.nodes.items()
            if node_id not in configured_ids
            and node.get("type") in {"field", "topic"}
            and (node.get("properties") or {}).get("origin") == "registry-taxonomy"
        }
        for node_id in stale_ids:
            state.nodes.pop(node_id, None)
        if stale_ids:
            state.edges = {
                key: edge
                for key, edge in state.edges.items()
                if edge.get("source") not in stale_ids and edge.get("target") not in stale_ids
            }

    for topic_id, (label, topic_fields, spec) in configured_topics.items():
        previous = copy.deepcopy(state.nodes.get(topic_id) or {})
        properties = dict(previous.get("properties") or {})
        properties.update(
            {
                "kind": "topic",
                "aliases": list(properties.get("aliases", [])),
                "origin": "registry-taxonomy",
                "source_status": "meta",
                "subject": spec.subject,
                "course": spec.course,
                "fields": list(topic_fields),
            }
        )
        state.nodes[topic_id] = {
            "id": topic_id,
            "type": "topic",
            "label": label,
            "text": str(previous.get("text", "")),
            "properties": properties,
        }

    configured_topic_ids = set(configured_topics)
    for key, edge in list(state.edges.items()):
        if edge.get("relation") != "contains":
            continue
        source_type = (state.nodes.get(str(edge.get("source"))) or {}).get("type")
        target = str(edge.get("target", ""))
        if (
            (target in configured_topic_ids and source_type == "field")
            or (target in selected_knowledge_ids and source_type in {"field", "topic"})
        ):
            state.edges.pop(key)

    for topic_id, (_, topic_fields, _) in configured_topics.items():
        for field_id in topic_fields:
            edge = {
                "source": field_id,
                "relation": "contains",
                "target": topic_id,
                "origin": "registry-taxonomy",
                "confidence": "high",
                "evidence": f"configured topic {topic_id} is classified in field {field_id}",
            }
            state.edges[edge_key(edge)] = edge

    for definition in definitions:
        node_properties = (state.nodes.get(definition.id) or {}).get("properties") or {}
        additional_fields = tuple(
            dict.fromkeys(str(item) for item in node_properties.get("additional_fields", []))
        )
        unknown_additional = sorted(set(additional_fields) - set(field_index))
        if unknown_additional:
            raise KnowledgeError(
                f"knowledge node {definition.id} references undefined additional fields: "
                + ", ".join(unknown_additional)
            )
        parents = (
            tuple(dict.fromkeys((definition.topic, *additional_fields)))
            if definition.topic
            else tuple(dict.fromkeys((*definition.fields, *additional_fields)))
        )
        for parent in parents:
            edge = {
                "source": parent,
                "relation": "contains",
                "target": definition.id,
                "origin": "registry-taxonomy",
                "confidence": "high",
                "evidence": f"canonical definition is authored in {definition.authority}",
            }
            state.edges[edge_key(edge)] = edge


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
        if edge.get("relation") == "contains":
            continue
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


def knowledge_field_memberships(state: GraphState) -> dict[str, set[str]]:
    field_ids = {
        node_id for node_id, node in state.nodes.items() if node.get("type") == "field"
    }
    topic_fields: dict[str, set[str]] = defaultdict(set)
    memberships: dict[str, set[str]] = defaultdict(set)
    for edge in state.edges.values():
        if edge.get("relation") != "contains":
            continue
        source = str(edge.get("source", ""))
        target = str(edge.get("target", ""))
        target_type = (state.nodes.get(target) or {}).get("type")
        if source in field_ids and target_type == "topic":
            topic_fields[target].add(source)
        elif source in field_ids and target_type == "knowledge":
            memberships[target].add(source)
    for edge in state.edges.values():
        if edge.get("relation") != "contains":
            continue
        source = str(edge.get("source", ""))
        target = str(edge.get("target", ""))
        if (state.nodes.get(source) or {}).get("type") == "topic":
            memberships[target].update(topic_fields.get(source, set()))
    return memberships


def validate_state(state: GraphState) -> dict[str, list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    node_ids = set(state.nodes)
    allowed_node_types = {"field", "topic", "knowledge"}
    for node in state.nodes.values():
        if node.get("type") not in allowed_node_types:
            errors.append(
                diagnostic(
                    "unknown-node-type",
                    f"unsupported node type in field-facet graph: {node.get('type')}",
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
        if edge.get("relation") == "contains":
            source_type = (state.nodes.get(str(edge.get("source"))) or {}).get("type")
            target_type = (state.nodes.get(str(edge.get("target"))) or {}).get("type")
            if (source_type, target_type) not in {
                ("field", "topic"),
                ("field", "knowledge"),
                ("topic", "knowledge"),
            }:
                errors.append(
                    diagnostic(
                        "invalid-taxonomy-edge",
                        f"contains must be field -> topic/knowledge or topic -> knowledge, got {source_type} -> {target_type}",
                        node=str(edge.get("target", "")),
                    )
                )
        if edge.get("relation") != "contains" and edge.get("curation_status") == "needs-review":
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
    field_memberships = knowledge_field_memberships(state)
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
        if node.get("type") == "knowledge" and properties.get("typst_name") and not properties.get("label_html"):
            errors.append(
                diagnostic(
                    "missing-label-html",
                    "Typst-authored knowledge node has no math-aware HTML label",
                    node=node["id"],
                )
            )
        if (
            node.get("type") == "knowledge"
            and (node.get("provenance") or {}).get("active")
            and not field_memberships.get(str(node.get("id", "")))
        ):
            errors.append(
                diagnostic(
                    "unclassified-knowledge",
                    "active knowledge node has no field membership",
                    source=(node.get("provenance") or {}).get("authority"),
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
    """Create the deterministic, self-contained Agent index input contract."""
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
    computed_artifacts = make_artifacts(
        copy.deepcopy(state),
        dict(state.manifest.get("source_hashes") or {}),
        identity_sha256=str(state.manifest.get("identity_sha256", "")) or None,
        git_revision=str(state.manifest.get("git_revision", "")) or None,
    )
    computed_manifest = json.loads(computed_artifacts["manifest.json"])
    if computed_manifest.get("graph_sha256") != graph_sha256:
        raise KnowledgeError(
            "authority graph digest does not match its hydrated graph content"
        )

    snapshot: dict[str, Any] = {
        "schema": AGENT_SNAPSHOT_SCHEMA,
        "namespace": namespace,
        "graph": {
            "schema": GRAPH_SCHEMA,
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


def make_artifacts(
    state: GraphState,
    source_hashes: dict[str, str],
    *,
    identity_sha256: str | None = None,
    git_revision: str | None = None,
) -> dict[str, str]:
    refresh_node_curation_defaults(state)
    nodes = sorted(state.nodes.values(), key=lambda item: item["id"])
    edges = sorted(
        state.edges.values(),
        key=lambda item: (item["source"], item["relation"], item["target"]),
    )
    references = sorted(
        state.references,
        key=lambda item: (item.get("authority", ""), item.get("line", 0), item["target"]),
    )
    diagnostics = validate_state(state)
    entry_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    serialized_nodes: list[dict[str, Any]] = []
    for node in nodes:
        serialized = copy.deepcopy(node)
        text_value = str(serialized.pop("text", ""))
        structured_entry = serialized.pop("entry", None)
        properties = dict(serialized.get("properties") or {})
        if text_value or structured_entry:
            authority = str((node.get("provenance") or {}).get("authority", ""))
            if authority:
                authority_path = Path(authority)
                shard_path = (
                    Path("entries/by-source")
                    / authority_path.parent
                    / f"{authority_path.name}.jsonl"
                ).as_posix()
            else:
                subject = generated_id(str(properties.get("subject") or "global"))
                course = generated_id(str(properties.get("course") or "unscoped"))
                shard_path = f"entries/meta/{subject}/{course}.jsonl"
            record: dict[str, Any] = {"id": node["id"], "text": text_value}
            if isinstance(structured_entry, dict) and structured_entry:
                record["entry"] = structured_entry
            entry_groups[shard_path].append(record)
            properties["entry_path"] = shard_path
        else:
            properties.pop("entry_path", None)
        serialized["properties"] = properties
        serialized_nodes.append(serialized)
    nodes_text = jsonl(serialized_nodes)
    edges_text = jsonl(edges)
    references_text = jsonl(references)
    diagnostics_text = pretty_json(diagnostics)
    entry_artifacts: dict[str, str] = {}
    entry_shards: list[dict[str, Any]] = []
    for path, records in sorted(entry_groups.items()):
        content = jsonl(sorted(records, key=lambda item: item["id"]))
        size = len(content.encode("utf-8"))
        if size > ENTRY_SHARD_LIMIT:
            raise KnowledgeError(
                f"knowledge entry shard exceeds 48 MiB; split the authority file: {path}"
            )
        entry_artifacts[path] = content
        entry_shards.append(
            {
                "path": path,
                "count": len(records),
                "bytes": size,
                "sha256": sha256_text(content),
            }
        )
    digest_input = nodes_text + edges_text + references_text
    digest_input += "".join(path + entry_artifacts[path] for path in sorted(entry_artifacts))
    digest = sha256_text(digest_input)
    node_types = Counter(item["type"] for item in nodes)
    relations = Counter(item["relation"] for item in edges)
    statuses = Counter((item.get("properties") or {}).get("source_status", "") for item in nodes)
    curation_statuses = Counter(
        (item.get("properties") or {}).get("curation_status", "")
        for item in nodes
        if item.get("type") == "knowledge"
    )
    knowledge_origins = Counter(
        (item.get("properties") or {}).get("knowledge_origin", "personal-note")
        for item in nodes
        if item.get("type") == "knowledge"
    )
    manifest = {
        "schema": GRAPH_SCHEMA,
        "generator": "kgdistiller",
        "graph_sha256": digest,
        "counts": {
            "nodes": len(nodes),
            "edges": len(edges),
            "references": len(references),
        },
        "node_types": dict(sorted(node_types.items())),
        "relations": dict(sorted(relations.items())),
        "statuses": dict(sorted(statuses.items())),
        "curation_statuses": dict(sorted(curation_statuses.items())),
        "knowledge_origins": dict(sorted(knowledge_origins.items())),
        "source_hashes": dict(sorted(source_hashes.items())),
        "entry_store": {
            "schema": ENTRY_STORE_SCHEMA,
            "entries": sum(item["count"] for item in entry_shards),
            "shards": entry_shards,
        },
    }
    if identity_sha256:
        manifest["identity_sha256"] = identity_sha256
    if git_revision:
        manifest["git_revision"] = git_revision
    return {
        "manifest.json": pretty_json(manifest),
        "nodes.jsonl": nodes_text,
        "edges.jsonl": edges_text,
        "references.jsonl": references_text,
        "diagnostics.json": diagnostics_text,
    } | entry_artifacts


def write_artifacts(graph_dir: Path, artifacts: dict[str, str]) -> None:
    graph_dir.mkdir(parents=True, exist_ok=True)
    previous_manifest = read_json(graph_dir / "manifest.json", {})
    previous_shards = {
        str(item.get("path", ""))
        for item in ((previous_manifest.get("entry_store") or {}).get("shards", []))
        if item.get("path")
    }
    current_shards = {name for name in artifacts if name.startswith("entries/")}
    for name, content in artifacts.items():
        atomic_write(graph_dir / name, content)
    for name in sorted(previous_shards - current_shards):
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise KnowledgeError(f"unsafe stale knowledge entry shard path: {name}")
        path = graph_dir / relative
        if path.is_file():
            path.unlink()


def typst_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def render_typst_labels(state: GraphState) -> None:
    """Render authored knowledge names once with Typst's native HTML target."""
    candidates = [
        (node["id"], str((node.get("properties") or {}).get("typst_name", "")))
        for node in sorted(state.nodes.values(), key=lambda item: item["id"])
        if node.get("type") == "knowledge"
        and (node.get("properties") or {}).get("typst_name")
    ]
    if not candidates:
        return
    lines = [
        '#let graph-label(id, body) = html.elem("ql-label", attrs: (data-node-id: id))[#body]',
        "",
    ]
    for node_id, typst_name in candidates:
        lines.append(f'#graph-label("{typst_string(node_id)}")[{typst_name}]')
    with tempfile.TemporaryDirectory(prefix="qlkg-labels-") as temporary:
        source = Path(temporary) / "labels.typ"
        output = Path(temporary) / "labels.html"
        source.write_text("\n\n".join(lines) + "\n", encoding="utf-8")
        try:
            result = subprocess.run(
                [
                    "typst",
                    "compile",
                    "--features",
                    "html",
                    "--format",
                    "html",
                    str(source),
                    str(output),
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        except FileNotFoundError as error:
            raise KnowledgeError("Typst is required to render knowledge-node labels") from error
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown Typst error"
            raise KnowledgeError(f"knowledge-label rendering failed: {detail}")
        document = output.read_text(encoding="utf-8")
    rendered = {
        match.group("id"): match.group("html").strip()
        for match in LABEL_HTML_RE.finditer(document)
    }
    expected = {node_id for node_id, _ in candidates}
    if set(rendered) != expected:
        missing = ", ".join(sorted(expected - set(rendered))) or "none"
        raise KnowledgeError(f"Typst omitted knowledge labels: {missing}")
    for node_id, label_html in rendered.items():
        if UNSAFE_LABEL_HTML_RE.search(label_html):
            raise KnowledgeError(f"unsafe HTML in rendered knowledge label: {node_id}")
        node = state.nodes[node_id]
        properties = dict(node.get("properties") or {})
        properties["label_html"] = label_html
        node["properties"] = properties


def graph_entry_url(state: GraphState, node_id: str) -> str:
    for node in sorted(state.nodes.values(), key=lambda item: item["id"]):
        web = str((node.get("provenance") or {}).get("web", ""))
        marker = "/notes/"
        if marker in web:
            return f"{web.split(marker, 1)[0]}/knowledge/#node={node_id}"
    return f"/knowledge/#node={node_id}"


def write_registry(path: Path, state: GraphState) -> None:
    lines = ["// Generated by kgdistiller. Do not edit by hand.", "#let knowledge-registry = ("]
    typst_reference_names: dict[str, list[str]] = defaultdict(list)
    for reference in state.references:
        if reference.get("source_format") != "typst":
            continue
        target = str(reference.get("target", ""))
        source_name = str(reference.get("source_name", ""))
        if source_name and source_name not in typst_reference_names[target]:
            typst_reference_names[target].append(source_name)
    for node in sorted(state.nodes.values(), key=lambda item: item["id"]):
        node_id = node["id"]
        properties = node.get("properties") or {}
        typst_name = properties.get("typst_name")
        if node.get("type") != "knowledge":
            continue
        registry_name = str(typst_name) if typst_name else (
            f'#text("{typst_string(str(node.get("label", node_id)))}")'
        )
        names = [registry_name]
        names.extend(
            name for name in typst_reference_names.get(node_id, []) if name not in names
        )
        provenance = node.get("provenance") or {}
        if properties.get("source_status") == "active" and provenance.get("web"):
            url = str(provenance["web"])
        else:
            url = graph_entry_url(state, node_id)
        lines.extend(
            [
                "  (",
                f"    name: [{registry_name}],",
                "    names: (",
                *(f"      [{name}]," for name in names),
                "    ),",
                f'    id: "{typst_string(node_id)}",',
                f'    title: "{typst_string(str(node.get("label", node_id)))}",',
                f'    url: "{typst_string(url)}",',
                "  ),",
            ]
        )
    lines.append(")")
    atomic_write(path, "\n".join(lines) + "\n")


def write_database(
    path: Path,
    state: GraphState,
    alignments: Path | None = None,
) -> None:
    from kgdistiller.agent import write_agent_index
    from kgdistiller.alignment import load_alignment_set

    write_agent_index(
        path,
        make_agent_snapshot(state),
        load_alignment_set(alignments),
    )


def ensure_database(
    path: Path,
    state: GraphState,
    alignments: Path | None = None,
) -> bool:
    """Create or refresh the disposable Agent index only when inputs changed."""
    from kgdistiller.agent import AgentIndexError, index_status, write_agent_index
    from kgdistiller.alignment import load_alignment_set, sha256_json

    journal_path = path.parent / "kgdistiller-ingest/journal.json"
    journal = read_json(journal_path, {}) if journal_path.is_file() else {}
    if journal.get("schema") == "qlkg-ingest-journal-v1" and journal.get(
        "status"
    ) == "installing":
        try:
            index_status(path)
        except (AgentIndexError, OSError, sqlite3.Error, json.JSONDecodeError) as error:
            raise KnowledgeError(
                "transactional ingest is installing a new generation; retry the query"
            ) from error
        # Readers keep using the last complete disposable index until the
        # transaction atomically replaces it and commits its journal.
        return False
    alignment_set = load_alignment_set(alignments)
    try:
        status = index_status(path)
    except (AgentIndexError, OSError, sqlite3.Error, json.JSONDecodeError):
        status = {}
    if state.manifest.get("schema") != GRAPH_SCHEMA:
        if status:
            return False
        raise KnowledgeError("expected a qlkg-v2 graph before Agent index bootstrap")
    snapshot = make_agent_snapshot(state)
    if (
        status.get("snapshot_sha256") == snapshot["snapshot_sha256"]
        and status.get("alignment_sha256") == sha256_json(alignment_set)
    ):
        return False
    write_agent_index(path, snapshot, alignment_set)
    return True


def synchronize(
    repo_root: Path,
    registry: Path,
    graph_dir: Path,
    database: Path,
    typst_registry: Path,
    *,
    identities: Path | None = None,
    alignments: Path | None = None,
    files: list[Path],
    course: str | None,
    subject: str | None,
    write: bool,
) -> tuple[GraphState, dict[str, str], dict[str, Any]]:
    fields = load_fields(registry)
    specs = load_sources(repo_root, registry)
    previous = load_state(graph_dir)
    registered_identities = load_identity_registry(identities)
    git_context = git_source_context(
        repo_root,
        str(previous.manifest.get("git_revision", "")) or None,
        specs,
    )
    pairs, selected_keys, full = select_scope(repo_root, specs, files, course, subject)
    pairs, selected_keys = include_previous_authorities(
        repo_root,
        specs,
        pairs,
        selected_keys,
        previous,
        git_context,
        files=files,
        course=course,
        subject=subject,
        full=full,
    )
    state = copy.deepcopy(previous)
    scan = scan_scope(
        repo_root,
        pairs,
        build_identity_index(previous, registered_identities),
    )
    if scan.errors:
        raise KnowledgeError("\n".join(item["message"] for item in scan.errors))
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
    ensure_taxonomy_nodes_and_edges(
        state,
        fields,
        specs,
        scan.definitions,
        found_ids | set(orphaned),
        prune=full,
    )
    previous_source_hashes = dict(previous.manifest.get("source_hashes") or {})
    source_hashes = dict(previous_source_hashes)
    if full:
        source_hashes = {}
    for _, path in pairs:
        key = relative_path(repo_root, path)
        if path.is_file():
            source_hashes[key] = sha256_file(path)
        else:
            source_hashes.pop(key, None)
    refresh_semantic_edge_curation(state)
    render_typst_labels(state)
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
        identity_sha256=identity_registry_sha256(identities),
        git_revision=git_revision,
    )
    diagnostics = json.loads(artifacts["diagnostics.json"])
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
                if edge.get("relation") != "contains"
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
        write_artifacts(graph_dir, artifacts)
        # Build every downstream projection from the committed, hydrated
        # representation. The JSONL projection omits empty text and stores
        # entry bodies in shards, so using the pre-serialization state here
        # can give SQLite a different snapshot digest from the graph that was
        # just installed. That makes the next read-only query rebuild the
        # disposable index even though no authority changed.
        state = load_state(graph_dir)
        write_registry(typst_registry, state)
        write_database(database, state, alignments)
    return state, artifacts, report


def apply_delta(
    graph_dir: Path,
    database: Path,
    typst_registry: Path,
    delta_path: Path,
    alignments: Path | None = None,
) -> dict[str, Any]:
    delta = read_json(delta_path, {})
    if delta.get("schema") != DELTA_SCHEMA:
        raise KnowledgeError(f"expected {DELTA_SCHEMA} delta: {delta_path}")
    state = load_state(graph_dir)
    before = dict(state.manifest.get("counts") or {})
    removed_nodes = 0
    for raw_id in delta.get("remove_nodes", []):
        node_id = str(raw_id)
        existing = state.nodes.get(node_id)
        if existing is None:
            continue
        if existing.get("type") == "knowledge" and (existing.get("provenance") or {}).get("active"):
            raise KnowledgeError(f"cannot remove active authored knowledge node: {node_id}")
        state.nodes.pop(node_id)
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
        if node_type == "knowledge":
            knowledge_origin = str(properties.get("knowledge_origin", "personal-note"))
            if knowledge_origin not in KNOWLEDGE_ORIGINS:
                raise KnowledgeError(
                    f"invalid knowledge_origin for delta node {node_id}: {knowledge_origin!r}"
                )
            properties["knowledge_origin"] = knowledge_origin
        else:
            properties.pop("knowledge_origin", None)
        properties.setdefault("aliases", [])
        properties.setdefault("origin", "agent")
        properties.setdefault("source_status", "meta")
        raw_entry = raw.get("entry") if "entry" in raw else existing.get("entry")
        if raw_entry is not None and not isinstance(raw_entry, dict):
            raise KnowledgeError(f"structured entry must be an object: {node_id}")
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
    render_typst_labels(state)
    artifacts = make_artifacts(
        state,
        dict(state.manifest.get("source_hashes") or {}),
        identity_sha256=state.manifest.get("identity_sha256"),
        git_revision=state.manifest.get("git_revision"),
    )
    diagnostics = json.loads(artifacts["diagnostics.json"])
    if diagnostics["errors"]:
        raise KnowledgeError("\n".join(item["message"] for item in diagnostics["errors"]))
    state.manifest = json.loads(artifacts["manifest.json"])
    write_artifacts(graph_dir, artifacts)
    state = load_state(graph_dir)
    write_registry(typst_registry, state)
    write_database(database, state, alignments)
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
    database: Path,
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
) -> dict[str, Any]:
    """Persist one reviewed cross-namespace decision with content fingerprints."""
    from kgdistiller.agent import align_graph, get_index_node
    from kgdistiller.alignment import (
        load_alignment_set,
        make_reviewed_mapping,
        upsert_mapping,
    )

    candidate_namespace = str(candidate_snapshot.get("namespace", ""))
    align_graph(
        database,
        candidate_snapshot,
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
    target = get_index_node(
        database,
        target_id,
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
    write_database(database, state, alignment_path)
    return {
        "schema": alignment_set["schema"],
        "mapping": mapping,
        "alignment_registry": str(alignment_path),
        "mappings": len(alignment_set["mappings"]),
        "index_rebuilt": str(database),
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
        score += 10 if node.get("type") == "knowledge" else 0
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
) -> dict[str, Any]:
    """Validate deterministic consequences of prior agent curation decisions."""
    selected = {
        node_id: node
        for node_id, node in state.nodes.items()
        if node.get("type") == "knowledge"
        and (node.get("provenance") or {}).get("active")
        and (node.get("provenance") or {}).get("authority") in authorities
    }
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
            consumer_id not in selected
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
            edge.get("relation") != "contains"
            and edge.get("curation_status") == "needs-review"
            and ({str(edge.get("source", "")), str(edge.get("target", ""))} & set(selected))
        ):
            errors.append(
                diagnostic(
                    "stale-semantic-edge",
                    "semantic edge must be reviewed against the current authority",
                    node=str(edge.get("source", "")),
                )
            )

    return {
        "schema": "qlkg-curation-check-v1",
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
    semantic_edges = [
        edge
        for edge in state.edges.values()
        if edge.get("relation") != "contains"
    ]
    adjacency: dict[str, set[str]] = defaultdict(set)
    semantic_degree: Counter[str] = Counter()
    cross_course_edges = 0
    for edge in semantic_edges:
        source = str(edge.get("source", ""))
        target = str(edge.get("target", ""))
        if source in active and target in active:
            adjacency[source].add(target)
            adjacency[target].add(source)
            semantic_degree[source] += 1
            semantic_degree[target] += 1
            source_course = str((active[source].get("properties") or {}).get("course", ""))
            target_course = str((active[target].get("properties") or {}).get("course", ""))
            if source_course and target_course and source_course != target_course:
                cross_course_edges += 1

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

    reference_authorities = Counter(
        str(reference.get("authority", ""))
        for reference in state.references
    )
    authority_course = {
        authority: str((nodes[0].get("properties") or {}).get("course", "unknown"))
        for authority, nodes in authorities.items()
        if nodes
    }
    courses: dict[str, dict[str, Any]] = {}
    for course in sorted(
        {str((node.get("properties") or {}).get("course", "unknown")) for node in active.values()}
    ):
        course_nodes = {
            node_id: node
            for node_id, node in active.items()
            if str((node.get("properties") or {}).get("course", "unknown")) == course
        }
        course_entries = sum(
            bool(str(node.get("text", "")).strip()) for node in course_nodes.values()
        )
        courses[course] = {
            "nodes": len(course_nodes),
            "entries": course_entries,
            "entry_ratio": round(course_entries / len(course_nodes), 6) if course_nodes else 1.0,
            "semantic_nodes": sum(bool(adjacency[node_id]) for node_id in course_nodes),
            "isolated_nodes": sum(not adjacency[node_id] for node_id in course_nodes),
            "references": sum(
                count
                for authority, count in reference_authorities.items()
                if authority_course.get(authority) == course
            ),
        }

    taxonomy_parents: Counter[str] = Counter()
    for edge in state.edges.values():
        if edge.get("relation") == "contains" and edge.get("target") in active:
            taxonomy_parents[str(edge["target"])] += 1
    field_memberships = knowledge_field_memberships(state)
    field_counts: Counter[str] = Counter()
    for node_id in active:
        field_counts.update(field_memberships.get(node_id, set()))
    unclassified_nodes = sorted(
        node_id for node_id in active if not field_memberships.get(node_id)
    )
    multiply_classified_nodes = sorted(
        node_id for node_id in active if len(field_memberships.get(node_id, set())) > 1
    )
    entry_count = sum(bool(str(node.get("text", "")).strip()) for node in active.values())
    relation_counts = Counter(str(edge.get("relation", "")) for edge in state.edges.values())
    return {
        "schema": "qlkg-audit-v1",
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
            "cross_course_edges": cross_course_edges,
            "field_membership_histogram": {
                str(count): occurrences
                for count, occurrences in sorted(
                    Counter(len(field_memberships.get(node_id, set())) for node_id in active).items()
                )
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
        "fields": {
            field_id: field_counts[field_id]
            for field_id, node in sorted(state.nodes.items())
            if node.get("type") == "field"
        },
        "courses": courses,
        "quality": {
            "semantic_edges_missing_evidence": sum(
                not str(edge.get("evidence", "")).strip() for edge in semantic_edges
            ),
            "semantic_edges_missing_confidence": sum(
                not str(edge.get("confidence", "")).strip() for edge in semantic_edges
            ),
            "knowledge_nodes_without_taxonomy_parent": sorted(
                node_id for node_id in active if not taxonomy_parents[node_id]
            ),
            "knowledge_nodes_without_field": unclassified_nodes,
            "knowledge_nodes_with_multiple_fields": len(multiply_classified_nodes),
        },
    }


def defaults(repo_root: Path, value: str) -> Path:
    return (repo_root / value).resolve()


def add_scope_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--file", action="append", default=[], type=Path)
    parser.add_argument("--course")
    parser.add_argument("--subject")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--local-profile",
        type=Path,
        help="machine-local qlkg-local-profile-v1 (default: knowledge/build/local-profile.json)",
    )
    parser.add_argument("--registry", default="knowledge/sources.json")
    parser.add_argument("--graph", default="knowledge/graph")
    parser.add_argument("--identities", default="knowledge/identities.json")
    parser.add_argument("--alignments", default="knowledge/alignments.json")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--store", type=Path)
    parser.add_argument("--embedding-profile")
    parser.add_argument(
        "--embedding-policy",
        type=Path,
        default=Path("knowledge/embedding-policy.json"),
        help=(
            "portable qlkg-embedding-policy-v1 "
            "(default: knowledge/embedding-policy.json)"
        ),
    )
    parser.add_argument(
        "--typst-registry",
        default="knowledge/build/knowledge-registry.typ",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    init_command = commands.add_parser("init")
    init_command.add_argument("--source-root", type=Path, default=Path("notes"))
    init_command.add_argument("--force", action="store_true")
    for name in ("sync", "build", "scan"):
        command = commands.add_parser(name)
        add_scope_arguments(command)
    apply_command = commands.add_parser("apply")
    apply_command.add_argument("delta", type=Path)
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
    publish_command = commands.add_parser("publish")
    publish_command.add_argument(
        "--format",
        required=True,
        choices=("typst", "markdown", "latex"),
        dest="source_format",
    )
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
    resolve_command = agent_commands.add_parser("resolve")
    resolve_command.add_argument("concept", nargs="+")
    resolve_command.add_argument("--namespace", default="personal")
    agent_search_command = agent_commands.add_parser("search")
    agent_search_command.add_argument("query", nargs="?")
    agent_search_command.add_argument(
        "--plan",
        type=Path,
        help="execute a qlkg-retrieval-plan-v1 JSON file instead of a legacy query",
    )
    agent_search_command.add_argument("--namespace")
    agent_search_command.add_argument("--type", action="append", dest="node_types")
    agent_search_command.add_argument("--limit", type=int)
    agent_search_command.add_argument("--depth", type=int)
    agent_search_command.add_argument(
        "--include-taxonomy", action="store_true", default=None
    )
    agent_search_command.add_argument("--include-stale", action="store_true", default=None)
    agent_search_command.add_argument(
        "--include-orphaned", action="store_true", default=None
    )
    agent_search_command.add_argument(
        "--graph-strategy", choices=("bfs", "ppr", "hybrid")
    )
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
    expand_command.add_argument("--include-taxonomy", action="store_true")
    expand_command.add_argument("--include-stale", action="store_true")
    expand_command.add_argument("--include-orphaned", action="store_true")
    ppr_command = agent_commands.add_parser("ppr")
    ppr_command.add_argument("id", nargs="+")
    ppr_command.add_argument("--namespace", default="personal")
    ppr_command.add_argument("--type", action="append", dest="node_types")
    ppr_command.add_argument("--relation", action="append", dest="edge_types")
    ppr_command.add_argument("--limit", type=int, default=50)
    ppr_command.add_argument("--include-taxonomy", action="store_true")
    ppr_command.add_argument("--no-similarity", action="store_true")
    ppr_command.add_argument("--include-stale", action="store_true")
    ppr_command.add_argument("--include-orphaned", action="store_true")
    context_command = agent_commands.add_parser("context")
    context_command.add_argument("query", nargs="?")
    context_command.add_argument(
        "--plan",
        type=Path,
        help="execute a qlkg-retrieval-plan-v1 JSON file instead of a legacy query",
    )
    context_command.add_argument("--namespace")
    context_command.add_argument("--type", action="append", dest="node_types")
    context_command.add_argument("--budget", type=int, default=6000)
    context_command.add_argument("--limit", type=int)
    context_command.add_argument("--depth", type=int)
    context_command.add_argument(
        "--include-taxonomy", action="store_true", default=None
    )
    context_command.add_argument("--include-stale", action="store_true", default=None)
    context_command.add_argument(
        "--include-orphaned", action="store_true", default=None
    )
    context_command.add_argument(
        "--graph-strategy", choices=("bfs", "ppr", "hybrid")
    )
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
    profile_command = commands.add_parser("profile")
    profile_commands = profile_command.add_subparsers(
        dest="profile_command", required=True
    )
    profile_commands.add_parser("status")
    embedding_command = commands.add_parser("embedding")
    embedding_commands = embedding_command.add_subparsers(
        dest="embedding_command", required=True
    )
    embedding_status_command = embedding_commands.add_parser("status")
    embedding_status_command.add_argument("--namespace", default="personal")
    embedding_sync_command = embedding_commands.add_parser("sync")
    embedding_sync_command.add_argument("--namespace", default="personal")
    embedding_sync_command.add_argument(
        "--batch-size",
        default=32,
        help="document inputs per provider call (default: 32)",
    )
    embedding_sync_command.add_argument(
        "--max-retries",
        default=2,
        help="retry bound for each provider batch (default: 2)",
    )
    embedding_sync_command.add_argument(
        "--max-nodes",
        default=10_000,
        help="maximum missing or stale nodes in one sync (default: 10000)",
    )
    embedding_sync_command.add_argument(
        "--profile",
        action="append",
        dest="embedding_sync_profiles",
        help=(
            "policy/profile name to synchronize; repeat for multiple profiles "
            "(default: selected machine-local embedding profile)"
        ),
    )
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
    store_commands.add_parser("materialize")
    commands.add_parser("mcp")
    serve_command = commands.add_parser("serve")
    serve_command.add_argument("--host", default="127.0.0.1")
    serve_command.add_argument("--port", type=int, default=8765)
    serve_command.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
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
                "node_types",
                "limit",
                "depth",
                "include_taxonomy",
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
    repo_root = args.repo_root.resolve()
    try:
        runtime = resolve_runtime_config(
            repo_root,
            local_profile=args.local_profile,
            database=args.database,
            portable_store=args.store,
            embedding_profile=args.embedding_profile,
        )
        registry = defaults(repo_root, args.registry)
        graph_dir = defaults(repo_root, args.graph)
        identities = defaults(repo_root, args.identities)
        alignments = defaults(repo_root, args.alignments)
        database = runtime.database
        typst_registry = defaults(repo_root, args.typst_registry)
        if args.command == "embedding":
            try:
                from .embedding import (
                    EmbeddingError,
                    embedding_status,
                    load_embedding_policy,
                    sync_embeddings,
                )
                from .providers import ProviderError, default_provider_registry
            except ImportError:  # Direct execution during compatibility tests.
                from kgdistiller.embedding import (
                    EmbeddingError,
                    embedding_status,
                    load_embedding_policy,
                    sync_embeddings,
                )
                from kgdistiller.providers import (
                    ProviderError,
                    default_provider_registry,
                )

            policy_argument = args.embedding_policy
            policy_path = (
                policy_argument.resolve()
                if policy_argument.is_absolute()
                else (repo_root / policy_argument).resolve()
            )
            try:
                policy = load_embedding_policy(policy_path)
                if args.embedding_command == "status":
                    result = embedding_status(
                        database,
                        policy,
                        runtime.provider_profiles,
                        namespace=args.namespace,
                    )
                else:
                    profile_names = list(args.embedding_sync_profiles or [])
                    if not profile_names and runtime.embedding_profile is not None:
                        profile_names = [runtime.embedding_profile]
                    if not profile_names:
                        raise EmbeddingError(
                            "profile-not-selected",
                            "embedding sync requires a selected embedding profile",
                        )
                    work_budget: dict[str, int] = {}
                    for name in ("batch_size", "max_retries", "max_nodes"):
                        raw_value = str(getattr(args, name))
                        if len(raw_value) > 16 or not re.fullmatch(
                            r"-?[0-9]+", raw_value
                        ):
                            raise EmbeddingError(
                                "invalid-work-budget",
                                "embedding work budget is invalid",
                            )
                        work_budget[name] = int(raw_value)
                    ensure_database(database, load_state(graph_dir), alignments)
                    result = sync_embeddings(
                        database,
                        policy,
                        runtime.provider_profiles,
                        registry=default_provider_registry(),
                        namespace=args.namespace,
                        profile_names=profile_names,
                        batch_size=work_budget["batch_size"],
                        max_retries=work_budget["max_retries"],
                        max_nodes=work_budget["max_nodes"],
                    )
            except (EmbeddingError, ProviderError) as error:
                print(pretty_json(error.payload()), end="", file=sys.stderr)
                return 1
            except (
                KnowledgeError,
                OSError,
                UnicodeError,
                ValueError,
                json.JSONDecodeError,
                sqlite3.Error,
            ):
                print(
                    pretty_json(
                        {
                            "kind": "kgdistiller-embedding-error",
                            "code": "embedding-command-failed",
                            "message": "embedding command could not be completed",
                        }
                    ),
                    end="",
                    file=sys.stderr,
                )
                return 1
            print(pretty_json(result), end="")
            return 0
        if args.command == "profile":
            try:
                from .providers import (
                    ProviderError,
                    default_provider_registry,
                    provider_status,
                )
            except ImportError:
                from kgdistiller.providers import (
                    ProviderError,
                    default_provider_registry,
                    provider_status,
                )

            adapter_registry = default_provider_registry()
            selected_provider = runtime.provider_profile
            try:
                provider = (
                    provider_status(
                        str(runtime.embedding_profile),
                        selected_provider,
                        adapter_registry,
                    )
                    if selected_provider is not None
                    else None
                )
            except ProviderError as error:
                print(pretty_json(error.payload()), end="", file=sys.stderr)
                return 1
            print(
                pretty_json(
                    {
                        "profile_path": str(runtime.profile_path),
                        "profile_loaded": runtime.profile_loaded,
                        "profile_sha256": runtime.profile_sha256,
                        "database": str(runtime.database),
                        "portable_store": str(runtime.portable_store),
                        "embedding_profile": runtime.embedding_profile,
                        "sources": runtime.sources,
                        "provider": provider,
                    }
                ),
                end="",
            )
            return 0
        if args.command == "candidate":
            from .agent import validate_agent_snapshot
            from .candidate import build_candidate_snapshot

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
                database=database,
                typst_registry=typst_registry,
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
            from .store import materialize_store, snapshot_store, verify_store

            if args.store_command == "snapshot":
                _, artifacts, _ = synchronize(
                    repo_root,
                    registry,
                    graph_dir,
                    database,
                    typst_registry,
                    identities=identities,
                    alignments=alignments,
                    files=[],
                    course=None,
                    subject=None,
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
                ensure_database(database, load_state(graph_dir), alignments)
                output = args.output or runtime.portable_store
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
                    database=database,
                )
            elif args.store_command == "verify":
                result = verify_store(runtime.portable_store)
            else:
                result = materialize_store(runtime.portable_store, database)
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
                database,
                typst_registry,
                identities=identities,
                alignments=alignments,
                files=[],
                course=None,
                subject=None,
                write=True,
            )
            print(pretty_json({"initialized": str(repo_root), **report}), end="")
            return 0
        if args.command in {"sync", "build", "scan"}:
            pairs_files = list(args.file)
            if args.command == "scan":
                specs = load_sources(repo_root, registry)
                state = load_state(graph_dir)
                pairs, selected, full = select_scope(
                    repo_root, specs, pairs_files, args.course, args.subject
                )
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
                    course=args.course,
                    subject=args.subject,
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
                database,
                typst_registry,
                identities=identities,
                alignments=alignments,
                files=pairs_files,
                course=args.course,
                subject=args.subject,
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
                        database,
                        typst_registry,
                        delta,
                        alignments,
                    )
                ),
                end="",
            )
            return 0
        if args.command == "reconcile":
            state = load_state(graph_dir)
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
                            database,
                            alignments,
                            read_json(candidate_path, {}),
                            args.candidate_id,
                            args.target_id,
                            predicate=args.predicate,
                            status=args.status,
                            justification=args.justification,
                            evidence=args.evidence,
                            target_namespace=args.target_namespace,
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
                database,
                typst_registry,
                identities=identities,
                alignments=alignments,
                files=[],
                course=None,
                subject=None,
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
        if args.command == "publish":
            specs = load_sources(repo_root, registry)
            pairs, _, _ = select_scope(repo_root, specs, [], None, None)
            selected_paths = [
                path for _, path in pairs if source_format(path) == args.source_format
            ]
            missing = [
                relative_path(repo_root, path)
                for path in selected_paths
                if not path.is_file()
            ]
            if missing:
                raise KnowledgeError(f"publication source does not exist: {', '.join(missing)}")
            sync_report: dict[str, Any] | None = None
            if selected_paths:
                _, _, sync_report = synchronize(
                    repo_root,
                    registry,
                    graph_dir,
                    database,
                    typst_registry,
                    identities=identities,
                    alignments=alignments,
                    files=selected_paths,
                    course=None,
                    subject=None,
                    write=True,
                )
            state = load_state(graph_dir)
            authorities = {
                relative_path(repo_root, path) for path in selected_paths
            }
            report = curation_report(state, authorities)
            report["source_format"] = args.source_format
            report["synchronized_files"] = len(selected_paths)
            if sync_report is not None:
                report["graph_counts"] = sync_report["counts"]
            print(pretty_json(report), end="")
            return 1 if report["errors"] else 0
        if args.command == "serve":
            from .web import serve_graph

            serve_graph(
                repo_root,
                graph_dir,
                host=args.host,
                port=args.port,
                open_browser=not args.no_open,
            )
            return 0
        if args.command == "mcp":
            from kgdistiller.mcp import serve_stdio
            from kgdistiller.providers import default_provider_registry

            def current_mcp_authority_graph_sha256() -> str:
                authority_state = load_state(graph_dir)
                digest = str(authority_state.manifest.get("graph_sha256", ""))
                if not re.fullmatch(r"[0-9a-f]{64}", digest):
                    raise KnowledgeError("authority graph has no valid graph_sha256")
                return digest

            # Fail before entering a long-lived stdio loop, then re-resolve for every
            # retrieval call so the server cannot silently outlive its authority view.
            current_mcp_authority_graph_sha256()
            serve_stdio(
                database,
                embedding_profile=runtime.embedding_profile,
                provider_config=runtime.provider_profile,
                provider_registry=default_provider_registry(),
                environ=os.environ,
                expected_graph_sha256_resolver=current_mcp_authority_graph_sha256,
            )
            return 0
        if args.command == "agent":
            from kgdistiller.agent import (
                PROPOSAL_SCHEMA,
                align_graph,
                compare_graph,
                create_proposal,
                expand_index,
                get_index_node,
                index_status,
                personalized_pagerank,
                resolve_concepts,
            )
            from kgdistiller.providers import default_provider_registry
            from kgdistiller.retrieval import (
                RetrievalError,
                build_context_from_execution,
                execute_retrieval_plan,
                legacy_retrieval_plan,
                load_retrieval_plan,
            )

            def current_authority_graph_sha256() -> str:
                authority_state = load_state(graph_dir)
                digest = str(authority_state.manifest.get("graph_sha256", ""))
                if not re.fullmatch(r"[0-9a-f]{64}", digest):
                    raise KnowledgeError("authority graph has no valid graph_sha256")
                return digest

            if args.agent_command == "status":
                result = index_status(database)
            elif args.agent_command == "resolve":
                result = resolve_concepts(
                    database,
                    list(args.concept),
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
                            node_types=args.node_types,
                            limit=args.limit if args.limit is not None else 20,
                            max_depth=args.depth if args.depth is not None else 1,
                            include_taxonomy=bool(args.include_taxonomy),
                            include_stale=bool(args.include_stale),
                            include_orphaned=bool(args.include_orphaned),
                            graph_strategy=args.graph_strategy or "hybrid",
                        )
                        plan_mode = "legacy"
                    result = execute_retrieval_plan(
                        database,
                        plan,
                        plan_mode=plan_mode,
                        namespace=execution_namespace_argument,
                        embedding_profile=runtime.embedding_profile,
                        provider_config=runtime.provider_profile,
                        provider_registry=default_provider_registry(),
                        environ=os.environ,
                        expected_graph_sha256=expected_graph_sha256,
                    )
                except RetrievalError as error:
                    print(pretty_json(error.to_payload()), end="", file=sys.stderr)
                    return 1
            elif args.agent_command == "get":
                result = get_index_node(
                    database,
                    args.id,
                    namespace=args.namespace,
                )
            elif args.agent_command == "expand":
                result = expand_index(
                    database,
                    list(args.id),
                    namespace=args.namespace,
                    direction=args.direction,
                    edge_types=args.edge_types,
                    max_depth=args.depth,
                    limit=args.limit,
                    include_taxonomy=args.include_taxonomy,
                    include_stale=args.include_stale,
                    include_orphaned=args.include_orphaned,
                )
            elif args.agent_command == "ppr":
                result = personalized_pagerank(
                    database,
                    {str(node_id): 1.0 for node_id in args.id},
                    namespace=args.namespace,
                    node_types=args.node_types,
                    edge_types=args.edge_types,
                    limit=args.limit,
                    include_taxonomy=args.include_taxonomy,
                    include_similarity=not args.no_similarity,
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
                            node_types=args.node_types,
                            limit=args.limit if args.limit is not None else 50,
                            max_depth=args.depth if args.depth is not None else 1,
                            include_taxonomy=bool(args.include_taxonomy),
                            include_stale=bool(args.include_stale),
                            include_orphaned=bool(args.include_orphaned),
                            graph_strategy=args.graph_strategy or "hybrid",
                        )
                        plan_mode = "legacy"
                        execution_namespace_argument = execution_namespace
                    execution = execute_retrieval_plan(
                        database,
                        plan,
                        plan_mode=plan_mode,
                        namespace=execution_namespace_argument,
                        embedding_profile=runtime.embedding_profile,
                        provider_config=runtime.provider_profile,
                        provider_registry=default_provider_registry(),
                        environ=os.environ,
                        expected_graph_sha256=expected_graph_sha256,
                    )
                    result = build_context_from_execution(
                        database,
                        execution,
                        plan=plan,
                        token_budget=args.budget,
                        namespace=execution_namespace,
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
                alignment_report = align_graph(
                    database,
                    read_json(candidate_path, {}),
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
                result = compare_graph(
                    database,
                    read_json(candidate_path, {}),
                    target_namespace=args.target_namespace,
                )
            else:
                candidate_path = (
                    args.candidate.resolve()
                    if args.candidate.is_absolute()
                    else (repo_root / args.candidate).resolve()
                )
                proposal = create_proposal(
                    database,
                    read_json(candidate_path, {}),
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
        state = load_state(graph_dir)
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
            pairs, authorities, _ = select_scope(
                repo_root,
                specs,
                list(args.file),
                None,
                None,
            )
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
    except ProfileError as error:
        print(pretty_json(error.payload()), end="", file=sys.stderr)
        return 1
    except (KnowledgeError, OSError, UnicodeError, ValueError, json.JSONDecodeError, sqlite3.Error) as error:
        print(f"knowledge command failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
