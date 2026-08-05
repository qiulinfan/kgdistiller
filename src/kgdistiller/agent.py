"""Derived, disposable Agent index and deterministic retrieval primitives."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import struct
import tempfile
import unicodedata
from collections import deque
from pathlib import Path
from typing import Any, Iterable, Protocol

from .alignment import (
    ALIGNMENT_REPORT_SCHEMA,
    ALIGNMENT_SCHEMA,
    AlignmentError,
    canonical_acronym,
    extract_scoped_aliases,
    mapping_id,
    node_fingerprint,
    normalize_surface,
    sha256_json as alignment_sha256_json,
    validate_alignment_set,
)


SNAPSHOT_SCHEMA = "qlkg-agent-snapshot-v1"
INDEX_SCHEMA = "qlkg-agent-index-v2"
EMBEDDING_INPUT_SCHEMA = "qlkg-node-embedding-text-v1"
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
PPR_SCHEMA = "qlkg-ppr-result-v1"
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
    node_ids = [str(node.get("id", "")) for node in snapshot.get("nodes") or []]
    if any(not node_id for node_id in node_ids) or len(node_ids) != len(set(node_ids)):
        raise AgentIndexError("snapshot contains an empty or duplicate node ID")
    known_ids = set(node_ids)
    for edge in snapshot.get("edges") or []:
        if str(edge.get("source", "")) not in known_ids or str(
            edge.get("target", "")
        ) not in known_ids:
            raise AgentIndexError("snapshot contains a dangling edge")
    return namespace, counts


def validate_agent_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Validate the public Agent snapshot contract without indexing it."""
    namespace, counts = _validated_snapshot(snapshot)
    return {
        "schema": SNAPSHOT_SCHEMA,
        "namespace": namespace,
        "counts": counts,
        "snapshot_sha256": snapshot["snapshot_sha256"],
    }


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
        CREATE TABLE scoped_aliases (
            alias_id TEXT PRIMARY KEY,
            namespace TEXT NOT NULL,
            node_id TEXT NOT NULL,
            surface TEXT NOT NULL,
            normalized_surface TEXT NOT NULL,
            expansion TEXT NOT NULL,
            authority TEXT NOT NULL,
            payload TEXT NOT NULL,
            FOREIGN KEY (namespace, node_id) REFERENCES nodes(namespace, id)
        );
        CREATE INDEX scoped_aliases_lookup
            ON scoped_aliases(namespace, normalized_surface, node_id);
        CREATE TABLE alignment_mappings (
            mapping_id TEXT PRIMARY KEY,
            subject_namespace TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            subject_sha256 TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object_namespace TEXT NOT NULL,
            object_id TEXT NOT NULL,
            object_sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            payload TEXT NOT NULL
        );
        CREATE INDEX alignment_subject_lookup
            ON alignment_mappings(subject_namespace, subject_id, object_namespace, status);
        CREATE INDEX alignment_object_lookup
            ON alignment_mappings(object_namespace, object_id, subject_namespace, status);
        CREATE TABLE similarity_edges (
            namespace TEXT NOT NULL,
            source TEXT NOT NULL,
            target TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            score REAL NOT NULL,
            evidence TEXT NOT NULL,
            PRIMARY KEY (namespace, source, target, provider, model),
            FOREIGN KEY (namespace, source) REFERENCES nodes(namespace, id),
            FOREIGN KEY (namespace, target) REFERENCES nodes(namespace, id)
        );
        CREATE INDEX similarity_edges_source
            ON similarity_edges(namespace, source, score DESC, target);
        """
    )


def _reusable_embedding_rows(
    path: Path,
    nodes: list[dict[str, Any]],
    namespace: str,
) -> list[tuple[str, str, str, str, int, str, bytes]]:
    """Read exact still-current vectors before an atomic index rebuild."""
    if not path.is_file():
        return []
    expected = {
        str(node["id"]): embedding_input_sha256(node)
        for node in nodes
    }
    connection: sqlite3.Connection | None = None
    try:
        connection = _connect(path)
        rows = connection.execute(
            """
            SELECT namespace, node_id, provider, model, dimensions,
                   content_sha256, vector
            FROM embeddings
            WHERE namespace = ?
            ORDER BY namespace, node_id, provider, model
            """,
            (namespace,),
        ).fetchall()
        reusable: list[tuple[str, str, str, str, int, str, bytes]] = []
        for row in rows:
            node_id = str(row["node_id"])
            digest = str(row["content_sha256"])
            dimensions = int(row["dimensions"])
            vector = bytes(row["vector"])
            if expected.get(node_id) != digest:
                continue
            try:
                _validate_vector(_unpack_vector(vector, dimensions), dimensions)
            except (AgentIndexError, ValueError, struct.error):
                continue
            reusable.append(
                (
                    str(row["namespace"]),
                    node_id,
                    str(row["provider"]),
                    str(row["model"]),
                    dimensions,
                    digest,
                    vector,
                )
            )
        return reusable
    except (AgentIndexError, OSError, sqlite3.Error, ValueError, json.JSONDecodeError):
        return []
    finally:
        if connection is not None:
            connection.close()


def write_agent_index(
    path: Path,
    snapshot: dict[str, Any],
    alignment_set: dict[str, Any] | None = None,
) -> None:
    """Atomically rebuild a provider-neutral SQLite index from a snapshot."""
    namespace, counts = _validated_snapshot(snapshot)
    try:
        validated_alignments = validate_alignment_set(alignment_set)
        scoped_aliases = extract_scoped_aliases(snapshot)
    except AlignmentError as error:
        raise AgentIndexError(str(error)) from error
    nodes = list(snapshot.get("nodes") or [])
    reusable_embeddings = _reusable_embedding_rows(path, nodes, namespace)
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
            "retrieval_lanes": ["exact", "scoped-alias", "fts", "graph", "ppr"],
            "capabilities": [
                "read-only-query-v2",
                "transactional-ingest-v1",
                "portable-store-v1",
            ],
            "alignment_schema": ALIGNMENT_SCHEMA,
            "alignment_sha256": alignment_sha256_json(validated_alignments),
            "alignment_counts": {
                "mappings": len(validated_alignments["mappings"]),
                "scoped_aliases": int(scoped_aliases["count"]),
                "similarity_edges": 0,
            },
            "provider_config_sha256": None,
            "providers": {
                "embedding": None,
                "reranker": None,
                "token_estimator": "conservative-char-v1",
                "candidate_analyzer": None,
            },
        }
        embedding_configs = sorted(
            {
                canonical_json(
                    {
                        "name": row[2],
                        "model": row[3],
                        "dimensions": row[4],
                    }
                )
                for row in reusable_embeddings
            }
        )
        if embedding_configs:
            configs = [json.loads(value) for value in embedding_configs]
            metadata["retrieval_lanes"].append("embedding")
            metadata["provider_config_sha256"] = sha256_json(configs)
            metadata["providers"]["embedding"] = (
                configs[0] if len(configs) == 1 else {"configurations": configs}
            )
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
        for alias in scoped_aliases["aliases"]:
            scope = alias.get("scope") or {}
            connection.execute(
                "INSERT INTO scoped_aliases VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(alias["id"]),
                    namespace,
                    str(alias["node_id"]),
                    str(alias["surface"]),
                    str(alias["normalized_surface"]),
                    str(alias["expansion"]),
                    str(scope.get("authority", "")),
                    canonical_json(alias),
                ),
            )
        for mapping in validated_alignments["mappings"]:
            subject = mapping["subject"]
            object_ = mapping["object"]
            connection.execute(
                "INSERT INTO alignment_mappings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(mapping["id"]),
                    str(subject["namespace"]),
                    str(subject["node_id"]),
                    str(subject.get("node_sha256", "")),
                    str(mapping["predicate"]),
                    str(object_["namespace"]),
                    str(object_["node_id"]),
                    str(object_.get("node_sha256", "")),
                    str(mapping["status"]),
                    canonical_json(mapping),
                ),
            )
        connection.executemany(
            """
            INSERT INTO embeddings
            (namespace, node_id, provider, model, dimensions, content_sha256, vector)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            reusable_embeddings,
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
                scoped_rows = connection.execute(
                    """
                    SELECT node_id, payload
                    FROM scoped_aliases
                    WHERE namespace = ? AND normalized_surface = ?
                    ORDER BY node_id, alias_id
                    """,
                    (namespace, normalize_surface(raw)),
                ).fetchall()
                scoped_evidence: dict[str, list[dict[str, Any]]] = {}
                for row in scoped_rows:
                    node_id = str(row["node_id"])
                    scoped_evidence.setdefault(node_id, []).append(json.loads(row["payload"]))
                for node_id in sorted(scoped_evidence):
                    node = _node_by_id(connection, namespace, node_id)
                    if node is not None:
                        matches.append(node)
                if matches:
                    results.append(
                        {
                            "query": raw,
                            "status": "scoped-alias" if len(matches) == 1 else "ambiguous",
                            "match_kind": "scoped-alias",
                            "matches": matches,
                            "evidence": [
                                evidence
                                for node_id in sorted(scoped_evidence)
                                for evidence in scoped_evidence[node_id]
                            ],
                        }
                    )
                    continue
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


def _embedding_text(node: dict[str, Any]) -> str:
    properties = node.get("properties") or {}
    entry = node.get("entry") or {}
    parts = [
        str(node.get("label", "")),
        *[str(item) for item in properties.get("aliases", []) if str(item).strip()],
        str(node.get("text", "")),
        *_json_strings(entry),
    ]
    return "\n".join(dict.fromkeys(part.strip() for part in parts if part.strip()))


def embedding_input_sha256(node: dict[str, Any]) -> str:
    """Return the stable digest used to invalidate one node embedding."""
    return hashlib.sha256(_embedding_text(node).encode("utf-8")).hexdigest()


def _validate_vector(vector: list[float], dimensions: int) -> list[float]:
    if len(vector) != dimensions:
        raise AgentIndexError(
            f"embedding dimensions mismatch: expected {dimensions}, got {len(vector)}"
        )
    normalized = [float(value) for value in vector]
    if not all(math.isfinite(value) for value in normalized):
        raise AgentIndexError("embedding contains a non-finite value")
    if not any(value != 0.0 for value in normalized):
        raise AgentIndexError("embedding vector cannot be all zero")
    return normalized


def _pack_vector(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack_vector(payload: bytes, dimensions: int) -> list[float]:
    expected = dimensions * 4
    if len(payload) != expected:
        raise AgentIndexError("stored embedding has an invalid byte length")
    return list(struct.unpack(f"<{dimensions}f", payload))


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise AgentIndexError("cannot compare embeddings with different dimensions")
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


def index_embeddings(
    path: Path,
    provider: EmbeddingProvider,
    *,
    namespace: str = "personal",
    build_similarity_edges: bool = True,
    similarity_top_k: int = 5,
    similarity_threshold: float = 0.78,
) -> dict[str, Any]:
    """Cache provider embeddings and optional soft edges in disposable index state."""
    _validate_namespace(namespace)
    provider_name = str(getattr(provider, "name", "")).strip()
    model = str(getattr(provider, "model", "")).strip()
    dimensions = int(getattr(provider, "dimensions", 0))
    if not provider_name or not model or dimensions < 1:
        raise AgentIndexError("embedding provider metadata is incomplete")
    if similarity_top_k < 1 or similarity_top_k > 100:
        raise AgentIndexError("similarity_top_k must be between 1 and 100")
    if similarity_threshold < -1.0 or similarity_threshold > 1.0:
        raise AgentIndexError("similarity_threshold must be between -1 and 1")
    connection = _connect(path)
    try:
        rows = connection.execute(
            "SELECT * FROM nodes WHERE namespace = ? ORDER BY id", (namespace,)
        ).fetchall()
        nodes = [_node_payload(row) for row in rows]
        texts = [_embedding_text(node) for node in nodes]
        digests = [embedding_input_sha256(node) for node in nodes]
        pending_indices: list[int] = []
        for index, (node, digest) in enumerate(zip(nodes, digests)):
            cached = connection.execute(
                """
                SELECT content_sha256, dimensions FROM embeddings
                WHERE namespace = ? AND node_id = ? AND provider = ? AND model = ?
                """,
                (namespace, str(node["id"]), provider_name, model),
            ).fetchone()
            if (
                cached is None
                or str(cached["content_sha256"]) != digest
                or int(cached["dimensions"]) != dimensions
            ):
                pending_indices.append(index)
        if pending_indices:
            vectors = provider.embed([texts[index] for index in pending_indices])
            if len(vectors) != len(pending_indices):
                raise AgentIndexError("embedding provider returned the wrong vector count")
            for index, vector in zip(pending_indices, vectors):
                normalized = _validate_vector(vector, dimensions)
                connection.execute(
                    """
                    INSERT OR REPLACE INTO embeddings
                    (namespace, node_id, provider, model, dimensions, content_sha256, vector)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        namespace,
                        str(nodes[index]["id"]),
                        provider_name,
                        model,
                        dimensions,
                        digests[index],
                        _pack_vector(normalized),
                    ),
                )
        soft_edge_count = 0
        if build_similarity_edges:
            connection.execute(
                "DELETE FROM similarity_edges WHERE namespace = ? AND provider = ? AND model = ?",
                (namespace, provider_name, model),
            )
            vector_rows = connection.execute(
                """
                SELECT node_id, vector FROM embeddings
                WHERE namespace = ? AND provider = ? AND model = ? AND dimensions = ?
                ORDER BY node_id
                """,
                (namespace, provider_name, model, dimensions),
            ).fetchall()
            vectors_by_id = {
                str(row["node_id"]): _unpack_vector(row["vector"], dimensions)
                for row in vector_rows
            }
            node_ids = sorted(vectors_by_id)
            for source in node_ids:
                candidates = sorted(
                    (
                        (_cosine(vectors_by_id[source], vectors_by_id[target]), target)
                        for target in node_ids
                        if target != source
                    ),
                    key=lambda item: (-item[0], item[1]),
                )
                for score, target in candidates[:similarity_top_k]:
                    if score < similarity_threshold:
                        continue
                    evidence = {
                        "kind": "embedding-similarity",
                        "provider": provider_name,
                        "model": model,
                        "score": round(score, 12),
                        "identity_authority": False,
                    }
                    connection.execute(
                        "INSERT INTO similarity_edges VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            namespace,
                            source,
                            target,
                            provider_name,
                            model,
                            score,
                            canonical_json(evidence),
                        ),
                    )
                    soft_edge_count += 1
        provider_meta = {
            "name": provider_name,
            "model": model,
            "dimensions": dimensions,
        }
        providers_row = connection.execute(
            "SELECT value FROM index_meta WHERE key = 'providers'"
        ).fetchone()
        providers = json.loads(providers_row["value"]) if providers_row else {}
        configuration_rows = connection.execute(
            """
            SELECT DISTINCT provider, model, dimensions
            FROM embeddings
            ORDER BY provider, model, dimensions
            """
        ).fetchall()
        configurations = [
            {
                "name": str(row["provider"]),
                "model": str(row["model"]),
                "dimensions": int(row["dimensions"]),
            }
            for row in configuration_rows
        ]
        providers["embedding"] = (
            configurations[0]
            if len(configurations) == 1
            else {"configurations": configurations}
        )
        connection.execute(
            "INSERT OR REPLACE INTO index_meta(key, value) VALUES ('providers', ?)",
            (canonical_json(providers),),
        )
        connection.execute(
            "INSERT OR REPLACE INTO index_meta(key, value) VALUES ('provider_config_sha256', ?)",
            (canonical_json(sha256_json(configurations)),),
        )
        lanes_row = connection.execute(
            "SELECT value FROM index_meta WHERE key = 'retrieval_lanes'"
        ).fetchone()
        lanes = json.loads(lanes_row["value"]) if lanes_row else []
        if "embedding" not in lanes:
            lanes.append("embedding")
        connection.execute(
            "INSERT OR REPLACE INTO index_meta(key, value) VALUES ('retrieval_lanes', ?)",
            (canonical_json(lanes),),
        )
        counts_row = connection.execute(
            "SELECT value FROM index_meta WHERE key = 'alignment_counts'"
        ).fetchone()
        alignment_counts = json.loads(counts_row["value"]) if counts_row else {}
        alignment_counts["similarity_edges"] = connection.execute(
            "SELECT count(*) FROM similarity_edges WHERE namespace = ?", (namespace,)
        ).fetchone()[0]
        connection.execute(
            "INSERT OR REPLACE INTO index_meta(key, value) VALUES ('alignment_counts', ?)",
            (canonical_json(alignment_counts),),
        )
        connection.commit()
        return {
            "namespace": namespace,
            "provider": provider_meta,
            "nodes": len(nodes),
            "embedded": len(pending_indices),
            "cached": len(nodes) - len(pending_indices),
            "similarity_edges": soft_edge_count,
        }
    finally:
        connection.close()


def semantic_search(
    path: Path,
    query: str,
    provider: EmbeddingProvider,
    *,
    namespace: str = "personal",
    node_types: list[str] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Retrieve semantic candidates; scores are evidence, never identity decisions."""
    limit = _validated_limit(limit)
    if not str(query).strip():
        raise AgentIndexError("semantic query cannot be empty")
    index_embeddings(path, provider, namespace=namespace)
    provider_name = str(provider.name)
    model = str(provider.model)
    dimensions = int(provider.dimensions)
    query_vectors = provider.embed([str(query)])
    if len(query_vectors) != 1:
        raise AgentIndexError("embedding provider returned the wrong query vector count")
    query_vector = _validate_vector(query_vectors[0], dimensions)
    allowed_types = set(node_types or [])
    connection = _connect(path)
    try:
        rows = connection.execute(
            """
            SELECT e.node_id, e.vector, n.*
            FROM embeddings AS e
            JOIN nodes AS n ON n.namespace = e.namespace AND n.id = e.node_id
            WHERE e.namespace = ? AND e.provider = ? AND e.model = ? AND e.dimensions = ?
            ORDER BY e.node_id
            """,
            (namespace, provider_name, model, dimensions),
        ).fetchall()
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            node = _node_payload(row)
            if allowed_types and node.get("type") not in allowed_types:
                continue
            score = _cosine(query_vector, _unpack_vector(row["vector"], dimensions))
            scored.append((score, node))
        scored.sort(key=lambda item: (-item[0], str(item[1].get("label", "")), str(item[1]["id"])))
        return [
            {
                "rank": rank,
                "node": node,
                "reasons": [
                    {
                        "method": "semantic",
                        "rank": rank,
                        "score": round(score, 12),
                        "provider": provider_name,
                        "model": model,
                        "identity_authority": False,
                    }
                ],
            }
            for rank, (score, node) in enumerate(scored[:limit], start=1)
        ]
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


def personalized_pagerank(
    path: Path,
    seeds: dict[str, float],
    *,
    namespace: str = "personal",
    node_types: list[str] | None = None,
    edge_types: list[str] | None = None,
    include_taxonomy: bool = False,
    include_similarity: bool = True,
    include_stale: bool = False,
    include_orphaned: bool = False,
    damping: float = 0.85,
    max_iterations: int = 60,
    tolerance: float = 1e-10,
    limit: int = 50,
    _candidate_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Run deterministic weighted PPR over trusted edges and disposable soft edges."""
    _validate_namespace(namespace)
    limit = _validated_limit(limit)
    if not 0.0 < damping < 1.0:
        raise AgentIndexError("PPR damping must be between 0 and 1")
    if max_iterations < 1 or max_iterations > 1000:
        raise AgentIndexError("PPR max_iterations must be between 1 and 1000")
    if tolerance <= 0.0:
        raise AgentIndexError("PPR tolerance must be positive")
    relations = set(edge_types or DEFAULT_SEMANTIC_RELATIONS)
    if include_taxonomy:
        relations.add("contains")
    else:
        relations.discard("contains")
    allowed_types = set(node_types or [])
    bounded_ids = sorted(set(_candidate_ids or []))
    connection = _connect(path)
    try:
        nodes: dict[str, dict[str, Any]] = {}
        if bounded_ids:
            placeholders = ",".join("?" for _ in bounded_ids)
            node_rows = connection.execute(
                f"SELECT * FROM nodes WHERE namespace = ? AND id IN ({placeholders}) ORDER BY id",
                [namespace, *bounded_ids],
            )
        else:
            node_rows = connection.execute(
                "SELECT * FROM nodes WHERE namespace = ? ORDER BY id", (namespace,)
            )
        for row in node_rows:
            node = _node_payload(row)
            if allowed_types and node.get("type") not in allowed_types:
                continue
            if not _node_allowed(
                node,
                include_stale=include_stale,
                include_orphaned=include_orphaned,
            ):
                continue
            nodes[str(node["id"])] = node
        positive_seeds = {
            str(node_id): float(weight)
            for node_id, weight in seeds.items()
            if str(node_id) in nodes and math.isfinite(float(weight)) and float(weight) > 0.0
        }
        if not positive_seeds:
            raise AgentIndexError("PPR requires at least one positive indexed seed")
        seed_total = sum(positive_seeds.values())
        reset = {
            node_id: positive_seeds.get(node_id, 0.0) / seed_total for node_id in nodes
        }
        adjacency: dict[str, dict[str, float]] = {node_id: {} for node_id in nodes}
        relation_weights = {
            "prerequisite-for": 1.0,
            "implies": 1.0,
            "generalizes": 0.9,
            "derived-from": 0.9,
            "contrasts-with": 0.7,
            "contains": 0.3,
        }
        trusted_edge_count = 0
        if bounded_ids:
            placeholders = ",".join("?" for _ in bounded_ids)
            edge_rows = connection.execute(
                f"""
                SELECT * FROM edges
                WHERE namespace = ?
                  AND source IN ({placeholders})
                  AND target IN ({placeholders})
                ORDER BY edge_id
                """,
                [namespace, *bounded_ids, *bounded_ids],
            )
        else:
            edge_rows = connection.execute(
                "SELECT * FROM edges WHERE namespace = ? ORDER BY edge_id", (namespace,)
            )
        for row in edge_rows:
            relation = str(row["relation"])
            source = str(row["source"])
            target = str(row["target"])
            if relation not in relations or source not in nodes or target not in nodes:
                continue
            edge = _edge_payload(row)
            if not _edge_allowed(edge, include_stale=include_stale):
                continue
            weight = relation_weights.get(relation, 0.8)
            adjacency[source][target] = adjacency[source].get(target, 0.0) + weight
            adjacency[target][source] = adjacency[target].get(source, 0.0) + weight
            trusted_edge_count += 1
        similarity_edge_count = 0
        if include_similarity:
            if bounded_ids:
                placeholders = ",".join("?" for _ in bounded_ids)
                similarity_rows = connection.execute(
                    f"""
                    SELECT source, target, score FROM similarity_edges
                    WHERE namespace = ?
                      AND source IN ({placeholders})
                      AND target IN ({placeholders})
                    ORDER BY source, score DESC, target
                    """,
                    [namespace, *bounded_ids, *bounded_ids],
                )
            else:
                similarity_rows = connection.execute(
                    """
                    SELECT source, target, score FROM similarity_edges
                    WHERE namespace = ? ORDER BY source, score DESC, target
                    """,
                    (namespace,),
                )
            for row in similarity_rows:
                source = str(row["source"])
                target = str(row["target"])
                if source not in nodes or target not in nodes:
                    continue
                weight = max(0.0, float(row["score"])) * 0.35
                if weight == 0.0:
                    continue
                adjacency[source][target] = adjacency[source].get(target, 0.0) + weight
                similarity_edge_count += 1
        rank = dict(reset)
        converged = False
        iterations = 0
        for iteration in range(1, max_iterations + 1):
            next_rank = {node_id: (1.0 - damping) * reset[node_id] for node_id in nodes}
            sink_mass = 0.0
            for source, source_rank in rank.items():
                neighbors = adjacency[source]
                total_weight = sum(neighbors.values())
                if total_weight == 0.0:
                    sink_mass += source_rank
                    continue
                for target, weight in neighbors.items():
                    next_rank[target] += damping * source_rank * weight / total_weight
            if sink_mass:
                for node_id in nodes:
                    next_rank[node_id] += damping * sink_mass * reset[node_id]
            delta = sum(abs(next_rank[node_id] - rank[node_id]) for node_id in nodes)
            rank = next_rank
            iterations = iteration
            if delta <= tolerance:
                converged = True
                break
        ordered = sorted(
            (node_id for node_id in nodes if rank[node_id] > 0.0),
            key=lambda node_id: (
                -rank[node_id],
                str(nodes[node_id].get("label", "")),
                node_id,
            ),
        )[:limit]
        return {
            "schema": PPR_SCHEMA,
            "namespace": namespace,
            "seeds": [
                {"id": node_id, "weight": positive_seeds[node_id], "reset": reset[node_id]}
                for node_id in sorted(positive_seeds)
            ],
            "policy": {
                "damping": damping,
                "max_iterations": max_iterations,
                "tolerance": tolerance,
                "edge_types": sorted(relations),
                "include_similarity": include_similarity,
                "scope": "bounded-neighborhood" if bounded_ids else "namespace",
                "scope_nodes": len(nodes),
                "trusted_edges": trusted_edge_count,
                "similarity_edges": similarity_edge_count,
            },
            "iterations": iterations,
            "converged": converged,
            "results": [
                {
                    "rank": position,
                    "score": round(rank[node_id], 15),
                    "node": nodes[node_id],
                    "seed": node_id in positive_seeds,
                }
                for position, node_id in enumerate(ordered, start=1)
            ],
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
    graph_strategy: str = "hybrid",
    embedding_provider: EmbeddingProvider | None = None,
    rerank_provider: RerankProvider | None = None,
) -> list[dict[str, Any]]:
    """Fuse exact, lexical, semantic, BFS, and PPR lanes using deterministic RRF."""
    limit = _validated_limit(limit)
    _validate_traversal(direction="both", max_depth=max_depth, limit=limit)
    if graph_strategy not in {"bfs", "ppr", "hybrid"}:
        raise AgentIndexError(f"unsupported graph strategy: {graph_strategy!r}")
    allowed_types = set(node_types or [])
    lane_nodes: dict[str, list[str]] = {
        "exact": [],
        "fts": [],
        "semantic": [],
        "graph": [],
        "ppr": [],
    }
    nodes: dict[str, dict[str, Any]] = {}
    reasons: dict[str, list[dict[str, Any]]] = {}

    resolution = resolve_concepts(path, [query], namespace=namespace)[0]
    if resolution["status"] in {"exact", "alias", "scoped-alias", "ambiguous"}:
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
                    **(
                        {"evidence": resolution.get("evidence", [])}
                        if resolution.get("match_kind") == "scoped-alias"
                        else {}
                    ),
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

    if embedding_provider is not None:
        semantic_results = semantic_search(
            path,
            query,
            embedding_provider,
            namespace=namespace,
            node_types=node_types,
            limit=min(MAX_LIMIT, max(limit * 3, 20)),
        )
        for result in semantic_results:
            node = result["node"]
            if not _node_allowed(
                node,
                include_stale=include_stale,
                include_orphaned=include_orphaned,
            ):
                continue
            node_id = str(node["id"])
            lane_nodes["semantic"].append(node_id)
            nodes[node_id] = node
            reasons.setdefault(node_id, []).extend(result["reasons"])

    graph_seeds = list(
        dict.fromkeys([*lane_nodes["exact"], *lane_nodes["fts"], *lane_nodes["semantic"]])
    )[:8]
    bounded_graph_ids = set(graph_seeds)
    if graph_seeds and max_depth > 0 and graph_strategy in {"bfs", "hybrid"}:
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
        bounded_graph_ids.update(
            str(record["node"]["id"]) for record in expansion["nodes"]
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

    if graph_seeds and max_depth > 0 and graph_strategy in {"ppr", "hybrid"}:
        if graph_strategy == "ppr":
            ppr_expansion = expand_index(
                path,
                graph_seeds,
                namespace=namespace,
                max_depth=max_depth,
                limit=min(MAX_LIMIT, max(limit * 4, 40)),
                include_taxonomy=include_taxonomy,
                include_stale=include_stale,
                include_orphaned=include_orphaned,
            )
            bounded_graph_ids.update(
                str(record["node"]["id"]) for record in ppr_expansion["nodes"]
            )
        seed_weights: dict[str, float] = {}
        for lane, weight in (("exact", 3.0), ("fts", 1.0), ("semantic", 0.8)):
            for rank, node_id in enumerate(dict.fromkeys(lane_nodes[lane]), start=1):
                seed_weights[node_id] = seed_weights.get(node_id, 0.0) + weight / rank
        ppr_candidate_ids = set(graph_seeds)
        ppr_candidate_ids.update(
            sorted(bounded_graph_ids - ppr_candidate_ids)[: 400 - len(ppr_candidate_ids)]
        )
        ppr = personalized_pagerank(
            path,
            seed_weights,
            namespace=namespace,
            node_types=node_types,
            include_taxonomy=include_taxonomy,
            include_similarity=True,
            include_stale=include_stale,
            include_orphaned=include_orphaned,
            limit=min(MAX_LIMIT, max(limit * 4, 40)),
            _candidate_ids=ppr_candidate_ids,
        )
        for result in ppr["results"]:
            node = result["node"]
            node_id = str(node["id"])
            lane_nodes["ppr"].append(node_id)
            nodes[node_id] = node
            reasons.setdefault(node_id, []).append(
                {
                    "method": "ppr",
                    "rank": result["rank"],
                    "score": result["score"],
                    "seed": result["seed"],
                    "trusted_edges": ppr["policy"]["trusted_edges"],
                    "similarity_edges": ppr["policy"]["similarity_edges"],
                }
            )

    lane_weights = {
        "exact": 3.0,
        "fts": 1.0,
        "semantic": 0.9,
        "graph": 0.7,
        "ppr": 0.6,
    }
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
    results = [
        {
            "rank": rank,
            "score": round(scores[node_id], 12),
            "node": nodes[node_id],
            "reasons": reasons.get(node_id, []),
        }
        for rank, node_id in enumerate(ordered, start=1)
    ]
    if rerank_provider is None or not results:
        return results
    reranked = rerank_provider.rerank(query, list(results))
    expected = {str(result["node"]["id"]): result for result in results}
    ordered_ids: list[str] = []
    for item in reranked:
        node = item.get("node") if isinstance(item, dict) else None
        node_id = str((node or {}).get("id", ""))
        if node_id in expected and node_id not in ordered_ids:
            ordered_ids.append(node_id)
    ordered_ids.extend(node_id for node_id in ordered if node_id not in ordered_ids)
    final: list[dict[str, Any]] = []
    for rank, node_id in enumerate(ordered_ids[:limit], start=1):
        result = dict(expected[node_id])
        result["rank"] = rank
        result["reasons"] = [
            *result["reasons"],
            {
                "method": "rerank",
                "rank": rank,
                "provider": str(rerank_provider.name),
                "model": str(rerank_provider.model),
            },
        ]
        final.append(result)
    return final


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
    graph_strategy: str = "hybrid",
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
        graph_strategy=graph_strategy,
    )
    policy = {
        "node_types": sorted(set(node_types or [])),
        "max_depth": max_depth,
        "include_taxonomy": include_taxonomy,
        "include_stale": include_stale,
        "include_orphaned": include_orphaned,
        "graph_strategy": graph_strategy,
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


def _trusted_resolution_matches(
    path: Path,
    candidate: dict[str, Any],
    target_namespace: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    properties = candidate.get("properties") or {}
    probes: list[tuple[str, str]] = []
    explicit_target = str(properties.get("target_id", "")).strip()
    if explicit_target:
        probes.append((explicit_target, "explicit-target-id"))
    probes.extend(
        [
            (str(candidate.get("id", "")), "id"),
            (str(candidate.get("label", "")), "label"),
        ]
    )
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
        if resolution["status"] not in {"exact", "alias", "ambiguous"}:
            continue
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
                    "identity_authority": resolution["status"] in {"exact", "alias"},
                }
            )
        if explicit_target and source == "explicit-target-id":
            return list(matches.values()), evidence, len(matches) != 1
        if source == "id" and resolution["status"] == "exact":
            return list(matches.values()), evidence, False
    return list(matches.values()), evidence, ambiguous or len(matches) > 1


def _indexed_alignment_mappings(
    path: Path,
    subject_namespace: str,
    subject_id: str,
    object_namespace: str,
) -> list[dict[str, Any]]:
    connection = _connect(path)
    try:
        return [
            json.loads(row["payload"])
            for row in connection.execute(
                """
                SELECT payload FROM alignment_mappings
                WHERE subject_namespace = ? AND subject_id = ? AND object_namespace = ?
                ORDER BY status, predicate, object_id, mapping_id
                """,
                (subject_namespace, subject_id, object_namespace),
            )
        ]
    finally:
        connection.close()


def _mapping_freshness(
    mapping: dict[str, Any],
    candidate: dict[str, Any],
    target: dict[str, Any] | None,
) -> dict[str, Any]:
    subject = mapping.get("subject") or {}
    object_ = mapping.get("object") or {}
    subject_expected = str(subject.get("node_sha256", ""))
    object_expected = str(object_.get("node_sha256", ""))
    subject_actual = node_fingerprint(candidate)
    object_actual = node_fingerprint(target) if target is not None else ""
    return {
        "subject_fresh": not subject_expected or subject_expected == subject_actual,
        "object_fresh": target is not None and (
            not object_expected or object_expected == object_actual
        ),
        "subject_expected": subject_expected,
        "subject_actual": subject_actual,
        "object_expected": object_expected,
        "object_actual": object_actual,
    }


def _candidate_probe_texts(
    candidate: dict[str, Any], scoped_aliases: list[dict[str, Any]]
) -> list[tuple[str, str]]:
    properties = candidate.get("properties") or {}
    probes = [
        (str(candidate.get("label", "")), "label"),
        *[
            (str(alias), "declared-alias")
            for alias in properties.get("aliases", [])
            if str(alias).strip()
        ],
        *[
            (str(alias.get("expansion", "")), "scoped-alias-expansion")
            for alias in scoped_aliases
            if str(alias.get("expansion", "")).strip()
        ],
    ]
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for probe, source in probes:
        normalized = normalize_name(probe)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append((probe, source))
    return result


def _alignment_lexical_overlap(probe: str, target: dict[str, Any]) -> float:
    stopwords = {
        "a",
        "an",
        "and",
        "concept",
        "in",
        "of",
        "paper",
        "the",
        "theorem",
    }
    probe_terms = {
        term.casefold()
        for term in QUERY_TERM_RE.findall(unicodedata.normalize("NFKC", probe))
        if term.casefold() not in stopwords and len(term) > 1
    }
    properties = target.get("properties") or {}
    target_text = " ".join(
        [
            str(target.get("label", "")),
            *[str(alias) for alias in properties.get("aliases", [])],
        ]
    )
    target_terms = {
        term.casefold()
        for term in QUERY_TERM_RE.findall(unicodedata.normalize("NFKC", target_text))
        if term.casefold() not in stopwords and len(term) > 1
    }
    if not probe_terms or not target_terms:
        return 0.0
    return len(probe_terms & target_terms) / len(probe_terms | target_terms)


def _target_graph_edges(path: Path, namespace: str) -> set[tuple[str, str, str]]:
    connection = _connect(path)
    try:
        return {
            (str(row["source"]), str(row["relation"]), str(row["target"]))
            for row in connection.execute(
                "SELECT source, relation, target FROM edges WHERE namespace = ?",
                (namespace,),
            )
        }
    finally:
        connection.close()


def _all_index_nodes(path: Path, namespace: str) -> list[dict[str, Any]]:
    connection = _connect(path)
    try:
        return [
            _node_payload(row)
            for row in connection.execute(
                "SELECT * FROM nodes WHERE namespace = ? ORDER BY id", (namespace,)
            )
        ]
    finally:
        connection.close()


def align_graph(
    path: Path,
    candidate_snapshot: dict[str, Any],
    *,
    target_namespace: str = "personal",
    limit_per_node: int = 10,
    embedding_provider: EmbeddingProvider | None = None,
    rerank_provider: RerankProvider | None = None,
    candidate_analyzer: CandidateAnalyzer | None = None,
) -> dict[str, Any]:
    """Propose explainable cross-namespace mappings without committing identity."""
    candidate_namespace, _ = _validated_snapshot(candidate_snapshot)
    _validate_namespace(target_namespace)
    limit_per_node = _validated_limit(limit_per_node)
    if candidate_namespace == target_namespace:
        raise AgentIndexError("candidate and target namespaces must be distinct")
    status = index_status(path)
    if status.get("namespace") != target_namespace:
        raise AgentIndexError(f"target namespace is not indexed: {target_namespace!r}")
    try:
        scoped = extract_scoped_aliases(candidate_snapshot)
    except AlignmentError as error:
        raise AgentIndexError(str(error)) from error
    aliases_by_node: dict[str, list[dict[str, Any]]] = {}
    for alias in scoped["aliases"]:
        aliases_by_node.setdefault(str(alias["node_id"]), []).append(alias)
    target_nodes = _all_index_nodes(path, target_namespace)
    targets_by_id = {str(node["id"]): node for node in target_nodes}
    target_edges = _target_graph_edges(path, target_namespace)
    candidate_nodes = sorted(
        candidate_snapshot.get("nodes") or [], key=lambda item: str(item.get("id", ""))
    )
    hard_anchors: dict[str, str] = {}
    trusted_evidence: dict[str, list[dict[str, Any]]] = {}
    mapping_evidence: dict[str, list[dict[str, Any]]] = {}
    rejected_targets: dict[str, set[str]] = {}
    for candidate in candidate_nodes:
        candidate_id = str(candidate["id"])
        matches, evidence, ambiguous = _trusted_resolution_matches(
            path, candidate, target_namespace
        )
        trusted_evidence[candidate_id] = evidence
        if len(matches) == 1 and not ambiguous:
            hard_anchors[candidate_id] = str(matches[0]["id"])
        for mapping in _indexed_alignment_mappings(
            path, candidate_namespace, candidate_id, target_namespace
        ):
            object_id = str((mapping.get("object") or {}).get("node_id", ""))
            target = targets_by_id.get(object_id)
            freshness = _mapping_freshness(mapping, candidate, target)
            decision_fresh = all(
                (freshness["subject_fresh"], freshness["object_fresh"])
            )
            record = {
                "kind": "alignment-registry",
                "mapping_id": mapping["id"],
                "predicate": mapping["predicate"],
                "status": mapping["status"],
                "target_id": object_id,
                "freshness": freshness,
                "decision_fresh": decision_fresh,
                "identity_authority": False,
            }
            mapping_evidence.setdefault(candidate_id, []).append(record)
            if (
                mapping["status"] == "reviewed"
                and mapping["predicate"] == "exact-match"
                and decision_fresh
            ):
                hard_anchors[candidate_id] = object_id
                record["identity_authority"] = True
            if decision_fresh and (
                mapping["status"] == "rejected"
                or (
                    mapping["status"] == "reviewed"
                    and mapping["predicate"] == "different-from"
                )
            ):
                rejected_targets.setdefault(candidate_id, set()).add(object_id)
        if hard_anchors.get(candidate_id) in rejected_targets.get(candidate_id, set()):
            hard_anchors.pop(candidate_id, None)

    raw_candidates: dict[str, dict[str, dict[str, Any]]] = {}
    for candidate in candidate_nodes:
        candidate_id = str(candidate["id"])
        pool: dict[str, dict[str, Any]] = {}

        def add_target(target: dict[str, Any], signal: dict[str, Any], rank_points: float) -> None:
            target_id = str(target["id"])
            if target_id in rejected_targets.get(candidate_id, set()):
                return
            record = pool.setdefault(
                target_id,
                {
                    "target": {
                        "namespace": target_namespace,
                        "id": target_id,
                        "label": target.get("label", ""),
                        "type": target.get("type", ""),
                    },
                    "rank_score": 0.0,
                    "signals": [],
                },
            )
            if signal not in record["signals"]:
                record["signals"].append(signal)
                record["rank_score"] += rank_points

        for target_id, target in targets_by_id.items():
            if target_id == hard_anchors.get(candidate_id):
                add_target(
                    target,
                    {
                        "kind": "trusted-identity",
                        "identity_authority": True,
                        "evidence": [
                            *trusted_evidence.get(candidate_id, []),
                            *mapping_evidence.get(candidate_id, []),
                        ],
                    },
                    1000.0,
                )
        for registry_signal in mapping_evidence.get(candidate_id, []):
            if registry_signal.get("status") == "deprecated":
                continue
            target_id = str(registry_signal.get("target_id", ""))
            target = targets_by_id.get(target_id)
            if target is None:
                continue
            add_target(
                target,
                {
                    **registry_signal,
                    "kind": "stale-or-nonidentity-alignment",
                    "identity_authority": False,
                },
                (
                    500.0
                    if registry_signal.get("status") == "reviewed"
                    and registry_signal.get("decision_fresh")
                    else 180.0
                ),
            )
        for probe, probe_source in _candidate_probe_texts(
            candidate, aliases_by_node.get(candidate_id, [])
        ):
            resolution = resolve_concepts(path, [probe], namespace=target_namespace)[0]
            for target in resolution.get("matches", []):
                identity_authority = probe_source != "scoped-alias-expansion" and resolution[
                    "status"
                ] in {"exact", "alias"}
                points = 900.0 if identity_authority else 700.0
                add_target(
                    target,
                    {
                        "kind": (
                            "name-resolution"
                            if identity_authority
                            else "explicit-scoped-alias"
                        ),
                        "probe": probe,
                        "probe_source": probe_source,
                        "resolution": resolution["status"],
                        "identity_authority": identity_authority,
                    },
                    points,
                )
            try:
                lexical = search_index(
                    path,
                    probe,
                    namespace=target_namespace,
                    limit=min(MAX_LIMIT, max(limit_per_node * 2, 10)),
                )
            except AgentIndexError:
                lexical = []
            for result in lexical:
                overlap = _alignment_lexical_overlap(probe, result["node"])
                if overlap <= 0.0:
                    continue
                add_target(
                    result["node"],
                    {
                        "kind": "lexical-candidate",
                        "probe": probe,
                        "probe_source": probe_source,
                        "rank": result["rank"],
                        "overlap": round(overlap, 12),
                        "identity_authority": False,
                    },
                    240.0 / max(1, int(result["rank"])),
                )
        surfaces = [
            str(candidate.get("label", "")),
            *[
                str(alias)
                for alias in (candidate.get("properties") or {}).get("aliases", [])
            ],
            *[str(alias.get("surface", "")) for alias in aliases_by_node.get(candidate_id, [])],
        ]
        short_forms = {
            re.sub(r"[^A-Za-z0-9]", "", surface).upper()
            for surface in surfaces
            if re.fullmatch(r"[A-Z][A-Z0-9-]{1,15}", surface.strip())
        }
        for short in sorted(short_forms):
            for target in target_nodes:
                target_names = [
                    str(target.get("label", "")),
                    *[
                        str(alias)
                        for alias in (target.get("properties") or {}).get("aliases", [])
                    ],
                ]
                if any(canonical_acronym(name) == short for name in target_names):
                    add_target(
                        target,
                        {
                            "kind": "acronym-candidate",
                            "surface": short,
                            "identity_authority": False,
                        },
                        350.0,
                    )
        if embedding_provider is not None:
            semantic_query = _embedding_text(candidate)
            for result in semantic_search(
                path,
                semantic_query,
                embedding_provider,
                namespace=target_namespace,
                limit=min(MAX_LIMIT, max(limit_per_node * 2, 10)),
            ):
                reason = result["reasons"][0]
                add_target(
                    result["node"],
                    {
                        "kind": "semantic-candidate",
                        "rank": result["rank"],
                        "score": reason["score"],
                        "provider": reason["provider"],
                        "model": reason["model"],
                        "identity_authority": False,
                    },
                    180.0 / max(1, int(result["rank"])),
                )
        for record in pool.values():
            target = targets_by_id[str(record["target"]["id"])]
            same_type = str(candidate.get("type", "")) == str(target.get("type", ""))
            record["signals"].append(
                {
                    "kind": "type-compatibility",
                    "compatible": same_type,
                    "candidate_type": str(candidate.get("type", "")),
                    "target_type": str(target.get("type", "")),
                    "identity_authority": False,
                }
            )
            record["rank_score"] += 40.0 if same_type else -100.0
            if candidate_analyzer is not None:
                analysis = candidate_analyzer.compare(
                    candidate,
                    target,
                    list(record["signals"]),
                )
                record["signals"].append(
                    {
                        "kind": "analyzer-proposal",
                        "provider": str(candidate_analyzer.name),
                        "analysis": analysis,
                        "identity_authority": False,
                    }
                )
        raw_candidates[candidate_id] = pool

    candidate_edges = candidate_snapshot.get("edges") or []
    for candidate_id, pool in raw_candidates.items():
        for target_id, record in pool.items():
            checked = 0
            matched = 0
            evidence: list[dict[str, Any]] = []
            for edge in candidate_edges:
                source = str(edge.get("source", ""))
                target = str(edge.get("target", ""))
                relation = str(edge.get("relation", ""))
                expected: tuple[str, str, str] | None = None
                neighbor_id = ""
                if source == candidate_id and target in hard_anchors:
                    neighbor_id = target
                    expected = (target_id, relation, hard_anchors[target])
                elif target == candidate_id and source in hard_anchors:
                    neighbor_id = source
                    expected = (hard_anchors[source], relation, target_id)
                if expected is None:
                    continue
                checked += 1
                is_match = expected in target_edges
                matched += int(is_match)
                evidence.append(
                    {
                        "candidate_neighbor": neighbor_id,
                        "mapped_neighbor": hard_anchors[neighbor_id],
                        "relation": relation,
                        "direction": "outgoing" if source == candidate_id else "incoming",
                        "matched": is_match,
                    }
                )
            if checked:
                record["signals"].append(
                    {
                        "kind": "graph-consistency",
                        "matched": matched,
                        "checked": checked,
                        "evidence": evidence,
                        "identity_authority": False,
                    }
                )
                record["rank_score"] += matched * 120.0 - (checked - matched) * 25.0

    results: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    for candidate in candidate_nodes:
        candidate_id = str(candidate["id"])
        ordered = sorted(
            raw_candidates[candidate_id].values(),
            key=lambda item: (
                -float(item["rank_score"]),
                str(item["target"].get("label", "")),
                str(item["target"]["id"]),
            ),
        )
        if rerank_provider is not None and ordered:
            reranked = rerank_provider.rerank(_embedding_text(candidate), list(ordered))
            by_id = {str(item["target"]["id"]): item for item in ordered}
            ordered_ids: list[str] = []
            for item in reranked:
                target_id = str((item.get("target") or {}).get("id", ""))
                if target_id in by_id and target_id not in ordered_ids:
                    ordered_ids.append(target_id)
            ordered_ids.extend(target_id for target_id in by_id if target_id not in ordered_ids)
            ordered = [by_id[target_id] for target_id in ordered_ids]
        ordered = ordered[:limit_per_node]
        for rank, item in enumerate(ordered, start=1):
            item["rank"] = rank
            item["rank_score"] = round(float(item["rank_score"]), 12)
        identity_target = hard_anchors.get(candidate_id)
        if identity_target:
            alignment_status = "exact"
        elif not ordered:
            alignment_status = "unresolved"
        elif len(ordered) == 1:
            alignment_status = "candidate"
        else:
            alignment_status = "ambiguous"
        result = {
            "candidate": {
                "namespace": candidate_namespace,
                "id": candidate_id,
                "label": candidate.get("label", ""),
                "type": candidate.get("type", ""),
                "node_sha256": node_fingerprint(candidate),
            },
            "status": alignment_status,
            "identity_target_id": identity_target,
            "candidates": ordered,
            "scoped_aliases": aliases_by_node.get(candidate_id, []),
            "registry_evidence": mapping_evidence.get(candidate_id, []),
            "rejected_target_ids": sorted(rejected_targets.get(candidate_id, set())),
        }
        results.append(result)
        if not identity_target:
            for item in ordered[:3]:
                target_id = str(item["target"]["id"])
                proposal = {
                    "subject": {
                        "namespace": candidate_namespace,
                        "node_id": candidate_id,
                        "node_sha256": node_fingerprint(candidate),
                    },
                    "predicate": "exact-match",
                    "object": {
                        "namespace": target_namespace,
                        "node_id": target_id,
                        "node_sha256": node_fingerprint(targets_by_id[target_id]),
                    },
                    "status": "proposed",
                    "mapping_justification": sorted(
                        {
                            str(signal["kind"])
                            for signal in item["signals"]
                            if signal["kind"] != "type-compatibility"
                        }
                    ),
                    "evidence": item["signals"],
                    "scores": {"rank_score": item["rank_score"]},
                }
                proposal["id"] = mapping_id(proposal)
                proposals.append(proposal)
    summary = {
        name: sum(result["status"] == name for result in results)
        for name in ("exact", "candidate", "ambiguous", "unresolved")
    }
    summary["total"] = len(results)
    report = {
        "schema": ALIGNMENT_REPORT_SCHEMA,
        "candidate": {
            "namespace": candidate_namespace,
            "snapshot_sha256": candidate_snapshot["snapshot_sha256"],
        },
        "target": {
            "namespace": target_namespace,
            "snapshot_sha256": status["snapshot_sha256"],
        },
        "scoped_aliases": scoped,
        "results": results,
        "proposals": sorted(proposals, key=lambda item: str(item["id"])),
        "summary": summary,
    }
    report["report_sha256"] = sha256_json(report)
    return report


def _candidate_matches(
    path: Path,
    candidate: dict[str, Any],
    target_namespace: str,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    matches, evidence, ambiguous = _trusted_resolution_matches(
        path, candidate, target_namespace
    )
    if len(matches) == 1 and not ambiguous:
        return "matched", matches, evidence
    if matches or ambiguous:
        return "ambiguous", matches, evidence
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
    alignment = align_graph(
        path,
        candidate_snapshot,
        target_namespace=target_namespace,
    )
    alignments_by_id = {
        str(item["candidate"]["id"]): item for item in alignment["results"]
    }
    indexed_targets = {
        str(node["id"]): node for node in _all_index_nodes(path, target_namespace)
    }
    target_map: dict[str, str] = {}
    records: dict[str, dict[str, Any]] = {}
    for candidate in candidate_nodes:
        candidate_id = str(candidate["id"])
        alignment_record = alignments_by_id[candidate_id]
        identity_target_id = alignment_record.get("identity_target_id")
        if identity_target_id and str(identity_target_id) in indexed_targets:
            match_status = "matched"
            matches = [indexed_targets[str(identity_target_id)]]
        elif alignment_record["candidates"]:
            match_status = "ambiguous"
            matches = [
                indexed_targets[str(item["target"]["id"])]
                for item in alignment_record["candidates"]
                if str(item["target"]["id"]) in indexed_targets
            ]
        else:
            match_status = "missing"
            matches = []
        evidence = [
            {
                "kind": "alignment",
                "alignment_status": alignment_record["status"],
                "scoped_aliases": alignment_record["scoped_aliases"],
                "registry_evidence": alignment_record["registry_evidence"],
                "rejected_target_ids": alignment_record["rejected_target_ids"],
                "candidates": alignment_record["candidates"],
            }
        ]
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
    summary = {
        status_name: 0
        for status_name in ("known", "partial", "new", "conflict", "uncertain")
    }
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
        "alignment_report_sha256": alignment["report_sha256"],
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
