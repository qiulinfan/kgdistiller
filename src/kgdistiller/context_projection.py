"""Lossless semantic context projection over a validated graph execution.

The projection removes only an explicit bookkeeping allowlist and byte-equal
duplicate entry content. Definitions, conditions, provenance, unknown semantic
fields and actual edge evidence remain complete. It is derived content, not a
new graph authority or an independent scientific review.
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any

from .alignment import node_fingerprint
from .contracts import ContractError, canonical_json, sha256_json, validate_contract
from .query import GraphView, finalize_token_estimate
from .semantic_retrieval import search_document

COMPACT_CONTEXT_SCHEMA = "kgdistiller-context-bundle-v3"
CONTEXT_PROJECTION = "kgdistiller-context-projection-v1"
FULL_CONTEXT_PROJECTION = "kgdistiller-context-projection-full-v1"
# These hashes describe the rebuildable curated-entry mirror; original node
# fingerprints retain them. Source locations, source hashes, curation flags,
# entry paths and author-defined fields remain available in content.
BOOKKEEPING_PROPERTIES = frozenset({
    "entry_sha256", "entry_source_current_sha256", "entry_source_sha256",
})


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def project_node(node: dict[str, Any]) -> dict[str, Any]:
    """Return complete semantic content with conservative exact deduplication."""
    projected = copy.deepcopy(node)
    properties = projected.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    for key in BOOKKEEPING_PROPERTIES:
        if _is_sha256(properties.get(key)):
            properties.pop(key)
    provenance = projected.get("provenance")
    provenance = provenance if isinstance(provenance, dict) else {}
    if _is_sha256(properties.get("curated_definition_sha256")) and properties["curated_definition_sha256"] == provenance.get("definition_sha256"):
        properties.pop("curated_definition_sha256")
    if _is_sha256(properties.get("model_declared_source_sha256")) and properties["model_declared_source_sha256"] == properties.get("source_sha256"):
        properties.pop("model_declared_source_sha256")
    entry = projected.get("entry")
    if isinstance(entry, dict):
        originals = [node[key] for key in ("text",) if key in node]
        if "conditions" in properties:
            originals.append(properties["conditions"])
        for key in ("summary", "context"):
            if key in entry and any(canonical_json(entry[key]).encode("utf-8") == canonical_json(value).encode("utf-8") for value in originals):
                del entry[key]
    return projected


def node_record(node: dict[str, Any], *, node_projection: str = "compact") -> dict[str, Any]:
    content = project_node(node) if node_projection == "compact" else copy.deepcopy(node)
    return {
        "node_id": node["id"],
        "node_sha256": node_fingerprint(node),
        "document_sha256": hashlib.sha256(search_document(node).encode("utf-8")).hexdigest(),
        "projection_sha256": sha256_json(content),
        "content": content,
    }


def _plain_context_inputs(view: GraphView, execution: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Bind v1/v2 hits as direct sources; legacy graph paths confer no proof."""
    from .graph_retrieval import view_content_sha256
    from .retrieval import RetrievalError, _passes_filters, _validated_plan

    _validated_plan(plan)
    try:
        validate_contract(execution)
    except ContractError as error:
        raise RetrievalError("invalid-execution", "invalid plain search execution") from error
    if execution["schema"] not in {"kgdistiller-search-execution-v1", "kgdistiller-search-execution-v2"}:
        raise RetrievalError("invalid-execution", "expected supported plain search execution")
    if execution["result"]["plan_sha256"] != sha256_json(plan):
        raise RetrievalError("invalid-execution", "plain search does not belong to supplied plan")
    if execution["namespace"] != plan["namespace"] or view.snapshot["namespace"] != plan["namespace"]:
        raise RetrievalError("namespace-conflict", "compact context namespace conflicts with search")
    if execution["snapshot_sha256"] != view.snapshot["snapshot_sha256"] or execution["graph_sha256"] != view.snapshot["graph"]["sha256"]:
        raise RetrievalError("stale-generation", "plain search belongs to another graph generation")
    try:
        original_digest = sha256_json({key: value for key, value in view.snapshot.items() if key != "snapshot_sha256"})
    except ContractError as error:
        raise RetrievalError("stale-generation", "original graph snapshot is not finite canonical content") from error
    if original_digest != view.snapshot["snapshot_sha256"]:
        raise RetrievalError("stale-generation", "original graph snapshot digest does not match its content")
    snapshot_nodes = {node["id"]: node for node in view.snapshot["nodes"]}
    snapshot_edges = {(edge["source"], edge["relation"], edge["target"]): edge for edge in view.snapshot["edges"]}
    current_edges = {(edge["source"], edge["relation"], edge["target"]): edge for edge in view.edges}
    snapshot_references = {reference["id"]: reference for reference in view.snapshot["references"]}
    current_references = {reference["id"]: reference for reference in view.references}
    if snapshot_nodes != view.nodes or snapshot_edges != current_edges or snapshot_references != current_references:
        raise RetrievalError("stale-generation", "plain source view changed without a new snapshot")
    question_hash = hashlib.sha256(plan["question"].encode("utf-8")).hexdigest()
    for provenance in execution["result"].get("ranking", {}).values():
        if provenance["query_sha256"] != question_hash:
            raise RetrievalError("invalid-execution", "model query does not match supplied plan")
        for candidate in provenance.get("candidates", []):
            node = view.nodes.get(candidate["node_id"])
            if node is None or candidate["node_sha256"] != node_fingerprint(node) or candidate["document_sha256"] != hashlib.sha256(search_document(node).encode("utf-8")).hexdigest():
                raise RetrievalError("stale-generation", "plain reranker candidate source changed")
    rows = []
    for row in execution["result"]["results"]:
        node_id = row["node_id"]
        if node_id not in view.nodes or not _passes_filters(view.nodes[node_id], plan["filters"]):
            raise RetrievalError("stale-generation", "plain search source is no longer eligible")
        rows.append({"node_id": node_id, "lanes": {"direct": {}}, "path_evidence": []})
    # Force the same complete-view hash computation before any source packing.
    view_content_sha256(view)
    return rows


def validate_compact_context(payload: dict[str, Any], view: GraphView | None = None) -> None:
    """Validate compact closure/digests, and original sources when supplied.

    A standalone projection digest checks the serialized projection only. To
    verify its relationship to omitted bookkeeping, resolve the original graph
    generation and pass its GraphView; hashes are not scientific attestations.
    Schema validation is performed by the calling versioned contract validator.
    """
    if payload.get("schema") != COMPACT_CONTEXT_SCHEMA:
        return
    if payload["search_execution_schema"].replace("search-execution", "search-result") != payload["search_result_schema"]:
        raise ContractError("compact context search schema versions do not match")
    if payload["search_execution_schema"] != "kgdistiller-search-execution-v3" and (payload["edges"] or any(packet["kind"] == "graph-path" for packet in payload["support_packets"])):
        raise ContractError("plain search context cannot promote unbound legacy graph paths")
    node_records = payload["nodes"]
    nodes = {record["node_id"]: record for record in node_records}
    if len(nodes) != len(node_records):
        raise ContractError("compact context contains duplicate node records")
    for node_id, record in nodes.items():
        if record["content"].get("id") != node_id or record["projection_sha256"] != sha256_json(record["content"]):
            raise ContractError("compact node projection digest or ID does not match its content")
        if view is not None:
            original = view.nodes.get(node_id)
            if original is None or record != node_record(original, node_projection="full" if payload["projection"] == FULL_CONTEXT_PROJECTION else "compact"):
                raise ContractError("compact node does not match its original source-bound projection")
    edge_records = payload["edges"]
    edge_keys = [(record["content"].get("source"), record["content"].get("relation"), record["content"].get("target")) for record in edge_records]
    if len(edge_keys) != len(set(edge_keys)):
        raise ContractError("compact context contains duplicate edge records")
    source_edges = {(edge["source"], edge["relation"], edge["target"]): edge for edge in view.edges} if view is not None else {}
    for key, record in zip(edge_keys, edge_records):
        edge = record["content"]
        if key[0] not in nodes or key[2] not in nodes or record["edge_sha256"] != sha256_json(edge):
            raise ContractError("compact edge proof is disconnected or its digest does not match")
        if view is not None and source_edges.get(key) != edge:
            raise ContractError("compact edge does not match its original source")
        if payload["edge_policy"] == "high-confidence" and (edge.get("confidence") != "high" or edge.get("curation_status") != "current" or not str(edge.get("evidence", "")).strip()):
            raise ContractError("compact edge does not satisfy declared high-confidence gate")
    for packet in payload["support_packets"]:
        ids, steps = packet["nodes"], packet["steps"]
        if not ids or ids[-1] != packet["node_id"] or any(node_id not in nodes for node_id in ids):
            raise ContractError("compact support packet is missing its complete node path")
        if packet["kind"] == "direct-source":
            if ids != [packet["node_id"]] or steps or packet["lane"] is not None:
                raise ContractError("compact direct-source packet cannot claim a graph path")
            continue
        if len(ids) < 2 or ids[0] == ids[-1] or len(steps) != len(ids) - 1 or packet["lane"] not in {"graph", "ppr"}:
            raise ContractError("compact graph packet requires a distinct-root complete route")
        for index, step in enumerate(steps):
            edge_index = step["edge_index"]
            if isinstance(edge_index, bool) or not isinstance(edge_index, int) or not 0 <= edge_index < len(edge_records):
                raise ContractError("compact route contains an unknown edge index")
            edge = edge_records[edge_index]["content"]
            left, right = (edge["source"], edge["target"]) if step["direction"] == "outgoing" else (edge["target"], edge["source"])
            if (left, right) != (ids[index], ids[index + 1]):
                raise ContractError("compact route direction does not match its source edge")
    if any(ref.get("target") not in nodes for ref in payload["references"]):
        raise ContractError("compact reference target is outside the complete context")
    if view is not None:
        source_references = {reference["id"]: reference for reference in view.references}
        if any(source_references.get(reference.get("id")) != reference for reference in payload["references"]):
            raise ContractError("compact reference does not match its original source")
    if payload["omitted_support_packets"] < len(payload["gaps"]):
        raise ContractError("compact gap count exceeds omitted complete packets")
    if any(packet.get("selection_origin") == "query-support" for packet in payload["support_packets"]) and "support_selection_sha256" not in payload:
        raise ContractError("query support packets require their selection binding")
    actual_bytes = len(canonical_json(payload).encode("utf-8"))
    if actual_bytes != payload["budget"]["estimated_tokens"] or actual_bytes > payload["budget"]["token_budget"]:
        raise ContractError("compact context budget does not match canonical byte size")
    if view is not None:
        from .graph_retrieval import view_content_sha256
        if payload["snapshot_sha256"] != view.snapshot["snapshot_sha256"] or payload["graph_sha256"] != view.snapshot["graph"]["sha256"] or payload["view_content_sha256"] != view_content_sha256(view):
            raise ContractError("compact context belongs to a different original graph generation")


def build_compact_context(view: GraphView, execution: dict[str, Any], plan: dict[str, Any], token_budget: int, *, support_selection: dict[str, Any] | None = None, node_projection: str = "compact") -> dict[str, Any]:
    """Pack source-validated routes using shared node/edge tables atomically."""
    from .graph_retrieval import validate_graph_context_inputs, view_content_sha256
    from .retrieval import MAX_CONTEXT_BUDGET, RetrievalError

    if isinstance(token_budget, bool) or not isinstance(token_budget, int) or not 1 <= token_budget <= MAX_CONTEXT_BUDGET:
        raise RetrievalError("invalid-context-budget", f"token budget must be between 1 and {MAX_CONTEXT_BUDGET}")
    if node_projection not in {"compact", "full"}:
        raise RetrievalError("invalid-context-projection", "node projection must be compact or full")
    graph_execution = execution.get("schema") == "kgdistiller-search-execution-v3"
    ordered = validate_graph_context_inputs(view, execution, plan) if graph_execution else _plain_context_inputs(view, execution, plan)
    selection_sha256 = None
    if support_selection is not None:
        from .support_selection import validate_support_selection
        try:
            selection_sha256 = sha256_json(support_selection)
        except ContractError as error:
            raise RetrievalError("invalid-support-selection", "support selection is not finite canonical content") from error
        selected = validate_support_selection(view, execution, plan, support_selection)
        if sha256_json(support_selection) != selection_sha256:
            raise RetrievalError("stale-generation", "support selection changed during validation")
        selected_ids = {row["node_id"] for row in selected}
        ordered = [*selected, *[row for row in ordered if row["node_id"] not in selected_ids or row["path_evidence"]]]
    result = execution["result"]
    metadata = result.get("graph_retrieval")
    bundle: dict[str, Any] = {
        "schema": COMPACT_CONTEXT_SCHEMA, "projection": CONTEXT_PROJECTION if node_projection == "compact" else FULL_CONTEXT_PROJECTION,
        "namespace": plan["namespace"], "snapshot_sha256": execution["snapshot_sha256"],
        "graph_sha256": execution["graph_sha256"], "view_content_sha256": view_content_sha256(view),
        "question": plan["question"], "plan_sha256": sha256_json(plan),
        "execution_sha256": sha256_json(execution),
        "search_execution_schema": execution["schema"], "search_result_schema": result["schema"],
        "edge_policy": metadata["policy"]["edge_policy"] if graph_execution else "current",
        "confidence_is_independent_review": False,
        "nodes": [], "edges": [], "references": [], "support_packets": [], "gaps": [],
        "omitted_support_packets": len(ordered), "diagnostics_truncated": False,
        "budget": {"token_budget": token_budget, "estimated_tokens": 0},
    }
    if support_selection is not None:
        bundle["support_selection_sha256"] = selection_sha256
    if finalize_token_estimate(bundle) > token_budget:
        raise RetrievalError("context-failed", "budget-too-small for source context metadata")
    source_edges = {(edge["source"], edge["relation"], edge["target"]): edge for edge in view.edges}
    projections: dict[str, dict[str, Any]] = {}
    gaps: list[dict[str, str]] = []
    for row in ordered:
        has_path = bool(set(row["lanes"]) & {"graph", "ppr"})
        path = row["path_evidence"][0] if has_path else None
        ids = path["nodes"] if path else [row["node_id"]]
        candidate = copy.deepcopy(bundle)
        included_nodes = {record["node_id"] for record in candidate["nodes"]}
        for node_id in ids:
            if node_id not in included_nodes:
                if node_id not in projections:
                    projections[node_id] = node_record(view.nodes[node_id], node_projection=node_projection)
                candidate["nodes"].append(copy.deepcopy(projections[node_id]))
                included_nodes.add(node_id)
        indices = {(record["content"]["source"], record["content"]["relation"], record["content"]["target"]): index for index, record in enumerate(candidate["edges"])}
        steps = []
        for step in path["steps"] if path else []:
            key = (step["source"], step["relation"], step["target"])
            if key not in indices:
                edge = source_edges[key]
                indices[key] = len(candidate["edges"])
                candidate["edges"].append({"edge_sha256": sha256_json(edge), "content": copy.deepcopy(edge)})
            steps.append({"edge_index": indices[key], "direction": step["direction"]})
        candidate["support_packets"].append({"node_id": row["node_id"], "kind": "graph-path" if path else "direct-source", "nodes": list(ids), "steps": steps, "lane": path["lane"] if path else None, "logical_entailment": False})
        if row.get("selection_origin") == "query-support":
            candidate["support_packets"][-1]["selection_origin"] = "query-support"
            candidate["support_packets"][-1]["requirement_ids"] = list(row["requirement_ids"])
        if finalize_token_estimate(candidate) <= token_budget:
            candidate["omitted_support_packets"] -= 1
            bundle = candidate
        else:
            gaps.append({"node_id": row["node_id"], "reason": "support-packet-exceeds-budget"})
    included = {record["node_id"] for record in bundle["nodes"]}
    for reference in view.references:
        if reference["target"] in included:
            candidate = copy.deepcopy(bundle)
            candidate["references"].append(copy.deepcopy(reference))
            if finalize_token_estimate(candidate) <= token_budget:
                bundle = candidate
    for gap in gaps:
        candidate = copy.deepcopy(bundle)
        candidate["gaps"].append(gap)
        if finalize_token_estimate(candidate) <= token_budget:
            bundle = candidate
        else:
            bundle["diagnostics_truncated"] = True
    finalize_token_estimate(bundle)
    if sha256_json(execution) != bundle["execution_sha256"] or sha256_json(plan) != bundle["plan_sha256"]:
        raise RetrievalError("stale-generation", "compact context inputs changed while packing")
    if support_selection is not None and sha256_json(support_selection) != selection_sha256:
        raise RetrievalError("stale-generation", "support selection changed while packing")
    validate_compact_context(bundle, view)
    return validate_contract(bundle)
