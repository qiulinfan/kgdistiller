"""The knowledge store: reviewed entries plus one file of accepted semantic edges.

Nodes are the entry files under ``.knowledge/entries/``. Accepted semantic
edges live in ``.knowledge/edges.jsonl``. Consistency with the cited sources
is checked by text: an entry is current while its Evidence quote still matches
its cited lines, ignoring whitespace.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .entries import (
    ENTRIES_DIR,
    EntryError,
    cited_text,
    identity_key,
    locate_evidence,
    normalize_evidence,
    normalize_record,
    parse_entry,
    render_entry,
    split_lines,
    validate_id,
    validate_source_path,
)
from .home import (
    KNOWLEDGE_DIRECTORY,
    Base,
    DocumentType,
    KnowledgeError,
    atomic_write_text,
    knowledge_root,
)

EDGES_PATH = Path(KNOWLEDGE_DIRECTORY, "edges.jsonl")
EDGE_FIELDS = ("source", "relation", "target", "origin", "confidence", "evidence")
EDGE_KEY_FIELDS = ("source", "relation", "target")
SEMANTIC_RELATIONS = frozenset({
    "prerequisite-for",
    "implies",
    "generalizes",
    "contrasts-with",
    "derived-from",
})
ACYCLIC_RELATIONS = frozenset({"prerequisite-for"})
DELTA_SCHEMA = "kgdistiller-agent-delta-v1"
DELTA_KEYS = ("create_entries", "update_entries", "remove_entries", "add_edges", "remove_edges")

EdgeKey = tuple[str, str, str]


class StoreError(KnowledgeError):
    """A knowledge store operation failed with a stable error code."""

    def __init__(
        self, code: str, message: str, diagnostics: list[dict[str, Any]] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.diagnostics = diagnostics or []


@dataclass
class KnowledgeState:
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: dict[EdgeKey, dict[str, Any]] = field(default_factory=dict)


def entries_root(repo_root: Path) -> Path:
    return knowledge_root(repo_root) / ENTRIES_DIR.name


def edges_file(repo_root: Path) -> Path:
    return knowledge_root(repo_root) / EDGES_PATH.name


def edge_key(edge: dict[str, Any]) -> EdgeKey:
    return (edge["source"], edge["relation"], edge["target"])


def key_record(key: EdgeKey) -> dict[str, str]:
    return dict(zip(EDGE_KEY_FIELDS, key, strict=True))


def normalize_edge(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict) or set(raw) != set(EDGE_FIELDS):
        raise ValueError(f"an edge needs exactly the fields {', '.join(EDGE_FIELDS)}")
    if not all(isinstance(raw[name], str) and raw[name].strip() for name in EDGE_FIELDS):
        raise ValueError("edge fields must be nonempty strings")
    if raw["relation"] not in SEMANTIC_RELATIONS:
        raise ValueError(f"unknown relation {raw['relation']!r}")
    return {name: raw[name] for name in EDGE_FIELDS}


def _reject_symlinks(repo_root: Path, path: Path) -> None:
    cursor = path
    while cursor != repo_root and cursor.parent != cursor:
        if cursor.is_symlink():
            raise StoreError("invalid-store", f"knowledge store path must not be a symlink: {cursor}")
        cursor = cursor.parent


def load_state(repo_root: Path, errors: list[dict[str, Any]] | None = None) -> KnowledgeState:
    """Read every entry and the accepted edges.

    Readers pass no ``errors`` list and fail closed on the first malformed entry
    or edge line. ``check`` passes a list: each malformed entry file or edge line
    is then recorded as an ``invalid-entry`` or ``invalid-edge`` diagnostic and
    left out, so every problem is reported in one run.
    """
    state = KnowledgeState()

    def reject(code: str, message: str) -> None:
        if errors is None:
            raise StoreError("invalid-store", message)
        errors.append(_diagnostic(code, message))

    root = entries_root(repo_root)
    if root.exists() or root.is_symlink():
        _reject_symlinks(repo_root, root)
        if not root.is_dir():
            raise StoreError("invalid-store", f"entries must be a directory: {root}")
        for path in sorted(root.glob("*.md")):
            relative = path.relative_to(repo_root)
            if path.is_symlink() or not path.is_file():
                reject("invalid-entry", f"entry is not an ordinary file: {relative}")
                continue
            try:
                record = parse_entry(path.read_text(encoding="utf-8"), relative)
            except (EntryError, UnicodeError) as error:
                reject("invalid-entry", str(error))
                continue
            if path.name != f"{record['id']}.md":
                reject("invalid-entry", f"{relative}: file name must be {record['id']}.md")
                continue
            state.entries[record["id"]] = record
    path = edges_file(repo_root)
    if path.exists() or path.is_symlink():
        _reject_symlinks(repo_root, path)
        if not path.is_file():
            raise StoreError("invalid-store", f"edges must be a regular file: {path}")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                edge = normalize_edge(json.loads(line))
            except ValueError as error:
                reject("invalid-edge", f"{EDGES_PATH}:{number}: {error}")
                continue
            key = edge_key(edge)
            if key in state.edges:
                reject("invalid-edge", f"{EDGES_PATH}:{number}: duplicate edge {key}")
                continue
            state.edges[key] = edge
    return state


def render_edges(edges: Iterable[dict[str, Any]]) -> str:
    rows = sorted(edges, key=edge_key)
    return "".join(
        json.dumps(edge, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for edge in rows
    )


def write_entry(repo_root: Path, record: dict[str, Any]) -> Path:
    path = entries_root(repo_root) / f"{validate_id(record['id'])}.md"
    atomic_write_text(path, render_entry(record))
    return path


def write_edges(repo_root: Path, edges: Iterable[dict[str, Any]]) -> None:
    atomic_write_text(edges_file(repo_root), render_edges(edges))


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


@dataclass(frozen=True)
class _Source:
    type: DocumentType
    text: str
    lines: list[str]


def _diagnostic(code: str, message: str, **context: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **context}


class _Sources:
    """Read cited source files once, checking that the base's globs register them."""

    def __init__(self, base: Base) -> None:
        self.base = base
        self.repo_root = base.root
        self.registered, self.conflicts = base.source_types()
        self.cache: dict[str, _Source | dict[str, Any]] = {}

    def read(self, relative: str) -> _Source | dict[str, Any]:
        if relative not in self.cache:
            self.cache[relative] = self._read(relative)
        return self.cache[relative]

    def _read(self, relative: str) -> _Source | dict[str, Any]:
        try:
            validate_source_path(relative)
        except EntryError as error:
            return _diagnostic("missing-source", str(error), source=relative)
        path = self.repo_root / relative
        try:
            _reject_symlinks(self.repo_root, path)
        except StoreError:
            return _diagnostic("missing-source", f"source must not traverse a symlink: {relative}",
                               source=relative)
        if not path.is_file():
            return _diagnostic("missing-source", f"source file does not exist: {relative}",
                               source=relative)
        if relative in self.conflicts or relative not in self.registered:
            try:
                self.base.type_of(relative)
            except KnowledgeError as error:
                return _diagnostic("source-not-registered", str(error), source=relative)
        document_type = self.base.types[self.registered[relative]]
        try:
            with path.open("r", encoding="utf-8", newline=None) as handle:
                text = handle.read()
        except (OSError, UnicodeError) as error:
            return _diagnostic("missing-source", f"source is not readable UTF-8 text: {relative}: {error}",
                               source=relative)
        return _Source(type=document_type, text=text, lines=split_lines(text))


def _evidence_status(record: dict[str, Any], source: _Source) -> dict[str, Any] | None:
    start, end = record["line_start"], record["line_end"]
    if end <= len(source.lines):
        cited = cited_text(source.lines, start, end)
        if normalize_evidence(cited) == normalize_evidence(record["evidence"]):
            return None
    ranges = locate_evidence(source.text, record["evidence"])
    base = {"entry": record["id"], "source": record["source"], "line_start": start, "line_end": end}
    if len(ranges) == 1:
        return {**base, "status": "moved", "new_line_start": ranges[0][0],
                "new_line_end": ranges[0][1]}
    if len(ranges) > 1:
        return {**base, "status": "ambiguous",
                "candidates": [{"line_start": first, "line_end": last} for first, last in ranges]}
    return {**base, "status": "stale"}


def validate(state: KnowledgeState, base: Base) -> dict[str, list[dict[str, Any]]]:
    """Check the store contract and report entries whose Evidence no longer matches."""
    errors: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    sources = _Sources(base)
    for path, names in sorted(sources.conflicts.items()):
        errors.append(_diagnostic(
            "source-type-conflict",
            f"source {path} of base {base.name} matches globs of several types: {', '.join(names)}",
            source=path))
    names: dict[str, str] = {}
    for entry_id, record in sorted(state.entries.items()):
        for name in (record["label"], *record["aliases"]):
            owner = names.setdefault(identity_key(name), entry_id)
            if owner != entry_id:
                errors.append(_diagnostic(
                    "identity-collision", f"name {name!r} of {entry_id} is already used by {owner}",
                    entry=entry_id))
        source = sources.read(record["source"])
        if isinstance(source, dict):
            errors.append({**source, "entry": entry_id})
            continue
        if record["kind"] not in source.type.node_kinds:
            errors.append(_diagnostic(
                "kind-not-allowed",
                f"kind {record['kind']!r} of {entry_id} is not allowed by document type {source.type.name!r}",
                entry=entry_id))
        if record["line_end"] > len(source.lines):
            errors.append(_diagnostic(
                "line-range",
                f"{entry_id} cites lines {record['line_start']}-{record['line_end']} but "
                f"{record['source']} has {len(source.lines)} lines",
                entry=entry_id))
        status = _evidence_status(record, source)
        if status is not None:
            stale.append(status)
    for key, edge in sorted(state.edges.items()):
        if edge["relation"] not in SEMANTIC_RELATIONS:
            errors.append(_diagnostic("invalid-edge", f"unknown relation {edge['relation']!r}",
                                      edge=key_record(key)))
        for endpoint in (edge["source"], edge["target"]):
            if endpoint not in state.entries:
                errors.append(_diagnostic("dangling-edge", f"edge endpoint {endpoint!r} has no entry",
                                          edge=key_record(key)))
    for relation in sorted(ACYCLIC_RELATIONS):
        for cycle in graph_cycles(set(state.entries), state.edges.values(), relation):
            errors.append(_diagnostic("cycle", f"{relation} cycle: {' -> '.join(cycle)}",
                                      relation=relation, cycle=cycle))
    return {"errors": errors, "stale": stale}


def fix_lines(state: KnowledgeState, base: Base) -> list[dict[str, Any]]:
    """Rewrite only the line ranges of moved entries and return what changed."""
    fixed = []
    for item in validate(state, base)["stale"]:
        if item["status"] != "moved":
            continue
        record = dict(state.entries[item["entry"]])
        record["line_start"], record["line_end"] = item["new_line_start"], item["new_line_end"]
        write_entry(base.root, record)
        state.entries[record["id"]] = normalize_record(record)
        fixed.append(item)
    return fixed


def identity_index(state: KnowledgeState) -> dict[str, str]:
    """Map each normalized label and alias to its entry id."""
    index: dict[str, str] = {}
    for entry_id, record in sorted(state.entries.items()):
        for name in (record["label"], *record["aliases"]):
            owner = index.setdefault(identity_key(name), entry_id)
            if owner != entry_id:
                raise StoreError("identity-collision",
                                 f"name {name!r} is used by both {owner} and {entry_id}")
    return index


def _object(value: Any, keys: set[str], field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise StoreError("invalid-request", f"{field_name} needs exactly: {', '.join(sorted(keys))}")
    return value


def _record(value: Any, field_name: str) -> dict[str, Any]:
    try:
        record = normalize_record(value)
        render_entry(record)
        return record
    except EntryError as error:
        raise StoreError("invalid-request", f"{field_name}: {error}") from error


def _check_cited(record: dict[str, Any], sources: _Sources) -> None:
    source = sources.read(record["source"])
    if isinstance(source, dict):
        raise StoreError(source["code"], source["message"], [{**source, "entry": record["id"]}])
    if record["line_end"] > len(source.lines):
        raise StoreError("line-range", f"{record['id']} cites lines {record['line_start']}-"
                         f"{record['line_end']} but {record['source']} has {len(source.lines)} lines")
    if cited_text(source.lines, record["line_start"], record["line_end"]) != record["evidence"]:
        raise StoreError("stale-evidence", f"Evidence of {record['id']} differs from "
                         f"{record['source']}:{record['line_start']}-{record['line_end']}")


def _changes(before: KnowledgeState, after: KnowledgeState) -> dict[str, list[Any]]:
    both = set(before.entries) & set(after.entries)
    every = set(before.entries) | set(after.entries)
    return {
        "entries_created": sorted(set(after.entries) - set(before.entries)),
        "entries_updated": sorted(i for i in both if before.entries[i] != after.entries[i]),
        "entries_removed": sorted(set(before.entries) - set(after.entries)),
        "aliases_changed": sorted(
            i for i in every
            if (before.entries.get(i) or {}).get("aliases", []) != (after.entries.get(i) or {}).get("aliases", [])
        ),
        "edges_added": [key_record(key) for key in sorted(after.edges)
                        if before.edges.get(key) != after.edges[key]],
        "edges_removed": [key_record(key) for key in sorted(before.edges) if key not in after.edges],
    }


def apply_delta(
    state: KnowledgeState, delta: Any, base: Base
) -> tuple[KnowledgeState, dict[str, list[Any]]]:
    """Apply a reviewed delta in memory; the result must satisfy the store contract."""
    _object(delta, {"schema", *DELTA_KEYS}, "delta")
    if delta["schema"] != DELTA_SCHEMA:
        raise StoreError("invalid-request", f"delta schema must be {DELTA_SCHEMA}")
    for name in DELTA_KEYS:
        if not isinstance(delta[name], list):
            raise StoreError("invalid-request", f"delta.{name} must be a list")
    after = KnowledgeState(entries=dict(state.entries), edges=dict(state.edges))
    touched: set[str] = set()

    def claim(entry_id: str) -> None:
        if entry_id in touched:
            raise StoreError("invalid-request", f"entry {entry_id} appears more than once in the delta")
        touched.add(entry_id)

    for index, raw in enumerate(delta["remove_edges"]):
        item = _object(raw, set(EDGE_KEY_FIELDS), f"remove_edges[{index}]")
        key = edge_key(item)
        if key not in after.edges:
            raise StoreError("missing-edge", f"edge {key} does not exist")
        del after.edges[key]
    for index, raw in enumerate(delta["remove_entries"]):
        item = _object(raw, {"id", "expected_label"}, f"remove_entries[{index}]")
        entry_id = str(item["id"])
        claim(entry_id)
        existing = after.entries.get(entry_id)
        if existing is None:
            raise StoreError("missing-entry", f"entry {entry_id} does not exist")
        if existing["label"] != item["expected_label"]:
            raise StoreError("label-mismatch", f"entry {entry_id} is labelled {existing['label']!r}, "
                             f"not {item['expected_label']!r}")
        del after.entries[entry_id]
    changed: list[dict[str, Any]] = []
    for index, raw in enumerate(delta["update_entries"]):
        item = _object(raw, {"expected_label", "entry"}, f"update_entries[{index}]")
        record = _record(item["entry"], f"update_entries[{index}].entry")
        claim(record["id"])
        existing = after.entries.get(record["id"])
        if existing is None:
            raise StoreError("missing-entry", f"entry {record['id']} does not exist")
        if existing["label"] != item["expected_label"]:
            raise StoreError("label-mismatch", f"entry {record['id']} is labelled {existing['label']!r}, "
                             f"not {item['expected_label']!r}")
        after.entries[record["id"]] = record
        changed.append(record)
    for index, raw in enumerate(delta["create_entries"]):
        record = _record(raw, f"create_entries[{index}]")
        claim(record["id"])
        if record["id"] in after.entries:
            raise StoreError("entry-exists", f"entry {record['id']} already exists")
        after.entries[record["id"]] = record
        changed.append(record)
    for index, raw in enumerate(delta["add_edges"]):
        try:
            edge = normalize_edge(raw)
        except ValueError as error:
            raise StoreError("invalid-request", f"add_edges[{index}]: {error}") from error
        after.edges[edge_key(edge)] = edge
    sources = _Sources(base)
    for record in changed:
        _check_cited(record, sources)
    errors = validate(after, base)["errors"]
    if errors:
        raise StoreError(errors[0]["code"], errors[0]["message"], errors)
    return after, _changes(state, after)
