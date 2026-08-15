"""Deterministic, read-only queries over one validated JSON graph generation.

The query layer owns no secondary index or model service. A :class:`GraphView`
is a complete immutable-in-practice snapshot of one authority generation.
Callers may retain a view for a request, but should load a fresh view for each
independent CLI or MCP operation.
"""

from __future__ import annotations

import copy
import json
import math
import re
import unicodedata
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .alignment import (
    ALIGNMENT_REPORT_SCHEMA,
    AlignmentError,
    empty_alignment_set,
    extract_scoped_aliases,
    mapping_id,
    node_fingerprint,
    validate_alignment_set,
)
from .cli import (
    DELTA_SCHEMA,
    GRAPH_SCHEMA,
    ID_RE,
    MAX_NODE_ID_LENGTH,
    MAX_NODE_LABEL_LENGTH,
    KnowledgeError,
    load_state,
    make_agent_snapshot,
)
from .contracts import MAX_NAMESPACE_LENGTH, canonical_json, sha256_json


SNAPSHOT_SCHEMA = "qlkg-agent-snapshot-v2"
QUERY_STATUS_SCHEMA = "qlkg-query-status-v1"
COMPARISON_SCHEMA = "qlkg-graph-comparison-v2"
PROPOSAL_SCHEMA = "qlkg-agent-proposal-v2"
CONTEXT_SCHEMA = "qlkg-context-bundle-v2"
QUERY_CAPABILITY = "json-memory"
DEFAULT_SEMANTIC_RELATIONS = {
    "prerequisite-for",
    "implies",
    "generalizes",
    "contrasts-with",
    "derived-from",
}
ALL_RELATIONS = {*DEFAULT_SEMANTIC_RELATIONS, "contains"}
MAX_LIMIT = 500
MAX_BATCH_CONCEPTS = 512
MAX_GRAPH_SEEDS = 128
MAX_GRAPH_DEPTH = 8
MAX_QUERY_LENGTH = 4096
MAX_QUERY_TERMS = 128
MAX_SNAPSHOT_NODES = 100_000
MAX_SNAPSHOT_EDGES = 500_000
MAX_SNAPSHOT_REFERENCES = 500_000
MAX_SNAPSHOT_DIAGNOSTICS = 100_000
MAX_REFERENCE_ID_LENGTH = 256
MAX_AUTHORITY_LENGTH = 4096
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_NAMESPACE_RE = re.compile(
    rf"(?=.{{1,{MAX_NAMESPACE_LENGTH}}}\Z)[a-z0-9][a-z0-9._-]*"
    r"(?::[a-z0-9][a-z0-9._-]*)*"
)
_WORD_RE = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*", re.UNICODE)


class QueryError(ValueError):
    """Stable failure raised before a partial or ambiguous view is exposed."""


def normalize_text(value: str) -> str:
    """Apply the only cross-language normalization used by query operations."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        if value.strip():
            yield value
    elif isinstance(value, Mapping):
        for key in sorted(value):
            yield from _strings(value[key])
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _manifest_payload(graph_dir: Path) -> dict[str, Any]:
    path = graph_dir / "manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise QueryError("authority graph manifest is unavailable or invalid") from error
    if not isinstance(payload, dict):
        raise QueryError("authority graph manifest must be a JSON object")
    return payload


def _generation_token(manifest: Mapping[str, Any]) -> str:
    return sha256_json(dict(manifest))


def validate_agent_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Validate the self-contained snapshot contract and all graph references."""
    if not isinstance(snapshot, dict) or snapshot.get("schema") != SNAPSHOT_SCHEMA:
        raise QueryError(f"expected {SNAPSHOT_SCHEMA} snapshot")
    if set(snapshot) != {
        "schema",
        "namespace",
        "graph",
        "nodes",
        "edges",
        "references",
        "diagnostics",
        "snapshot_sha256",
    }:
        raise QueryError("snapshot has unsupported top-level fields")
    namespace = snapshot.get("namespace")
    if not isinstance(namespace, str) or not _NAMESPACE_RE.fullmatch(namespace):
        raise QueryError(f"invalid snapshot namespace: {namespace!r}")
    rows = {
        "nodes": snapshot.get("nodes"),
        "edges": snapshot.get("edges"),
        "references": snapshot.get("references"),
    }
    if any(not isinstance(value, list) for value in rows.values()):
        raise QueryError("snapshot graph records must be arrays")
    if (
        len(rows["nodes"]) > MAX_SNAPSHOT_NODES
        or len(rows["edges"]) > MAX_SNAPSHOT_EDGES
        or len(rows["references"]) > MAX_SNAPSHOT_REFERENCES
    ):
        raise QueryError("snapshot exceeds deterministic graph limits")
    claimed = snapshot.get("snapshot_sha256")
    digest_payload = dict(snapshot)
    digest_payload.pop("snapshot_sha256", None)
    try:
        digest = sha256_json(digest_payload)
    except (TypeError, ValueError) as error:
        raise QueryError("snapshot is not finite canonical JSON") from error
    if not isinstance(claimed, str) or not _SHA256_RE.fullmatch(claimed) or digest != claimed:
        raise QueryError("snapshot digest does not match its content")
    graph = snapshot.get("graph")
    if not isinstance(graph, dict) or graph.get("schema") != GRAPH_SCHEMA:
        raise QueryError(f"snapshot has no valid {GRAPH_SCHEMA} graph identity")
    if set(graph) != {"schema", "sha256", "counts"}:
        raise QueryError("snapshot graph identity has unsupported fields")
    graph_sha = graph.get("sha256")
    if not isinstance(graph_sha, str) or not _SHA256_RE.fullmatch(graph_sha):
        raise QueryError("snapshot graph sha256 is invalid")
    counts = {key: len(value) for key, value in rows.items()}
    claimed_counts = graph.get("counts")
    if (
        not isinstance(claimed_counts, dict)
        or set(claimed_counts) != set(counts)
        or any(
            isinstance(claimed_counts.get(key), bool)
            or not isinstance(claimed_counts.get(key), int)
            or claimed_counts.get(key) != value
            for key, value in counts.items()
        )
    ):
        raise QueryError("snapshot counts do not match its records")
    node_ids: list[str] = []
    for node in rows["nodes"]:
        if not isinstance(node, dict):
            raise QueryError("snapshot contains an invalid node")
        node_id = node.get("id")
        label = node.get("label")
        if not isinstance(node_id, str) or not ID_RE.fullmatch(node_id):
            raise QueryError(
                "snapshot node ID must be lowercase ASCII kebab-case and at most "
                f"{MAX_NODE_ID_LENGTH} characters"
            )
        if (
            not isinstance(label, str)
            or not label
            or len(label) > MAX_NODE_LABEL_LENGTH
        ):
            raise QueryError(
                "snapshot node label must be a non-empty string of at most "
                f"{MAX_NODE_LABEL_LENGTH} characters"
            )
        if node.get("type") not in {"knowledge", "field", "topic"}:
            raise QueryError("snapshot node has an unsupported type")
        for field in ("properties", "provenance", "entry"):
            if field in node and not isinstance(node[field], dict):
                raise QueryError(f"snapshot node {field} must be an object")
        if "text" in node and not isinstance(node["text"], str):
            raise QueryError("snapshot node text must be a string")
        properties = node.get("properties") or {}
        aliases = properties.get("aliases", [])
        if not isinstance(aliases, list) or any(
            not isinstance(alias, str) for alias in aliases
        ):
            raise QueryError("snapshot node aliases must be an array of strings")
        if node.get("type") == "knowledge":
            if "curation_status" in properties and properties["curation_status"] not in {
                "current",
                "pending",
                "needs-review",
            }:
                raise QueryError("snapshot node curation status is invalid")
            if "source_status" in properties and properties["source_status"] not in {
                "active",
                "orphaned",
            }:
                raise QueryError("snapshot node source status is invalid")
        else:
            for field in ("curation_status", "source_status"):
                if field in properties and (
                    not isinstance(properties[field], str) or not properties[field]
                ):
                    raise QueryError(f"snapshot taxonomy node {field} is invalid")
        provenance = node.get("provenance") or {}
        if "authority" in provenance and (
            not isinstance(provenance["authority"], str)
            or not provenance["authority"]
            or len(provenance["authority"]) > MAX_AUTHORITY_LENGTH
        ):
            raise QueryError("snapshot node authority is invalid")
        if "line" in provenance and (
            isinstance(provenance["line"], bool)
            or not isinstance(provenance["line"], int)
            or provenance["line"] < 1
        ):
            raise QueryError("snapshot node source line is invalid")
        if "page" in provenance and (
            isinstance(provenance["page"], bool)
            or not isinstance(provenance["page"], int)
            or provenance["page"] < 1
        ):
            raise QueryError("snapshot node source page is invalid")
        for field in ("section", "equation"):
            if field in provenance and (
                not isinstance(provenance[field], str)
                or not provenance[field].strip()
            ):
                raise QueryError("snapshot node source location is invalid")
        if "active" in provenance and not isinstance(provenance["active"], bool):
            raise QueryError("snapshot node provenance.active must be boolean")
        requires_source = namespace != "personal" or node.get("type") == "knowledge"
        if requires_source and (
            not isinstance(provenance.get("authority"), str)
            or not provenance["authority"]
            or not any(
                provenance.get(field) not in (None, "")
                for field in ("line", "page", "section", "equation")
            )
        ):
            raise QueryError("snapshot source-backed node has no bounded provenance")
        node_ids.append(node_id)
    if len(node_ids) != len(set(node_ids)):
        raise QueryError("snapshot contains duplicate node IDs")
    known = set(node_ids)
    node_types = {
        str(node["id"]): str(node["type"])
        for node in rows["nodes"]
    }
    edge_keys: set[tuple[str, str, str]] = set()
    for edge in rows["edges"]:
        if not isinstance(edge, dict):
            raise QueryError("snapshot contains an invalid edge")
        source = edge.get("source")
        relation = edge.get("relation")
        target = edge.get("target")
        if (
            not isinstance(source, str)
            or not ID_RE.fullmatch(source)
            or not isinstance(target, str)
            or not ID_RE.fullmatch(target)
            or not isinstance(relation, str)
            or relation not in ALL_RELATIONS
        ):
            raise QueryError("snapshot contains an invalid edge")
        key = (source, relation, target)
        if key[0] not in known or key[2] not in known:
            raise QueryError("snapshot contains a dangling edge")
        if key in edge_keys:
            raise QueryError("snapshot contains an invalid or duplicate edge")
        if relation != "contains" and (
            not isinstance(edge.get("evidence"), str)
            or not edge["evidence"].strip()
        ):
            raise QueryError("snapshot semantic edge has no evidence")
        if "evidence" in edge and not isinstance(edge["evidence"], str):
            raise QueryError("snapshot edge evidence must be a string")
        if relation == "contains":
            if (node_types[source], node_types[target]) not in {
                ("field", "topic"),
                ("field", "knowledge"),
                ("topic", "knowledge"),
            }:
                raise QueryError("snapshot contains edge has invalid node types")
        edge_keys.add(key)
    reference_ids: set[str] = set()
    for reference in rows["references"]:
        if not isinstance(reference, dict):
            raise QueryError("snapshot contains an invalid reference")
        reference_id = reference.get("id")
        target = reference.get("target")
        authority = reference.get("authority")
        if (
            not isinstance(reference_id, str)
            or not reference_id
            or len(reference_id) > MAX_REFERENCE_ID_LENGTH
            or reference_id in reference_ids
        ):
            raise QueryError("snapshot contains an invalid or duplicate reference ID")
        if not isinstance(target, str) or not ID_RE.fullmatch(target) or target not in known:
            raise QueryError("snapshot contains a dangling reference")
        if (
            not isinstance(authority, str)
            or not authority
            or len(authority) > MAX_AUTHORITY_LENGTH
        ):
            raise QueryError("snapshot reference authority is invalid")
        for field in ("line", "page"):
            if field in reference and (
                isinstance(reference[field], bool)
                or not isinstance(reference[field], int)
                or reference[field] < 1
            ):
                raise QueryError("snapshot reference source location is invalid")
        for field in ("section", "equation"):
            if field in reference and (
                not isinstance(reference[field], str) or not reference[field].strip()
            ):
                raise QueryError("snapshot reference source location is invalid")
        if not any(
            reference.get(field) not in (None, "")
            for field in ("line", "page", "section", "equation")
        ):
            raise QueryError("snapshot reference has no bounded source location")
        reference_ids.add(reference_id)
    diagnostics = snapshot.get("diagnostics")
    if not isinstance(diagnostics, dict) or set(diagnostics) != {"errors", "warnings"}:
        raise QueryError("snapshot diagnostics are invalid")
    if any(not isinstance(diagnostics[key], list) for key in ("errors", "warnings")):
        raise QueryError("snapshot diagnostics are invalid")
    if any(
        len(diagnostics[key]) > MAX_SNAPSHOT_DIAGNOSTICS
        for key in ("errors", "warnings")
    ):
        raise QueryError("snapshot diagnostics exceed deterministic limits")
    for severity in ("errors", "warnings"):
        for item in diagnostics[severity]:
            if not isinstance(item, dict) or set(item) - {
                "code",
                "message",
                "source",
                "node",
            }:
                raise QueryError("snapshot diagnostics are invalid")
            if (
                not isinstance(item.get("code"), str)
                or not item["code"]
                or len(item["code"]) > 256
                or not isinstance(item.get("message"), str)
                or not item["message"]
            ):
                raise QueryError("snapshot diagnostics are invalid")
            if "source" in item and (
                not isinstance(item["source"], str)
                or not item["source"]
                or len(item["source"]) > MAX_AUTHORITY_LENGTH
            ):
                raise QueryError("snapshot diagnostics are invalid")
            if "node" in item and (
                not isinstance(item["node"], str) or not ID_RE.fullmatch(item["node"])
            ):
                raise QueryError("snapshot diagnostics are invalid")
    if diagnostics["errors"]:
        raise QueryError("snapshot diagnostics contain authority errors")
    return {
        "schema": SNAPSHOT_SCHEMA,
        "namespace": namespace,
        "counts": counts,
        "snapshot_sha256": claimed,
        "graph_sha256": graph_sha,
    }


@dataclass(frozen=True)
class GraphView:
    """One validated, fully loaded authority graph generation."""

    graph_dir: Path
    snapshot: dict[str, Any]
    alignments: dict[str, Any]
    generation: str
    nodes: dict[str, dict[str, Any]]
    edges: tuple[dict[str, Any], ...]
    references: tuple[dict[str, Any], ...]
    outgoing: dict[str, tuple[dict[str, Any], ...]]
    incoming: dict[str, tuple[dict[str, Any], ...]]
    backlinks: dict[str, tuple[dict[str, Any], ...]]
    labels: dict[str, tuple[str, ...]]
    aliases: dict[str, tuple[str, ...]]
    scoped_aliases: dict[str, tuple[dict[str, Any], ...]]
    source_hashes: dict[str, str]

    @classmethod
    def load(
        cls,
        graph_dir: Path,
        alignments: Path | None = None,
        *,
        max_attempts: int = 3,
    ) -> "GraphView":
        graph_dir = Path(graph_dir)
        if max_attempts < 1 or max_attempts > 10:
            raise QueryError("max_attempts must be between 1 and 10")
        last_error: Exception | None = None
        for _ in range(max_attempts):
            before = _manifest_payload(graph_dir)
            token = _generation_token(before)
            try:
                state = load_state(graph_dir)
                snapshot = make_agent_snapshot(state)
                validate_agent_snapshot(snapshot)
                alignment_payload = _load_alignments(alignments)
            except (KnowledgeError, AlignmentError, OSError, ValueError) as error:
                last_error = error
                after = _manifest_payload(graph_dir)
                if token != _generation_token(after):
                    continue
                raise QueryError(str(error)) from error
            after = _manifest_payload(graph_dir)
            if token != _generation_token(after):
                last_error = QueryError("authority graph generation changed while loading")
                continue
            return cls._from_snapshot(
                graph_dir,
                snapshot,
                alignment_payload,
                generation=token,
                source_hashes=dict(before.get("source_hashes") or {}),
            )
        raise QueryError(
            "authority graph generation changed while loading; retry the query"
        ) from last_error

    @classmethod
    def from_snapshot(
        cls,
        snapshot: dict[str, Any],
        *,
        alignments: dict[str, Any] | None = None,
    ) -> "GraphView":
        """Construct a test/candidate view without filesystem access."""
        validate_agent_snapshot(snapshot)
        validated = validate_alignment_set(alignments or empty_alignment_set())
        return cls._from_snapshot(
            Path("."),
            copy.deepcopy(snapshot),
            validated,
            generation=sha256_json(snapshot),
            source_hashes={},
        )

    @classmethod
    def _from_snapshot(
        cls,
        graph_dir: Path,
        snapshot: dict[str, Any],
        alignments: dict[str, Any],
        *,
        generation: str,
        source_hashes: dict[str, str],
    ) -> "GraphView":
        nodes = {
            str(node["id"]): copy.deepcopy(node)
            for node in sorted(snapshot["nodes"], key=lambda item: str(item["id"]))
        }
        edges = tuple(
            copy.deepcopy(edge)
            for edge in sorted(
                snapshot["edges"],
                key=lambda item: (item["source"], item["relation"], item["target"]),
            )
        )
        references = tuple(
            copy.deepcopy(reference)
            for reference in sorted(
                snapshot["references"],
                key=lambda item: (
                    str(item.get("authority", "")),
                    int(item.get("line", 0)),
                    str(item.get("target", "")),
                    str(item.get("id", "")),
                ),
            )
        )
        outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
        incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
        backlinks: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in edges:
            outgoing[str(edge["source"])].append(edge)
            incoming[str(edge["target"])].append(edge)
        for reference in references:
            backlinks[str(reference["target"])].append(reference)
        labels: dict[str, list[str]] = defaultdict(list)
        aliases_by_surface: dict[str, list[str]] = defaultdict(list)
        for node_id, node in nodes.items():
            label = normalize_text(str(node.get("label", "")))
            if label:
                labels[label].append(node_id)
            properties = node.get("properties")
            properties = properties if isinstance(properties, dict) else {}
            for alias in properties.get("aliases", []):
                normalized = normalize_text(str(alias))
                if normalized:
                    aliases_by_surface[normalized].append(node_id)
        try:
            scoped = extract_scoped_aliases(snapshot)
        except AlignmentError as error:
            raise QueryError(str(error)) from error
        scoped_by_surface: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for alias in scoped["aliases"]:
            scoped_by_surface[str(alias["normalized_surface"])].append(alias)
        return cls(
            graph_dir=graph_dir,
            snapshot=copy.deepcopy(snapshot),
            alignments=copy.deepcopy(alignments),
            generation=generation,
            nodes=nodes,
            edges=edges,
            references=references,
            outgoing={key: tuple(value) for key, value in outgoing.items()},
            incoming={key: tuple(value) for key, value in incoming.items()},
            backlinks={key: tuple(value) for key, value in backlinks.items()},
            labels={key: tuple(sorted(set(value))) for key, value in labels.items()},
            aliases={key: tuple(sorted(set(value))) for key, value in aliases_by_surface.items()},
            scoped_aliases={
                key: tuple(sorted(value, key=lambda item: (item["node_id"], item["id"])))
                for key, value in scoped_by_surface.items()
            },
            source_hashes=dict(sorted(source_hashes.items())),
        )


def _load_alignments(path: Path | None) -> dict[str, Any]:
    if path is None:
        return empty_alignment_set()
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty_alignment_set()
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise QueryError("alignment registry is unavailable or invalid") from error
    try:
        return validate_alignment_set(payload)
    except AlignmentError as error:
        raise QueryError(str(error)) from error


def load_graph_view(
    graph_dir: Path, alignments: Path | None = None, *, max_attempts: int = 3
) -> GraphView:
    return GraphView.load(graph_dir, alignments, max_attempts=max_attempts)


def _view(source: GraphView | Path, alignments: Path | None = None) -> GraphView:
    return source if isinstance(source, GraphView) else load_graph_view(source, alignments)


def _namespace(view: GraphView, namespace: str) -> None:
    if not _NAMESPACE_RE.fullmatch(namespace):
        raise QueryError(f"invalid namespace: {namespace!r}")
    if namespace != view.snapshot["namespace"]:
        raise QueryError(f"namespace is not loaded: {namespace!r}")


def _limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_LIMIT:
        raise QueryError(f"limit must be between 1 and {MAX_LIMIT}")
    return value


def _allowed(node: Mapping[str, Any], *, include_stale: bool, include_orphaned: bool) -> bool:
    properties = node.get("properties")
    properties = properties if isinstance(properties, Mapping) else {}
    provenance = node.get("provenance")
    provenance = provenance if isinstance(provenance, Mapping) else {}
    source_status = properties.get("source_status")
    active = provenance.get("active") is not False
    return (
        (active or (include_orphaned and source_status == "orphaned"))
        and (include_stale or properties.get("curation_status") != "needs-review")
        and (include_orphaned or source_status != "orphaned")
    )


def _edge_allowed(edge: Mapping[str, Any], *, include_stale: bool) -> bool:
    return include_stale or edge.get("curation_status") != "needs-review"


def query_status(
    source: GraphView | Path, *, alignments: Path | None = None
) -> dict[str, Any]:
    view = _view(source, alignments)
    graph = view.snapshot["graph"]
    return {
        "schema": QUERY_STATUS_SCHEMA,
        "snapshot_schema": SNAPSHOT_SCHEMA,
        "namespace": view.snapshot["namespace"],
        "snapshot_sha256": view.snapshot["snapshot_sha256"],
        "graph_schema": graph["schema"],
        "graph_sha256": graph["sha256"],
        "generation": view.generation,
        "counts": copy.deepcopy(graph["counts"]),
        "backend": QUERY_CAPABILITY,
        "retrieval_lanes": ["identity", "lexical", "graph", "ppr"],
        "capabilities": [QUERY_CAPABILITY, "read-only-query-v3"],
        "alignment_schema": view.alignments["schema"],
        "alignment_sha256": sha256_json(view.alignments),
        "alignment_counts": {"mappings": len(view.alignments["mappings"])},
    }


def resolve_concepts(
    source: GraphView | Path,
    concepts: list[str],
    *,
    namespace: str = "personal",
    match_limit: int = MAX_LIMIT,
    alignments: Path | None = None,
) -> list[dict[str, Any]]:
    view = _view(source, alignments)
    _namespace(view, namespace)
    match_limit = _limit(match_limit)
    if not isinstance(concepts, list) or len(concepts) > MAX_BATCH_CONCEPTS:
        raise QueryError(f"concept batch exceeds {MAX_BATCH_CONCEPTS}")
    if any(
        not isinstance(value, str)
        or not value.strip()
        or len(value) > MAX_QUERY_LENGTH
        for value in concepts
    ):
        raise QueryError(
            f"each concept must be a non-empty string of at most {MAX_QUERY_LENGTH} characters"
        )
    results: list[dict[str, Any]] = []
    for value in concepts:
        raw = value.strip()
        normalized = normalize_text(raw)
        kind: str | None = None
        candidates: list[str] = []
        if raw in view.nodes:
            kind, candidates = "id", [raw]
        elif normalized in view.labels:
            kind, candidates = "label", list(view.labels[normalized])
        elif normalized in view.aliases:
            kind, candidates = "alias", list(view.aliases[normalized])
        total = len(candidates)
        matches = [copy.deepcopy(view.nodes[node_id]) for node_id in candidates[:match_limit]]
        if total == 1:
            status = "alias" if kind == "alias" else "exact"
        elif total > 1:
            status = "ambiguous"
        else:
            status = "missing"
        record: dict[str, Any] = {
            "query": raw,
            "status": status,
            "match_kind": kind,
            "matches": matches,
            "candidate_ids": [node["id"] for node in matches],
            "overflow": total > match_limit,
            "identity_authority": total > 0,
        }
        # Explicit document-scoped aliases are ranking evidence only.  They are
        # surfaced without changing the missing/ambiguous identity decision.
        scoped = view.scoped_aliases.get(normalized, ())
        if scoped:
            record["ranked_candidates"] = [
                {
                    "id": alias["node_id"],
                    "method": "scoped-alias",
                    "identity_authority": False,
                    "evidence": copy.deepcopy(alias["evidence"]),
                }
                for alias in scoped[:match_limit]
            ]
        results.append(record)
    return results


def _tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return [normalize_text(token) for token in _WORD_RE.findall(normalized)][:MAX_QUERY_TERMS]


def _node_search_fields(node: Mapping[str, Any]) -> tuple[str, str, str]:
    properties = node.get("properties")
    properties = properties if isinstance(properties, Mapping) else {}
    aliases = " ".join(str(item) for item in properties.get("aliases", []))
    body = " ".join(
        [str(node.get("text", "")), *list(_strings(node.get("entry") or {}))]
    )
    return str(node.get("label", "")), aliases, body


def search(
    source: GraphView | Path,
    query: str,
    *,
    namespace: str = "personal",
    node_types: list[str] | None = None,
    limit: int = 20,
    include_stale: bool = False,
    include_orphaned: bool = False,
    alignments: Path | None = None,
) -> list[dict[str, Any]]:
    view = _view(source, alignments)
    _namespace(view, namespace)
    limit = _limit(limit)
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_LENGTH:
        raise QueryError(f"query must contain 1 to {MAX_QUERY_LENGTH} characters")
    terms = set(_tokens(query))
    if not terms:
        return []
    allowed_types = set(node_types or [])
    normalized_query = normalize_text(query)
    ranked: list[tuple[float, str, list[dict[str, Any]]]] = []
    scoped_ids = {
        str(record["node_id"])
        for record in view.scoped_aliases.get(normalized_query, ())
    }
    for node_id, node in view.nodes.items():
        if allowed_types and node.get("type") not in allowed_types:
            continue
        if not _allowed(node, include_stale=include_stale, include_orphaned=include_orphaned):
            continue
        label, aliases, body = _node_search_fields(node)
        label_tokens = set(_tokens(label))
        alias_tokens = set(_tokens(aliases))
        body_tokens = set(_tokens(body))
        overlap = 8 * len(terms & label_tokens) + 6 * len(terms & alias_tokens) + len(terms & body_tokens)
        phrase = (
            8 if normalized_query and normalized_query in normalize_text(label) else 0
        ) + (6 if normalized_query and normalized_query in normalize_text(aliases) else 0)
        scoped_bonus = 4 if node_id in scoped_ids else 0
        score = float(overlap + phrase + scoped_bonus)
        if score <= 0:
            continue
        reasons = [{"method": "lexical", "score": score, "identity_authority": False}]
        if scoped_bonus:
            reasons.append(
                {"method": "scoped-alias", "score": float(scoped_bonus), "identity_authority": False}
            )
        ranked.append((score, node_id, reasons))
    ranked.sort(key=lambda item: (-item[0], normalize_text(str(view.nodes[item[1]].get("label", ""))), item[1]))
    return [
        {
            "rank": rank,
            "node": copy.deepcopy(view.nodes[node_id]),
            "reasons": reasons,
        }
        for rank, (_, node_id, reasons) in enumerate(ranked[:limit], start=1)
    ]


def get(
    source: GraphView | Path,
    node_id: str,
    *,
    namespace: str = "personal",
    alignments: Path | None = None,
) -> dict[str, Any]:
    view = _view(source, alignments)
    _namespace(view, namespace)
    if not isinstance(node_id, str) or not ID_RE.fullmatch(node_id):
        raise QueryError("concept ID must be a bounded lowercase ASCII kebab-case string")
    if node_id not in view.nodes:
        raise QueryError(f"unknown concept: {namespace}:{node_id}")
    return {
        "namespace": namespace,
        "node": copy.deepcopy(view.nodes[node_id]),
        "incoming": copy.deepcopy(list(view.incoming.get(node_id, ()))),
        "outgoing": copy.deepcopy(list(view.outgoing.get(node_id, ()))),
        "backlinks": copy.deepcopy(list(view.backlinks.get(node_id, ()))),
    }


def _direction(value: str) -> str:
    values = {"out": "outgoing", "in": "incoming", "outgoing": "outgoing", "incoming": "incoming", "both": "both"}
    if value not in values:
        raise QueryError("invalid graph direction")
    return values[value]


def expand(
    source: GraphView | Path,
    seed_ids: list[str],
    *,
    namespace: str = "personal",
    node_types: list[str] | None = None,
    direction: str = "both",
    edge_types: list[str] | None = None,
    max_depth: int = 1,
    limit: int = 50,
    include_taxonomy: bool = False,
    include_stale: bool = False,
    include_orphaned: bool = False,
    alignments: Path | None = None,
) -> dict[str, Any]:
    view = _view(source, alignments)
    _namespace(view, namespace)
    normalized_direction = _direction(direction)
    limit = _limit(limit)
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or not 0 <= max_depth <= MAX_GRAPH_DEPTH:
        raise QueryError(f"max_depth must be between 0 and {MAX_GRAPH_DEPTH}")
    if not isinstance(seed_ids, list) or not 1 <= len(seed_ids) <= MAX_GRAPH_SEEDS:
        raise QueryError(f"graph seed batch must contain 1 to {MAX_GRAPH_SEEDS} IDs")
    if any(not isinstance(seed, str) or not ID_RE.fullmatch(seed) for seed in seed_ids):
        raise QueryError("graph seed IDs must be bounded lowercase ASCII kebab-case strings")
    seeds = list(dict.fromkeys(seed_ids))
    if any(seed not in view.nodes for seed in seeds):
        unknown = next(seed for seed in seeds if seed not in view.nodes)
        raise QueryError(f"unknown graph seed: {namespace}:{unknown}")
    if len(seeds) > limit:
        raise QueryError("graph seed batch exceeds the result limit")
    allowed_types = set(node_types or [])
    relations = set(edge_types) if edge_types is not None else set(DEFAULT_SEMANTIC_RELATIONS)
    if edge_types is None and include_taxonomy:
        relations.add("contains")
    visited: dict[str, tuple[int, list[dict[str, Any]], str]] = {}
    queue: deque[tuple[str, int, list[dict[str, Any]], str]] = deque()
    for seed in seeds:
        if allowed_types and view.nodes[seed].get("type") not in allowed_types:
            raise QueryError(f"graph seed is excluded by filters: {namespace}:{seed}")
        if not _allowed(view.nodes[seed], include_stale=include_stale, include_orphaned=include_orphaned):
            raise QueryError(f"graph seed is excluded by filters: {namespace}:{seed}")
        visited[seed] = (0, [], seed)
        queue.append((seed, 0, [], seed))
    traversed: dict[tuple[str, str, str], dict[str, Any]] = {}
    while queue and len(visited) < limit:
        current, depth, path, root = queue.popleft()
        if depth >= max_depth:
            continue
        candidates: list[tuple[dict[str, Any], str, str]] = []
        if normalized_direction in {"outgoing", "both"}:
            candidates.extend((edge, str(edge["target"]), "outgoing") for edge in view.outgoing.get(current, ()))
        if normalized_direction in {"incoming", "both"}:
            candidates.extend((edge, str(edge["source"]), "incoming") for edge in view.incoming.get(current, ()))
        candidates.sort(key=lambda item: (str(item[0]["relation"]), item[1], item[2]))
        for edge, neighbor, edge_direction in candidates:
            if edge.get("relation") not in relations or not _edge_allowed(edge, include_stale=include_stale):
                continue
            node = view.nodes[neighbor]
            if allowed_types and node.get("type") not in allowed_types:
                continue
            if not _allowed(node, include_stale=include_stale, include_orphaned=include_orphaned):
                continue
            key = (str(edge["source"]), str(edge["relation"]), str(edge["target"]))
            traversed[key] = edge
            if neighbor in visited:
                continue
            step = {"source": key[0], "relation": key[1], "target": key[2], "direction": edge_direction}
            next_path = [*path, step]
            visited[neighbor] = (depth + 1, next_path, root)
            queue.append((neighbor, depth + 1, next_path, root))
            if len(visited) >= limit:
                break
    rows = [
        {
            "node": copy.deepcopy(view.nodes[node_id]),
            "depth": depth,
            "path": copy.deepcopy(path),
            "seed": node_id in seeds,
            "seed_id": root,
        }
        for node_id, (depth, path, root) in visited.items()
    ]
    rows.sort(key=lambda item: (item["depth"], normalize_text(str(item["node"].get("label", ""))), item["node"]["id"]))
    return {
        "namespace": namespace,
        "seeds": seeds,
        "policy": {
            "direction": direction,
            "node_types": sorted(allowed_types),
            "edge_types": sorted(relations),
            "max_depth": max_depth,
            "limit": limit,
            "include_taxonomy": include_taxonomy,
            "include_stale": include_stale,
            "include_orphaned": include_orphaned,
        },
        "nodes": rows,
        "edges": [copy.deepcopy(traversed[key]) for key in sorted(traversed)],
    }


def personalized_pagerank(
    source: GraphView | Path,
    seeds: Mapping[str, float],
    *,
    namespace: str = "personal",
    node_types: list[str] | None = None,
    edge_types: list[str] | None = None,
    direction: str = "out",
    include_taxonomy: bool = False,
    include_stale: bool = False,
    include_orphaned: bool = False,
    damping: float = 0.85,
    max_iterations: int = 60,
    tolerance: float = 1e-10,
    limit: int = 50,
    alignments: Path | None = None,
) -> dict[str, Any]:
    view = _view(source, alignments)
    _namespace(view, namespace)
    normalized_direction = _direction(direction)
    limit = _limit(limit)
    if not isinstance(seeds, Mapping) or not 1 <= len(seeds) <= MAX_GRAPH_SEEDS:
        raise QueryError(f"PPR seed batch must contain 1 to {MAX_GRAPH_SEEDS} IDs")
    if any(not isinstance(node_id, str) or not ID_RE.fullmatch(node_id) for node_id in seeds):
        raise QueryError("PPR seed IDs must be bounded lowercase ASCII kebab-case strings")
    if not 0 < damping < 1 or not 1 <= max_iterations <= 1000 or tolerance <= 0:
        raise QueryError("invalid PPR convergence policy")
    relations = set(edge_types) if edge_types is not None else set(DEFAULT_SEMANTIC_RELATIONS)
    if edge_types is None and include_taxonomy:
        relations.add("contains")
    allowed_types = set(node_types or [])
    nodes = {
        node_id: node
        for node_id, node in view.nodes.items()
        if (not allowed_types or node.get("type") in allowed_types)
        and _allowed(node, include_stale=include_stale, include_orphaned=include_orphaned)
    }
    positive: dict[str, float] = {}
    for node_id, raw_weight in seeds.items():
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError):
            continue
        if node_id in nodes and math.isfinite(weight) and weight > 0:
            positive[str(node_id)] = weight
    if not positive:
        raise QueryError("PPR requires at least one positive graph seed")
    total = sum(positive.values())
    reset = {node_id: positive.get(node_id, 0.0) / total for node_id in nodes}
    adjacency: dict[str, dict[str, float]] = {node_id: {} for node_id in nodes}
    weights = {"prerequisite-for": 1.0, "implies": 1.0, "generalizes": 0.9, "derived-from": 0.9, "contrasts-with": 0.7, "contains": 0.3}
    edge_count = 0
    for edge in view.edges:
        source_id, target_id, relation = str(edge["source"]), str(edge["target"]), str(edge["relation"])
        if source_id not in nodes or target_id not in nodes or relation not in relations or not _edge_allowed(edge, include_stale=include_stale):
            continue
        pairs: set[tuple[str, str]] = set()
        if normalized_direction in {"outgoing", "both"}:
            pairs.add((source_id, target_id))
        if normalized_direction in {"incoming", "both"}:
            pairs.add((target_id, source_id))
        if relation == "contrasts-with":
            pairs.update({(source_id, target_id), (target_id, source_id)})
        for left, right in pairs:
            adjacency[left][right] = adjacency[left].get(right, 0.0) + weights.get(
                relation, 1.0
            )
        edge_count += 1
    reachable_seed: dict[str, str] = {}
    reachability: deque[str] = deque()
    for seed_id in sorted(positive):
        reachable_seed[seed_id] = seed_id
        reachability.append(seed_id)
    while reachability:
        current = reachability.popleft()
        for neighbor in sorted(adjacency[current]):
            if neighbor in reachable_seed:
                continue
            reachable_seed[neighbor] = reachable_seed[current]
            reachability.append(neighbor)
    scores = dict(reset)
    iterations = 0
    converged = False
    for iterations in range(1, max_iterations + 1):
        next_scores = {node_id: (1 - damping) * reset[node_id] for node_id in nodes}
        dangling = 0.0
        for source_id, score in scores.items():
            outgoing = adjacency[source_id]
            if not outgoing:
                dangling += score
                continue
            weight_total = sum(outgoing.values())
            for target_id, weight in outgoing.items():
                next_scores[target_id] += damping * score * weight / weight_total
        if dangling:
            for node_id, reset_weight in reset.items():
                next_scores[node_id] += damping * dangling * reset_weight
        delta = sum(abs(next_scores[node_id] - scores.get(node_id, 0.0)) for node_id in nodes)
        scores = next_scores
        if delta <= tolerance:
            converged = True
            break
    ranked = sorted(
        ((node_id, score) for node_id, score in scores.items() if score > 0.0),
        key=lambda item: (-item[1], item[0]),
    )[:limit]
    return {
        "namespace": namespace,
        "seeds": dict(sorted(positive.items())),
        "policy": {
            "damping": damping,
            "max_iterations": max_iterations,
            "tolerance": tolerance,
            "edge_types": sorted(relations),
            "direction": direction,
            "include_taxonomy": include_taxonomy,
        },
        "iterations": iterations,
        "converged": converged,
        "trusted_edge_count": edge_count,
        "results": [
            {
                "rank": rank,
                "score": round(score, 15),
                "seed_ids": [reachable_seed[node_id]],
                "node": copy.deepcopy(nodes[node_id]),
            }
            for rank, (node_id, score) in enumerate(ranked, start=1)
        ],
    }


def estimate_tokens(value: Any) -> int:
    """Return a provider-neutral upper bound using canonical UTF-8 bytes."""
    return max(1, len(canonical_json(value).encode("utf-8")))


def finalize_token_estimate(value: dict[str, Any]) -> int:
    """Set ``budget.estimated_tokens`` to its exact serialized fixed point."""
    budget = value.get("budget")
    if not isinstance(budget, dict) or "estimated_tokens" not in budget:
        raise QueryError("token estimate requires budget.estimated_tokens")
    while True:
        estimated = estimate_tokens(value)
        if budget["estimated_tokens"] == estimated:
            return estimated
        budget["estimated_tokens"] = estimated


def context(
    source: GraphView | Path,
    node_ids: list[str],
    *,
    namespace: str = "personal",
    node_types: list[str] | None = None,
    edge_types: list[str] | None = None,
    include_stale: bool = False,
    include_orphaned: bool = False,
    token_budget: int = 6000,
    alignments: Path | None = None,
) -> dict[str, Any]:
    """Pack selected nodes and their internal evidence under a strict budget."""
    view = _view(source, alignments)
    _namespace(view, namespace)
    if isinstance(token_budget, bool) or not isinstance(token_budget, int) or token_budget < 1:
        raise QueryError("token_budget must be positive")
    allowed_types = set(node_types or [])
    allowed_relations = None if edge_types is None else set(edge_types)
    selected = list(
        dict.fromkeys(
            node_id
            for node_id in node_ids
            if node_id in view.nodes
            and (not allowed_types or view.nodes[node_id].get("type") in allowed_types)
            and _allowed(
                view.nodes[node_id],
                include_stale=include_stale,
                include_orphaned=include_orphaned,
            )
        )
    )
    bundle: dict[str, Any] = {
        "schema": CONTEXT_SCHEMA,
        "namespace": namespace,
        "snapshot_sha256": view.snapshot["snapshot_sha256"],
        "graph_sha256": view.snapshot["graph"]["sha256"],
        "nodes": [],
        "edges": [],
        "references": [],
        "omissions": [],
        "budget": {"token_budget": token_budget, "estimated_tokens": 0},
    }
    for node_id in selected:
        candidate = copy.deepcopy(bundle)
        candidate["nodes"].append(copy.deepcopy(view.nodes[node_id]))
        if finalize_token_estimate(candidate) <= token_budget:
            bundle = candidate
        else:
            bundle["omissions"].append({"kind": "node", "id": node_id, "reason": "token-budget"})
    included = {str(node["id"]) for node in bundle["nodes"]}
    evidence: list[tuple[str, dict[str, Any]]] = []
    evidence.extend(
        ("edge", edge)
        for edge in view.edges
        if edge["source"] in included
        and edge["target"] in included
        and (allowed_relations is None or edge.get("relation") in allowed_relations)
        and _edge_allowed(edge, include_stale=include_stale)
    )
    evidence.extend(("reference", ref) for ref in view.references if ref["target"] in included)
    for kind, record in evidence:
        candidate = copy.deepcopy(bundle)
        candidate[f"{kind}s"].append(copy.deepcopy(record))
        if finalize_token_estimate(candidate) <= token_budget:
            bundle = candidate
        else:
            identifier = str(record.get("id") or f"{record.get('source')}:{record.get('relation')}:{record.get('target')}")
            bundle["omissions"].append({"kind": kind, "id": identifier, "reason": "token-budget"})
    while bundle["omissions"] and finalize_token_estimate(bundle) > token_budget:
        bundle["omissions"].pop()
    if finalize_token_estimate(bundle) > token_budget:
        raise QueryError("budget-too-small after context packing")
    return bundle


def _mapping_freshness(
    mapping: Mapping[str, Any],
    candidate: Mapping[str, Any],
    target: Mapping[str, Any] | None,
) -> dict[str, Any]:
    subject = mapping.get("subject") or {}
    object_ = mapping.get("object") or {}
    subject_expected = str(subject.get("node_sha256", ""))
    object_expected = str(object_.get("node_sha256", ""))
    subject_actual = node_fingerprint(dict(candidate))
    object_actual = node_fingerprint(dict(target)) if target is not None else ""
    return {
        "subject_fresh": not subject_expected or subject_expected == subject_actual,
        "object_fresh": target is not None
        and (not object_expected or object_expected == object_actual),
        "subject_expected": subject_expected,
        "subject_actual": subject_actual,
        "object_expected": object_expected,
        "object_actual": object_actual,
    }


def _candidate_registry_decisions(
    view: GraphView,
    candidate: Mapping[str, Any],
    candidate_namespace: str,
) -> tuple[str | None, set[str], list[dict[str, Any]]]:
    """Return fresh reviewed identity/negative decisions for one candidate."""
    reviewed_exact_target: str | None = None
    rejected_target_ids: set[str] = set()
    evidence: list[dict[str, Any]] = []
    for mapping in view.alignments["mappings"]:
        subject = mapping["subject"]
        object_ = mapping["object"]
        if (
            subject["namespace"] != candidate_namespace
            or subject["node_id"] != candidate.get("id")
            or object_["namespace"] != view.snapshot["namespace"]
        ):
            continue
        target_id = str(object_["node_id"])
        freshness = _mapping_freshness(
            mapping, candidate, view.nodes.get(target_id)
        )
        decision_fresh = bool(
            freshness["subject_fresh"] and freshness["object_fresh"]
        )
        identity_authority = bool(
            decision_fresh
            and mapping["status"] == "reviewed"
            and mapping["predicate"] == "exact-match"
        )
        record = {
            "kind": "alignment-registry",
            "mapping_id": mapping["id"],
            "predicate": mapping["predicate"],
            "status": mapping["status"],
            "target_id": target_id,
            "freshness": freshness,
            "decision_fresh": decision_fresh,
            "identity_authority": identity_authority,
            "mapping_justification": copy.deepcopy(
                mapping.get("mapping_justification") or []
            ),
            "evidence": copy.deepcopy(mapping.get("evidence") or []),
        }
        evidence.append(record)
        if identity_authority:
            reviewed_exact_target = target_id
        if decision_fresh and (
            (
                mapping["status"] == "reviewed"
                and mapping["predicate"] == "different-from"
            )
            or (
                mapping["status"] == "rejected"
                and mapping["predicate"] == "exact-match"
            )
        ):
            rejected_target_ids.add(target_id)
    evidence.sort(key=lambda item: str(item["mapping_id"]))
    return reviewed_exact_target, rejected_target_ids, evidence


def _identity_resolution_evidence(
    *,
    probe: str,
    probe_source: str,
    status: str,
    candidate_ids: list[str],
    rejected_target_ids: set[str],
) -> dict[str, Any]:
    rejected = sorted(set(candidate_ids).intersection(rejected_target_ids))
    return {
        "kind": "identity-resolution",
        "probe": probe,
        "probe_source": probe_source,
        "status": status,
        "candidate_ids": candidate_ids,
        "rejected_target_ids": rejected,
        "identity_authority": bool(set(candidate_ids) - set(rejected)),
    }


def _candidate_identity(
    view: GraphView,
    candidate: Mapping[str, Any],
    reviewed_exact_target: str | None,
    rejected_target_ids: set[str],
) -> tuple[str, list[str], list[dict[str, Any]]]:
    """Resolve identity from reviewed mappings or the bounded authority probes."""
    if reviewed_exact_target is not None:
        return (
            "matched",
            [reviewed_exact_target],
            [
                {
                    "kind": "reviewed-exact-alignment",
                    "target_id": reviewed_exact_target,
                    "identity_authority": True,
                }
            ],
        )

    properties = candidate.get("properties")
    properties = properties if isinstance(properties, Mapping) else {}
    evidence: list[dict[str, Any]] = []

    # Explicit target IDs and machine IDs are exact ID probes.  They never use
    # label or alias fallback, and a fresh reviewed negative overrides them.
    for probe, source in (
        (str(properties.get("target_id", "")).strip(), "explicit-target-id"),
        (str(candidate.get("id", "")).strip(), "id"),
    ):
        if not probe:
            continue
        raw_ids = [probe] if probe in view.nodes else []
        evidence.append(
            _identity_resolution_evidence(
                probe=probe,
                probe_source=source,
                status="exact" if raw_ids else "missing",
                candidate_ids=raw_ids,
                rejected_target_ids=rejected_target_ids,
            )
        )
        accepted_ids = [
            target_id
            for target_id in raw_ids
            if target_id not in rejected_target_ids
        ]
        if accepted_ids:
            return "matched", accepted_ids, evidence

    # Only the candidate's canonical label participates in name identity.
    # Candidate-declared aliases are retrieval evidence below, never identity
    # authority.  Target aliases are authored global aliases in the target
    # graph, so they remain valid canonical-label resolutions.
    label = str(candidate.get("label", "")).strip()
    if label:
        normalized = normalize_text(label)
        if normalized in view.labels:
            raw_ids = list(view.labels[normalized])
            match_kind = "label"
        elif normalized in view.aliases:
            raw_ids = list(view.aliases[normalized])
            match_kind = "alias"
        else:
            raw_ids = []
            match_kind = None
        status = (
            "exact"
            if len(raw_ids) == 1 and match_kind == "label"
            else "alias"
            if len(raw_ids) == 1
            else "ambiguous"
            if raw_ids
            else "missing"
        )
        evidence.append(
            _identity_resolution_evidence(
                probe=label,
                probe_source="label",
                status=status,
                candidate_ids=raw_ids,
                rejected_target_ids=rejected_target_ids,
            )
        )
        accepted_ids = sorted(set(raw_ids) - rejected_target_ids)
        if len(accepted_ids) == 1:
            return "matched", accepted_ids, evidence
        if len(accepted_ids) > 1:
            return "ambiguous", accepted_ids, evidence
    return "unmatched", [], evidence


def align(
    source: GraphView | Path,
    candidate_snapshot: dict[str, Any],
    *,
    target_namespace: str = "personal",
    limit_per_node: int = 10,
    alignments: Path | None = None,
) -> dict[str, Any]:
    view = _view(source, alignments)
    _namespace(view, target_namespace)
    validation = validate_agent_snapshot(candidate_snapshot)
    if validation["namespace"] == target_namespace:
        raise QueryError("candidate and target namespaces must be distinct")
    limit_per_node = _limit(limit_per_node)
    scoped_aliases = extract_scoped_aliases(candidate_snapshot)
    scoped_by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for alias in scoped_aliases["aliases"]:
        scoped_by_node[str(alias["node_id"])].append(alias)
    rows: list[dict[str, Any]] = []
    for candidate in sorted(candidate_snapshot["nodes"], key=lambda item: str(item["id"])):
        reviewed_exact_target, rejected_ids, registry_evidence = (
            _candidate_registry_decisions(
                view, candidate, validation["namespace"]
            )
        )
        state, ids, evidence = _candidate_identity(
            view, candidate, reviewed_exact_target, rejected_ids
        )
        ranked: dict[str, dict[str, Any]] = {}
        for node_id in ids:
            ranked[node_id] = {
                "target": {
                    "namespace": target_namespace,
                    "id": node_id,
                    "label": view.nodes[node_id].get("label", ""),
                    "type": view.nodes[node_id].get("type", ""),
                },
                "score": 1000.0,
                "identity_authority": state == "matched",
                "signals": copy.deepcopy([*evidence, *registry_evidence]),
            }
        properties = candidate.get("properties")
        properties = properties if isinstance(properties, Mapping) else {}
        retrieval_probes = [
            (str(candidate.get("label", "")), "label"),
            *[
                (str(alias), "candidate-alias")
                for alias in properties.get("aliases", [])
            ],
        ]
        seen_retrieval_probes: set[str] = set()
        for probe, probe_source in retrieval_probes:
            normalized_probe = normalize_text(probe)
            if not normalized_probe or normalized_probe in seen_retrieval_probes:
                continue
            seen_retrieval_probes.add(normalized_probe)
            for result in search(
                view,
                probe,
                namespace=target_namespace,
                limit=min(MAX_LIMIT, max(limit_per_node * 2, 10)),
            ):
                node_id = str(result["node"]["id"])
                if node_id in rejected_ids:
                    continue
                signal = {
                    "kind": "lexical-candidate",
                    "probe": probe,
                    "probe_source": probe_source,
                    "rank": result["rank"],
                    "score": float(result["reasons"][0]["score"]),
                    "identity_authority": False,
                }
                record = ranked.get(node_id)
                if record is None:
                    ranked[node_id] = {
                        "target": {
                            "namespace": target_namespace,
                            "id": node_id,
                            "label": result["node"].get("label", ""),
                            "type": result["node"].get("type", ""),
                        },
                        "score": signal["score"],
                        "identity_authority": False,
                        "signals": [signal],
                    }
                elif signal not in record["signals"]:
                    record["signals"].append(signal)
                    if not record["identity_authority"]:
                        record["score"] = max(record["score"], signal["score"])
        candidates = sorted(ranked.values(), key=lambda item: (-item["score"], item["target"]["id"]))[:limit_per_node]
        rows.append(
            {
                "candidate_id": str(candidate["id"]),
                "status": state,
                "matched_target_id": ids[0] if state == "matched" else None,
                "candidates": candidates,
                "identity_evidence": copy.deepcopy(evidence),
                "registry_evidence": copy.deepcopy(registry_evidence),
                "rejected_target_ids": sorted(rejected_ids),
            }
        )
    proposals: list[dict[str, Any]] = []
    for row in rows:
        if row["status"] == "matched":
            continue
        candidate = next(
            node
            for node in candidate_snapshot["nodes"]
            if node["id"] == row["candidate_id"]
        )
        for ranked in row["candidates"][:3]:
            target = view.nodes[ranked["target"]["id"]]
            proposal = {
                "subject": {
                    "namespace": validation["namespace"],
                    "node_id": row["candidate_id"],
                    "node_sha256": node_fingerprint(candidate),
                },
                "predicate": "exact-match",
                "object": {
                    "namespace": target_namespace,
                    "node_id": target["id"],
                    "node_sha256": node_fingerprint(target),
                },
                "status": "proposed",
                "mapping_justification": sorted(
                    {
                        str(signal["kind"])
                        for signal in ranked["signals"]
                        if signal.get("kind")
                    }
                ),
                "evidence": copy.deepcopy(ranked["signals"]),
                "scores": {"rank_score": ranked["score"]},
            }
            proposal["id"] = mapping_id(proposal)
            proposals.append(proposal)
    normalized_results = []
    for row in rows:
        status = (
            "exact"
            if row["status"] == "matched"
            else "ambiguous"
            if row["status"] == "ambiguous"
            else "candidate"
            if row["candidates"]
            else "unresolved"
        )
        normalized_results.append(
            {
                "candidate": {
                    "namespace": validation["namespace"],
                    "id": row["candidate_id"],
                },
                "status": status,
                "identity_target_id": row["matched_target_id"],
                "candidates": copy.deepcopy(row["candidates"]),
                "scoped_aliases": copy.deepcopy(
                    scoped_by_node.get(row["candidate_id"], [])
                ),
                "registry_evidence": copy.deepcopy(row["registry_evidence"]),
                "rejected_target_ids": copy.deepcopy(
                    row["rejected_target_ids"]
                ),
            }
        )
    summary = {
        name: sum(row["status"] == name for row in normalized_results)
        for name in ("exact", "candidate", "ambiguous", "unresolved")
    }
    summary["total"] = len(normalized_results)
    report = {
        "schema": ALIGNMENT_REPORT_SCHEMA,
        "candidate_namespace": validation["namespace"],
        "target_namespace": target_namespace,
        "candidate_snapshot_sha256": validation["snapshot_sha256"],
        "target_snapshot_sha256": view.snapshot["snapshot_sha256"],
        "alignment_sha256": sha256_json(view.alignments),
        "alignments": rows,
        "candidate": {
            "namespace": validation["namespace"],
            "snapshot_sha256": validation["snapshot_sha256"],
        },
        "target": {
            "namespace": target_namespace,
            "snapshot_sha256": view.snapshot["snapshot_sha256"],
        },
        "scoped_aliases": scoped_aliases,
        "results": normalized_results,
        "proposals": sorted(proposals, key=lambda item: item["id"]),
        "summary": summary,
    }
    report["report_sha256"] = sha256_json(report)
    return report


def compare(
    source: GraphView | Path,
    candidate_snapshot: dict[str, Any],
    *,
    target_namespace: str = "personal",
    alignments: Path | None = None,
) -> dict[str, Any]:
    view = _view(source, alignments)
    report = align(view, candidate_snapshot, target_namespace=target_namespace)
    mapping = {
        row["candidate_id"]: row["matched_target_id"]
        for row in report["alignments"]
        if row["status"] == "matched"
    }
    target_edges = {(edge["source"], edge["relation"], edge["target"]) for edge in view.edges}
    edge_rows: list[dict[str, Any]] = []
    for edge in sorted(candidate_snapshot["edges"], key=lambda item: (item["source"], item["relation"], item["target"])):
        translated = (mapping.get(edge["source"]), edge["relation"], mapping.get(edge["target"]))
        if translated[0] is not None and translated[2] is not None and translated in target_edges:
            status = "present"
        else:
            status = "missing"
        edge_rows.append({"candidate_edge": copy.deepcopy(edge), "target_edge": {"source": translated[0], "relation": translated[1], "target": translated[2]}, "status": status})
    result = {
        "schema": COMPARISON_SCHEMA,
        "candidate_namespace": candidate_snapshot["namespace"],
        "target_namespace": target_namespace,
        "alignment_sha256": report["alignment_sha256"],
        "nodes": report["alignments"],
        "edges": edge_rows,
        "summary": {
            "matched": sum(row["status"] == "matched" for row in report["alignments"]),
            "ambiguous": sum(row["status"] == "ambiguous" for row in report["alignments"]),
            "unmatched": sum(row["status"] == "unmatched" for row in report["alignments"]),
            "present_edges": sum(row["status"] == "present" for row in edge_rows),
            "missing_edges": sum(row["status"] == "missing" for row in edge_rows),
        },
    }
    result["candidate"] = {
        "namespace": candidate_snapshot["namespace"],
        "snapshot_sha256": candidate_snapshot["snapshot_sha256"],
        "graph_sha256": candidate_snapshot["graph"]["sha256"],
    }
    result["target"] = {
        "namespace": target_namespace,
        "snapshot_sha256": view.snapshot["snapshot_sha256"],
        "graph_sha256": view.snapshot["graph"]["sha256"],
    }
    result["results"] = [
        {
            "candidate": {
                "namespace": candidate_snapshot["namespace"],
                "id": row["candidate_id"],
            },
            "status": row["status"],
            "identity_target_id": row["matched_target_id"],
            "candidates": copy.deepcopy(row["candidates"]),
            "registry_evidence": copy.deepcopy(row["registry_evidence"]),
            "rejected_target_ids": copy.deepcopy(row["rejected_target_ids"]),
        }
        for row in report["alignments"]
    ]
    result["alignment_report_sha256"] = report["report_sha256"]
    result["comparison_sha256"] = sha256_json(result)
    return result


def propose(
    source: GraphView | Path,
    candidate_snapshot: dict[str, Any],
    *,
    target_namespace: str = "personal",
    target_authority: str | None = None,
    alignments: Path | None = None,
) -> dict[str, Any]:
    comparison = compare(source, candidate_snapshot, target_namespace=target_namespace, alignments=alignments)
    comparison_by_id = {
        str(row["candidate_id"]): row for row in comparison["nodes"]
    }
    additions = [
        copy.deepcopy(node)
        for node in candidate_snapshot["nodes"]
        if comparison_by_id[str(node["id"])]["status"] == "unmatched"
    ]
    blockers = [
        {
            "code": "source-marker-required",
            "candidate_id": node["id"],
            "message": "Add and review an authority marker before applying node curation.",
        }
        for node in additions
    ]
    blockers.extend(
        {
            "code": "identity-review-required",
            "candidate_id": row["candidate_id"],
            "message": "Ambiguous identity requires a reviewed decision.",
        }
        for row in comparison["nodes"]
        if row["status"] == "ambiguous"
    )
    delta_preview = {
        "schema": DELTA_SCHEMA,
        "remove_nodes": [],
        "nodes": [],
        "edges": [],
        "remove_edges": [],
    }
    proposal = {
        "schema": PROPOSAL_SCHEMA,
        "candidate_namespace": candidate_snapshot["namespace"],
        "target_namespace": target_namespace,
        "target_authority": target_authority,
        "candidate_snapshot_sha256": candidate_snapshot["snapshot_sha256"],
        "comparison_sha256": comparison["comparison_sha256"],
        "alignment_sha256": comparison["alignment_sha256"],
        "operations": [
            {"action": "review-new-node", "node": node} for node in additions
        ],
        "review_required": True,
        "warnings": ["Proposal is read-only and must not be treated as committed identity."],
        "candidate": comparison["candidate"],
        "target": comparison["target"],
        "results": copy.deepcopy(comparison["results"]),
        "comparison_summary": comparison["summary"],
        "delta_preview": delta_preview,
        "blockers": blockers,
        "delta_ready": False,
        "fully_resolved": not blockers,
        "instructions": [
            "Review identity and source-marker operations.",
            "Author source-backed markers before applying any proposal.",
        ],
    }
    proposal["proposal_sha256"] = sha256_json(proposal)
    return proposal
