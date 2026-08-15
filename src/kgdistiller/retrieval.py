"""Bounded deterministic retrieval over the JSON/in-memory query layer."""

from __future__ import annotations

import copy
import json
import math
import os
import stat
from pathlib import Path
from typing import Any, Mapping

from .contracts import ContractError, canonical_json, sha256_json, validate_contract
from .query import (
    CONTEXT_SCHEMA,
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


RETRIEVAL_PLAN_SCHEMA = "qlkg-retrieval-plan-v2"
SEARCH_RESULT_SCHEMA = "qlkg-search-result-v3"
SEARCH_EXECUTION_SCHEMA = "qlkg-search-execution-v2"
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
    if not filters["include_orphaned"] and source_status == "orphaned":
        return False
    return True


def execute_retrieval_plan(
    graph_dir: GraphView | Path,
    plan: dict[str, Any],
    *,
    plan_mode: str = "planned",
    namespace: str | None = None,
    alignments: Path | None = None,
    expected_graph_sha256: str | None = None,
) -> dict[str, Any]:
    """Execute identity, Unicode lexical, BFS, and PPR lanes deterministically."""
    plan = _validated_plan(plan)
    if plan_mode not in {"planned", "legacy"}:
        raise RetrievalError("invalid-retrieval-request", "plan_mode must be planned or legacy")
    if namespace is not None and namespace != plan["namespace"]:
        raise RetrievalError("namespace-conflict", "request namespace conflicts with retrieval plan")
    try:
        view = graph_dir if isinstance(graph_dir, GraphView) else load_graph_view(graph_dir, alignments)
    except QueryError as error:
        raise RetrievalError("graph-unavailable", str(error)) from error
    if expected_graph_sha256 is not None and view.snapshot["graph"]["sha256"] != expected_graph_sha256:
        raise RetrievalError("stale-generation", "authority graph changed before retrieval execution")
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
            ranking = personalized_pagerank(view, {node_id: 1.0 for node_id in seed_ids}, namespace=namespace_value, node_types=filters["node_types"], edge_types=plan["graph"]["edge_types"], direction=plan["graph"]["direction"], include_taxonomy="contains" in plan["graph"]["edge_types"], include_stale=filters["include_stale"], include_orphaned=filters["include_orphaned"], limit=MAX_INTERNAL_LANE_RESULTS)
            if ranking.get("converged") is False:
                ppr_degraded_reason = "not-converged"
            accepted_ppr_seed_ids = [str(node_id) for node_id in ranking["seeds"]]
            for row in ranking["results"]:
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
    for node_id, evidence in fused.items():
        score = sum(1.0 / (60 + lane["rank"]) for lane in evidence["lanes"].values())
        node = view.nodes[node_id]
        explanation = [f"{lane} rank {lane_data['rank']}" for lane, lane_data in sorted(evidence["lanes"].items())]
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
            "lanes": evidence["lanes"],
            "seed_evidence": evidence["seed_evidence"][:32],
            "path_evidence": evidence["path_evidence"][:32],
            "fusion": {"method": "single-lane" if len(evidence["lanes"]) == 1 else "rrf", "score": score, "explanation": explanation},
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
    result = {
        "schema": SEARCH_RESULT_SCHEMA,
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
    try:
        result = validate_contract(result)
    except ContractError as error:
        raise RetrievalError(
            "internal-contract-error",
            f"generated result does not satisfy {SEARCH_RESULT_SCHEMA}",
        ) from error
    execution = {
        "schema": SEARCH_EXECUTION_SCHEMA,
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
            f"generated execution does not satisfy {SEARCH_EXECUTION_SCHEMA}",
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
) -> dict[str, Any]:
    plan = _validated_plan(plan)
    if not isinstance(token_budget, int) or isinstance(token_budget, bool) or not 1 <= token_budget <= MAX_CONTEXT_BUDGET:
        raise RetrievalError("invalid-context-budget", f"token budget must be between 1 and {MAX_CONTEXT_BUDGET}")
    try:
        execution = validate_contract(execution)
    except ContractError as error:
        raise RetrievalError(
            "invalid-execution",
            f"expected {SEARCH_EXECUTION_SCHEMA} containing {SEARCH_RESULT_SCHEMA}",
        ) from error
    expected_plan_sha256 = sha256_json(plan)
    if execution["result"].get("plan_sha256") != expected_plan_sha256:
        raise RetrievalError(
            "invalid-execution", "search result does not belong to the supplied retrieval plan"
        )
    if execution.get("namespace") != plan["namespace"] or (namespace is not None and namespace != plan["namespace"]):
        raise RetrievalError("namespace-conflict", "context namespace conflicts with execution")
    try:
        view = graph_dir if isinstance(graph_dir, GraphView) else load_graph_view(graph_dir, alignments)
    except QueryError as error:
        raise RetrievalError("graph-unavailable", str(error)) from error
    if execution.get("snapshot_sha256") != view.snapshot["snapshot_sha256"]:
        raise RetrievalError("stale-generation", "search execution belongs to another graph generation")
    if execution.get("graph_sha256") != view.snapshot["graph"]["sha256"]:
        raise RetrievalError("stale-generation", "search execution belongs to another graph generation")
    ids = [str(row["node_id"]) for row in execution["result"]["results"]]
    try:
        bundle = context(
            view,
            ids,
            namespace=plan["namespace"],
            node_types=plan["filters"]["node_types"],
            edge_types=plan["graph"]["edge_types"],
            include_stale=plan["filters"]["include_stale"],
            include_orphaned=plan["filters"]["include_orphaned"],
            token_budget=token_budget,
        )
    except QueryError as error:
        raise RetrievalError("context-failed", str(error)) from error
    bundle["question"] = plan["question"]
    bundle["plan_sha256"] = expected_plan_sha256
    bundle["search_execution_schema"] = SEARCH_EXECUTION_SCHEMA
    bundle["search_result_schema"] = SEARCH_RESULT_SCHEMA
    for section, kind in (("references", "reference"), ("edges", "edge"), ("nodes", "node")):
        while bundle[section] and finalize_token_estimate(bundle) > token_budget:
            removed = bundle[section].pop()
            identifier = str(
                removed.get("id")
                or removed.get("node_id")
                or f"{removed.get('source')}:{removed.get('relation')}:{removed.get('target')}"
            )
            bundle["omissions"].append(
                {"kind": kind, "id": identifier, "reason": "token-budget"}
            )
    while bundle["omissions"] and finalize_token_estimate(bundle) > token_budget:
        bundle["omissions"].pop()
    if finalize_token_estimate(bundle) > token_budget:
        raise RetrievalError(
            "context-failed", "budget-too-small after context metadata packing"
        )
    return bundle
