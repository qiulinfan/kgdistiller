"""Derived, disposable Agent index and deterministic retrieval primitives."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Iterable


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


class AgentIndexError(ValueError):
    """Raised when an Agent snapshot or derived index is invalid."""


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
    return " AND ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)


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
