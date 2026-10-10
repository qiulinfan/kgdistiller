"""Packaged versioned contracts and deterministic canonical JSON helpers."""

from __future__ import annotations

import copy
import json
from importlib import resources
from typing import Any

from .json_schema import SchemaViolation, validate_json_schema

DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
CONTRACT_SCHEMAS = {
    name: f"{name}.schema.json"
    for name in (
        "kgdistiller-query-status-v1",
        "kgdistiller-retrieval-plan-v1",
        "kgdistiller-search-result-v1",
        "kgdistiller-search-execution-v1",
        "kgdistiller-search-result-v2",
        "kgdistiller-search-execution-v2",
        "kgdistiller-search-result-v3",
        "kgdistiller-search-execution-v3",
        "kgdistiller-context-bundle-v2",
        "kgdistiller-obsidian-graph-v1",
    )
}


class ContractError(ValueError):
    """Raised when a packaged closure contract fails closed."""


def canonical_json(value: Any) -> str:
    """Return the project's immutable UTF-8 canonical JSON representation."""
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ContractError(f"value is not finite canonical JSON: {error}") from error


def parse_contract_json(text: str) -> Any:
    """Parse strict JSON, rejecting NaN and Infinity before schema validation."""

    def reject_constant(value: str) -> None:
        raise ContractError(f"non-finite JSON constant is forbidden: {value}")

    try:
        return json.loads(text, parse_constant=reject_constant)
    except ContractError:
        raise
    except json.JSONDecodeError as error:
        raise ContractError(f"malformed contract JSON: {error.msg}") from error


def load_contract_schema(discriminator: str) -> dict[str, Any]:
    """Load one supported immutable schema from installed package resources."""
    filename = CONTRACT_SCHEMAS.get(discriminator)
    if filename is None:
        raise ContractError(f"unsupported contract schema: {discriminator!r}")
    resource = resources.files("kgdistiller").joinpath("schemas", filename)
    try:
        schema = parse_contract_json(resource.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError) as error:
        raise ContractError(f"packaged contract schema is unavailable: {filename}") from error
    if not isinstance(schema, dict):
        raise ContractError(f"packaged contract schema is not an object: {filename}")
    if schema.get("$schema") != DRAFT_2020_12:
        raise ContractError(f"packaged contract schema is not Draft 2020-12: {filename}")
    discriminator_rule = (schema.get("properties") or {}).get("schema")
    if not isinstance(discriminator_rule, dict) or discriminator_rule.get("const") != discriminator:
        raise ContractError(f"packaged contract discriminator mismatch: {filename}")
    return schema


def _format_violation(error: SchemaViolation) -> str:
    path = ".".join(str(item) for item in error.path) or "contract"
    return f"contract JSON Schema violation at {path}: {error.message}"


def _validate_search_execution(payload: dict[str, Any]) -> None:
    execution_schema = payload.get("schema")
    if execution_schema not in {"kgdistiller-search-execution-v1", "kgdistiller-search-execution-v2", "kgdistiller-search-execution-v3"}:
        return
    resolutions = payload.get("identity_resolutions") or []
    indices = [resolution.get("query_index") for resolution in resolutions]
    if indices != list(range(len(resolutions))):
        raise ContractError(
            "identity resolution query_index values must be unique and contiguous"
        )
    result = payload.get("result")
    result_schema = execution_schema.replace("search-execution", "search-result")
    if not isinstance(result, dict) or result.get("schema") != result_schema:
        raise ContractError(
            f"{execution_schema} must contain {result_schema}"
        )
    validate_contract(result)
    if execution_schema.endswith("v3"):
        authoritative_ids = {node_id for resolution in resolutions if resolution["status"] in {"exact", "alias"} and resolution["identity_authority"] for node_id in resolution["candidate_ids"]}
        if any(node_id not in authoritative_ids for node_id in result["graph_retrieval"]["seeds"]["identity"]):
            raise ContractError("graph identity roots require authoritative exact or alias resolution")


def _validate_model_search_result(payload: dict[str, Any]) -> None:
    if payload.get("schema") not in {"kgdistiller-search-result-v2", "kgdistiller-search-result-v3"}:
        return
    if "ranking" not in payload:
        if "embedding" in payload["lanes"] or "reranker" in payload["lanes"] or any(set(row["lanes"]) & {"embedding", "reranker"} for row in payload["results"]):
            raise ContractError("model lanes require source-bound model provenance")
        return
    provenance = payload["ranking"]["embedding"]
    for kind, record in payload["ranking"].items():
        descriptor = record["model"]
        if any(not descriptor[key].strip() for key in ("provider", "model", "revision")):
            raise ContractError(f"{kind} model descriptor strings must be nonempty")
        if len(canonical_json(descriptor["inference"]).encode("utf-8")) > 4096:
            raise ContractError(f"{kind} model inference metadata exceeds the byte limit")
    empty = provenance["document_count"] == 0
    if empty != (provenance["dimensions"] == 0) or empty != (provenance["cache_status"] == "empty"):
        raise ContractError("embedding empty-cache metadata is inconsistent")
    if payload["lanes"]["embedding"]["status"] != "enabled" or payload["lanes"]["embedding"]["results"] > provenance["document_count"]:
        raise ContractError("embedding lane status or count is inconsistent")
    ids = [item["node_id"] for item in payload["results"]]
    if len(ids) != len(set(ids)):
        raise ContractError("search result contains duplicate node IDs")
    reranker = payload["ranking"].get("reranker")
    if (reranker is not None) != ("reranker" in payload["lanes"]):
        raise ContractError("reranker lane and provenance must occur together")
    if reranker is None:
        if any("reranker" in row["lanes"] for row in payload["results"]):
            raise ContractError("reranked result has no reranker provenance")
        return
    if reranker["projection"] != provenance["projection"]:
        raise ContractError("reranker projection does not match embedding retrieval")
    candidates = reranker["candidates"]
    candidate_ids = [candidate["node_id"] for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)) or len(candidates) > reranker["candidate_limit"]:
        raise ContractError("reranker candidate IDs must be unique and within the candidate limit")
    if payload["lanes"]["reranker"]["status"] != "enabled" or payload["lanes"]["reranker"]["results"] != len(candidates):
        raise ContractError("reranker lane status or count is inconsistent")
    ordered = sorted(candidates, key=lambda item: (-item["score"], item["node_id"]))
    by_id = {item["node_id"]: (rank, item["score"]) for rank, item in enumerate(ordered, start=1)}
    base_ranks = {item["node_id"]: rank for rank, item in enumerate(candidates, start=1)}
    for row in payload["results"]:
        if row["node_id"] not in by_id:
            raise ContractError("reranked result is outside the source-bound candidate pool")
        rank, score = by_id[row["node_id"]]
        fused_score = 1.0 / (60 + base_ranks[row["node_id"]]) + 1.0 / (60 + rank)
        if row["lanes"].get("reranker") != {"rank": rank, "score": score} or row["fusion"]["method"] != "rrf" or row["fusion"]["score"] != fused_score:
            raise ContractError("reranker scores and ranks do not match the source-bound candidate pool")


def _validate_graph_path(path: dict[str, Any], node_id: str) -> None:
    nodes, steps = path["nodes"], path["steps"]
    if len(nodes) < 2 or nodes[-1] != node_id or len(steps) != len(nodes) - 1:
        raise ContractError("graph path must connect a root to its result")
    if path["edge_types"] != [step["relation"] for step in steps]:
        raise ContractError("graph path edge types must match its steps")
    purposes = {"prerequisite-for": "learning-prerequisite", "derived-from": "source-derivation", "contrasts-with": "comparison"}
    for index, step in enumerate(steps):
        left, right = (step["source"], step["target"]) if step["direction"] == "outgoing" else (step["target"], step["source"])
        if (left, right) != (nodes[index], nodes[index + 1]):
            raise ContractError("graph path direction does not match its edge")
        if step["purpose"] != purposes.get(step["relation"], "relation-navigation"):
            raise ContractError("graph path purpose does not match its relation")


def _validate_graph_search_result(payload: dict[str, Any]) -> None:
    if payload.get("schema") != "kgdistiller-search-result-v3":
        return
    graph = payload["graph_retrieval"]
    policy, seeds = graph["policy"], graph["seeds"]
    expected_gate = "declared-high-with-evidence" if policy["edge_policy"] == "high-confidence" else "none"
    if policy["confidence_gate"] != expected_gate:
        raise ContractError("graph confidence gate does not match edge policy")
    candidate_ids = [item["node_id"] for item in seeds["candidate"]]
    for ids in [seeds["explicit"], seeds["identity"], candidate_ids, seeds["effective"]]:
        if len(ids) != len(set(ids)):
            raise ContractError("graph seeds must be unique within each origin")
    if seeds["effective"] != list(dict.fromkeys([*seeds["explicit"], *seeds["identity"], *candidate_ids])):
        raise ContractError("effective graph seeds must preserve declared seed origins")
    if len(candidate_ids) > policy["candidate_limit"] or [item["base_rank"] for item in seeds["candidate"]] != list(range(1, len(candidate_ids) + 1)):
        raise ContractError("graph candidate root ranks must be bounded and contiguous")
    if any(not item["lanes"] for item in seeds["candidate"]):
        raise ContractError("graph candidate roots require text or model ranking evidence")
    for candidate in seeds["candidate"]:
        if candidate["base_score"] != sum(1.0 / (60 + item["rank"]) for item in candidate["lanes"].values()):
            raise ContractError("graph candidate root score does not match base ranking evidence")
    if any(payload["lanes"][lane]["seeds"] != len(seeds["effective"]) for lane in ("graph", "ppr")):
        raise ContractError("graph lane seed count does not match exploration provenance")
    ppr = graph["ppr"]
    if ppr["status"] == "degraded" and (ppr["converged"] or payload["lanes"]["ppr"]["status"] != "degraded" or payload["lanes"]["ppr"]["results"] != 0):
        raise ContractError("nonconverged PPR must be degraded without fused results")
    if ppr["status"] == "enabled" and not ppr["converged"]:
        raise ContractError("enabled PPR must have converged")
    ids = [row["node_id"] for row in payload["results"]]
    if len(ids) != len(set(ids)):
        raise ContractError("search result contains duplicate node IDs")
    origins = {node_id: "explicit" for node_id in seeds["explicit"]}
    origins.update({node_id: "identity" for node_id in seeds["identity"]})
    for node_id in candidate_ids:
        origins.setdefault(node_id, "candidate")
    neighbors = graph["neighbors"]
    if any(set(row["lanes"]) - {"graph", "ppr"} or not row["lanes"] or row["fusion"]["method"] != "navigation" or row["fusion"]["score"] != 0 for row in neighbors):
        raise ContractError("graph support neighbors cannot claim query relevance scores")
    neighbor_ids = [row["node_id"] for row in neighbors]
    if len(neighbor_ids) != len(set(neighbor_ids)):
        raise ContractError("graph support neighbors must have unique IDs")
    for row in [*payload["results"], *neighbors]:
        graph_lanes = set(row["lanes"]) & {"graph", "ppr"}
        if graph_lanes and (set(row["lanes"]) - {"graph", "ppr"} or row["fusion"]["method"] != "navigation" or row["fusion"]["score"] != 0):
            raise ContractError("graph navigation cannot boost query relevance ranking")
        if graph_lanes and (not row["path_evidence"] or any(path["nodes"][0] == row["node_id"] for path in row["path_evidence"])):
            raise ContractError("graph navigation requires a path from a distinct source root")
        if ppr["status"] == "degraded" and "ppr" in graph_lanes:
            raise ContractError("nonconverged PPR cannot contribute to fusion")
        for path in row["path_evidence"]:
            _validate_graph_path(path, row["node_id"])
            if path["lane"] not in graph_lanes or path["nodes"][0] not in origins or len(path["steps"]) > policy["max_depth"]:
                raise ContractError("graph path exceeds execution policy or declared roots")
            if policy["edge_policy"] == "high-confidence" and any(step["confidence"] != "high" or not step["evidence"].strip() for step in path["steps"]):
                raise ContractError("graph path does not satisfy declared high-confidence gate")
        for item in row["seed_evidence"]:
            root = item["seed_id"]
            if origins.get(root) != item["origin"] or item["identity_authority"] != (item["origin"] == "identity") or not any(path["lane"] == item["lane"] and path["nodes"][0] == root for path in row["path_evidence"]):
                raise ContractError("graph seed evidence does not match its exploration origin")


def _validate_graph_context(payload: dict[str, Any]) -> None:
    if payload.get("schema") != "kgdistiller-context-bundle-v2":
        return
    nodes = {node["id"]: node for node in payload["nodes"] if isinstance(node.get("id"), str)}
    edges = {(edge.get("source"), edge.get("relation"), edge.get("target")): edge for edge in payload["edges"]}
    if len(nodes) != len(payload["nodes"]) or len(edges) != len(payload["edges"]):
        raise ContractError("graph context has duplicate or invalid node/edge records")
    if any(source not in nodes or target not in nodes for source, _, target in edges):
        raise ContractError("graph context has a disconnected edge proof")
    if payload["omitted_support_packets"] < len(payload["gaps"]):
        raise ContractError("graph context gap count exceeds omitted packets")
    for packet in payload["support_packets"]:
        path = packet["path"]
        if any(node_id not in nodes for node_id in packet["nodes"]):
            raise ContractError("graph support packet is missing complete path nodes")
        if packet["kind"] == "direct-source":
            if path is not None or packet["nodes"] != [packet["node_id"]]:
                raise ContractError("direct source packet must contain its own node only")
            continue
        if path is None or packet["nodes"] != path["nodes"]:
            raise ContractError("graph support packet requires its complete path")
        _validate_graph_path(path, packet["node_id"])
        for step in path["steps"]:
            edge = edges.get((step["source"], step["relation"], step["target"]))
            if edge is None:
                raise ContractError("graph support packet is missing its edge")
            if any(step[key] != str(edge.get(key, default)) for key, default in (("confidence", "unverified"), ("evidence", ""))):
                raise ContractError("graph path evidence and confidence do not match the source edge")
    actual = len(canonical_json(payload).encode("utf-8"))
    if actual != payload["budget"]["estimated_tokens"] or actual > payload["budget"]["token_budget"]:
        raise ContractError("graph context budget does not match canonical byte size")


def _validate_obsidian_graph(payload: dict[str, Any]) -> None:
    if payload.get("schema") != "kgdistiller-obsidian-graph-v1":
        return
    concepts = payload["concepts"]
    sources = payload["sources"]
    semantic_edges = payload["semantic_edges"]
    definitions = payload["definitions"]
    expected_counts = {
        "concepts": len(concepts),
        "sources": len(sources),
        "semantic_edges": len(semantic_edges),
        "definitions": len(definitions),
    }
    if payload["counts"] != expected_counts:
        raise ContractError("Obsidian graph counts do not match its arrays")
    concept_ids = [item["id"] for item in concepts]
    source_authorities = [item["authority"] for item in sources]
    if len(concept_ids) != len(set(concept_ids)):
        raise ContractError("Obsidian graph contains duplicate concept IDs")
    if len(source_authorities) != len(set(source_authorities)):
        raise ContractError("Obsidian graph contains duplicate source authorities")
    concept_set = set(concept_ids)
    source_set = set(source_authorities)
    edge_keys: set[tuple[str, str, str]] = set()
    for edge in semantic_edges:
        key = (edge["source"], edge["relation"], edge["target"])
        if key[0] not in concept_set or key[2] not in concept_set:
            raise ContractError("Obsidian graph semantic edge has an unknown endpoint")
        if key in edge_keys:
            raise ContractError("Obsidian graph contains duplicate semantic edges")
        edge_keys.add(key)
    definition_targets: set[str] = set()
    for definition in definitions:
        if definition["source_authority"] not in source_set or definition["target"] not in concept_set:
            raise ContractError("Obsidian graph definition has an unknown endpoint")
        if definition["line_end"] < definition["line_start"]:
            raise ContractError("Obsidian graph definition line range is reversed")
        if definition["target"] in definition_targets:
            raise ContractError("Obsidian graph concept has multiple definitions")
        definition_targets.add(definition["target"])
    if definition_targets != concept_set:
        raise ContractError("Obsidian graph concepts must each have one definition")
    if {item["source_authority"] for item in definitions} != source_set:
        raise ContractError("Obsidian graph sources must each define a concept")


def validate_contract(payload: Any) -> dict[str, Any]:
    """Validate a supported contract, failing closed."""
    if not isinstance(payload, dict):
        raise ContractError("contract payload must be an object")
    discriminator = payload.get("schema")
    if not isinstance(discriminator, str):
        raise ContractError("contract payload has no schema discriminator")
    schema = load_contract_schema(discriminator)
    try:
        errors = validate_json_schema(payload, schema)
    except (TypeError, ValueError) as error:
        raise ContractError(f"contract schema evaluation failed: {error}") from error
    if errors:
        raise ContractError(_format_violation(errors[0]))
    _validate_search_execution(payload)
    _validate_model_search_result(payload)
    _validate_graph_search_result(payload)
    _validate_graph_context(payload)
    _validate_obsidian_graph(payload)
    return copy.deepcopy(payload)
