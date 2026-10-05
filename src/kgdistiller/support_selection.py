"""Validate explicit query support choices without promoting rank or identity.

Selections bind an existing query execution to current source records. Reasons,
requirement IDs and producer trace hashes document the caller's choice; none is
proof of semantic relevance or an authorization to create an alias or edge.
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any

from .alignment import node_fingerprint
from .contracts import ContractError, canonical_json, sha256_json, validate_contract
from .query import GraphView
from .semantic_retrieval import search_document


SUPPORT_SELECTION_SCHEMA = "kgdistiller-support-selection-v1"
SUPPORT_SELECTION_KIND = "caller-selected-query-support"
MAX_SUPPORT_ITEMS = 32
MAX_SUPPORT_MANIFEST_BYTES = 128 * 1024


def _execution_binding(view: GraphView, execution: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    from .retrieval import RetrievalError, _validated_plan

    plan = _validated_plan(plan)
    try:
        execution = validate_contract(execution)
    except ContractError as error:
        raise RetrievalError("invalid-execution", "support choice requires a validated search execution") from error
    if execution["schema"] not in {"kgdistiller-search-execution-v1", "kgdistiller-search-execution-v2", "kgdistiller-search-execution-v3"}:
        raise RetrievalError("invalid-execution", "support choice requires a supported search execution")
    namespace = plan["namespace"]
    if execution["namespace"] != namespace or view.snapshot["namespace"] != namespace:
        raise RetrievalError("namespace-conflict", "support choice query and source namespaces differ")
    try:
        original_digest = sha256_json({key: value for key, value in view.snapshot.items() if key != "snapshot_sha256"})
    except (ContractError, UnicodeError, RecursionError) as error:
        raise RetrievalError("stale-generation", "support source snapshot is not finite canonical content") from error
    if original_digest != view.snapshot["snapshot_sha256"]:
        raise RetrievalError("stale-generation", "support source snapshot digest does not match its content")
    snapshot_nodes = {node["id"]: node for node in view.snapshot["nodes"]}
    edge_key = lambda edge: (edge["source"], edge["relation"], edge["target"])
    snapshot_edges = {edge_key(edge): edge for edge in view.snapshot["edges"]}
    current_edges = {edge_key(edge): edge for edge in view.edges}
    snapshot_references = {reference["id"]: reference for reference in view.snapshot["references"]}
    current_references = {reference["id"]: reference for reference in view.references}
    if snapshot_nodes != view.nodes or len(view.edges) != len(view.snapshot["edges"]) or snapshot_edges != current_edges or len(view.references) != len(view.snapshot["references"]) or snapshot_references != current_references:
        raise RetrievalError("stale-generation", "support source records changed without a current snapshot")
    if execution["snapshot_sha256"] != original_digest or execution["graph_sha256"] != view.snapshot["graph"]["sha256"]:
        raise RetrievalError("stale-generation", "support search execution belongs to another source generation")
    question_hash = hashlib.sha256(plan["question"].encode("utf-8")).hexdigest()
    plan_hash = sha256_json(plan)
    if execution["result"]["plan_sha256"] != plan_hash:
        raise RetrievalError("invalid-execution", "support search execution does not belong to supplied plan")
    if any(provenance["query_sha256"] != question_hash for provenance in execution["result"].get("ranking", {}).values()):
        raise RetrievalError("invalid-execution", "support model question does not belong to supplied plan")
    return {"namespace": namespace, "snapshot_sha256": original_digest,
            "graph_sha256": view.snapshot["graph"]["sha256"],
            "question_sha256": question_hash, "plan_sha256": plan_hash}


def validate_support_selection(view: GraphView, execution: dict[str, Any], plan: dict[str, Any],
                               manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Return validated direct-source support rows, never answer ranking rows."""
    from .retrieval import RetrievalError, _passes_filters, _validated_plan

    try:
        if not isinstance(manifest, dict) or manifest.get("schema") != SUPPORT_SELECTION_SCHEMA:
            raise ContractError("unsupported support selection version")
        if len(canonical_json(manifest).encode("utf-8")) > MAX_SUPPORT_MANIFEST_BYTES:
            raise ContractError("support selection exceeds byte limit")
        selected = validate_contract(manifest)
    except (ContractError, UnicodeError, RecursionError) as error:
        raise RetrievalError("invalid-support-selection", "support selection must satisfy its bounded versioned contract") from error
    binding = _execution_binding(view, execution, plan)
    if selected["namespace"] != binding["namespace"]:
        raise RetrievalError("namespace-conflict", "support selection namespace differs from supplied query")
    if any(selected[key] != binding[key] for key in ("snapshot_sha256", "graph_sha256")):
        raise RetrievalError("stale-generation", "support selection belongs to another graph generation")
    if any(selected[key] != binding[key] for key in ("question_sha256", "plan_sha256")):
        raise RetrievalError("invalid-support-selection", "support selection does not belong to supplied question and plan")
    ids = [item["node_id"] for item in selected["items"]]
    if len(ids) != len(set(ids)):
        raise RetrievalError("invalid-support-selection", "support node IDs must be unique")
    filters = _validated_plan(plan)["filters"]
    rows: list[dict[str, Any]] = []
    for item in selected["items"]:
        node_id = item["node_id"]
        node = view.nodes.get(node_id)
        if node is None:
            raise RetrievalError("invalid-support-selection", "support selection contains an unknown source node")
        if not _passes_filters(node, filters):
            raise RetrievalError("invalid-support-selection", "support source node is excluded by current query filters")
        if item["node_sha256"] != node_fingerprint(node) or item["document_sha256"] != hashlib.sha256(search_document(node).encode("utf-8")).hexdigest():
            raise RetrievalError("stale-generation", "support node fingerprint does not match current source")
        if not item["reason"].strip() or any(not requirement.strip() for requirement in item["requirement_ids"]):
            raise RetrievalError("invalid-support-selection", "support requirements and reasons must be nonempty")
        rows.append({"node_id": node_id, "lanes": {"support": {}}, "path_evidence": [],
                     "selection_origin": "query-support", "requirement_ids": list(item["requirement_ids"])})
    return rows


def make_support_selection(view: GraphView, execution: dict[str, Any], plan: dict[str, Any],
                           items: list[dict[str, Any]]) -> dict[str, Any]:
    """Bind caller choices to current sources; no model or relevance decision."""
    from .retrieval import RetrievalError

    binding = _execution_binding(view, execution, plan)
    if not isinstance(items, list) or len(items) > MAX_SUPPORT_ITEMS:
        raise RetrievalError("invalid-support-selection", "support choices must contain at most 32 source nodes")
    records: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or set(item) - {"node_id", "requirement_ids", "reason", "producer_trace_sha256"} or not {"node_id", "requirement_ids", "reason"} <= set(item):
            raise RetrievalError("invalid-support-selection", "caller support choices have unsupported or missing fields")
        node_id = item["node_id"]
        if not isinstance(node_id, str) or node_id not in view.nodes:
            raise RetrievalError("invalid-support-selection", "caller support choice has no current source node")
        node = view.nodes[node_id]
        records.append({**copy.deepcopy(item), "node_sha256": node_fingerprint(node),
                        "document_sha256": hashlib.sha256(search_document(node).encode("utf-8")).hexdigest()})
    manifest = {"schema": SUPPORT_SELECTION_SCHEMA, **binding, "identity_authority": False,
                "kind": SUPPORT_SELECTION_KIND, "items": records}
    validate_support_selection(view, execution, plan, manifest)
    return manifest
