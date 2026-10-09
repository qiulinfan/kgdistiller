"""Opt-in source-bound graph exploration, independent of graph identity.

Candidate roots are hypotheses for navigation. A high-confidence gate reads
the author's declared confidence; it is not independent scientific review.
"""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .alignment import node_fingerprint
from .contracts import sha256_json, validate_contract
from .query import (
    GraphView,
    QueryError,
    expand,
    finalize_token_estimate,
    personalized_pagerank,
    resolve_concepts,
)
from .semantic_retrieval import search_document

GRAPH_SEARCH_RESULT_SCHEMA = "kgdistiller-search-result-v3"
GRAPH_SEARCH_EXECUTION_SCHEMA = "kgdistiller-search-execution-v3"
GRAPH_CONTEXT_SCHEMA = "kgdistiller-context-bundle-v2"
MAX_GRAPH_PATHS = 32


@dataclass(frozen=True)
class GraphRetrievalPolicy:
    """Explicit runtime policy; it never changes a retrieval-plan identity."""

    candidate_limit: int = 5
    edge_policy: str = "high-confidence"

    def __post_init__(self) -> None:
        if isinstance(self.candidate_limit, bool) or not isinstance(self.candidate_limit, int) or not 1 <= self.candidate_limit <= 32:
            raise ValueError("graph candidate_limit must be between 1 and 32")
        if self.edge_policy not in {"current", "high-confidence"}:
            raise ValueError("graph edge_policy must be current or high-confidence")


def view_content_sha256(view: GraphView) -> str:
    """Catch source mutation even when an in-memory caller keeps old digests."""
    return sha256_json({"snapshot": view.snapshot, "nodes": view.nodes, "edges": view.edges, "references": view.references})


def _node_binding(view: GraphView, node_id: str) -> dict[str, str]:
    node = view.nodes[node_id]
    return {"node_id": node_id, "node_sha256": node_fingerprint(node), "document_sha256": hashlib.sha256(search_document(node).encode("utf-8")).hexdigest()}


def _purpose(relation: str) -> str:
    return {"prerequisite-for": "learning-prerequisite", "derived-from": "source-derivation", "contrasts-with": "comparison"}.get(relation, "relation-navigation")


def _bound_path(view: GraphView, lane: str, row: Mapping[str, Any]) -> dict[str, Any]:
    edges = {(str(edge["source"]), str(edge["relation"]), str(edge["target"])): edge for edge in view.edges}
    root = str(row["seed_id"])
    node_ids = [root]
    steps: list[dict[str, Any]] = []
    for raw in row["path"]:
        edge = edges[(raw["source"], raw["relation"], raw["target"])]
        node_ids.append(str(raw["target"] if raw["direction"] == "outgoing" else raw["source"]))
        steps.append({**dict(raw), "confidence": str(edge.get("confidence", "unverified")), "curation_status": str(edge.get("curation_status", "unspecified")), "evidence": str(edge.get("evidence", "")), "edge_sha256": sha256_json(edge), "purpose": _purpose(str(edge["relation"])), "logical_entailment": False})
    return {"lane": lane, "nodes": node_ids, "edge_types": [step["relation"] for step in steps], "node_bindings": [_node_binding(view, node_id) for node_id in node_ids], "steps": steps, "logical_entailment": False}


def graph_lanes(
    view: GraphView,
    plan: dict[str, Any],
    policy: GraphRetrievalPolicy,
    fused: dict[str, dict[str, Any]],
    seed_ids: list[str],
    explicit_seed_ids: list[str],
) -> tuple[list, list, list[str], dict[str, Any], str | None]:
    """Select filtered text/model hypotheses, then traverse explicit edges."""
    base: list[tuple[str, float, dict[str, Any]]] = []
    for node_id, row in fused.items():
        lanes = {name: copy.deepcopy(value) for name, value in row["lanes"].items() if name in {"lexical", "embedding"}}
        if lanes:
            base.append((node_id, sum(1.0 / (60 + item["rank"]) for item in lanes.values()), lanes))
    base.sort(key=lambda item: (-item[1], item[0]))
    candidate_seeds = [{**_node_binding(view, node_id), "base_rank": rank, "base_score": score, "lanes": lanes, "identity_authority": False} for rank, (node_id, score, lanes) in enumerate(base[:policy.candidate_limit], start=1)]
    identity_seed_ids = [node_id for node_id in seed_ids if node_id not in explicit_seed_ids]
    effective = list(dict.fromkeys([*seed_ids, *[item["node_id"] for item in candidate_seeds]]))
    if len(effective) > 128:
        raise QueryError("combined graph exploration seed batch exceeds 128 IDs")
    graph_plan, filters = plan["graph"], plan["filters"]
    metadata = {
        "pipeline_version": "kgdistiller-graph-retrieval-v1",
        "candidate_projection": "kgdistiller-search-document-v1",
        "policy": {"candidate_limit": policy.candidate_limit, "edge_policy": policy.edge_policy, "confidence_gate": "declared-high-with-evidence" if policy.edge_policy == "high-confidence" else "none", "confidence_is_independent_review": False, "max_depth": graph_plan["max_depth"], "ppr_max_iterations": 256},
        "binding": {"namespace": plan["namespace"], "snapshot_sha256": view.snapshot["snapshot_sha256"], "graph_sha256": view.snapshot["graph"]["sha256"], "plan_sha256": sha256_json(plan), "view_content_sha256": view_content_sha256(view)},
        "seeds": {"explicit": explicit_seed_ids, "identity": identity_seed_ids, "candidate": candidate_seeds, "effective": effective},
        "ppr": {"status": "disabled", "iterations": 0, "converged": False, "final_l1_residual": None, "stationary_error_bound": None, "probability_mass": None, "reachable_node_count": 0, "allowed_edge_count": 0, "tolerance": 1e-10, "discarded_results": 0},
        "path_limit": MAX_GRAPH_PATHS,
    }
    graph_rows: list = []
    ppr_rows: list = []
    degraded = None
    if not effective or graph_plan["max_depth"] == 0:
        return graph_rows, ppr_rows, effective, metadata, degraded
    expansion = expand(view, effective, namespace=plan["namespace"], direction=graph_plan["direction"], edge_types=graph_plan["edge_types"], max_depth=graph_plan["max_depth"], limit=500, include_stale=filters["include_stale"], include_orphaned=filters["include_orphaned"], edge_policy=policy.edge_policy)
    by_id = {str(row["node"]["id"]): row for row in expansion["nodes"] if row["depth"] > 0 and str(row["node"]["id"]) not in effective}
    # Multi-source BFS visits every root at depth zero. That must not erase
    # an actual comparison/dependency edge between two independently recalled
    # roots. Navigation has no relevance boost, so preserve its one-edge proof
    # without treating a root's empty reset path as supporting evidence.
    root_set = set(effective)
    for edge in expansion["edges"]:
        source_id, target_id = str(edge["source"]), str(edge["target"])
        if source_id == target_id or source_id not in root_set or target_id not in root_set:
            continue
        pairs: list[tuple[str, str, str]] = []
        if graph_plan["direction"] in {"out", "both"} or edge["relation"] == "contrasts-with":
            pairs.append((source_id, target_id, "outgoing"))
        if graph_plan["direction"] in {"in", "both"} or edge["relation"] == "contrasts-with":
            pairs.append((target_id, source_id, "incoming"))
        for root_id, neighbor_id, direction in pairs:
            by_id.setdefault(neighbor_id, {"node": view.nodes[neighbor_id], "depth": 1, "seed_id": root_id, "path": [{"source": source_id, "relation": edge["relation"], "target": target_id, "direction": direction}]})
    origins = {node_id: "explicit" for node_id in explicit_seed_ids}
    origins.update({node_id: "identity" for node_id in identity_seed_ids})
    for item in candidate_seeds:
        origins.setdefault(item["node_id"], "candidate")

    def lane_row(lane: str, node_id: str, score: float) -> tuple:
        row = by_id[node_id]
        root = str(row["seed_id"])
        return (node_id, score, [{"lane": lane, "seed_id": root, "origin": origins[root], "identity_authority": origins[root] == "identity"}], [_bound_path(view, lane, row)])

    if graph_plan["strategy"] in {"bfs", "hybrid"}:
        graph_rows = [lane_row("graph", node_id, 1.0 / (1 + int(row["depth"]))) for node_id, row in by_id.items()]
    if graph_plan["strategy"] in {"ppr", "hybrid"}:
        ranking = personalized_pagerank(view, {node_id: 1.0 for node_id in effective}, namespace=plan["namespace"], edge_types=graph_plan["edge_types"], direction=graph_plan["direction"], include_stale=filters["include_stale"], include_orphaned=filters["include_orphaned"], edge_policy=policy.edge_policy, max_depth=graph_plan["max_depth"], max_iterations=256, limit=500)
        converged = bool(ranking["converged"])
        metadata["ppr"] = {"status": "enabled" if converged else "degraded", "iterations": ranking["iterations"], "converged": converged, "final_l1_residual": ranking["l1_residual"], "stationary_error_bound": ranking["stationary_error_bound"], "probability_mass": ranking["probability_mass"], "reachable_node_count": ranking["reachable_node_count"], "allowed_edge_count": ranking["allowed_edge_count"], "tolerance": ranking["policy"]["tolerance"], "discarded_results": 0 if converged else len(ranking["results"])}
        if converged:
            ppr_rows = [lane_row("ppr", str(row["node"]["id"]), float(row["score"])) for row in ranking["results"] if str(row["node"]["id"]) in by_id]
        else:
            degraded = "not-converged"
    return graph_rows, ppr_rows, effective, metadata, degraded


def validate_graph_context_inputs(view: GraphView, execution: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate the full source boundary before any budget selects packets."""
    from .contracts import ContractError
    from .query import _edge_allowed
    from .retrieval import RetrievalError, _passes_filters, _validated_plan

    plan = _validated_plan(plan)
    try:
        execution = validate_contract(execution)
    except ContractError as error:
        raise RetrievalError("invalid-execution", "expected a source-bound graph execution") from error
    if execution["schema"] != GRAPH_SEARCH_EXECUTION_SCHEMA:
        raise RetrievalError("invalid-execution", "expected graph search execution v3")
    if execution["namespace"] != plan["namespace"] or view.snapshot["namespace"] != plan["namespace"]:
        raise RetrievalError("namespace-conflict", "graph context namespace does not match plan")
    if execution["snapshot_sha256"] != view.snapshot["snapshot_sha256"] or execution["graph_sha256"] != view.snapshot["graph"]["sha256"]:
        raise RetrievalError("stale-generation", "graph execution belongs to another source generation")
    try:
        original_digest = sha256_json({key: value for key, value in view.snapshot.items() if key != "snapshot_sha256"})
    except ContractError as error:
        raise RetrievalError("stale-generation", "original graph snapshot is not finite canonical content") from error
    if original_digest != view.snapshot["snapshot_sha256"]:
        raise RetrievalError("stale-generation", "original graph snapshot digest does not match its content")
    if execution["result"]["plan_sha256"] != sha256_json(plan):
        raise RetrievalError("invalid-execution", "graph execution does not match supplied plan")
    for model in execution["result"].get("ranking", {}).values():
        if model["query_sha256"] != hashlib.sha256(plan["question"].encode("utf-8")).hexdigest():
            raise RetrievalError("invalid-execution", "model query does not match supplied plan")
    for candidate in execution["result"].get("ranking", {}).get("reranker", {}).get("candidates", []):
        node_id = candidate["node_id"]
        if node_id not in view.nodes or not _passes_filters(view.nodes[node_id], plan["filters"]) or any(candidate[key] != value for key, value in _node_binding(view, node_id).items()):
            raise RetrievalError("stale-generation", "reranker candidate source binding changed")

    result = execution["result"]
    metadata = result["graph_retrieval"]
    if metadata["binding"]["view_content_sha256"] != view_content_sha256(view):
        raise RetrievalError("stale-generation", "graph exploration source changed since execution")
    if metadata["policy"]["max_depth"] != plan["graph"]["max_depth"]:
        raise RetrievalError("invalid-execution", "graph exploration depth does not match supplied plan")
    expected_explicit = [node_id for node_id in plan["graph"]["seed_ids"] if node_id in view.nodes and _passes_filters(view.nodes[node_id], plan["filters"])]
    if metadata["seeds"]["explicit"] != expected_explicit:
        raise RetrievalError("invalid-execution", "explicit graph roots do not match supplied plan")
    if len(execution["identity_resolutions"]) != len(plan["identity_queries"]):
        raise RetrievalError("invalid-execution", "graph identity resolutions do not match supplied plan")
    resolutions = resolve_concepts(view, plan["identity_queries"], namespace=plan["namespace"], match_limit=500)
    actual_resolutions = [{"query_index": index, "status": resolution["status"], "match_kind": resolution["match_kind"], "candidate_ids": [node["id"] for node in resolution["matches"]], "overflow": bool(resolution["overflow"]), "identity_authority": bool(resolution["identity_authority"])} for index, resolution in enumerate(resolutions)]
    if execution["identity_resolutions"] != actual_resolutions:
        raise RetrievalError("invalid-execution", "graph identity resolutions do not match source names and plan")
    for candidate in metadata["seeds"]["candidate"]:
        node_id = candidate["node_id"]
        if node_id not in view.nodes or not _passes_filters(view.nodes[node_id], plan["filters"]) or any(candidate[key] != value for key, value in _node_binding(view, node_id).items()):
            raise RetrievalError("stale-generation", "candidate graph root source binding changed")
    # Pack a support route near the root that motivated it. Its node/edge
    # records are supplemental source context, never an answer rank.
    ordered: list[dict[str, Any]] = []
    added: set[tuple[str, bool]] = set()

    def add_row(row: dict[str, Any]) -> None:
        key = (row["node_id"], bool(row["path_evidence"]))
        if key not in added:
            ordered.append(row)
            added.add(key)

    for row in result["results"]:
        for neighbor in metadata["neighbors"]:
            if any(path["nodes"][0] == row["node_id"] for path in neighbor["path_evidence"]):
                add_row(neighbor)
        add_row(row)
    for neighbor in metadata["neighbors"]:
        add_row(neighbor)
    edge_by_key = {(edge["source"], edge["relation"], edge["target"]): edge for edge in view.edges}
    for row in [*result["results"], *metadata["neighbors"]]:
        if row["node_id"] not in view.nodes or not _passes_filters(view.nodes[row["node_id"]], plan["filters"]):
            raise RetrievalError("stale-generation", "context result source is missing or excluded")
        for path in row["path_evidence"]:
            for binding in path["node_bindings"]:
                node_id = binding["node_id"]
                if node_id not in view.nodes or not _passes_filters(view.nodes[node_id], plan["filters"]) or binding != _node_binding(view, node_id):
                    raise RetrievalError("stale-generation", "support node source binding changed")
            for step in path["steps"]:
                edge = edge_by_key.get((step["source"], step["relation"], step["target"]))
                if edge is None or sha256_json(edge) != step["edge_sha256"]:
                    raise RetrievalError("stale-generation", "support edge source binding changed")
                if any(step[key] != str(edge.get(key, default)) for key, default in (("confidence", "unverified"), ("curation_status", "unspecified"), ("evidence", ""))):
                    raise RetrievalError("invalid-execution", "support path evidence does not match source edge")
                if not _edge_allowed(edge, include_stale=plan["filters"]["include_stale"], edge_policy=metadata["policy"]["edge_policy"]):
                    raise RetrievalError("invalid-execution", "support edge violates requested confidence policy")
                if step["relation"] not in plan["graph"]["edge_types"] or (step["relation"] != "contrasts-with" and plan["graph"]["direction"] != "both" and step["direction"] != {"out": "outgoing", "in": "incoming"}[plan["graph"]["direction"]]):
                    raise RetrievalError("invalid-execution", "support path does not match supplied graph plan")
    return ordered


def build_graph_context(view: GraphView, execution: dict[str, Any], plan: dict[str, Any], token_budget: int) -> dict[str, Any]:
    """Pack complete full-source paths atomically with explicit omissions."""
    from .retrieval import RetrievalError, _passes_filters

    ordered = validate_graph_context_inputs(view, execution, plan)
    result = execution["result"]
    metadata = result["graph_retrieval"]
    bundle = {"schema": GRAPH_CONTEXT_SCHEMA, "namespace": plan["namespace"], "snapshot_sha256": execution["snapshot_sha256"], "graph_sha256": execution["graph_sha256"], "question": plan["question"], "plan_sha256": sha256_json(plan), "search_execution_schema": execution["schema"], "search_result_schema": result["schema"], "edge_policy": metadata["policy"]["edge_policy"], "nodes": [], "edges": [], "references": [], "support_packets": [], "gaps": [], "omissions": [], "omitted_support_packets": len(ordered), "diagnostics_truncated": False, "budget": {"token_budget": token_budget, "estimated_tokens": 0}}
    if finalize_token_estimate(bundle) > token_budget:
        raise RetrievalError("context-failed", "budget-too-small for graph context metadata")
    edge_by_key = {(edge["source"], edge["relation"], edge["target"]): edge for edge in view.edges}
    gaps: list[dict[str, str]] = []
    for row in ordered:
        node_id = row["node_id"]
        paths = row["path_evidence"]
        has_graph = any(lane in row["lanes"] for lane in ("graph", "ppr"))
        if has_graph and not paths:
            raise RetrievalError("invalid-execution", "graph result has no source-bound support path")
        path = paths[0] if has_graph else None
        ids = path["nodes"] if path else [node_id]
        if any(item not in view.nodes or not _passes_filters(view.nodes[item], plan["filters"]) for item in ids):
            raise RetrievalError("stale-generation", "support path is no longer eligible")
        candidate = copy.deepcopy(bundle)
        existing = {node["id"] for node in candidate["nodes"]}
        candidate["nodes"].extend(copy.deepcopy(view.nodes[item]) for item in ids if item not in existing)
        keys = {(edge["source"], edge["relation"], edge["target"]) for edge in candidate["edges"]}
        if path:
            for step in path["steps"]:
                key = (step["source"], step["relation"], step["target"])
                edge = edge_by_key.get(key)
                if edge is None or sha256_json(edge) != step["edge_sha256"]:
                    raise RetrievalError("stale-generation", "support edge source binding changed")
                if any(step[key] != str(edge.get(key, default)) for key, default in (("confidence", "unverified"), ("curation_status", "unspecified"), ("evidence", ""))):
                    raise RetrievalError("invalid-execution", "support path evidence does not match source edge")
                if step["relation"] not in plan["graph"]["edge_types"] or (step["relation"] != "contrasts-with" and plan["graph"]["direction"] != "both" and step["direction"] != {"out": "outgoing", "in": "incoming"}[plan["graph"]["direction"]]):
                    raise RetrievalError("invalid-execution", "support path does not match supplied graph plan")
                if key not in keys:
                    candidate["edges"].append(copy.deepcopy(edge))
                    keys.add(key)
        candidate["support_packets"].append({"node_id": node_id, "kind": "graph-path" if path else "direct-source", "nodes": ids, "path": copy.deepcopy(path), "logical_entailment": False})
        # Definitions, conditions, roots and edge evidence are one atomic unit.
        # Optional references and omission ledgers cannot displace its proof.
        if finalize_token_estimate(candidate) <= token_budget:
            candidate["omitted_support_packets"] -= 1
            bundle = candidate
        else:
            gaps.append({"node_id": node_id, "reason": "support-packet-exceeds-budget"})
    included = {node["id"] for node in bundle["nodes"]}
    for ref in view.references:
        if ref["target"] in included:
            candidate = copy.deepcopy(bundle)
            candidate["references"].append(copy.deepcopy(ref))
            if finalize_token_estimate(candidate) <= token_budget:
                bundle = candidate
    for gap in gaps:
        candidate = copy.deepcopy(bundle)
        candidate["gaps"].append(gap)
        if finalize_token_estimate(candidate) <= token_budget:
            bundle = candidate
        else:
            bundle["diagnostics_truncated"] = True
    # Reserve changes in the decimal omission counter and true/false width.
    while finalize_token_estimate(bundle) > token_budget and bundle["references"]:
        bundle["references"].pop()
    while finalize_token_estimate(bundle) > token_budget and bundle["gaps"]:
        bundle["gaps"].pop()
        bundle["diagnostics_truncated"] = True
    if finalize_token_estimate(bundle) > token_budget:
        raise RetrievalError("context-failed", "budget-too-small for complete graph support")
    return validate_contract(bundle)
