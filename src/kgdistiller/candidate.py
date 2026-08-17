"""Deterministic builder for isolated Agent candidate snapshots."""

from __future__ import annotations

import copy
import heapq
import json
import re
from importlib import resources
from typing import Any

from .cli import GRAPH_SCHEMA, ID_RE, MAX_NODE_LABEL_LENGTH
from .contracts import MAX_NAMESPACE_LENGTH, sha256_json
from .query import QueryError, SNAPSHOT_SCHEMA, validate_agent_snapshot
from .json_schema import validate_json_schema


CANDIDATE_SOURCE_SCHEMA = "kgdistiller-candidate-graph-v1"
NAMESPACE_RE = re.compile(
    rf"(?=.{{1,{MAX_NAMESPACE_LENGTH}}}\Z)[a-z0-9][a-z0-9._-]*"
    r"(?::[a-z0-9][a-z0-9._-]*)*"
)
RELATIONS = {
    "contains",
    "prerequisite-for",
    "implies",
    "generalizes",
    "contrasts-with",
    "derived-from",
}
MAX_NODES = 100_000
MAX_EDGES = 500_000
MAX_REFERENCES = 500_000


class CandidateError(ValueError):
    """Raised when a candidate graph cannot become a valid snapshot."""


def _schema() -> dict[str, Any]:
    return json.loads(
        resources.files("kgdistiller")
        .joinpath("schemas", "kgdistiller-candidate-graph-v1.schema.json")
        .read_text(encoding="utf-8")
    )


def validate_candidate_graph(payload: Any) -> dict[str, Any]:
    errors = validate_json_schema(payload, _schema())
    if errors:
        first = errors[0]
        path = ".".join(str(item) for item in first.path) or "candidate"
        raise CandidateError(
            f"candidate JSON Schema validation failed at {path}: {first.message}"
        )
    candidate = copy.deepcopy(payload)
    namespace = str(candidate["namespace"])
    if namespace == "personal" or not NAMESPACE_RE.fullmatch(namespace):
        raise CandidateError(
            "candidate namespace must be valid, isolated, and distinct from personal"
        )
    nodes = candidate["nodes"]
    edges = candidate["edges"]
    references = candidate["references"]
    if len(nodes) > MAX_NODES or len(edges) > MAX_EDGES or len(references) > MAX_REFERENCES:
        raise CandidateError("candidate graph exceeds deterministic builder limits")
    node_ids: set[str] = set()
    if candidate["diagnostics"]["errors"]:
        raise CandidateError("candidate diagnostics contain extraction errors")
    for node in nodes:
        node_id = str(node["id"])
        if not ID_RE.fullmatch(node_id) or node_id in node_ids:
            raise CandidateError(f"duplicate or invalid candidate node id: {node_id!r}")
        if len(str(node.get("label", ""))) > MAX_NODE_LABEL_LENGTH:
            raise CandidateError(f"candidate node label is too long: {node_id!r}")
        node_ids.add(node_id)
        provenance = node.get("provenance") or {}
        if not str(provenance.get("authority", "")).strip():
            raise CandidateError(f"candidate node has no source authority: {node_id}")
        if not any(
            provenance.get(field) not in {None, ""}
            for field in ("line", "page", "section", "equation")
        ):
            raise CandidateError(f"candidate node has no bounded source location: {node_id}")
    seen_edges: set[tuple[str, str, str]] = set()
    adjacency: dict[str, dict[str, set[str]]] = {
        relation: {} for relation in ("contains", "prerequisite-for")
    }
    for edge in edges:
        key = (str(edge["source"]), str(edge["relation"]), str(edge["target"]))
        if key[0] not in node_ids or key[2] not in node_ids:
            raise CandidateError(f"candidate contains a dangling edge: {key}")
        if key[1] not in RELATIONS:
            raise CandidateError(f"unsupported candidate relation: {key[1]!r}")
        if key in seen_edges:
            raise CandidateError(f"duplicate candidate edge: {key}")
        if key[1] == "contrasts-with" and (key[2], key[1], key[0]) in seen_edges:
            raise CandidateError(f"duplicate symmetric candidate edge: {key}")
        seen_edges.add(key)
        if key[1] != "contains" and not str(edge.get("evidence", "")).strip():
            raise CandidateError(f"semantic candidate edge has no evidence: {key}")
        if key[1] in adjacency:
            adjacency[key[1]].setdefault(key[0], set()).add(key[2])
    for relation, graph in adjacency.items():
        indegree = {node_id: 0 for node_id in node_ids}
        for targets in graph.values():
            for target in targets:
                indegree[target] += 1
        ready = [node_id for node_id, degree in indegree.items() if degree == 0]
        heapq.heapify(ready)
        visited = 0
        while ready:
            node_id = heapq.heappop(ready)
            visited += 1
            for target in sorted(graph.get(node_id, set())):
                indegree[target] -= 1
                if indegree[target] == 0:
                    heapq.heappush(ready, target)
        if visited != len(node_ids):
            raise CandidateError(f"candidate {relation} relation contains a cycle")
    reference_ids: set[str] = set()
    for reference in references:
        reference_id = str(reference["id"])
        if reference_id in reference_ids:
            raise CandidateError(f"duplicate candidate reference id: {reference_id!r}")
        reference_ids.add(reference_id)
        if str(reference["target"]) not in node_ids:
            raise CandidateError(
                f"candidate reference targets an unknown node: {reference['target']}"
            )
    return candidate


def build_candidate_snapshot(payload: Any) -> dict[str, Any]:
    candidate = validate_candidate_graph(payload)
    nodes = sorted(candidate["nodes"], key=lambda item: str(item["id"]))
    edges = sorted(
        candidate["edges"],
        key=lambda item: (
            str(item["source"]),
            str(item["relation"]),
            str(item["target"]),
        ),
    )
    references = sorted(
        candidate["references"],
        key=lambda item: (
            str(item.get("authority", "")),
            int(item.get("line", 0) or 0),
            str(item["target"]),
            str(item["id"]),
        ),
    )
    graph_payload = {
        "nodes": nodes,
        "edges": edges,
        "references": references,
    }
    snapshot: dict[str, Any] = {
        "schema": SNAPSHOT_SCHEMA,
        "namespace": candidate["namespace"],
        "graph": {
            "schema": GRAPH_SCHEMA,
            "sha256": sha256_json(graph_payload),
            "counts": {
                "nodes": len(nodes),
                "edges": len(edges),
                "references": len(references),
            },
        },
        **graph_payload,
        "diagnostics": copy.deepcopy(candidate["diagnostics"]),
    }
    snapshot["snapshot_sha256"] = sha256_json(snapshot)
    try:
        validate_agent_snapshot(snapshot)
    except QueryError as error:
        raise CandidateError(f"candidate snapshot is invalid: {error}") from error
    return snapshot
