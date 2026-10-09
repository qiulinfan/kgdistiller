"""Bounded deterministic retrieval over the JSON/in-memory query layer."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .alignment import node_fingerprint
from .contracts import ContractError, canonical_json, sha256_json, validate_contract
from .graph_retrieval import (
    GRAPH_SEARCH_EXECUTION_SCHEMA,
    GRAPH_SEARCH_RESULT_SCHEMA,
    GraphRetrievalPolicy,
    build_graph_context,
    graph_lanes,
    view_content_sha256,
)
from .query import (
    DEFAULT_SEMANTIC_RELATIONS,
    GraphView,
    QueryError,
    context,
    expand,
    finalize_token_estimate,
    load_graph_view,
    personalized_pagerank,
    resolve_concepts,
    search,
)
from .semantic_retrieval import (
    SemanticRankingService,
    SemanticRetrievalError,
    search_document,
)

RETRIEVAL_PLAN_SCHEMA = "kgdistiller-retrieval-plan-v1"
SEARCH_RESULT_SCHEMA = "kgdistiller-search-result-v1"
SEARCH_EXECUTION_SCHEMA = "kgdistiller-search-execution-v1"
MODEL_SEARCH_RESULT_SCHEMA = "kgdistiller-search-result-v2"
MODEL_SEARCH_EXECUTION_SCHEMA = "kgdistiller-search-execution-v2"
MAX_RETRIEVAL_PLAN_BYTES = 1024 * 1024
MAX_RETRIEVAL_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_CONTEXT_BUDGET = 200_000
MAX_IDENTITY_MATCHES = 500
MAX_INTERNAL_LANE_RESULTS = 500
MAX_PLAN_GRAPH_SEEDS = 128
_PLAN_FIELDS = {
    "schema",
    "question",
    "namespace",
    "identity_queries",
    "lexical_queries",
    "graph",
    "filters",
    "limit",
}


class RetrievalError(ValueError):
    """Stable bounded-retrieval error safe for CLI and MCP responses."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message

    def to_payload(self) -> dict[str, str]:
        return {"kind": "kgdistiller-retrieval-error", "code": self.code, "message": self.message}

    payload = to_payload


def _bounded_json_int(value: str) -> int:
    if len(value.lstrip("-")) > 32:
        raise ValueError("JSON integer is too long")
    return int(value)


def _bounded_json_float(value: str) -> float:
    if len(value) > 64:
        raise ValueError("JSON number is too long")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("JSON number is not finite")
    return parsed


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _read_bounded_regular_file(path: Path) -> bytes:
    handle: int | None = None
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        handle = os.open(path, flags)
        metadata = os.fstat(handle)
        if not stat.S_ISREG(metadata.st_mode):
            raise RetrievalError("invalid-plan", "retrieval plan must be a regular file")
        if metadata.st_size > MAX_RETRIEVAL_PLAN_BYTES:
            raise RetrievalError("plan-too-large", "retrieval plan exceeds the byte limit")
        chunks: list[bytes] = []
        remaining = MAX_RETRIEVAL_PLAN_BYTES + 1
        while remaining:
            chunk = os.read(handle, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > MAX_RETRIEVAL_PLAN_BYTES:
            raise RetrievalError("plan-too-large", "retrieval plan exceeds the byte limit")
        return payload
    except FileNotFoundError as error:
        raise RetrievalError("plan-not-found", "retrieval plan does not exist") from error
    except RetrievalError:
        raise
    except OSError as error:
        raise RetrievalError("plan-unreadable", "retrieval plan could not be read") from error
    finally:
        if handle is not None:
            try:
                os.close(handle)
            except OSError:
                pass


def _queries(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 32:
        raise RetrievalError("invalid-plan", f"{RETRIEVAL_PLAN_SCHEMA} field {field} must contain at most 32 strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > 2048:
            raise RetrievalError("invalid-plan", f"{RETRIEVAL_PLAN_SCHEMA} field {field} contains an invalid query")
        if item in result:
            raise RetrievalError("invalid-plan", f"{RETRIEVAL_PLAN_SCHEMA} field {field} must be unique")
        result.append(item)
    return result


def _validated_plan(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema") != RETRIEVAL_PLAN_SCHEMA:
        raise RetrievalError("invalid-plan", f"expected schema {RETRIEVAL_PLAN_SCHEMA}")
    if set(payload) != _PLAN_FIELDS:
        raise RetrievalError("invalid-plan", f"{RETRIEVAL_PLAN_SCHEMA} requires exactly: {', '.join(sorted(_PLAN_FIELDS))}")
    question = payload.get("question")
    namespace = payload.get("namespace")
    if not isinstance(question, str) or not question.strip() or len(question) > 8192:
        raise RetrievalError("invalid-plan", "plan question is invalid")
    if not isinstance(namespace, str) or not namespace or len(namespace) > 256:
        raise RetrievalError("invalid-plan", "plan namespace is invalid")
    _queries(payload.get("identity_queries"), "identity_queries")
    _queries(payload.get("lexical_queries"), "lexical_queries")
    if "semantic_queries" in payload:
        raise RetrievalError("invalid-plan", f"{RETRIEVAL_PLAN_SCHEMA} does not accept semantic_queries")
    graph = payload.get("graph")
    expected_graph = {"seed_ids", "edge_types", "direction", "max_depth", "strategy"}
    if not isinstance(graph, dict) or set(graph) != expected_graph:
        raise RetrievalError("invalid-plan", f"{RETRIEVAL_PLAN_SCHEMA} graph requires exactly: {', '.join(sorted(expected_graph))}")
    seeds = graph.get("seed_ids")
    edge_types = graph.get("edge_types")
    if not isinstance(seeds, list) or len(seeds) > MAX_PLAN_GRAPH_SEEDS or len(seeds) != len(set(seeds)) or any(not isinstance(item, str) or not item for item in seeds):
        raise RetrievalError("invalid-plan", "plan graph.seed_ids is invalid")
    if not isinstance(edge_types, list) or len(edge_types) > 16 or len(edge_types) != len(set(edge_types)) or any(item not in {*DEFAULT_SEMANTIC_RELATIONS, "contains"} for item in edge_types):
        raise RetrievalError("invalid-plan", "plan graph.edge_types is invalid")
    if graph.get("direction") not in {"out", "in", "both"} or graph.get("strategy") not in {"bfs", "ppr", "hybrid"}:
        raise RetrievalError("invalid-plan", "plan graph direction or strategy is invalid")
    depth = graph.get("max_depth")
    if isinstance(depth, bool) or not isinstance(depth, int) or not 0 <= depth <= 8:
        raise RetrievalError("invalid-plan", "plan graph.max_depth is invalid")
    filters = payload.get("filters")
    expected_filters = {"node_types", "include_stale", "include_orphaned"}
    if not isinstance(filters, dict) or set(filters) != expected_filters:
        raise RetrievalError("invalid-plan", f"{RETRIEVAL_PLAN_SCHEMA} filters requires exactly: {', '.join(sorted(expected_filters))}")
    node_types = filters.get("node_types")
    if not isinstance(node_types, list) or len(node_types) > 16 or len(node_types) != len(set(node_types)) or any(item not in {"knowledge", "field", "topic"} for item in node_types):
        raise RetrievalError("invalid-plan", "plan filters.node_types is invalid")
    if not all(isinstance(filters.get(key), bool) for key in ("include_stale", "include_orphaned")):
        raise RetrievalError("invalid-plan", "plan filter flags must be booleans")
    limit = payload.get("limit")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise RetrievalError("invalid-plan", "plan limit must be between 1 and 500")
    try:
        canonical_json(payload)
    except Exception as error:
        raise RetrievalError("invalid-plan", "retrieval plan is not canonical finite JSON") from error
    try:
        return validate_contract(payload)
    except ContractError as error:
        raise RetrievalError("invalid-plan", f"{RETRIEVAL_PLAN_SCHEMA} contract validation failed") from error


def load_retrieval_plan(path: Path) -> dict[str, Any]:
    raw = _read_bounded_regular_file(Path(path))
    try:
        payload = json.loads(raw.decode("utf-8"), parse_int=_bounded_json_int, parse_float=_bounded_json_float, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, RecursionError, OverflowError) as error:
        raise RetrievalError("invalid-plan", "retrieval plan is invalid") from error
    return _validated_plan(payload)


def legacy_retrieval_plan(
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
) -> dict[str, Any]:
    if not isinstance(query, str) or not query.strip() or len(query) > 4096:
        raise RetrievalError("invalid-retrieval-request", "legacy retrieval query is invalid")
    edge_types = sorted(DEFAULT_SEMANTIC_RELATIONS | ({"contains"} if include_taxonomy else set()))
    return _validated_plan(
        {
            "schema": RETRIEVAL_PLAN_SCHEMA,
            "question": query,
            "namespace": namespace,
            "identity_queries": [query[:2048]],
            "lexical_queries": [query[:2048]],
            "graph": {"seed_ids": [], "edge_types": edge_types, "direction": "both", "max_depth": max_depth, "strategy": graph_strategy},
            "filters": {"node_types": sorted(set(node_types or [])), "include_stale": include_stale, "include_orphaned": include_orphaned},
            "limit": limit,
        }
    )


def _query_lane(queries: int, results: int) -> dict[str, Any]:
    return {"status": "enabled", "queries": queries, "results": min(results, MAX_INTERNAL_LANE_RESULTS)}


def _seed_lane(
    seeds: int,
    results: int,
    *,
    enabled: bool = True,
    degraded_reason: str | None = None,
) -> dict[str, Any]:
    if enabled and degraded_reason is not None:
        return {
            "status": "degraded",
            "seeds": seeds,
            "results": min(results, MAX_INTERNAL_LANE_RESULTS),
            "reason": degraded_reason,
        }
    if enabled:
        return {"status": "enabled", "seeds": seeds, "results": min(results, MAX_INTERNAL_LANE_RESULTS)}
    return {"status": "disabled", "seeds": seeds, "results": 0, "reason": "strategy-disabled"}


def _add_lane(
    fused: dict[str, dict[str, Any]],
    lane: str,
    rows: list[tuple[str, float, list[dict[str, Any]], list[dict[str, Any]]]],
) -> None:
    for rank, (node_id, raw_score, seed_evidence, path_evidence) in enumerate(rows[:MAX_INTERNAL_LANE_RESULTS], start=1):
        record = fused.setdefault(node_id, {"lanes": {}, "seed_evidence": [], "path_evidence": []})
        existing = record["lanes"].get(lane)
        if existing is None:
            record["lanes"][lane] = {"rank": rank, "score": float(raw_score)}
        else:
            existing["rank"] = min(int(existing["rank"]), rank)
            existing["score"] = max(float(existing["score"]), float(raw_score))
        for item in seed_evidence:
            if item not in record["seed_evidence"]:
                record["seed_evidence"].append(item)
        for item in path_evidence:
            if item not in record["path_evidence"]:
                record["path_evidence"].append(item)


def _passes_filters(node: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
    node_types = set(filters["node_types"])
    if node_types and node.get("type") not in node_types:
        return False
    properties = node.get("properties")
    properties = properties if isinstance(properties, Mapping) else {}
    provenance = node.get("provenance")
    provenance = provenance if isinstance(provenance, Mapping) else {}
    source_status = properties.get("source_status")
    active = provenance.get("active") is not False
    if not active and not (filters["include_orphaned"] and source_status == "orphaned"):
        return False
    if not filters["include_stale"] and properties.get("curation_status") == "needs-review":
        return False
    return filters["include_orphaned"] or source_status != "orphaned"


def execute_retrieval_plan(
    graph_dir: GraphView | Path,
    plan: dict[str, Any],
    *,
    plan_mode: str = "planned",
    namespace: str | None = None,
    alignments: Path | None = None,
    repo_root: Path | None = None,
    expected_graph_sha256: str | None = None,
    ranking_service: SemanticRankingService | None = None,
    graph_policy: GraphRetrievalPolicy | None = None,
) -> dict[str, Any]:
    """Execute deterministic lanes and explicitly opted-in embedding candidates."""
    plan = _validated_plan(plan)
    if plan_mode not in {"planned", "legacy"}:
        raise RetrievalError("invalid-retrieval-request", "plan_mode must be planned or legacy")
    if namespace is not None and namespace != plan["namespace"]:
        raise RetrievalError("namespace-conflict", "request namespace conflicts with retrieval plan")
    try:
        view = graph_dir if isinstance(graph_dir, GraphView) else load_graph_view(graph_dir, alignments, repo_root=repo_root)
    except QueryError as error:
        raise RetrievalError("graph-unavailable", str(error)) from error
    if expected_graph_sha256 is not None and view.snapshot["graph"]["sha256"] != expected_graph_sha256:
        raise RetrievalError("stale-generation", "authority graph changed before retrieval execution")
    if graph_policy is not None and not isinstance(graph_policy, GraphRetrievalPolicy):
        raise RetrievalError("invalid-graph-policy", "expected GraphRetrievalPolicy")
    initial_content_sha256 = view_content_sha256(view) if graph_policy is not None else None
    namespace_value = str(plan["namespace"])
    filters = plan["filters"]
    limit = int(plan["limit"])
    fused: dict[str, dict[str, Any]] = {}

    identity_resolutions: list[dict[str, Any]] = []
    identity_rows: list[tuple[str, float, list[dict[str, Any]], list[dict[str, Any]]]] = []
    unknown_seeds = [
        node_id for node_id in plan["graph"]["seed_ids"] if node_id not in view.nodes
    ]
    if unknown_seeds:
        raise RetrievalError(
            "query-failed", f"unknown graph seed: {namespace_value}:{unknown_seeds[0]}"
        )
    seed_ids = [
        node_id
        for node_id in plan["graph"]["seed_ids"]
        if _passes_filters(view.nodes[node_id], filters)
    ]
    explicit_seed_ids = list(seed_ids)
    identity_priority: dict[str, int] = {}
    try:
        resolutions = resolve_concepts(view, list(plan["identity_queries"]), namespace=namespace_value, match_limit=MAX_IDENTITY_MATCHES)
        for index, resolution in enumerate(resolutions):
            ids = [str(node["id"]) for node in resolution["matches"]]
            identity_resolutions.append(
                {
                    "query_index": index,
                    "status": resolution["status"],
                    "match_kind": resolution["match_kind"],
                    "candidate_ids": ids,
                    "overflow": bool(resolution["overflow"]),
                    "identity_authority": bool(resolution["identity_authority"]),
                }
            )
            for node_id in ids:
                if not _passes_filters(view.nodes[node_id], filters):
                    continue
                if resolution["status"] in {"exact", "alias"}:
                    identity_priority[node_id] = max(
                        identity_priority.get(node_id, 0),
                        2 if resolution["status"] == "exact" else 1,
                    )
                if (
                    node_id not in seed_ids
                    and resolution["status"] != "ambiguous"
                ):
                    seed_ids.append(node_id)
                identity_rows.append((node_id, 1.0 if resolution["status"] != "ambiguous" else 0.5, [], []))
        if len(seed_ids) > MAX_PLAN_GRAPH_SEEDS:
            raise QueryError(
                f"combined graph seed batch exceeds {MAX_PLAN_GRAPH_SEEDS} IDs"
            )
        _add_lane(fused, "identity", identity_rows)

        lexical_best: dict[str, float] = {}
        for query in plan["lexical_queries"]:
            for result in search(view, query, namespace=namespace_value, node_types=filters["node_types"], limit=MAX_INTERNAL_LANE_RESULTS, include_stale=filters["include_stale"], include_orphaned=filters["include_orphaned"]):
                node_id = str(result["node"]["id"])
                score = float(result["reasons"][0]["score"])
                lexical_best[node_id] = max(lexical_best.get(node_id, 0.0), score)
        lexical_rows = [(node_id, score, [], []) for node_id, score in sorted(lexical_best.items(), key=lambda item: (-item[1], item[0]))]
        _add_lane(fused, "lexical", lexical_rows)

        embedding_rows: list[tuple[str, float, list[dict[str, Any]], list[dict[str, Any]]]] = []
        embedding_provenance: dict[str, Any] | None = None
        if ranking_service is not None:
            try:
                candidates, embedding_provenance = ranking_service.rank(
                    view,
                    namespace=namespace_value,
                    question=plan["question"],
                    eligible_ids=[node_id for node_id, node in view.nodes.items() if _passes_filters(node, filters)],
                    limit=MAX_INTERNAL_LANE_RESULTS,
                )
            except SemanticRetrievalError as error:
                raise RetrievalError(error.code, error.message) from error
            embedding_rows = [(node_id, score, [], []) for node_id, score in candidates]
            _add_lane(fused, "embedding", embedding_rows)

        graph_provenance: dict[str, Any] | None = None
        strategy = plan["graph"]["strategy"]
        if graph_policy is not None:
            graph_rows, ppr_rows, seed_ids, graph_provenance, ppr_degraded_reason = graph_lanes(
                view, plan, graph_policy, fused, seed_ids, explicit_seed_ids
            )
            bfs_seed_ids = seed_ids
            accepted_ppr_seed_ids = seed_ids
            _add_lane(fused, "graph", graph_rows)
            _add_lane(fused, "ppr", ppr_rows)
        else:
            graph_rows: list[tuple[str, float, list[dict[str, Any]], list[dict[str, Any]]]] = []
            strategy = plan["graph"]["strategy"]
            bfs_seed_ids = seed_ids
            if bfs_seed_ids and strategy in {"bfs", "hybrid"}:
                expansion = expand(view, bfs_seed_ids, namespace=namespace_value, node_types=filters["node_types"], direction=plan["graph"]["direction"], edge_types=plan["graph"]["edge_types"], max_depth=plan["graph"]["max_depth"], limit=MAX_INTERNAL_LANE_RESULTS, include_taxonomy="contains" in plan["graph"]["edge_types"], include_stale=filters["include_stale"], include_orphaned=filters["include_orphaned"])
                for row in expansion["nodes"]:
                    if not _passes_filters(row["node"], filters):
                        continue
                    path = row["path"]
                    graph_rows.append((str(row["node"]["id"]), 1.0 / (1 + int(row["depth"])), [{"lane": "graph", "seed_id": row["seed_id"]}], [{"lane": "graph", "nodes": [row["seed_id"], *[step["target"] if step["direction"] == "outgoing" else step["source"] for step in path]], "edge_types": [step["relation"] for step in path]}]))
                _add_lane(fused, "graph", graph_rows)

            ppr_rows: list[tuple[str, float, list[dict[str, Any]], list[dict[str, Any]]]] = []
            ppr_degraded_reason: str | None = None
            if seed_ids and strategy in {"ppr", "hybrid"}:
                ranking = personalized_pagerank(view, {node_id: 1.0 for node_id in seed_ids}, namespace=namespace_value, node_types=filters["node_types"], edge_types=plan["graph"]["edge_types"], direction=plan["graph"]["direction"], max_depth=plan["graph"]["max_depth"], include_taxonomy="contains" in plan["graph"]["edge_types"], include_stale=filters["include_stale"], include_orphaned=filters["include_orphaned"], max_iterations=256, limit=MAX_INTERNAL_LANE_RESULTS)
                if ranking.get("converged") is False:
                    ppr_degraded_reason = "not-converged"
                accepted_ppr_seed_ids = [str(node_id) for node_id in ranking["seeds"]]
                for row in ([] if ppr_degraded_reason else ranking["results"]):
                    raw_row_seed_ids = row.get("seed_ids")
                    row_seed_ids = [
                        str(node_id)
                        for node_id in (
                            raw_row_seed_ids
                            if isinstance(raw_row_seed_ids, list)
                            else accepted_ppr_seed_ids
                        )
                        if str(node_id) in ranking["seeds"]
                    ]
                    row_seed_evidence = [
                        {"lane": "ppr", "seed_id": node_id}
                        for node_id in row_seed_ids[:32]
                    ]
                    ppr_rows.append((str(row["node"]["id"]), float(row["score"]), row_seed_evidence, []))
                _add_lane(fused, "ppr", ppr_rows)
    except QueryError as error:
        raise RetrievalError("query-failed", str(error)) from error

    ranked_results: list[tuple[int, int, float, str, dict[str, Any]]] = []
    navigation_rows: list[dict[str, Any]] = []
    for node_id, evidence in fused.items():
        ranking_lanes = evidence["lanes"]
        if graph_policy is not None:
            # Relation proximity is support provenance, not independent query
            # relevance. Correlated BFS/PPR must not boost a neighbor's answer
            # rank or displace the BM25/embedding reranker candidate pool.
            ranking_lanes = {name: value for name, value in evidence["lanes"].items() if name in {"identity", "lexical", "embedding"}}
            navigation_lanes = {name: value for name, value in evidence["lanes"].items() if name in {"graph", "ppr"}}
            if navigation_lanes:
                navigation_rows.append({
                    "node_id": node_id, "node_type": view.nodes[node_id].get("type", "knowledge"),
                    "label": view.nodes[node_id].get("label", node_id),
                    "lanes": navigation_lanes, "seed_evidence": evidence["seed_evidence"][:32],
                    "path_evidence": evidence["path_evidence"][:32],
                    "fusion": {"method": "navigation", "score": 0.0, "explanation": ["graph relation support; not scored as query relevance"]},
                })
            if not ranking_lanes:
                continue
        score = sum(1.0 / (60 + lane["rank"]) for lane in ranking_lanes.values())
        node = view.nodes[node_id]
        explanation = [f"{lane} rank {lane_data['rank']}" for lane, lane_data in sorted(ranking_lanes.items())]
        priority = identity_priority.get(node_id, 0)
        identity_rank = int(
            evidence["lanes"].get("identity", {}).get(
                "rank", MAX_INTERNAL_LANE_RESULTS + 1
            )
        )
        if priority:
            kind = "exact" if priority == 2 else "alias"
            explanation.insert(0, f"authoritative {kind} identity match")
        row = {
            "node_id": node_id,
            "node_type": node.get("type", "knowledge"),
            "label": node.get("label", node_id),
            "lanes": ranking_lanes,
            "seed_evidence": [] if graph_policy is not None else evidence["seed_evidence"][:32],
            "path_evidence": [] if graph_policy is not None else evidence["path_evidence"][:32],
            "fusion": {"method": "single-lane" if len(ranking_lanes) == 1 else "rrf", "score": score, "explanation": explanation},
        }
        ranked_results.append((priority, identity_rank, score, node_id, row))
    ranked_results.sort(
        key=lambda item: (
            -item[0],
            item[1] if item[0] else MAX_INTERNAL_LANE_RESULTS + 1,
            -item[2],
            item[3],
        )
    )
    if graph_policy is not None:
        navigation_rows.sort(key=lambda row: (min(item["rank"] for item in row["lanes"].values()), row["lanes"].get("graph", {}).get("rank", MAX_INTERNAL_LANE_RESULTS + 1), row["node_id"]))
        graph_provenance["mode"] = "support-expansion"
        graph_provenance["neighbor_limit"] = 32
        graph_provenance["neighbors_truncated"] = len(navigation_rows) > 32
        graph_provenance["neighbors"] = navigation_rows[:32]
        if not ranked_results:
            # An explicit graph-only plan is navigation, with zero answer
            # relevance score and the bounded graph diagnostic ordering.
            ranked_results = [(0, MAX_INTERNAL_LANE_RESULTS + 1, 0.0, row["node_id"], row) for row in navigation_rows[:32]]
    reranker_provenance: dict[str, Any] | None = None
    if ranking_service is not None and ranking_service.rerank_enabled:
        # The candidate pool is taken from the complete fused union before the
        # caller's final limit. Reranking cannot rescue a candidate already cut
        # by a top-20 result limit or add candidates outside this source-bound pool.
        candidates = ranked_results[:ranking_service.candidate_limit]
        try:
            reranked, reranker_provenance = ranking_service.rerank(
                view,
                namespace=namespace_value,
                question=plan["question"],
                candidate_ids=[item[3] for item in candidates],
                expected_snapshot_sha256=embedding_provenance["snapshot_sha256"],
                expected_graph_sha256=embedding_provenance["graph_sha256"],
            )
        except SemanticRetrievalError as error:
            raise RetrievalError(error.code, error.message) from error
        reranker_provenance["fusion"] = "rrf-base-reranker"
        by_id = {item[3]: (base_rank, item) for base_rank, item in enumerate(candidates, start=1)}
        ranked_results = []
        for rank, (node_id, raw_score) in enumerate(reranked, start=1):
            base_rank, (priority, identity_rank, _, _, row) = by_id[node_id]
            row["lanes"]["reranker"] = {"rank": rank, "score": raw_score}
            score = 1.0 / (60 + base_rank) + 1.0 / (60 + rank)
            row["fusion"] = {
                "method": "rrf",
                "score": score,
                "explanation": [*row["fusion"]["explanation"], f"base fusion rank {base_rank}", f"reranker rank {rank} within fused candidate pool"],
            }
            ranked_results.append((priority, identity_rank, score, node_id, row))
        ranked_results.sort(key=lambda item: (-item[0], item[1] if item[0] else MAX_INTERNAL_LANE_RESULTS + 1, -item[2], item[3]))
    result = {
        "schema": GRAPH_SEARCH_RESULT_SCHEMA if graph_policy is not None else MODEL_SEARCH_RESULT_SCHEMA if ranking_service is not None else SEARCH_RESULT_SCHEMA,
        "plan_sha256": sha256_json(plan),
        "lanes": {
            "identity": _query_lane(len(plan["identity_queries"]), len({row[0] for row in identity_rows})),
            "lexical": _query_lane(len(plan["lexical_queries"]), len(lexical_rows)),
            "graph": _seed_lane(len(bfs_seed_ids), len(graph_rows), enabled=plan["graph"]["strategy"] in {"bfs", "hybrid"}),
            "ppr": _seed_lane(
                len(accepted_ppr_seed_ids)
                if seed_ids and strategy in {"ppr", "hybrid"}
                else len(seed_ids),
                len(ppr_rows),
                enabled=plan["graph"]["strategy"] in {"ppr", "hybrid"},
                degraded_reason=ppr_degraded_reason,
            ),
        },
        "results": [row for _, _, _, _, row in ranked_results[:limit]],
    }
    if graph_policy is not None:
        result["graph_retrieval"] = graph_provenance
    if ranking_service is not None:
        result["lanes"]["embedding"] = _query_lane(1, len(embedding_rows))
        result["ranking"] = {"embedding": embedding_provenance}
        if reranker_provenance is not None:
            result["lanes"]["reranker"] = _query_lane(1, len(reranker_provenance["candidates"]))
            result["ranking"]["reranker"] = reranker_provenance
    if graph_policy is not None and view_content_sha256(view) != initial_content_sha256:
        raise RetrievalError("stale-generation", "graph source changed during retrieval execution")
    try:
        result = validate_contract(result)
    except ContractError as error:
        raise RetrievalError(
            "internal-contract-error",
            f"generated result does not satisfy {result['schema']}",
        ) from error
    execution = {
        "schema": GRAPH_SEARCH_EXECUTION_SCHEMA if graph_policy is not None else MODEL_SEARCH_EXECUTION_SCHEMA if ranking_service is not None else SEARCH_EXECUTION_SCHEMA,
        "plan_mode": plan_mode,
        "namespace": namespace_value,
        "snapshot_sha256": view.snapshot["snapshot_sha256"],
        "graph_sha256": view.snapshot["graph"]["sha256"],
        "identity_resolutions": identity_resolutions,
        "result": result,
    }
    try:
        execution = validate_contract(execution)
    except ContractError as error:
        raise RetrievalError(
            "internal-contract-error",
            f"generated execution does not satisfy {execution['schema']}",
        ) from error
    if len(canonical_json(execution).encode("utf-8")) > MAX_RETRIEVAL_RESPONSE_BYTES:
        raise RetrievalError("response-too-large", "retrieval response exceeds the byte limit")
    return execution


def build_context_from_execution(
    graph_dir: GraphView | Path,
    execution: dict[str, Any],
    *,
    plan: dict[str, Any],
    token_budget: int = 6000,
    namespace: str | None = None,
    alignments: Path | None = None,
    repo_root: Path | None = None,
    context_projection: str = "full",
    support_selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan = _validated_plan(plan)
    if context_projection not in {"full", "compact"}:
        raise RetrievalError("invalid-context-projection", "context projection must be full or compact")
    if not isinstance(token_budget, int) or isinstance(token_budget, bool) or not 1 <= token_budget <= MAX_CONTEXT_BUDGET:
        raise RetrievalError("invalid-context-budget", f"token budget must be between 1 and {MAX_CONTEXT_BUDGET}")
    try:
        execution = validate_contract(execution)
    except ContractError as error:
        raise RetrievalError(
            "invalid-execution",
            "expected a supported search execution containing its matching search result",
        ) from error
    if execution["schema"] not in {SEARCH_EXECUTION_SCHEMA, MODEL_SEARCH_EXECUTION_SCHEMA, GRAPH_SEARCH_EXECUTION_SCHEMA}:
        raise RetrievalError("invalid-execution", "expected a supported search execution")
    expected_plan_sha256 = sha256_json(plan)
    if execution["result"].get("plan_sha256") != expected_plan_sha256:
        raise RetrievalError(
            "invalid-execution", "search result does not belong to the supplied retrieval plan"
        )
    if "ranking" in execution["result"] and execution["result"]["ranking"]["embedding"]["query_sha256"] != hashlib.sha256(plan["question"].encode("utf-8")).hexdigest():
        raise RetrievalError("invalid-execution", "embedding query does not belong to the supplied retrieval plan")
    if "ranking" in execution["result"] and "reranker" in execution["result"]["ranking"] and execution["result"]["ranking"]["reranker"]["query_sha256"] != hashlib.sha256(plan["question"].encode("utf-8")).hexdigest():
        raise RetrievalError("invalid-execution", "reranker query does not belong to the supplied retrieval plan")
    if execution.get("namespace") != plan["namespace"] or (namespace is not None and namespace != plan["namespace"]):
        raise RetrievalError("namespace-conflict", "context namespace conflicts with execution")
    try:
        view = graph_dir if isinstance(graph_dir, GraphView) else load_graph_view(graph_dir, alignments, repo_root=repo_root)
    except QueryError as error:
        raise RetrievalError("graph-unavailable", str(error)) from error
    if execution.get("snapshot_sha256") != view.snapshot["snapshot_sha256"]:
        raise RetrievalError("stale-generation", "search execution belongs to another graph generation")
    if execution.get("graph_sha256") != view.snapshot["graph"]["sha256"]:
        raise RetrievalError("stale-generation", "search execution belongs to another graph generation")
    if "ranking" in execution["result"]:
        for candidate in execution["result"]["ranking"].get("reranker", {}).get("candidates", []):
            node = view.nodes.get(candidate["node_id"])
            if node is None or candidate["node_sha256"] != node_fingerprint(node) or candidate["document_sha256"] != hashlib.sha256(search_document(node).encode("utf-8")).hexdigest():
                raise RetrievalError("stale-generation", "reranker candidate source binding does not match graph")
    if support_selection is not None:
        from .context_projection import build_compact_context
        return build_compact_context(view, execution, plan, token_budget, support_selection=support_selection, node_projection=context_projection)
    if execution["schema"] == GRAPH_SEARCH_EXECUTION_SCHEMA:
        if context_projection == "compact":
            from .context_projection import build_compact_context
            return build_compact_context(view, execution, plan, token_budget)
        return build_graph_context(view, execution, plan, token_budget)
    if context_projection == "compact":
        from .context_projection import build_compact_context
        return build_compact_context(view, execution, plan, token_budget)
    ids = [str(row["node_id"]) for row in execution["result"]["results"]]
    metadata = {
        "question": plan["question"],
        "plan_sha256": expected_plan_sha256,
        "search_execution_schema": execution["schema"],
        "search_result_schema": execution["result"]["schema"],
    }
    # Merging two non-empty JSON objects replaces the metadata braces with one
    # comma. Reserve that exact byte cost before choosing complete records.
    metadata_bytes = len(canonical_json(metadata).encode("utf-8")) - 1
    content_budget = token_budget - metadata_bytes
    while content_budget > 0:
        # Restoring the caller's budget and finalizing the estimate can each
        # increase a decimal counter's width across a power-of-ten boundary.
        counter_growth = max(0, len(str(token_budget)) - len(str(content_budget)))
        reserved_budget = token_budget - metadata_bytes - 2 * counter_growth
        if reserved_budget == content_budget:
            break
        content_budget = reserved_budget
    if content_budget < 1:
        raise RetrievalError("context-failed", "budget-too-small for context metadata")
    try:
        # Select complete nodes before collecting diagnostic omissions. An
        # oversized early result must not consume space needed by a later node.
        preview = context(view, [], namespace=plan["namespace"], token_budget=content_budget)
        selected_ids: list[str] = []
        omitted_ids: list[str] = []
        for node_id in dict.fromkeys(ids):
            if node_id not in view.nodes or not _passes_filters(view.nodes[node_id], plan["filters"]):
                continue
            candidate = copy.deepcopy(preview)
            candidate["nodes"].append(copy.deepcopy(view.nodes[node_id]))
            if finalize_token_estimate(candidate) <= content_budget:
                preview = candidate
                selected_ids.append(node_id)
            else:
                omitted_ids.append(node_id)
        bundle = context(
            view,
            selected_ids,
            namespace=plan["namespace"],
            node_types=plan["filters"]["node_types"],
            edge_types=plan["graph"]["edge_types"],
            include_stale=plan["filters"]["include_stale"],
            include_orphaned=plan["filters"]["include_orphaned"],
            token_budget=content_budget,
        )
    except QueryError as error:
        raise RetrievalError("context-failed", str(error)) from error
    bundle["omissions"].extend(
        {"kind": "node", "id": node_id, "reason": "token-budget"}
        for node_id in omitted_ids
    )
    # Detailed omission records are optional diagnostics; preserve content and
    # required bindings when the remaining space cannot hold every record.
    while bundle["omissions"] and finalize_token_estimate(bundle) > content_budget:
        bundle["omissions"].pop()
    bundle.update(metadata)
    bundle["budget"]["token_budget"] = token_budget
    if finalize_token_estimate(bundle) > token_budget:
        raise RetrievalError(
            "context-failed", "budget-too-small after context metadata packing"
        )
    if not bundle["nodes"] and not bundle["omissions"] and any(
        node_id in view.nodes and _passes_filters(view.nodes[node_id], plan["filters"])
        for node_id in ids
    ):
        raise RetrievalError(
            "context-failed", "budget-too-small to include context or an omission record"
        )
    return bundle
