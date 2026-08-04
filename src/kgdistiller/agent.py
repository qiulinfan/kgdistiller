"""Derived, disposable Agent index and deterministic retrieval primitives."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import unicodedata
from collections import deque
from pathlib import Path
from typing import Any, Iterable, Protocol


SNAPSHOT_SCHEMA = "qlkg-agent-snapshot-v1"
INDEX_SCHEMA = "qlkg-agent-index-v1"
NAMESPACE_RE = re.compile(
    r"^[a-z0-9][a-z0-9._-]*(?::[a-z0-9][a-z0-9._-]*)*$"
)
QUERY_TERM_RE = re.compile(r"[\w-]+", re.UNICODE)
MAX_QUERY_LENGTH = 4096
MAX_QUERY_TERMS = 64
MAX_BATCH_CONCEPTS = 512
MAX_LIMIT = 500
MAX_GRAPH_DEPTH = 5
MAX_CONTEXT_BUDGET = 200_000
CONTEXT_SCHEMA = "qlkg-context-bundle-v1"
COMPARISON_SCHEMA = "qlkg-graph-comparison-v1"
PROPOSAL_SCHEMA = "qlkg-agent-proposal-v1"
DEFAULT_SEMANTIC_RELATIONS = {
    "prerequisite-for",
    "implies",
    "generalizes",
    "contrasts-with",
    "derived-from",
}


class AgentIndexError(ValueError):
    """Raised when an Agent snapshot or derived index is invalid."""


class EmbeddingProvider(Protocol):
    """Optional semantic candidate provider; never an identity authority."""

    name: str
    model: str
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


class RerankProvider(Protocol):
    """Optional provider for ordering already-retrieved candidates."""

    name: str
    model: str

    def rerank(
        self, query: str, candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        ...


class TokenEstimator(Protocol):
    """Optional tokenizer-specific budget estimator."""

    name: str

    def count(self, text: str) -> int:
        ...


class CandidateAnalyzer(Protocol):
    """Optional analyzer that may propose, but never commit, alignments."""

    name: str

    def compare(
        self,
        candidate: dict[str, Any],
        target: dict[str, Any],
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        ...


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def _json_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        if value.strip():
            yield value
    elif isinstance(value, dict):
        for key in sorted(value):
            yield from _json_strings(value[key])
    elif isinstance(value, list):
        for item in value:
            yield from _json_strings(item)


def _validated_snapshot(snapshot: dict[str, Any]) -> tuple[str, dict[str, int]]:
    if snapshot.get("schema") != SNAPSHOT_SCHEMA:
        raise AgentIndexError(f"unsupported snapshot schema: {snapshot.get('schema')!r}")
    namespace = str(snapshot.get("namespace", ""))
    if not NAMESPACE_RE.fullmatch(namespace):
        raise AgentIndexError(f"invalid snapshot namespace: {namespace!r}")
    claimed_digest = str(snapshot.get("snapshot_sha256", ""))
    digest_payload = dict(snapshot)
    digest_payload.pop("snapshot_sha256", None)
    if not re.fullmatch(r"[0-9a-f]{64}", claimed_digest):
        raise AgentIndexError("snapshot has no valid snapshot_sha256")
    if sha256_json(digest_payload) != claimed_digest:
        raise AgentIndexError("snapshot digest does not match its content")

    graph = snapshot.get("graph") or {}
    graph_sha256 = str(graph.get("sha256", ""))
    if graph.get("schema") != "qlkg-v2" or not re.fullmatch(
        r"[0-9a-f]{64}", graph_sha256
    ):
        raise AgentIndexError("snapshot has no valid qlkg-v2 graph identity")
    counts = {
        "nodes": len(snapshot.get("nodes") or []),
        "edges": len(snapshot.get("edges") or []),
        "references": len(snapshot.get("references") or []),
    }
    claimed_counts = graph.get("counts") or {}
    if any(int(claimed_counts.get(key, -1)) != value for key, value in counts.items()):
        raise AgentIndexError("snapshot counts do not match its records")
    return namespace, counts


def _node_payload(row: sqlite3.Row) -> dict[str, Any]:
    node: dict[str, Any] = {
        "id": row["id"],
        "type": row["type"],
        "label": row["label"],
        "text": row["text"],
        "properties": json.loads(row["properties"]),
    }
    provenance = json.loads(row["provenance"])
    entry = json.loads(row["entry"])
    if provenance:
        node["provenance"] = provenance
    if entry:
        node["entry"] = entry
    return node


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE index_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE nodes (
            namespace TEXT NOT NULL,
            id TEXT NOT NULL,
            type TEXT NOT NULL,
            label TEXT NOT NULL,
            text TEXT NOT NULL,
            aliases TEXT NOT NULL,
            entry TEXT NOT NULL,
            properties TEXT NOT NULL,
            provenance TEXT NOT NULL,
            curation_status TEXT NOT NULL,
            source_status TEXT NOT NULL,
            PRIMARY KEY (namespace, id)
        );
        CREATE TABLE node_names (
            namespace TEXT NOT NULL,
            node_id TEXT NOT NULL,
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            kind TEXT NOT NULL,
            PRIMARY KEY (namespace, node_id, normalized_name, kind),
            FOREIGN KEY (namespace, node_id) REFERENCES nodes(namespace, id)
        );
        CREATE INDEX node_names_lookup
            ON node_names(namespace, normalized_name, kind, node_id);
        CREATE VIRTUAL TABLE node_fts USING fts5(
            namespace UNINDEXED,
            id UNINDEXED,
            label,
            aliases,
            text,
            tokenize='unicode61'
        );
        CREATE TABLE edges (
            namespace TEXT NOT NULL,
            edge_id TEXT NOT NULL,
            source TEXT NOT NULL,
            relation TEXT NOT NULL,
            target TEXT NOT NULL,
            evidence TEXT NOT NULL,
            confidence TEXT NOT NULL,
            origin TEXT NOT NULL,
            curation_status TEXT NOT NULL,
            payload TEXT NOT NULL,
            PRIMARY KEY (namespace, edge_id),
            FOREIGN KEY (namespace, source) REFERENCES nodes(namespace, id),
            FOREIGN KEY (namespace, target) REFERENCES nodes(namespace, id)
        );
        CREATE INDEX edges_outgoing
            ON edges(namespace, source, relation, target);
        CREATE INDEX edges_incoming
            ON edges(namespace, target, relation, source);
        CREATE TABLE refs (
            namespace TEXT NOT NULL,
            ref_id TEXT NOT NULL,
            target TEXT NOT NULL,
            authority TEXT NOT NULL,
            line INTEGER NOT NULL,
            context TEXT,
            payload TEXT NOT NULL,
            PRIMARY KEY (namespace, ref_id)
        );
        CREATE INDEX refs_target
            ON refs(namespace, target, authority, line);
        CREATE TABLE embeddings (
            namespace TEXT NOT NULL,
            node_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            content_sha256 TEXT NOT NULL,
            vector BLOB NOT NULL,
            PRIMARY KEY (namespace, node_id, provider, model),
            FOREIGN KEY (namespace, node_id) REFERENCES nodes(namespace, id)
        );
        """
    )


def write_agent_index(path: Path, snapshot: dict[str, Any]) -> None:
    """Atomically rebuild a provider-neutral SQLite index from a snapshot."""
    namespace, counts = _validated_snapshot(snapshot)
    nodes = list(snapshot.get("nodes") or [])
    node_ids = {str(node.get("id", "")) for node in nodes}
    if "" in node_ids or len(node_ids) != len(nodes):
        raise AgentIndexError("snapshot contains an empty or duplicate node ID")
    for edge in snapshot.get("edges") or []:
        if edge.get("source") not in node_ids or edge.get("target") not in node_ids:
            raise AgentIndexError("snapshot contains a dangling edge")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary.unlink()
    connection = sqlite3.connect(temporary)
    try:
        _create_schema(connection)
        metadata = {
            "schema": INDEX_SCHEMA,
            "snapshot_schema": SNAPSHOT_SCHEMA,
            "snapshot_sha256": snapshot["snapshot_sha256"],
            "graph_schema": snapshot["graph"]["schema"],
            "graph_sha256": snapshot["graph"]["sha256"],
            "namespace": namespace,
            "counts": counts,
            "retrieval_lanes": ["exact", "fts", "graph"],
            "provider_config_sha256": None,
            "providers": {
                "embedding": None,
                "reranker": None,
                "token_estimator": "conservative-char-v1",
                "candidate_analyzer": None,
            },
        }
        connection.executemany(
            "INSERT INTO index_meta(key, value) VALUES (?, ?)",
            ((key, canonical_json(value)) for key, value in sorted(metadata.items())),
        )
        for node in sorted(nodes, key=lambda item: str(item["id"])):
            node_id = str(node["id"])
            properties = dict(node.get("properties") or {})
            aliases = list(
                dict.fromkeys(
                    str(alias).strip()
                    for alias in properties.get("aliases", [])
                    if str(alias).strip()
                )
            )
            entry = node.get("entry") if isinstance(node.get("entry"), dict) else {}
            text_parts = [str(node.get("text", "")), *_json_strings(entry)]
            search_text = "\n".join(dict.fromkeys(part for part in text_parts if part.strip()))
            connection.execute(
                "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    namespace,
                    node_id,
                    str(node.get("type", "")),
                    str(node.get("label", "")),
                    str(node.get("text", "")),
                    canonical_json(aliases),
                    canonical_json(entry),
                    canonical_json(properties),
                    canonical_json(node.get("provenance") or {}),
                    str(properties.get("curation_status", "")),
                    str(properties.get("source_status", "")),
                ),
            )
            names = [(node_id, "id"), (str(node.get("label", "")), "label")]
            names.extend((alias, "alias") for alias in aliases)
            seen_names: set[tuple[str, str]] = set()
            for name, kind in names:
                normalized = normalize_name(name)
                key = (normalized, kind)
                if not normalized or key in seen_names:
                    continue
                seen_names.add(key)
                connection.execute(
                    "INSERT INTO node_names VALUES (?, ?, ?, ?, ?)",
                    (namespace, node_id, name, normalized, kind),
                )
            connection.execute(
                "INSERT INTO node_fts(namespace, id, label, aliases, text) VALUES (?, ?, ?, ?, ?)",
                (
                    namespace,
                    node_id,
                    str(node.get("label", "")),
                    " ".join(aliases),
                    search_text,
                ),
            )
        for edge in sorted(
            snapshot.get("edges") or [],
            key=lambda item: (
                str(item.get("source", "")),
                str(item.get("relation", "")),
                str(item.get("target", "")),
            ),
        ):
            edge_id = str(edge.get("id") or sha256_json(edge)[:24])
            connection.execute(
                "INSERT INTO edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    namespace,
                    edge_id,
                    str(edge["source"]),
                    str(edge["relation"]),
                    str(edge["target"]),
                    str(edge.get("evidence", "")),
                    str(edge.get("confidence", "")),
                    str(edge.get("origin", "")),
                    str(edge.get("curation_status", "")),
                    canonical_json(edge),
                ),
            )
        for reference in sorted(
            snapshot.get("references") or [],
            key=lambda item: (
                str(item.get("authority", "")),
                int(item.get("line", 0)),
                str(item.get("target", "")),
                str(item.get("id", "")),
            ),
        ):
            ref_id = str(reference.get("id") or sha256_json(reference)[:24])
            connection.execute(
                "INSERT INTO refs VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    namespace,
                    ref_id,
                    str(reference["target"]),
                    str(reference.get("authority", "")),
                    int(reference.get("line", 0)),
                    str(reference.get("context")) if reference.get("context") else None,
                    canonical_json(reference),
                ),
            )
        connection.commit()
    except BaseException:
        connection.close()
        temporary.unlink(missing_ok=True)
        raise
    else:
        connection.close()
        os.replace(temporary, path)


def _connect(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise AgentIndexError(f"Agent index does not exist: {path}")
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    schema_row = connection.execute(
        "SELECT value FROM index_meta WHERE key = 'schema'"
    ).fetchone()
    schema = json.loads(schema_row[0]) if schema_row else None
    if schema != INDEX_SCHEMA:
        connection.close()
        raise AgentIndexError(f"unsupported Agent index schema: {schema!r}")
    return connection


def index_status(path: Path) -> dict[str, Any]:
    connection = _connect(path)
    try:
        metadata = {
            row["key"]: json.loads(row["value"])
            for row in connection.execute("SELECT key, value FROM index_meta ORDER BY key")
        }
        metadata["path"] = str(path)
        return metadata
    finally:
        connection.close()


def _validate_namespace(namespace: str) -> None:
    if not NAMESPACE_RE.fullmatch(namespace):
        raise AgentIndexError(f"invalid namespace: {namespace!r}")


def _node_by_id(
    connection: sqlite3.Connection, namespace: str, node_id: str
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM nodes WHERE namespace = ? AND id = ?",
        (namespace, node_id),
    ).fetchone()
    return _node_payload(row) if row else None


def resolve_concepts(
    path: Path,
    concepts: list[str],
    *,
    namespace: str = "personal",
) -> list[dict[str, Any]]:
    _validate_namespace(namespace)
    if len(concepts) > MAX_BATCH_CONCEPTS:
        raise AgentIndexError(f"concept batch exceeds {MAX_BATCH_CONCEPTS}")
    connection = _connect(path)
    try:
        results: list[dict[str, Any]] = []
        for concept in concepts:
            raw = str(concept).strip()
            if not raw:
                results.append({"query": str(concept), "status": "missing", "matches": []})
                continue
            direct = _node_by_id(connection, namespace, raw)
            if direct is not None:
                results.append(
                    {
                        "query": raw,
                        "status": "exact",
                        "match_kind": "id",
                        "matches": [direct],
                    }
                )
                continue
            rows = connection.execute(
                """
                SELECT nn.node_id, nn.kind
                FROM node_names AS nn
                WHERE nn.namespace = ? AND nn.normalized_name = ?
                ORDER BY CASE nn.kind WHEN 'label' THEN 0 ELSE 1 END, nn.node_id
                """,
                (namespace, normalize_name(raw)),
            ).fetchall()
            matches: list[dict[str, Any]] = []
            kinds: dict[str, str] = {}
            for row in rows:
                node_id = str(row["node_id"])
                if node_id in kinds:
                    continue
                kinds[node_id] = str(row["kind"])
                node = _node_by_id(connection, namespace, node_id)
                if node is not None:
                    matches.append(node)
            if not matches:
                results.append({"query": raw, "status": "missing", "matches": []})
            elif len(matches) > 1:
                results.append({"query": raw, "status": "ambiguous", "matches": matches})
            else:
                kind = kinds[matches[0]["id"]]
                results.append(
                    {
                        "query": raw,
                        "status": "exact" if kind == "label" else "alias",
                        "match_kind": kind,
                        "matches": matches,
                    }
                )
        return results
    finally:
        connection.close()


def _validated_limit(limit: int) -> int:
    if limit < 1 or limit > MAX_LIMIT:
        raise AgentIndexError(f"limit must be between 1 and {MAX_LIMIT}")
    return limit


def _fts_expression(query: str) -> str:
    if len(query) > MAX_QUERY_LENGTH:
        raise AgentIndexError(f"query exceeds {MAX_QUERY_LENGTH} characters")
    terms = QUERY_TERM_RE.findall(unicodedata.normalize("NFKC", query))
    terms = terms[:MAX_QUERY_TERMS]
    if not terms:
        raise AgentIndexError("query contains no searchable terms")
    return " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)


def search_index(
    path: Path,
    query: str,
    *,
    namespace: str = "personal",
    node_types: list[str] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    _validate_namespace(namespace)
    limit = _validated_limit(limit)
    expression = _fts_expression(query)
    allowed_types = sorted(set(node_types or []))
    connection = _connect(path)
    try:
        where = ["node_fts MATCH ?", "n.namespace = ?"]
        parameters: list[Any] = [expression, namespace]
        if allowed_types:
            where.append(f"n.type IN ({','.join('?' for _ in allowed_types)})")
            parameters.extend(allowed_types)
        parameters.append(limit)
        rows = connection.execute(
            f"""
            SELECT n.*, bm25(node_fts, 0.0, 0.0, 10.0, 6.0, 1.0) AS fts_score
            FROM node_fts
            JOIN nodes AS n
              ON n.namespace = node_fts.namespace AND n.id = node_fts.id
            WHERE {' AND '.join(where)}
            ORDER BY fts_score ASC, n.label ASC, n.id ASC
            LIMIT ?
            """,
            parameters,
        ).fetchall()
        results: list[dict[str, Any]] = []
        for rank, row in enumerate(rows, start=1):
            results.append(
                {
                    "rank": rank,
                    "node": _node_payload(row),
                    "reasons": [
                        {
                            "method": "fts",
                            "rank": rank,
                            "score": float(row["fts_score"]),
                        }
                    ],
                }
            )
        return results
    finally:
        connection.close()


def _edge_payload(row: sqlite3.Row) -> dict[str, Any]:
    return json.loads(row["payload"])


def _reference_payload(row: sqlite3.Row) -> dict[str, Any]:
    return json.loads(row["payload"])


def _node_allowed(
    node: dict[str, Any],
    *,
    include_stale: bool,
    include_orphaned: bool,
) -> bool:
    properties = node.get("properties") or {}
    if not include_stale and properties.get("curation_status") == "needs-review":
        return False
    if not include_orphaned and properties.get("source_status") == "orphaned":
        return False
    return True


def _edge_allowed(edge: dict[str, Any], *, include_stale: bool) -> bool:
    return include_stale or edge.get("curation_status") != "needs-review"


def get_index_node(
    path: Path,
    node_id: str,
    *,
    namespace: str = "personal",
) -> dict[str, Any]:
    """Return one node with direct typed edges, backlinks, and provenance."""
    _validate_namespace(namespace)
    connection = _connect(path)
    try:
        node = _node_by_id(connection, namespace, node_id)
        if node is None:
            raise AgentIndexError(f"unknown concept: {namespace}:{node_id}")
        incoming = [
            _edge_payload(row)
            for row in connection.execute(
                """
                SELECT payload FROM edges
                WHERE namespace = ? AND target = ?
                ORDER BY relation, source, edge_id
                """,
                (namespace, node_id),
            )
        ]
        outgoing = [
            _edge_payload(row)
            for row in connection.execute(
                """
                SELECT payload FROM edges
                WHERE namespace = ? AND source = ?
                ORDER BY relation, target, edge_id
                """,
                (namespace, node_id),
            )
        ]
        backlinks = [
            _reference_payload(row)
            for row in connection.execute(
                """
                SELECT payload FROM refs
                WHERE namespace = ? AND target = ?
                ORDER BY authority, line, ref_id
                """,
                (namespace, node_id),
            )
        ]
        return {
            "namespace": namespace,
            "node": node,
            "incoming": incoming,
            "outgoing": outgoing,
            "backlinks": backlinks,
        }
    finally:
        connection.close()


def _validate_traversal(
    *,
    direction: str,
    max_depth: int,
    limit: int,
) -> int:
    if direction not in {"incoming", "outgoing", "both"}:
        raise AgentIndexError(f"invalid graph direction: {direction!r}")
    if max_depth < 0 or max_depth > MAX_GRAPH_DEPTH:
        raise AgentIndexError(f"max_depth must be between 0 and {MAX_GRAPH_DEPTH}")
    return _validated_limit(limit)


def _neighbor_rows(
    connection: sqlite3.Connection,
    namespace: str,
    node_id: str,
    direction: str,
) -> list[tuple[sqlite3.Row, str, str]]:
    rows: list[tuple[sqlite3.Row, str, str]] = []
    if direction in {"outgoing", "both"}:
        rows.extend(
            (row, str(row["target"]), "outgoing")
            for row in connection.execute(
                """
                SELECT * FROM edges
                WHERE namespace = ? AND source = ?
                ORDER BY relation, target, edge_id
                """,
                (namespace, node_id),
            )
        )
    if direction in {"incoming", "both"}:
        rows.extend(
            (row, str(row["source"]), "incoming")
            for row in connection.execute(
                """
                SELECT * FROM edges
                WHERE namespace = ? AND target = ?
                ORDER BY relation, source, edge_id
                """,
                (namespace, node_id),
            )
        )
    return sorted(
        rows,
        key=lambda item: (
            str(item[0]["relation"]),
            item[1],
            item[2],
            str(item[0]["edge_id"]),
        ),
    )


def expand_index(
    path: Path,
    seed_ids: list[str],
    *,
    namespace: str = "personal",
    direction: str = "both",
    edge_types: list[str] | None = None,
    max_depth: int = 1,
    limit: int = 50,
    include_taxonomy: bool = False,
    include_stale: bool = False,
    include_orphaned: bool = False,
) -> dict[str, Any]:
    """Traverse a bounded, typed neighborhood with deterministic paths."""
    _validate_namespace(namespace)
    limit = _validate_traversal(direction=direction, max_depth=max_depth, limit=limit)
    seeds = list(dict.fromkeys(str(seed).strip() for seed in seed_ids if str(seed).strip()))
    if not seeds:
        raise AgentIndexError("at least one graph seed is required")
    if len(seeds) > 128:
        raise AgentIndexError("graph seed batch exceeds 128")
    relations = set(edge_types or DEFAULT_SEMANTIC_RELATIONS)
    if include_taxonomy:
        relations.add("contains")
    else:
        relations.discard("contains")

    connection = _connect(path)
    try:
        seed_nodes: dict[str, dict[str, Any]] = {}
        for seed in seeds:
            node = _node_by_id(connection, namespace, seed)
            if node is None:
                raise AgentIndexError(f"unknown graph seed: {namespace}:{seed}")
            seed_nodes[seed] = node

        queue: deque[tuple[str, int, list[dict[str, Any]]]] = deque(
            (seed, 0, []) for seed in seeds
        )
        visited: dict[str, tuple[int, list[dict[str, Any]]]] = {
            seed: (0, []) for seed in seeds
        }
        traversed_edges: dict[str, dict[str, Any]] = {}
        while queue and len(visited) < limit:
            current, depth, path_steps = queue.popleft()
            if depth >= max_depth:
                continue
            for row, neighbor_id, edge_direction in _neighbor_rows(
                connection, namespace, current, direction
            ):
                relation = str(row["relation"])
                if relation not in relations:
                    continue
                edge = _edge_payload(row)
                if not _edge_allowed(edge, include_stale=include_stale):
                    continue
                neighbor = _node_by_id(connection, namespace, neighbor_id)
                if neighbor is None or not _node_allowed(
                    neighbor,
                    include_stale=include_stale,
                    include_orphaned=include_orphaned,
                ):
                    continue
                edge_id = str(row["edge_id"])
                traversed_edges[edge_id] = edge
                if neighbor_id in visited:
                    continue
                step = {
                    "source": str(row["source"]),
                    "relation": relation,
                    "target": str(row["target"]),
                    "direction": edge_direction,
                }
                next_path = [*path_steps, step]
                visited[neighbor_id] = (depth + 1, next_path)
                queue.append((neighbor_id, depth + 1, next_path))
                if len(visited) >= limit:
                    break

        records: list[dict[str, Any]] = []
        for node_id, (depth, traversal_path) in visited.items():
            node = seed_nodes.get(node_id) or _node_by_id(connection, namespace, node_id)
            if node is None:
                continue
            records.append(
                {
                    "node": node,
                    "depth": depth,
                    "path": traversal_path,
                    "seed": node_id in seed_nodes,
                }
            )
        records.sort(
            key=lambda item: (
                int(item["depth"]),
                str(item["node"].get("label", "")),
                str(item["node"]["id"]),
            )
        )
        return {
            "namespace": namespace,
            "seeds": seeds,
            "policy": {
                "direction": direction,
                "edge_types": sorted(relations),
                "max_depth": max_depth,
                "limit": limit,
                "include_taxonomy": include_taxonomy,
                "include_stale": include_stale,
                "include_orphaned": include_orphaned,
            },
            "nodes": records,
            "edges": [traversed_edges[key] for key in sorted(traversed_edges)],
        }
    finally:
        connection.close()


def retrieve_index(
    path: Path,
    query: str,
    *,
    namespace: str = "personal",
    node_types: list[str] | None = None,
    limit: int = 20,
    max_depth: int = 1,
    include_taxonomy: bool = False,
    include_stale: bool = False,
    include_orphaned: bool = False,
) -> list[dict[str, Any]]:
    """Fuse exact, lexical, and graph lanes using deterministic RRF."""
    limit = _validated_limit(limit)
    _validate_traversal(direction="both", max_depth=max_depth, limit=limit)
    allowed_types = set(node_types or [])
    lane_nodes: dict[str, list[str]] = {"exact": [], "fts": [], "graph": []}
    nodes: dict[str, dict[str, Any]] = {}
    reasons: dict[str, list[dict[str, Any]]] = {}

    resolution = resolve_concepts(path, [query], namespace=namespace)[0]
    if resolution["status"] in {"exact", "alias", "ambiguous"}:
        for rank, node in enumerate(resolution["matches"], start=1):
            if allowed_types and node.get("type") not in allowed_types:
                continue
            if not _node_allowed(
                node,
                include_stale=include_stale,
                include_orphaned=include_orphaned,
            ):
                continue
            node_id = str(node["id"])
            lane_nodes["exact"].append(node_id)
            nodes[node_id] = node
            reasons.setdefault(node_id, []).append(
                {
                    "method": "exact",
                    "rank": rank,
                    "resolution": resolution["status"],
                    "match_kind": resolution.get("match_kind"),
                }
            )

    fts_results = search_index(
        path,
        query,
        namespace=namespace,
        node_types=node_types,
        limit=min(MAX_LIMIT, max(limit * 3, 20)),
    )
    for result in fts_results:
        node = result["node"]
        if not _node_allowed(
            node,
            include_stale=include_stale,
            include_orphaned=include_orphaned,
        ):
            continue
        node_id = str(node["id"])
        lane_nodes["fts"].append(node_id)
        nodes[node_id] = node
        reasons.setdefault(node_id, []).extend(result["reasons"])

    graph_seeds = list(dict.fromkeys([*lane_nodes["exact"], *lane_nodes["fts"]]))[:8]
    if graph_seeds and max_depth > 0:
        expansion = expand_index(
            path,
            graph_seeds,
            namespace=namespace,
            max_depth=max_depth,
            limit=min(MAX_LIMIT, max(limit * 4, 40)),
            include_taxonomy=include_taxonomy,
            include_stale=include_stale,
            include_orphaned=include_orphaned,
        )
        graph_records = [record for record in expansion["nodes"] if not record["seed"]]
        graph_records.sort(
            key=lambda item: (
                int(item["depth"]),
                str(item["node"].get("label", "")),
                str(item["node"]["id"]),
            )
        )
        for rank, record in enumerate(graph_records, start=1):
            node = record["node"]
            if allowed_types and node.get("type") not in allowed_types:
                continue
            node_id = str(node["id"])
            lane_nodes["graph"].append(node_id)
            nodes[node_id] = node
            reasons.setdefault(node_id, []).append(
                {
                    "method": "graph",
                    "rank": rank,
                    "depth": record["depth"],
                    "path": record["path"],
                }
            )

    lane_weights = {"exact": 3.0, "fts": 1.0, "graph": 0.7}
    scores: dict[str, float] = {}
    for lane, ordered_ids in lane_nodes.items():
        for rank, node_id in enumerate(dict.fromkeys(ordered_ids), start=1):
            scores[node_id] = scores.get(node_id, 0.0) + lane_weights[lane] / (60 + rank)
    ordered = sorted(
        scores,
        key=lambda node_id: (
            -scores[node_id],
            str(nodes[node_id].get("label", "")),
            node_id,
        ),
    )[:limit]
    return [
        {
            "rank": rank,
            "score": round(scores[node_id], 12),
            "node": nodes[node_id],
            "reasons": reasons.get(node_id, []),
        }
        for rank, node_id in enumerate(ordered, start=1)
    ]


def estimate_tokens(value: Any) -> int:
    """Conservatively estimate tokens as canonical JSON Unicode characters."""
    return len(canonical_json(value))


def _source_record(node: dict[str, Any]) -> dict[str, Any] | None:
    provenance = node.get("provenance") or {}
    authority = str(provenance.get("authority", ""))
    if not authority:
        return None
    result = {
        "node_id": node["id"],
        "authority": authority,
    }
    for key in (
        "line",
        "definition_start_line",
        "definition_end_line",
        "source_format",
        "web",
        "definition_sha256",
    ):
        if provenance.get(key) not in {None, ""}:
            result[key] = provenance[key]
    return result


def _index_digest(path: Path) -> str:
    return str(index_status(path)["snapshot_sha256"])


def build_context_bundle(
    path: Path,
    query: str,
    *,
    token_budget: int = 6000,
    namespace: str = "personal",
    node_types: list[str] | None = None,
    result_limit: int = 50,
    max_depth: int = 1,
    include_taxonomy: bool = False,
    include_stale: bool = False,
    include_orphaned: bool = False,
) -> dict[str, Any]:
    """Pack a deterministic evidence bundle without calling a language model."""
    if token_budget < 1 or token_budget > MAX_CONTEXT_BUDGET:
        raise AgentIndexError(
            f"token_budget must be between 1 and {MAX_CONTEXT_BUDGET}"
        )
    results = retrieve_index(
        path,
        query,
        namespace=namespace,
        node_types=node_types,
        limit=result_limit,
        max_depth=max_depth,
        include_taxonomy=include_taxonomy,
        include_stale=include_stale,
        include_orphaned=include_orphaned,
    )
    policy = {
        "node_types": sorted(set(node_types or [])),
        "max_depth": max_depth,
        "include_taxonomy": include_taxonomy,
        "include_stale": include_stale,
        "include_orphaned": include_orphaned,
    }
    bundle: dict[str, Any] = {
        "schema": CONTEXT_SCHEMA,
        "snapshot_sha256": _index_digest(path),
        "namespace": namespace,
        "query": query,
        "budget": {
            "requested_tokens": token_budget,
            "estimated_tokens": token_budget,
            "estimator": "conservative-char-v1",
        },
        "seeds": [],
        "nodes": [],
        "edges": [],
        "references": [],
        "sources": [],
        "omissions": [],
        "retrieval": {"policy": policy, "explanations": []},
    }
    if estimate_tokens(bundle) > token_budget:
        raise AgentIndexError("budget-too-small for the context bundle envelope")

    included: set[str] = set()
    for result in results:
        node = result["node"]
        node_id = str(node["id"])
        source = _source_record(node)
        candidate = json.loads(canonical_json(bundle))
        candidate["nodes"].append(node)
        if source is not None:
            candidate["sources"].append(source)
        candidate["retrieval"]["explanations"].append(
            {
                "node_id": node_id,
                "rank": result["rank"],
                "score": result["score"],
                "reasons": result["reasons"],
            }
        )
        if any(reason["method"] == "exact" for reason in result["reasons"]):
            candidate["seeds"].append(node_id)
        if estimate_tokens(candidate) <= token_budget:
            bundle = candidate
            included.add(node_id)
        else:
            bundle["omissions"].append(
                {"kind": "node", "id": node_id, "reason": "token-budget"}
            )

    if included:
        connection = _connect(path)
        try:
            placeholders = ",".join("?" for _ in included)
            parameters: list[Any] = [namespace, *sorted(included), *sorted(included)]
            edge_rows = connection.execute(
                f"""
                SELECT payload FROM edges
                WHERE namespace = ?
                  AND source IN ({placeholders})
                  AND target IN ({placeholders})
                ORDER BY relation, source, target, edge_id
                """,
                parameters,
            ).fetchall()
            for row in edge_rows:
                edge = _edge_payload(row)
                if not _edge_allowed(edge, include_stale=include_stale):
                    continue
                candidate = json.loads(canonical_json(bundle))
                candidate["edges"].append(edge)
                if estimate_tokens(candidate) <= token_budget:
                    bundle = candidate
                else:
                    bundle["omissions"].append(
                        {
                            "kind": "edge",
                            "id": f"{edge.get('source')}:{edge.get('relation')}:{edge.get('target')}",
                            "reason": "token-budget",
                        }
                    )
            ref_rows = connection.execute(
                f"""
                SELECT payload FROM refs
                WHERE namespace = ? AND target IN ({placeholders})
                ORDER BY authority, line, ref_id
                """,
                [namespace, *sorted(included)],
            ).fetchall()
            for row in ref_rows:
                reference = _reference_payload(row)
                candidate = json.loads(canonical_json(bundle))
                candidate["references"].append(reference)
                if estimate_tokens(candidate) <= token_budget:
                    bundle = candidate
                else:
                    bundle["omissions"].append(
                        {
                            "kind": "reference",
                            "id": str(reference.get("id", "")),
                            "reason": "token-budget",
                        }
                    )
        finally:
            connection.close()

    # Omission records also consume budget. Remove them from the tail if needed,
    # while retaining at least the selected evidence package.
    while bundle["omissions"] and estimate_tokens(bundle) > token_budget:
        bundle["omissions"].pop()
    estimated = estimate_tokens(bundle)
    bundle["budget"]["estimated_tokens"] = estimated
    while estimate_tokens(bundle) > token_budget and bundle["omissions"]:
        bundle["omissions"].pop()
        bundle["budget"]["estimated_tokens"] = estimate_tokens(bundle)
    final_estimate = estimate_tokens(bundle)
    bundle["budget"]["estimated_tokens"] = final_estimate
    if estimate_tokens(bundle) > token_budget:
        raise AgentIndexError("budget-too-small after context packing")
    return bundle


def _candidate_matches(
    path: Path,
    candidate: dict[str, Any],
    target_namespace: str,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    properties = candidate.get("properties") or {}
    explicit_target = str(properties.get("target_id", "")).strip()
    probes: list[tuple[str, str]] = []
    if explicit_target:
        probes.append((explicit_target, "explicit-target-id"))
    probes.append((str(candidate.get("id", "")), "id"))
    probes.append((str(candidate.get("label", "")), "label"))
    probes.extend(
        (str(alias), "alias")
        for alias in properties.get("aliases", [])
        if str(alias).strip()
    )
    matches: dict[str, dict[str, Any]] = {}
    evidence: list[dict[str, Any]] = []
    ambiguous = False
    for probe, source in probes:
        if not probe.strip():
            continue
        resolution = resolve_concepts(path, [probe], namespace=target_namespace)[0]
        if resolution["status"] == "ambiguous":
            ambiguous = True
        for node in resolution.get("matches", []):
            node_id = str(node["id"])
            matches[node_id] = node
            evidence.append(
                {
                    "kind": "identity-resolution",
                    "probe": probe,
                    "probe_source": source,
                    "status": resolution["status"],
                    "target_id": node_id,
                }
            )
        if explicit_target and source == "explicit-target-id":
            if len(matches) == 1:
                return "matched", list(matches.values()), evidence
            return "missing", [], evidence
        if source == "id" and resolution["status"] == "exact":
            return "matched", list(matches.values()), evidence
    if len(matches) == 1 and not ambiguous:
        return "matched", list(matches.values()), evidence
    if matches or ambiguous:
        return "ambiguous", list(matches.values()), evidence
    return "missing", [], evidence


def _claims(node: dict[str, Any]) -> dict[str, Any]:
    entry = node.get("entry") or {}
    claims = entry.get("claims") if isinstance(entry, dict) else None
    return claims if isinstance(claims, dict) else {}


def compare_graph(
    path: Path,
    candidate_snapshot: dict[str, Any],
    *,
    target_namespace: str = "personal",
) -> dict[str, Any]:
    """Compare an isolated candidate graph with a deterministic target index."""
    candidate_namespace, _ = _validated_snapshot(candidate_snapshot)
    _validate_namespace(target_namespace)
    status = index_status(path)
    if status.get("namespace") != target_namespace:
        raise AgentIndexError(
            f"target namespace is not indexed: {target_namespace!r}"
        )
    if candidate_namespace == target_namespace:
        raise AgentIndexError("candidate and target namespaces must be distinct")

    candidate_nodes = sorted(
        candidate_snapshot.get("nodes") or [], key=lambda node: str(node.get("id", ""))
    )
    target_map: dict[str, str] = {}
    records: dict[str, dict[str, Any]] = {}
    for candidate in candidate_nodes:
        candidate_id = str(candidate["id"])
        match_status, matches, evidence = _candidate_matches(
            path, candidate, target_namespace
        )
        match_records = [
            {
                "namespace": target_namespace,
                "id": target["id"],
                "label": target.get("label", ""),
            }
            for target in matches
        ]
        if match_status == "missing":
            result_status = "new"
        elif match_status == "ambiguous":
            result_status = "uncertain"
        else:
            result_status = "known"
            target_map[candidate_id] = str(matches[0]["id"])
        records[candidate_id] = {
            "candidate": {
                "namespace": candidate_namespace,
                "id": candidate_id,
                "label": candidate.get("label", ""),
            },
            "status": result_status,
            "matches": match_records,
            "missing": [],
            "conflicts": [],
            "evidence": evidence,
        }

        if result_status != "known":
            continue
        target = matches[0]
        if str(candidate.get("text", "")).strip() and not str(target.get("text", "")).strip():
            records[candidate_id]["missing"].append(
                {"kind": "entry", "target_id": target["id"]}
            )
        target_properties = target.get("properties") or {}
        if target_properties.get("curation_status") in {"pending", "needs-review"}:
            records[candidate_id]["missing"].append(
                {
                    "kind": "curation",
                    "target_id": target["id"],
                    "status": target_properties.get("curation_status"),
                }
            )
        candidate_claims = _claims(candidate)
        target_claims = _claims(target)
        for key in sorted(set(candidate_claims) & set(target_claims)):
            if canonical_json(candidate_claims[key]) != canonical_json(target_claims[key]):
                records[candidate_id]["conflicts"].append(
                    {
                        "kind": "claim",
                        "key": key,
                        "candidate": candidate_claims[key],
                        "target": target_claims[key],
                        "target_id": target["id"],
                    }
                )

    connection = _connect(path)
    try:
        target_edges = {
            (str(row["source"]), str(row["relation"]), str(row["target"]))
            for row in connection.execute(
                "SELECT source, relation, target FROM edges WHERE namespace = ?",
                (target_namespace,),
            )
        }
    finally:
        connection.close()
    for edge in sorted(
        candidate_snapshot.get("edges") or [],
        key=lambda item: (
            str(item.get("source", "")),
            str(item.get("relation", "")),
            str(item.get("target", "")),
        ),
    ):
        source = str(edge["source"])
        target = str(edge["target"])
        relation = str(edge["relation"])
        mapped_source = target_map.get(source)
        mapped_target = target_map.get(target)
        if not mapped_source or not mapped_target or relation == "contains":
            continue
        mapped_edge = (mapped_source, relation, mapped_target)
        if mapped_edge not in target_edges:
            records[source]["missing"].append(
                {
                    "kind": "edge",
                    "source": mapped_source,
                    "relation": relation,
                    "target": mapped_target,
                    "evidence": edge.get("evidence", ""),
                }
            )

    results: list[dict[str, Any]] = []
    for candidate in candidate_nodes:
        record = records[str(candidate["id"])]
        if record["conflicts"]:
            record["status"] = "conflict"
        elif record["status"] == "known" and record["missing"]:
            record["status"] = "partial"
        results.append(record)
    summary = {status_name: 0 for status_name in ("known", "partial", "new", "conflict", "uncertain")}
    for record in results:
        summary[record["status"]] += 1
    summary["total"] = len(results)
    return {
        "schema": COMPARISON_SCHEMA,
        "candidate": {
            "namespace": candidate_namespace,
            "snapshot_sha256": candidate_snapshot["snapshot_sha256"],
            "graph_sha256": candidate_snapshot["graph"]["sha256"],
        },
        "target": {
            "namespace": target_namespace,
            "snapshot_sha256": status["snapshot_sha256"],
            "graph_sha256": status["graph_sha256"],
        },
        "results": results,
        "summary": summary,
    }


def _marker_suggestions(label: str) -> dict[str, str]:
    return {
        "markdown": f"--[[{label}]]--",
        "typst": f"#kn[{label}]",
        "latex": f"\\kn{{{label}}}",
    }


def _candidate_by_id(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(node["id"]): node
        for node in snapshot.get("nodes") or []
    }


def create_proposal(
    path: Path,
    candidate_snapshot: dict[str, Any],
    *,
    target_namespace: str = "personal",
    target_authority: str | None = None,
) -> dict[str, Any]:
    """Create a deterministic review package without mutating authority data."""
    comparison = compare_graph(
        path,
        candidate_snapshot,
        target_namespace=target_namespace,
    )
    candidates = _candidate_by_id(candidate_snapshot)
    operations: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    delta_nodes: dict[str, dict[str, Any]] = {}
    delta_edges: dict[tuple[str, str, str], dict[str, Any]] = {}

    for record in comparison["results"]:
        candidate_id = str(record["candidate"]["id"])
        candidate = candidates[candidate_id]
        status_name = str(record["status"])
        if status_name == "new":
            label = str(candidate.get("label", candidate_id))
            operation: dict[str, Any] = {
                "op": "propose-node",
                "candidate": {
                    "namespace": candidate_snapshot["namespace"],
                    "id": candidate_id,
                },
                "label": label,
                "markers": _marker_suggestions(label),
                "content": {
                    "text": str(candidate.get("text", "")),
                    "entry": candidate.get("entry") or {},
                },
                "evidence": record.get("evidence") or [],
                "requires_source_marker": True,
            }
            if target_authority:
                operation["target_authority"] = target_authority
            operations.append(operation)
            blockers.append(
                {
                    "code": "source-marker-required",
                    "candidate_id": candidate_id,
                    "message": "Add and review an authority marker before applying node curation.",
                }
            )
            continue
        if status_name == "uncertain":
            operations.append(
                {
                    "op": "review-identity",
                    "candidate": record["candidate"],
                    "matches": record.get("matches") or [],
                    "evidence": record.get("evidence") or [],
                }
            )
            blockers.append(
                {
                    "code": "identity-review-required",
                    "candidate_id": candidate_id,
                    "message": "Ambiguous identity cannot be applied automatically.",
                }
            )
            continue
        if status_name == "conflict":
            operations.append(
                {
                    "op": "review-conflict",
                    "candidate": record["candidate"],
                    "matches": record.get("matches") or [],
                    "conflicts": record.get("conflicts") or [],
                    "evidence": record.get("evidence") or [],
                }
            )
            blockers.append(
                {
                    "code": "conflict-review-required",
                    "candidate_id": candidate_id,
                    "message": "Conflicting source-backed claims require a human decision.",
                }
            )
            continue
        if status_name != "partial":
            continue

        target_id = str(record["matches"][0]["id"])
        missing_entry = any(
            item.get("kind") in {"entry", "curation"}
            for item in record.get("missing") or []
        )
        if missing_entry and (
            str(candidate.get("text", "")).strip() or candidate.get("entry")
        ):
            node_delta: dict[str, Any] = {
                "id": target_id,
                "text": str(candidate.get("text", "")),
                "properties": {"origin": "agent-paper-comparison"},
            }
            if isinstance(candidate.get("entry"), dict) and candidate["entry"]:
                node_delta["entry"] = candidate["entry"]
            delta_nodes[target_id] = node_delta
            operations.append(
                {
                    "op": "propose-entry",
                    "target_id": target_id,
                    "candidate_id": candidate_id,
                    "delta": node_delta,
                    "evidence": record.get("evidence") or [],
                }
            )
        for missing in record.get("missing") or []:
            if missing.get("kind") != "edge":
                continue
            key = (
                str(missing["source"]),
                str(missing["relation"]),
                str(missing["target"]),
            )
            edge_delta = {
                "source": key[0],
                "relation": key[1],
                "target": key[2],
                "origin": "agent-paper-comparison",
                "confidence": "medium",
                "evidence": str(missing.get("evidence", "")),
            }
            delta_edges[key] = edge_delta
            operations.append(
                {
                    "op": "propose-edge",
                    "candidate_id": candidate_id,
                    "delta": edge_delta,
                }
            )

    delta_preview = {
        "schema": "qlkg-agent-delta-v2",
        "remove_nodes": [],
        "nodes": [delta_nodes[key] for key in sorted(delta_nodes)],
        "edges": [delta_edges[key] for key in sorted(delta_edges)],
        "remove_edges": [],
    }
    proposal: dict[str, Any] = {
        "schema": PROPOSAL_SCHEMA,
        "candidate": comparison["candidate"],
        "target": comparison["target"],
        "comparison_sha256": sha256_json(comparison),
        "comparison_summary": comparison["summary"],
        "operations": operations,
        "delta_preview": delta_preview,
        "blockers": blockers,
        "delta_ready": bool(delta_preview["nodes"] or delta_preview["edges"]),
        "fully_resolved": not blockers,
        "instructions": [
            "Review source-marker, identity, and conflict operations.",
            "Review delta_preview and save it as a qlkg-agent-delta-v2 file.",
            "Run kgdistiller apply, sync, curate-check, and check explicitly.",
        ],
    }
    proposal["proposal_sha256"] = sha256_json(proposal)
    return proposal
