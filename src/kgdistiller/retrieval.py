"""Bounded, provider-neutral execution of hybrid retrieval plans."""

from __future__ import annotations

import json
import math
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .agent import (
    AgentIndexError,
    DEFAULT_SEMANTIC_RELATIONS,
    agent_index_exists,
    embedding_inventory,
    estimate_tokens,
    expand_index,
    index_generation_token,
    index_status,
    open_agent_index,
    personalized_pagerank,
    resolve_concepts,
    search_index,
    semantic_search_batch,
    _edge_allowed,
    _node_payload,
)
from .contracts import ContractError, canonical_json, sha256_json, validate_contract
from .providers import (
    ProviderAdapterRegistry,
    ProviderError,
    default_provider_registry,
    provider_config_sha256,
    provider_configuration,
)


RETRIEVAL_PLAN_SCHEMA = "qlkg-retrieval-plan-v1"
SEARCH_RESULT_SCHEMA = "qlkg-search-result-v2"
SEARCH_EXECUTION_SCHEMA = "qlkg-search-execution-v1"
CONTEXT_SCHEMA = "qlkg-context-bundle-v1"
MAX_RETRIEVAL_PLAN_BYTES = 1024 * 1024
MAX_RETRIEVAL_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_CONTEXT_BUDGET = 200_000
MAX_CONTEXT_RELATION_ROWS = 5_000
MAX_IDENTITY_MATCHES = 500
MAX_INTERNAL_LANE_RESULTS = 500
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PROVIDER_REASON_CODES = {
    "adapter-initialization",
    "dimension-mismatch",
    "invalid-provider-config",
    "invalid-provider-request",
    "invalid-response",
    "missing-adapter",
    "missing-credential",
    "provider-timeout",
    "provider-unavailable",
}


class RetrievalError(ValueError):
    """Stable retrieval failure that does not expose query-provider internals."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message

    def to_payload(self) -> dict[str, str]:
        return {
            "kind": "kgdistiller-retrieval-error",
            "code": self.code,
            "message": self.message,
        }

    def payload(self) -> dict[str, str]:
        return self.to_payload()


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
    failure: tuple[str, str] | None = None
    handle: int | None = None
    payload = b""
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        handle = os.open(path, flags)
        metadata = os.fstat(handle)
        if not stat.S_ISREG(metadata.st_mode):
            failure = ("invalid-plan", "retrieval plan must be a regular file")
        elif metadata.st_size > MAX_RETRIEVAL_PLAN_BYTES:
            failure = ("plan-too-large", "retrieval plan exceeds the byte limit")
        else:
            chunks: list[bytes] = []
            remaining = MAX_RETRIEVAL_PLAN_BYTES + 1
            while remaining > 0:
                chunk = os.read(handle, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            if len(payload) > MAX_RETRIEVAL_PLAN_BYTES:
                failure = ("plan-too-large", "retrieval plan exceeds the byte limit")
    except FileNotFoundError:
        failure = ("plan-not-found", "retrieval plan does not exist")
    except (OSError, ValueError):
        failure = ("plan-unreadable", "retrieval plan could not be read")
    finally:
        if handle is not None:
            try:
                os.close(handle)
            except OSError:
                pass
    if failure is not None:
        raise RetrievalError(*failure)
    return payload


def _validated_plan(payload: Any) -> dict[str, Any]:
    invalid = False
    validated: dict[str, Any] | None = None
    try:
        validated = validate_contract(payload)
        canonical_json(validated).encode("utf-8")
    except Exception:
        invalid = True
    if invalid or validated is None or validated.get("schema") != RETRIEVAL_PLAN_SCHEMA:
        raise RetrievalError("invalid-plan", "retrieval plan is invalid")
    return validated


def load_retrieval_plan(path: Path) -> dict[str, Any]:
    """Load one bounded plan without retaining malformed source bytes in errors."""
    raw = _read_bounded_regular_file(Path(path))
    invalid = False
    payload: Any = None
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            parse_int=_bounded_json_int,
            parse_float=_bounded_json_float,
            parse_constant=_reject_json_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        RecursionError,
        OverflowError,
    ):
        invalid = True
    if invalid:
        raise RetrievalError("invalid-plan", "retrieval plan is invalid")
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
    """Format the legacy single-query input as the public bounded plan."""
    if not isinstance(query, str) or not query.strip() or len(query) > 4096:
        raise RetrievalError(
            "invalid-retrieval-request", "legacy retrieval query is invalid"
        )
    lane_query = query[:2048]
    edge_types = sorted(DEFAULT_SEMANTIC_RELATIONS)
    if include_taxonomy:
        edge_types.append("contains")
        edge_types.sort()
    plan = {
        "schema": RETRIEVAL_PLAN_SCHEMA,
        "question": query,
        "namespace": namespace,
        "identity_queries": [lane_query],
        "lexical_queries": [lane_query],
        "semantic_queries": [lane_query],
        "graph": {
            "seed_ids": [],
            "edge_types": edge_types,
            "direction": "both",
            "max_depth": max_depth,
            "strategy": graph_strategy,
        },
        "filters": {
            "node_types": sorted(set(node_types or [])),
            "include_stale": bool(include_stale),
            "include_orphaned": bool(include_orphaned),
        },
        "limit": limit,
    }
    return _validated_plan(plan)


def _node_allowed(
    node: Mapping[str, Any],
    *,
    node_types: set[str],
    include_stale: bool,
    include_orphaned: bool,
) -> bool:
    if node_types and str(node.get("type", "")) not in node_types:
        return False
    properties = node.get("properties")
    properties = properties if isinstance(properties, Mapping) else {}
    provenance = node.get("provenance")
    provenance = provenance if isinstance(provenance, Mapping) else {}
    return (
        (include_stale or properties.get("curation_status") != "needs-review")
        and (include_orphaned or properties.get("source_status") != "orphaned")
        and provenance.get("active") is not False
    )


def _query_lane(
    status: str, queries: int, results: int, reason: str | None = None
) -> dict[str, Any]:
    lane: dict[str, Any] = {
        "status": status,
        "queries": queries,
        "results": min(MAX_INTERNAL_LANE_RESULTS, results),
    }
    if status != "enabled":
        lane["reason"] = reason or "lane-unavailable"
    return lane


def _seed_lane(
    status: str, seeds: int, results: int, reason: str | None = None
) -> dict[str, Any]:
    lane: dict[str, Any] = {
        "status": status,
        "seeds": seeds,
        "results": min(MAX_INTERNAL_LANE_RESULTS, results),
    }
    if status != "enabled":
        lane["reason"] = reason or "lane-unavailable"
    return lane


def _safe_provider_reason(error: ProviderError) -> str:
    try:
        code = error.code
        if isinstance(code, str) and code in _PROVIDER_REASON_CODES:
            return code
    except Exception:
        pass
    return "provider-unavailable"


def _aggregate_result_lists(
    result_lists: list[list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    values: dict[str, dict[str, Any]] = {}
    for query_index, results in enumerate(result_lists):
        for result in results:
            node = result.get("node") if isinstance(result, dict) else None
            if not isinstance(node, dict) or not str(node.get("id", "")):
                continue
            node_id = str(node["id"])
            raw_rank = result.get("rank", 0)
            rank = raw_rank if isinstance(raw_rank, int) and not isinstance(raw_rank, bool) else 0
            if rank < 1 or rank > MAX_INTERNAL_LANE_RESULTS:
                continue
            value = values.setdefault(
                node_id,
                {
                    "node": node,
                    "score": 0.0,
                    "best_raw_score": None,
                    "first_query": query_index,
                },
            )
            value["score"] += 1.0 / (60.0 + rank)
            value["first_query"] = min(int(value["first_query"]), query_index)
            for reason in result.get("reasons", []):
                if not isinstance(reason, dict):
                    continue
                raw_score = reason.get("score")
                if isinstance(raw_score, (int, float)) and not isinstance(raw_score, bool):
                    score = float(raw_score)
                    if math.isfinite(score):
                        current = value["best_raw_score"]
                        value["best_raw_score"] = score if current is None else max(current, score)
    ordered = sorted(
        values.values(),
        key=lambda value: (
            -float(value["score"]),
            int(value["first_query"]),
            str(value["node"].get("label", "")),
            str(value["node"]["id"]),
        ),
    )[:MAX_INTERNAL_LANE_RESULTS]
    by_id = {str(value["node"]["id"]): value for value in ordered}
    return ordered, by_id


def _identity_lane(
    path: Path,
    queries: list[str],
    *,
    namespace: str,
    node_types: set[str],
    include_stale: bool,
    include_orphaned: bool,
    limit: int,
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    set[str],
    set[str],
    bool,
]:
    if not queries:
        return (
            _query_lane("disabled", 0, 0, "no-queries"),
            [],
            [],
            set(),
            set(),
            False,
        )
    failed = False
    resolutions: list[dict[str, Any]] = []
    try:
        resolutions = resolve_concepts(
            path, queries, namespace=namespace, match_limit=MAX_IDENTITY_MATCHES
        )
    except Exception:
        failed = True
    if failed:
        return (
            _query_lane("error", len(queries), 0, "identity-query-failed"),
            [],
            [],
            set(),
            set(),
            False,
        )

    envelope_rows: list[dict[str, Any]] = []
    fixed: list[dict[str, Any]] = []
    ambiguous_groups: list[list[dict[str, Any]]] = []
    all_ambiguous_ids: set[str] = set()
    missing = 0
    filtered = 0
    ambiguous = 0
    suppressed_ambiguity = False
    candidate_overflow = False
    result_limit_overflow = False
    for index, (query, resolution) in enumerate(zip(queries, resolutions)):
        raw_matches = resolution.get("matches")
        raw_matches = raw_matches if isinstance(raw_matches, list) else []
        all_matches = [node for node in raw_matches if isinstance(node, dict)]
        matches = [
            node
            for node in all_matches
            if _node_allowed(
                node,
                node_types=node_types,
                include_stale=include_stale,
                include_orphaned=include_orphaned,
            )
        ]
        status = str(resolution.get("status", "missing"))
        resolution_candidate_overflow = bool(resolution.get("overflow"))
        resolution_result_overflow = False
        if status == "ambiguous":
            ambiguous += 1
            all_ambiguous_ids.update(str(node["id"]) for node in all_matches)
            resolution_result_overflow = len(all_matches) > limit
            if len(matches) != len(all_matches) or len(matches) < 2:
                suppressed_ambiguity = True
            elif not resolution_candidate_overflow and not resolution_result_overflow:
                ambiguous_groups.append(matches)
        elif status in {"exact", "alias", "scoped-alias"}:
            fixed.extend(matches)
            if not matches:
                filtered += 1
        else:
            missing += 1
        candidate_overflow = candidate_overflow or resolution_candidate_overflow
        result_limit_overflow = result_limit_overflow or resolution_result_overflow
        row: dict[str, Any] = {
            "query_index": index,
            "status": status
            if status in {"exact", "alias", "scoped-alias", "ambiguous"}
            else "missing",
            "candidate_ids": [
                str(node["id"]) for node in all_matches[:MAX_IDENTITY_MATCHES]
            ],
            "overflow": resolution_candidate_overflow,
            "identity_authority": status != "missing",
            "match_kind": None,
        }
        match_kind = resolution.get("match_kind")
        if match_kind in {"id", "label", "alias", "scoped-alias"}:
            row["match_kind"] = match_kind
        envelope_rows.append(row)

    fixed_ids = {str(node["id"]) for node in fixed}
    ambiguous_ids = {
        str(node["id"]) for group in ambiguous_groups for node in group
    }
    fixed_overflow = len(fixed_ids) > limit
    if len(fixed_ids | ambiguous_ids) > limit:
        result_limit_overflow = bool(ambiguous_ids) or result_limit_overflow
        ambiguous_groups = []
        ambiguous_ids = set()

    candidate_lists: list[list[dict[str, Any]]] = []
    for resolution in resolutions:
        status = str(resolution.get("status", "missing"))
        matches = [
            node
            for node in (resolution.get("matches") or [])
            if isinstance(node, dict)
            and _node_allowed(
                node,
                node_types=node_types,
                include_stale=include_stale,
                include_orphaned=include_orphaned,
            )
        ]
        if status == "ambiguous":
            matches = [node for node in matches if str(node["id"]) in ambiguous_ids]
        elif status not in {"exact", "alias", "scoped-alias"}:
            matches = []
        candidate_lists.append(
            [
                {
                    "rank": rank,
                    "node": node,
                    "reasons": [{"score": 1.0 / rank}],
                }
                for rank, node in enumerate(matches, start=1)
            ]
        )
    ordered, _ = _aggregate_result_lists(candidate_lists)
    if candidate_overflow:
        status, reason = "degraded", "ambiguous-identity-overflow"
    elif result_limit_overflow:
        status, reason = "degraded", "ambiguous-identity-result-limit"
    elif fixed_overflow:
        status, reason = "degraded", "identity-result-overflow"
    elif suppressed_ambiguity:
        status, reason = "degraded", "ambiguous-identity-filtered"
    elif ambiguous:
        status, reason = "degraded", "ambiguous-identity"
    elif missing:
        status, reason = "degraded", "identity-not-found" if missing == len(queries) else "partial-identity"
    elif filtered:
        status, reason = "degraded", "identity-filtered"
    else:
        status, reason = "enabled", None
    return (
        _query_lane(status, len(queries), len(ordered), reason),
        ordered,
        envelope_rows,
        fixed_ids | ambiguous_ids,
        all_ambiguous_ids - ambiguous_ids,
        candidate_overflow or result_limit_overflow,
    )


def _lexical_lane(
    path: Path,
    queries: list[str],
    *,
    namespace: str,
    node_types: list[str],
    include_stale: bool,
    include_orphaned: bool,
    limit: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not queries:
        return _query_lane("disabled", 0, 0, "no-queries"), []
    result_lists: list[list[dict[str, Any]]] = []
    failures = 0
    per_query_limit = min(MAX_INTERNAL_LANE_RESULTS, max(limit * 3, 20))
    allowed_types = set(node_types)
    for query in queries:
        try:
            values = search_index(
                path,
                query,
                namespace=namespace,
                node_types=node_types,
                limit=per_query_limit,
            )
        except Exception:
            failures += 1
            values = []
        result_lists.append(
            [
                item
                for item in values
                if _node_allowed(
                    item["node"],
                    node_types=allowed_types,
                    include_stale=include_stale,
                    include_orphaned=include_orphaned,
                )
            ]
        )
    ordered, _ = _aggregate_result_lists(result_lists)
    if failures == len(queries):
        status, reason = "error", "lexical-query-failed"
    elif failures:
        status, reason = "degraded", "partial-lexical"
    else:
        status, reason = "enabled", None
    return _query_lane(status, len(queries), len(ordered), reason), ordered


def _ready_semantic_space(
    path: Path,
    config: Mapping[str, Any],
    *,
    namespace: str,
    node_types: set[str],
    include_stale: bool,
    include_orphaned: bool,
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        public = provider_configuration(config)
        digest = provider_config_sha256(config)
    except Exception:
        return None, "invalid-provider-config"
    try:
        inventory = embedding_inventory(path, namespace=namespace)
    except Exception:
        return None, "vector-index-unavailable"
    nodes = {
        str(node.get("node_id", "")): node
        for node in inventory.get("nodes", [])
        if isinstance(node, dict)
    }
    eligible_node_ids: set[str] = set()
    for node_id, node in nodes.items():
        node_type = str(node.get("node_type", ""))
        if node_types and node_type not in node_types:
            continue
        provenance_active = node.get("provenance_active")
        if (
            (not include_stale and str(node.get("curation_status", "")) == "needs-review")
            or (not include_orphaned and str(node.get("source_status", "")) == "orphaned")
            or provenance_active is False
            or (
                provenance_active is None
                and node.get("active") is False
                and str(node.get("curation_status", "")) != "needs-review"
                and str(node.get("source_status", "")) != "orphaned"
            )
        ):
            continue
        eligible_node_ids.add(node_id)
    ready_node_ids: set[str] = set()
    eligible_records = 0
    desired_space_records = 0
    for record in inventory.get("records", []):
        if not isinstance(record, dict):
            continue
        node = nodes.get(str(record.get("node_id", "")))
        if node is None or str(record.get("node_id", "")) not in eligible_node_ids:
            continue
        eligible_records += 1
        desired_space = (
            str(record.get("provider", "")) == str(public["adapter"])
            and str(record.get("model", "")) == str(public["model"])
            and record.get("dimensions") == public["dimensions"]
            and str(record.get("embedding_input_schema", ""))
            == "qlkg-node-embedding-text-v1"
            and str(record.get("provider_config_sha256", "")) == digest
        )
        if desired_space:
            desired_space_records += 1
        if (
            desired_space
            and str(record.get("content_sha256", ""))
            == str(node.get("content_sha256", ""))
            and record.get("vector_valid") is True
        ):
            ready_node_ids.add(str(record.get("node_id", "")))
    ready = len(ready_node_ids)
    eligible = len(eligible_node_ids)
    if ready == 0:
        if eligible_records and desired_space_records == 0:
            return None, "profile-mismatch"
        if desired_space_records:
            return None, "coverage-insufficient"
        return None, "vector-space-unavailable"
    return {
        "configuration": public,
        "digest": digest,
        "ready": ready,
        "eligible": eligible,
        "complete": ready == eligible,
    }, None


def _semantic_lane(
    path: Path,
    queries: list[str],
    *,
    namespace: str,
    node_types: list[str],
    include_stale: bool,
    include_orphaned: bool,
    limit: int,
    embedding_profile: str | None,
    provider_config: Mapping[str, Any] | None,
    provider_registry: ProviderAdapterRegistry | None,
    environ: Mapping[str, str] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not queries:
        return _query_lane("disabled", 0, 0, "no-queries"), []
    if embedding_profile is None or provider_config is None:
        return (
            _query_lane("degraded", len(queries), 0, "provider-unavailable"),
            [],
        )
    space, reason = _ready_semantic_space(
        path,
        provider_config,
        namespace=namespace,
        node_types=set(node_types),
        include_stale=include_stale,
        include_orphaned=include_orphaned,
    )
    if space is None:
        return _query_lane("degraded", len(queries), 0, reason), []
    registry = provider_registry or default_provider_registry()
    provider: Any = None
    failure_reason: str | None = None
    try:
        provider = registry.create(
            embedding_profile, provider_config, environ=environ
        )
    except ProviderError as error:
        failure_reason = _safe_provider_reason(error)
    except Exception:
        failure_reason = "provider-unavailable"
    if failure_reason is not None or provider is None:
        return (
            _query_lane(
                "degraded",
                len(queries),
                0,
                failure_reason or "provider-unavailable",
            ),
            [],
        )
    metadata_matches = False
    try:
        provider_dimensions = getattr(provider, "dimensions")
        metadata_matches = (
            type(getattr(provider, "name")) is str
            and str(getattr(provider, "name")) == str(space["configuration"]["adapter"])
            and type(getattr(provider, "model")) is str
            and str(getattr(provider, "model")) == str(space["configuration"]["model"])
            and not isinstance(provider_dimensions, bool)
            and provider_dimensions == space["configuration"]["dimensions"]
            and type(getattr(provider, "provider_config_sha256")) is str
            and str(getattr(provider, "provider_config_sha256")) == str(space["digest"])
        )
    except Exception:
        metadata_matches = False
    if not metadata_matches:
        return (
            _query_lane("degraded", len(queries), 0, "profile-mismatch"),
            [],
        )
    result_lists: list[list[dict[str, Any]]] = []
    try:
        result_lists = semantic_search_batch(
            path,
            queries,
            provider,
            namespace=namespace,
            node_types=node_types,
            limit=min(MAX_INTERNAL_LANE_RESULTS, max(limit * 3, 20)),
            include_stale=include_stale,
            include_orphaned=include_orphaned,
        )
    except Exception:
        return (
            _query_lane("degraded", len(queries), 0, "semantic-query-failed"),
            [],
        )
    ordered, _ = _aggregate_result_lists(result_lists)
    if not space["complete"]:
        return (
            _query_lane(
                "degraded", len(queries), len(ordered), "coverage-insufficient"
            ),
            ordered,
        )
    return _query_lane("enabled", len(queries), len(ordered)), ordered


def _path_evidence(record: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    path = record.get("path")
    if not isinstance(path, list) or not path:
        return None, None
    nodes: list[str] = []
    edge_types: list[str] = []
    seed_id: str | None = None
    for index, step in enumerate(path):
        if not isinstance(step, Mapping):
            return None, None
        source = str(step.get("source", ""))
        target = str(step.get("target", ""))
        direction = str(step.get("direction", ""))
        relation = str(step.get("relation", ""))
        if not source or not target or not relation:
            return None, None
        current = source if direction == "outgoing" else target
        neighbor = target if direction == "outgoing" else source
        if index == 0:
            seed_id = current
            nodes.append(current)
        elif nodes[-1] != current:
            return None, None
        nodes.append(neighbor)
        edge_types.append(relation)
    if len(nodes) > 10 or len(edge_types) > 9:
        return None, seed_id
    return {"lane": "graph", "nodes": nodes, "edge_types": edge_types}, seed_id


def _graph_lanes(
    path: Path,
    graph: Mapping[str, Any],
    *,
    namespace: str,
    node_types: list[str],
    include_stale: bool,
    include_orphaned: bool,
    limit: int,
    legacy_seeds: list[str],
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    dict[str, Any],
    list[dict[str, Any]],
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
]:
    raw_seeds = list(graph.get("seed_ids") or [])
    seeds = list(dict.fromkeys([*raw_seeds, *legacy_seeds]))[:256]
    strategy = str(graph.get("strategy", "hybrid"))
    max_depth = int(graph.get("max_depth", 0))
    edge_types = list(graph.get("edge_types") or [])
    direction = {"out": "outgoing", "in": "incoming", "both": "both"}.get(
        str(graph.get("direction", "both")), "both"
    )
    graph_disabled_reason: str | None = None
    ppr_disabled_reason: str | None = None
    if not seeds:
        graph_disabled_reason = ppr_disabled_reason = "no-seeds"
    elif max_depth == 0:
        graph_disabled_reason = ppr_disabled_reason = "max-depth-zero"
    elif not edge_types:
        graph_disabled_reason = ppr_disabled_reason = "no-edge-types"
    elif strategy == "ppr":
        graph_disabled_reason = "strategy-ppr"
    elif strategy == "bfs":
        ppr_disabled_reason = "strategy-bfs"

    if graph_disabled_reason is not None and ppr_disabled_reason is not None:
        return (
            _seed_lane("disabled", len(seeds), 0, graph_disabled_reason),
            [],
            _seed_lane("disabled", len(seeds), 0, ppr_disabled_reason),
            [],
            {},
            {},
        )

    valid_seeds: list[str] = []
    invalid_seed = False
    try:
        seed_resolutions = resolve_concepts(
            path, seeds, namespace=namespace, match_limit=1
        )
    except Exception:
        seed_resolutions = []
        invalid_seed = True
    allowed_types = set(node_types)
    for seed, resolution in zip(seeds, seed_resolutions):
        matches = resolution.get("matches") or []
        node = matches[0] if matches and isinstance(matches[0], dict) else None
        if (
            resolution.get("match_kind") == "id"
            and isinstance(node, dict)
            and str(node.get("id", "")) == seed
            and _node_allowed(
                node,
                node_types=allowed_types,
                include_stale=include_stale,
                include_orphaned=include_orphaned,
            )
        ):
            valid_seeds.append(seed)
        else:
            invalid_seed = True
    if not valid_seeds:
        reason = "graph-seeds-unavailable"
        return (
            _seed_lane("degraded", len(seeds), 0, reason)
            if graph_disabled_reason is None
            else _seed_lane("disabled", len(seeds), 0, graph_disabled_reason),
            [],
            _seed_lane("degraded", len(seeds), 0, reason)
            if ppr_disabled_reason is None
            else _seed_lane("disabled", len(seeds), 0, ppr_disabled_reason),
            [],
            {},
            {},
        )

    expansion: dict[str, Any] | None = None
    expansion_failed = False
    try:
        expansion = expand_index(
            path,
            valid_seeds,
            namespace=namespace,
            direction=direction,
            edge_types=edge_types,
            max_depth=max_depth,
            limit=min(
                MAX_INTERNAL_LANE_RESULTS,
                len(valid_seeds) + max(limit * 4, 40),
            ),
            include_taxonomy=False,
            include_stale=include_stale,
            include_orphaned=include_orphaned,
        )
    except Exception:
        expansion_failed = True
    if expansion_failed or expansion is None:
        reason = "graph-query-failed"
        return (
            _seed_lane("error", len(valid_seeds), 0, reason)
            if graph_disabled_reason is None
            else _seed_lane("disabled", len(valid_seeds), 0, graph_disabled_reason),
            [],
            _seed_lane("error", len(valid_seeds), 0, reason)
            if ppr_disabled_reason is None
            else _seed_lane("disabled", len(valid_seeds), 0, ppr_disabled_reason),
            [],
            {},
            {},
        )

    graph_values: list[dict[str, Any]] = []
    seed_evidence: dict[str, list[dict[str, Any]]] = {}
    path_evidence: dict[str, list[dict[str, Any]]] = {}
    if graph_disabled_reason is None:
        records = [
            item
            for item in expansion["nodes"]
            if not item.get("seed")
            and _node_allowed(
                item["node"],
                node_types=allowed_types,
                include_stale=include_stale,
                include_orphaned=include_orphaned,
            )
        ]
        for rank, record in enumerate(records[:MAX_INTERNAL_LANE_RESULTS], start=1):
            node = record["node"]
            node_id = str(node["id"])
            depth = int(record.get("depth", 0))
            graph_values.append(
                {
                    "node": node,
                    "score": 1.0 / (depth + 1),
                    "best_raw_score": 1.0 / (depth + 1),
                    "first_query": 0,
                    "rank": rank,
                }
            )
            evidence, seed_id = _path_evidence(record)
            if evidence is not None:
                path_evidence.setdefault(node_id, []).append(evidence)
            if seed_id is not None:
                seed_evidence.setdefault(node_id, []).append(
                    {"lane": "graph", "seed_id": seed_id}
                )
        graph_lane = _seed_lane(
            "degraded" if invalid_seed else "enabled",
            len(valid_seeds),
            len(graph_values),
            "partial-graph-seeds" if invalid_seed else None,
        )
    else:
        graph_lane = _seed_lane(
            "disabled", len(valid_seeds), 0, graph_disabled_reason
        )

    ppr_values: list[dict[str, Any]] = []
    if ppr_disabled_reason is None:
        ppr_seed_by_node: dict[str, str] = {}
        for record in expansion["nodes"]:
            if not isinstance(record, Mapping) or not isinstance(
                record.get("node"), Mapping
            ):
                continue
            expansion_node_id = str(record["node"].get("id", ""))
            if not expansion_node_id:
                continue
            if record.get("seed"):
                ppr_seed_by_node[expansion_node_id] = expansion_node_id
                continue
            _, path_seed_id = _path_evidence(record)
            if path_seed_id is not None:
                ppr_seed_by_node[expansion_node_id] = path_seed_id
        candidate_ids = {
            str(record["node"]["id"])
            for record in expansion["nodes"]
            if isinstance(record, dict) and isinstance(record.get("node"), dict)
        }
        ppr_failed = False
        ppr: dict[str, Any] | None = None
        try:
            ppr = personalized_pagerank(
                path,
                {seed: 1.0 for seed in valid_seeds},
                namespace=namespace,
                node_types=node_types,
                edge_types=edge_types,
                include_taxonomy=False,
                include_similarity=False,
                include_stale=include_stale,
                include_orphaned=include_orphaned,
                limit=min(MAX_INTERNAL_LANE_RESULTS, max(limit * 4, 40)),
                _candidate_ids=candidate_ids,
            )
        except Exception:
            ppr_failed = True
        if ppr_failed or ppr is None:
            ppr_lane = _seed_lane(
                "error", len(valid_seeds), 0, "ppr-query-failed"
            )
        else:
            for result in ppr["results"][:MAX_INTERNAL_LANE_RESULTS]:
                node = result["node"]
                node_id = str(node["id"])
                rank = int(result["rank"])
                score = float(result["score"])
                ppr_values.append(
                    {
                        "node": node,
                        "score": 1.0 / (60.0 + rank),
                        "best_raw_score": score,
                        "first_query": 0,
                        "rank": rank,
                    }
                )
                ppr_seed_id = ppr_seed_by_node.get(node_id)
                if ppr_seed_id is not None:
                    seed_evidence.setdefault(node_id, []).append(
                        {"lane": "ppr", "seed_id": ppr_seed_id}
                    )
            ppr_lane = _seed_lane(
                "degraded" if invalid_seed else "enabled",
                len(valid_seeds),
                len(ppr_values),
                "partial-graph-seeds" if invalid_seed else None,
            )
    else:
        ppr_lane = _seed_lane("disabled", len(valid_seeds), 0, ppr_disabled_reason)
    return graph_lane, graph_values, ppr_lane, ppr_values, seed_evidence, path_evidence


def _lane_evidence_score(value: Mapping[str, Any]) -> float:
    raw = value.get("best_raw_score")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        score = float(raw)
        if math.isfinite(score):
            return round(score, 12)
    return round(float(value.get("score", 0.0)), 12)


def _fused_results(
    lane_values: Mapping[str, list[dict[str, Any]]],
    *,
    limit: int,
    protected_identity_ids: set[str],
    seed_evidence: Mapping[str, list[dict[str, Any]]],
    path_evidence: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    weights = {
        "identity": 3.0,
        "lexical": 1.0,
        "semantic": 0.9,
        "graph": 0.7,
        "ppr": 0.6,
    }
    nodes: dict[str, dict[str, Any]] = {}
    evidence: dict[str, dict[str, dict[str, Any]]] = {}
    scores: dict[str, float] = {}
    identity_rank: dict[str, int] = {}
    for lane, values in lane_values.items():
        for rank, value in enumerate(values[:MAX_INTERNAL_LANE_RESULTS], start=1):
            node = value["node"]
            node_id = str(node["id"])
            nodes[node_id] = node
            scores[node_id] = scores.get(node_id, 0.0) + weights[lane] / (60.0 + rank)
            evidence.setdefault(node_id, {})[lane] = {
                "rank": rank,
                "score": _lane_evidence_score(value),
            }
            if lane == "identity":
                identity_rank[node_id] = rank
    protected = sorted(
        (node_id for node_id in protected_identity_ids if node_id in scores),
        key=lambda node_id: (
            identity_rank.get(node_id, MAX_INTERNAL_LANE_RESULTS + 1),
            str(nodes[node_id].get("label", "")),
            node_id,
        ),
    )
    remaining = sorted(
        (node_id for node_id in scores if node_id not in protected_identity_ids),
        key=lambda node_id: (
            -scores[node_id],
            str(nodes[node_id].get("label", "")),
            node_id,
        ),
    )
    ordered = [*protected, *remaining][:limit]
    output: list[dict[str, Any]] = []
    for node_id in ordered:
        lanes = evidence[node_id]
        explanation = [
            f"{lane} rank {lanes[lane]['rank']} contributed deterministic evidence."
            for lane in ("identity", "lexical", "semantic", "graph", "ppr")
            if lane in lanes
        ]
        node = nodes[node_id]
        output.append(
            {
                "node_id": node_id,
                "node_type": str(node.get("type", "")),
                "label": str(node.get("label", "")),
                "lanes": lanes,
                "seed_evidence": list(seed_evidence.get(node_id, []))[:32],
                "path_evidence": list(path_evidence.get(node_id, []))[:32],
                "fusion": {
                    "method": "single-lane" if len(lanes) == 1 else "weighted",
                    "score": round(scores[node_id], 12),
                    "explanation": explanation,
                },
            }
        )
    return output


def execute_retrieval_plan(
    path: Path,
    plan: Mapping[str, Any],
    *,
    plan_mode: str = "planned",
    namespace: str | None = None,
    embedding_profile: str | None = None,
    provider_config: Mapping[str, Any] | None = None,
    provider_registry: ProviderAdapterRegistry | None = None,
    environ: Mapping[str, str] | None = None,
    expected_graph_sha256: str | None = None,
) -> dict[str, Any]:
    """Execute all declared lanes without mutating or materializing the index."""
    if plan_mode not in {"planned", "legacy"}:
        raise RetrievalError("invalid-retrieval-request", "plan mode is invalid")
    validated = _validated_plan(plan)
    plan_namespace = str(validated["namespace"])
    if namespace is not None and namespace != plan_namespace:
        raise RetrievalError(
            "invalid-retrieval-request", "requested namespace does not match the plan"
        )
    database = Path(path)
    index_failure = False
    try:
        exists = agent_index_exists(database)
    except Exception:
        exists = False
        index_failure = True
    if index_failure or not exists:
        raise RetrievalError("index-unavailable", "Agent index is unavailable")
    metadata_failed = False
    try:
        before_token = index_generation_token(database)
        before_status = index_status(database)
    except Exception:
        metadata_failed = True
        before_token = ""
        before_status = {}
    if metadata_failed:
        raise RetrievalError("index-unavailable", "Agent index is unavailable")
    snapshot_sha256 = str(before_status.get("snapshot_sha256", ""))
    graph_sha256 = str(before_status.get("graph_sha256", ""))
    if not _SHA256_RE.fullmatch(snapshot_sha256) or not _SHA256_RE.fullmatch(graph_sha256):
        raise RetrievalError("index-unavailable", "Agent index metadata is invalid")
    if expected_graph_sha256 is not None:
        if not _SHA256_RE.fullmatch(str(expected_graph_sha256)):
            raise RetrievalError(
                "invalid-retrieval-request", "expected graph digest is invalid"
            )
        if graph_sha256 != str(expected_graph_sha256):
            raise RetrievalError("stale-index", "Agent index is not current")

    queries_identity = list(validated["identity_queries"])
    queries_lexical = list(validated["lexical_queries"])
    queries_semantic = list(validated["semantic_queries"])
    filters = validated["filters"]
    node_types = list(filters["node_types"])
    include_stale = bool(filters["include_stale"])
    include_orphaned = bool(filters["include_orphaned"])
    limit = int(validated["limit"])

    (
        identity_lane,
        identity_values,
        resolutions,
        protected,
        blocked_ambiguous_ids,
        suppress_automatic_lanes,
    ) = _identity_lane(
        database,
        queries_identity,
        namespace=plan_namespace,
        node_types=set(node_types),
        include_stale=include_stale,
        include_orphaned=include_orphaned,
        limit=limit,
    )
    if suppress_automatic_lanes:
        lexical_lane = _query_lane(
            "degraded" if queries_lexical else "disabled",
            len(queries_lexical),
            0,
            "identity-ambiguity" if queries_lexical else "no-queries",
        )
        lexical_values: list[dict[str, Any]] = []
    else:
        lexical_lane, lexical_values = _lexical_lane(
            database,
            queries_lexical,
            namespace=plan_namespace,
            node_types=node_types,
            include_stale=include_stale,
            include_orphaned=include_orphaned,
            limit=limit,
        )
    explicit_seed_ids = {str(seed) for seed in validated["graph"]["seed_ids"]}
    blocked_lane_ids = blocked_ambiguous_ids - explicit_seed_ids
    if blocked_lane_ids:
        filtered_lexical = [
            value
            for value in lexical_values
            if str(value["node"]["id"]) not in blocked_lane_ids
        ]
        if len(filtered_lexical) != len(lexical_values):
            lexical_values = filtered_lexical
            lexical_lane = _query_lane(
                "degraded",
                len(queries_lexical),
                len(lexical_values),
                "identity-ambiguity",
            )
    if suppress_automatic_lanes:
        semantic_lane = _query_lane(
            "degraded" if queries_semantic else "disabled",
            len(queries_semantic),
            0,
            "identity-ambiguity" if queries_semantic else "no-queries",
        )
        semantic_values: list[dict[str, Any]] = []
    else:
        semantic_lane, semantic_values = _semantic_lane(
            database,
            queries_semantic,
            namespace=plan_namespace,
            node_types=node_types,
            include_stale=include_stale,
            include_orphaned=include_orphaned,
            limit=limit,
            embedding_profile=embedding_profile,
            provider_config=provider_config,
            provider_registry=provider_registry,
            environ=environ,
        )
    if blocked_lane_ids:
        filtered_semantic = [
            value
            for value in semantic_values
            if str(value["node"]["id"]) not in blocked_lane_ids
        ]
        if len(filtered_semantic) != len(semantic_values):
            semantic_values = filtered_semantic
            semantic_lane = _query_lane(
                "degraded",
                len(queries_semantic),
                len(semantic_values),
                "identity-ambiguity",
            )
    legacy_seeds: list[str] = []
    if plan_mode == "legacy" and not validated["graph"]["seed_ids"]:
        legacy_seeds = list(
            dict.fromkeys(
                str(value["node"]["id"])
                for values in (identity_values, lexical_values, semantic_values)
                for value in values
            )
        )[:8]
    if suppress_automatic_lanes:
        declared_seeds = len(validated["graph"]["seed_ids"])
        graph_lane = _seed_lane(
            "degraded" if declared_seeds else "disabled",
            declared_seeds,
            0,
            "identity-ambiguity" if declared_seeds else "no-seeds",
        )
        ppr_lane = dict(graph_lane)
        graph_values: list[dict[str, Any]] = []
        ppr_values: list[dict[str, Any]] = []
        seed_evidence: dict[str, list[dict[str, Any]]] = {}
        path_evidence: dict[str, list[dict[str, Any]]] = {}
    else:
        (
            graph_lane,
            graph_values,
            ppr_lane,
            ppr_values,
            seed_evidence,
            path_evidence,
        ) = _graph_lanes(
            database,
            validated["graph"],
            namespace=plan_namespace,
            node_types=node_types,
            include_stale=include_stale,
            include_orphaned=include_orphaned,
            limit=limit,
            legacy_seeds=legacy_seeds,
        )
    if blocked_lane_ids:
        filtered_graph = [
            value
            for value in graph_values
            if str(value["node"]["id"]) not in blocked_lane_ids
        ]
        if len(filtered_graph) != len(graph_values):
            graph_values = filtered_graph
            graph_lane = _seed_lane(
                "degraded",
                int(graph_lane["seeds"]),
                len(graph_values),
                "identity-ambiguity",
            )
        filtered_ppr = [
            value
            for value in ppr_values
            if str(value["node"]["id"]) not in blocked_lane_ids
        ]
        if len(filtered_ppr) != len(ppr_values):
            ppr_values = filtered_ppr
            ppr_lane = _seed_lane(
                "degraded",
                int(ppr_lane["seeds"]),
                len(ppr_values),
                "identity-ambiguity",
            )
    result = {
        "schema": SEARCH_RESULT_SCHEMA,
        "plan_sha256": sha256_json(validated),
        "lanes": {
            "identity": identity_lane,
            "lexical": lexical_lane,
            "semantic": semantic_lane,
            "graph": graph_lane,
            "ppr": ppr_lane,
        },
        "results": _fused_results(
            {
                "identity": identity_values,
                "lexical": lexical_values,
                "semantic": semantic_values,
                "graph": graph_values,
                "ppr": ppr_values,
            },
            limit=limit,
            protected_identity_ids=protected,
            seed_evidence=seed_evidence,
            path_evidence=path_evidence,
        ),
    }
    generation_failed = False
    try:
        after_token = index_generation_token(database)
    except Exception:
        generation_failed = True
        after_token = ""
    if generation_failed:
        raise RetrievalError("stale-generation", "Agent index changed during retrieval")
    if after_token != before_token:
        raise RetrievalError("stale-generation", "Agent index changed during retrieval")
    execution = {
        "schema": SEARCH_EXECUTION_SCHEMA,
        "plan_mode": plan_mode,
        "namespace": plan_namespace,
        "snapshot_sha256": snapshot_sha256,
        "graph_sha256": graph_sha256,
        "identity_resolutions": resolutions,
        "result": result,
    }
    invalid_output = False
    try:
        validated_result = validate_contract(result)
        validated_execution = validate_contract(execution)
        encoded = canonical_json(validated_execution).encode("utf-8")
        if len(encoded) > MAX_RETRIEVAL_RESPONSE_BYTES:
            raise ValueError("retrieval response exceeds the byte limit")
    except Exception:
        invalid_output = True
        validated_result = None
        validated_execution = None
    if invalid_output or validated_result is None or validated_execution is None:
        raise RetrievalError(
            "invalid-retrieval-result", "retrieval produced an invalid bounded result"
        )
    validated_execution["result"] = validated_result
    return validated_execution


def _context_node_allowed(node: Mapping[str, Any]) -> bool:
    return bool(str(node.get("id", "")) and str(node.get("label", "")))


def _context_source(node: Mapping[str, Any]) -> dict[str, Any] | None:
    provenance = node.get("provenance")
    provenance = provenance if isinstance(provenance, Mapping) else {}
    authority = str(provenance.get("authority", ""))
    if not authority:
        return None
    source: dict[str, Any] = {"node_id": str(node["id"]), "authority": authority}
    for key in (
        "line",
        "definition_start_line",
        "definition_end_line",
        "source_format",
        "web",
        "definition_sha256",
    ):
        if provenance.get(key) not in {None, ""}:
            source[key] = provenance[key]
    return source


def build_context_from_execution(
    path: Path,
    execution: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    token_budget: int = 6000,
    namespace: str | None = None,
) -> dict[str, Any]:
    """Pack one retrieval execution into the existing deterministic context shape."""
    if (
        isinstance(token_budget, bool)
        or not isinstance(token_budget, int)
        or token_budget < 1
        or token_budget > MAX_CONTEXT_BUDGET
    ):
        raise RetrievalError("invalid-context-budget", "context budget is invalid")
    invalid = False
    try:
        value = validate_contract(dict(execution))
        result = validate_contract(value["result"])
    except (ContractError, TypeError, ValueError, RecursionError, OverflowError):
        invalid = True
        value = None
        result = None
    if invalid or value is None or result is None or value.get("schema") != SEARCH_EXECUTION_SCHEMA:
        raise RetrievalError("invalid-retrieval-result", "retrieval execution is invalid")
    validated_plan = _validated_plan(plan)
    if (
        sha256_json(validated_plan) != str(result["plan_sha256"])
        or str(validated_plan["namespace"]) != str(value["namespace"])
    ):
        raise RetrievalError(
            "invalid-retrieval-request",
            "context plan does not match the retrieval execution",
        )
    selected_namespace = str(value["namespace"])
    context_include_stale = bool(validated_plan["filters"]["include_stale"])
    if namespace is not None and namespace != selected_namespace:
        raise RetrievalError(
            "invalid-retrieval-request", "context namespace does not match retrieval"
        )
    database = Path(path)
    metadata_failed = False
    try:
        token = index_generation_token(database)
        status = index_status(database)
    except Exception:
        metadata_failed = True
        token = ""
        status = {}
    if metadata_failed:
        raise RetrievalError("index-unavailable", "Agent index is unavailable")
    if (
        str(status.get("snapshot_sha256", "")) != str(value["snapshot_sha256"])
        or str(status.get("graph_sha256", "")) != str(value["graph_sha256"])
    ):
        raise RetrievalError("stale-generation", "retrieval generation is no longer current")

    bundle: dict[str, Any] = {
        "schema": CONTEXT_SCHEMA,
        "snapshot_sha256": str(value["snapshot_sha256"]),
        "namespace": selected_namespace,
        "query": str(validated_plan["question"]),
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
        "retrieval": {
            "policy": {
                "plan_mode": str(value["plan_mode"]),
                "plan_sha256": str(result["plan_sha256"]),
                "lanes": result["lanes"],
                "identity_resolutions": value["identity_resolutions"],
            },
            "explanations": [],
        },
    }
    if estimate_tokens(bundle) > token_budget:
        raise RetrievalError("budget-too-small", "context budget is too small")
    result_records = list(result["results"])
    wanted = [str(item["node_id"]) for item in result_records]
    wanted_set = set(wanted)
    ambiguous_groups: list[set[str]] = []
    fixed_resolution_ids: set[str] = set()
    has_ambiguous_overflow = False
    for resolution in value["identity_resolutions"]:
        if resolution["status"] in {"exact", "alias", "scoped-alias"}:
            fixed_resolution_ids.update(
                str(node_id) for node_id in resolution["candidate_ids"]
            )
            continue
        if resolution["status"] != "ambiguous":
            continue
        if resolution["overflow"]:
            has_ambiguous_overflow = True
            continue
        group = {str(node_id) for node_id in resolution["candidate_ids"]}
        if len(group) < 2 or not (group & wanted_set):
            continue
        merged: list[set[str]] = []
        for existing in ambiguous_groups:
            if existing & group:
                group.update(existing)
            else:
                merged.append(existing)
        merged.append(group)
        ambiguous_groups = merged
    ambiguous_by_id = {
        node_id: group for group in ambiguous_groups for node_id in group
    }
    overflow_blocked_ids = (
        wanted_set - fixed_resolution_ids if has_ambiguous_overflow else set()
    )
    connection = None
    try:
        connection = open_agent_index(database)
    except Exception:
        pass
    if connection is None:
        raise RetrievalError("index-unavailable", "Agent index is unavailable")
    packing_failed = False
    try:
        nodes: dict[str, dict[str, Any]] = {}
        if wanted:
            placeholders = ",".join("?" for _ in wanted)
            rows = connection.execute(
                f"SELECT * FROM nodes WHERE namespace = ? AND id IN ({placeholders})",
                [selected_namespace, *wanted],
            )
            for row in rows:
                nodes[str(row["id"])] = _node_payload(row)
        included: set[str] = set()
        processed: set[str] = set()
        ranks = {
            str(search_record["node_id"]): rank
            for rank, search_record in enumerate(result_records, start=1)
        }
        records_by_id = {
            str(search_record["node_id"]): search_record
            for search_record in result_records
        }
        for search_record in result_records:
            node_id = str(search_record["node_id"])
            if node_id in processed:
                continue
            if node_id in overflow_blocked_ids:
                processed.update(overflow_blocked_ids)
                bundle["omissions"].append(
                    {
                        "kind": "identity-group",
                        "id": sha256_json(sorted(overflow_blocked_ids)),
                        "reason": "ambiguous-group-overflow",
                    }
                )
                continue
            group = ambiguous_by_id.get(node_id, {node_id})
            processed.update(group)
            group_records = [
                records_by_id[group_id]
                for group_id in wanted
                if group_id in group and group_id in records_by_id
            ]
            group_nodes = [nodes.get(str(record["node_id"])) for record in group_records]
            if (
                len(group_records) != len(group)
                or any(
                    node is None or not _context_node_allowed(node)
                    for node in group_nodes
                )
            ):
                bundle["omissions"].append(
                    {
                        "kind": "identity-group" if len(group) > 1 else "node",
                        "id": sha256_json(sorted(group)) if len(group) > 1 else node_id,
                        "reason": "generation-mismatch",
                    }
                )
                continue
            candidate = json.loads(canonical_json(bundle))
            for record, node in zip(group_records, group_nodes):
                assert node is not None
                record_id = str(record["node_id"])
                candidate["nodes"].append(node)
                source = _context_source(node)
                if source is not None:
                    candidate["sources"].append(source)
                candidate["retrieval"]["explanations"].append(
                    {
                        "node_id": record_id,
                        "rank": ranks[record_id],
                        "score": record["fusion"]["score"],
                        "reasons": [
                            {
                                "method": lane,
                                "rank": evidence["rank"],
                                "score": evidence["score"],
                            }
                            for lane, evidence in record["lanes"].items()
                        ],
                    }
                )
                if "identity" in record["lanes"]:
                    candidate["seeds"].append(record_id)
            if estimate_tokens(candidate) <= token_budget:
                bundle = candidate
                included.update(str(record["node_id"]) for record in group_records)
            else:
                bundle["omissions"].append(
                    {
                        "kind": "identity-group" if len(group) > 1 else "node",
                        "id": sha256_json(sorted(group)) if len(group) > 1 else node_id,
                        "reason": (
                            "ambiguous-group-budget"
                            if len(group) > 1
                            else "token-budget"
                        ),
                    }
                )

        if included:
            ordered_ids = sorted(included)
            placeholders = ",".join("?" for _ in ordered_ids)
            candidate_values = ",".join("(?)" for _ in ordered_ids)
            edge_cursor = connection.execute(
                f"""
                WITH candidate_ids(id) AS (VALUES {candidate_values})
                SELECT edges.payload FROM edges
                JOIN candidate_ids AS sources ON sources.id = edges.source
                JOIN candidate_ids AS targets ON targets.id = edges.target
                WHERE edges.namespace = ?
                ORDER BY edges.relation, edges.source, edges.target, edges.edge_id
                LIMIT ?
                """,
                [*ordered_ids, selected_namespace, MAX_CONTEXT_RELATION_ROWS + 1],
            )
            for offset, row in enumerate(edge_cursor):
                if offset >= MAX_CONTEXT_RELATION_ROWS:
                    bundle["omissions"].append(
                        {"kind": "edge", "id": "bounded-tail", "reason": "row-budget"}
                    )
                    break
                edge = json.loads(row["payload"])
                if not _edge_allowed(edge, include_stale=context_include_stale):
                    continue
                candidate = json.loads(canonical_json(bundle))
                candidate["edges"].append(edge)
                if estimate_tokens(candidate) <= token_budget:
                    bundle = candidate
                else:
                    bundle["omissions"].append(
                        {"kind": "edge", "id": "bounded", "reason": "token-budget"}
                    )
                    break
            ref_cursor = connection.execute(
                f"""
                SELECT payload FROM refs
                WHERE namespace = ? AND target IN ({placeholders})
                ORDER BY authority, line, ref_id
                LIMIT ?
                """,
                [selected_namespace, *ordered_ids, MAX_CONTEXT_RELATION_ROWS + 1],
            )
            for offset, row in enumerate(ref_cursor):
                if offset >= MAX_CONTEXT_RELATION_ROWS:
                    bundle["omissions"].append(
                        {"kind": "reference", "id": "bounded-tail", "reason": "row-budget"}
                    )
                    break
                reference = json.loads(row["payload"])
                candidate = json.loads(canonical_json(bundle))
                candidate["references"].append(reference)
                if estimate_tokens(candidate) <= token_budget:
                    bundle = candidate
                else:
                    bundle["omissions"].append(
                        {"kind": "reference", "id": "bounded", "reason": "token-budget"}
                    )
                    break
    except Exception:
        packing_failed = True
    finally:
        connection.close()
    if packing_failed:
        raise RetrievalError("index-unavailable", "Agent index is unavailable")
    generation_failed = False
    try:
        current_token = index_generation_token(database)
    except Exception:
        generation_failed = True
        current_token = ""
    if generation_failed or current_token != token:
        raise RetrievalError("stale-generation", "Agent index changed during context packing")
    while bundle["omissions"] and estimate_tokens(bundle) > token_budget:
        bundle["omissions"].pop()
    # The estimate includes the decimal representation of the estimate itself.
    # Converge that tiny feedback loop so the published budget is self-consistent.
    for _ in range(4):
        estimated = estimate_tokens(bundle)
        if bundle["budget"]["estimated_tokens"] == estimated:
            break
        bundle["budget"]["estimated_tokens"] = estimated
    final_estimate = estimate_tokens(bundle)
    bundle["budget"]["estimated_tokens"] = final_estimate
    if estimate_tokens(bundle) > token_budget:
        raise RetrievalError("budget-too-small", "context budget is too small")
    return bundle
