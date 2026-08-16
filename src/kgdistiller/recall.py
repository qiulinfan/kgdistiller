"""Closed, deterministic federated recall operations over coherent Vault views."""

from __future__ import annotations

import copy
import heapq
import itertools
import re
from collections import deque
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .contracts import ContractError, canonical_json, sha256_json, validate_contract
from .federation import (
    FederatedVault,
    FederationError,
    FederationSnapshot,
    capture_federation,
    project_federation,
    qualified_handle,
    query_terms,
)
from .query import DEFAULT_SEMANTIC_RELATIONS, normalize_text
from .source_archive import (
    SourceArchiveError,
    verified_version_text,
    verify_evidence_span,
)


REQUEST_SCHEMA = "qlkg-recall-request-v1"
REPORT_SCHEMA = "qlkg-recall-report-v1"
ERROR_SCHEMA = "qlkg-recall-error-v1"
MAX_RECALL_REQUEST_BYTES = 4 * 1024 * 1024
MAX_RECALL_REPORT_BYTES = 4 * 1024 * 1024
MAX_RESULT_NODES = 500
MAX_RESULT_EDGES = 5000
MAX_RESULT_EVIDENCE = 5000
MAX_RESULT_OMISSIONS = 5000
MAX_NODE_PARENTS = 512
MAX_NODE_ALIASES = 512
MAX_NODE_TEXT_CHARACTERS = 1_048_576
MAX_EVIDENCE_EXCERPT_CHARACTERS = 65_536
MAX_CONTEXT_SOURCE_BYTES = 128 * 1024 * 1024
MAX_CONTEXT_VERSIONS = 128
MAX_INTERNAL_CANDIDATES = 2_000
MAX_IDENTITY_HITS = 2_000
MAX_IDENTITY_WORK = 100_000
MAX_RESOLVE_IDENTITY_WORK = 100_000
MAX_LEXICAL_WORK = 100_000
MAX_TAXONOMY_FRONTIER = 10_000
MAX_GRAPH_SEEDS = 500
MAX_GRAPH_REACHED = 2_000
MAX_GRAPH_TRAVERSED = 5_000
MAX_GRAPH_WORK = 20_000
MAX_GRAPH_EDGE_WORK = 100_000
MAX_LANE_PATH = 8
LANE_ORDER = ("identity", "taxonomy", "lexical", "graph")
RELATIONS = {
    "contains",
    "prerequisite-for",
    "implies",
    "generalizes",
    "contrasts-with",
    "derived-from",
}


class RecallError(RuntimeError):
    """Stable, closed recall failure suitable for CLI and MCP responses."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        operation: str | None = None,
        vault_id: str | None = None,
        generation: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message[:1024] or "recall failed"
        self.operation = operation
        self.vault_id = vault_id
        self.generation = generation

    def payload(self) -> dict[str, Any]:
        return validate_contract(
            {
                "schema": ERROR_SCHEMA,
                "error": {
                    "code": self.code[:64],
                    "message": self.message,
                    "operation": self.operation,
                    "vault_id": self.vault_id,
                    "generation": self.generation,
                },
            }
        )


def make_recall_request(
    operation: str,
    *,
    vault_ids: Sequence[str] = (),
    queries: Sequence[str] = (),
    query: str | None = None,
    handle: str | None = None,
    handles: Sequence[str] = (),
    scopes: Sequence[str] = (),
    direction: str = "both",
    edge_types: Sequence[str] = (),
    max_depth: int = 1,
    limit: int = 20,
    token_budget: int = 6000,
    include_stale: bool = False,
) -> dict[str, Any]:
    """Build and validate the one action-independent closed recall request."""

    payload = {
        "schema": REQUEST_SCHEMA,
        "operation": operation,
        "vault_ids": list(vault_ids),
        "queries": list(queries),
        "query": query,
        "handle": handle,
        "handles": list(handles),
        "scopes": list(scopes),
        "direction": direction,
        "edge_types": list(edge_types),
        "max_depth": max_depth,
        "limit": limit,
        "token_budget": token_budget,
        "include_stale": include_stale,
    }
    try:
        return validate_contract(payload)
    except ContractError as error:
        raise RecallError(
            "invalid-recall-request",
            "recall request violates the closed v1 contract",
            operation=operation if operation in {
                "status", "roots", "children", "resolve", "search", "get", "expand", "context"
            } else None,
        ) from error


def _empty_result(query: str | None = None) -> dict[str, Any]:
    return {
        "query": query,
        "resolutions": [],
        "nodes": [],
        "edges": [],
        "evidence": [],
        "omissions": [],
        "truncated": False,
        "estimated_tokens": 0,
    }


def _add_omission(
    result: dict[str, Any], *, kind: str, identifier: str, reason: str
) -> None:
    record = {"kind": kind, "id": identifier[:4096], "reason": reason}
    if record in result["omissions"]:
        return
    if len(result["omissions"]) < MAX_RESULT_OMISSIONS:
        result["omissions"].append(record)
    result["truncated"] = True


def _finalize_estimate(result: dict[str, Any]) -> int:
    while True:
        size = len(canonical_json(result).encode("utf-8"))
        if result["estimated_tokens"] == size:
            return size
        result["estimated_tokens"] = size


def _parse_handle(handle: str) -> tuple[str, str]:
    if not isinstance(handle, str) or ":" not in handle:
        raise RecallError("invalid-handle", "recall handle is invalid")
    vault_id, node_id = handle.split(":", 1)
    return vault_id, node_id


def _lookup(snapshot: FederationSnapshot, handle: str) -> tuple[FederatedVault, dict[str, Any]]:
    vault_id, node_id = _parse_handle(handle)
    federated = snapshot.by_id.get(vault_id)
    if federated is None:
        if any(item["vault_id"] == vault_id for item in snapshot.incomplete_vaults):
            raise RecallError(
                "incomplete-vault",
                "requested handle belongs to an incomplete Vault",
                vault_id=vault_id,
                generation=snapshot.generation,
            )
        raise RecallError(
            "unknown-vault",
            "requested handle belongs to an unavailable Vault",
            vault_id=vault_id,
            generation=snapshot.generation,
        )
    node = federated.view.nodes.get(node_id)
    if node is None:
        raise RecallError(
            "unknown-node",
            "requested qualified node does not exist",
            vault_id=vault_id,
            generation=snapshot.generation,
        )
    return federated, node


def _lookup_multi(
    snapshot: FederationSnapshot,
    handle: str,
    result: dict[str, Any],
) -> tuple[FederatedVault, dict[str, Any]] | None:
    """Resolve one member of a bounded multi-handle operation without dropping peers."""

    try:
        return _lookup(snapshot, handle)
    except RecallError as error:
        if error.code not in {"incomplete-vault", "unknown-vault", "unknown-node"}:
            raise
        reason = "incomplete-vault" if error.code == "incomplete-vault" else "scope"
        _add_omission(result, kind="node", identifier=handle, reason=reason)
        return None


def _node_allowed(node: Mapping[str, Any], *, include_stale: bool) -> bool:
    properties = node.get("properties")
    properties = properties if isinstance(properties, Mapping) else {}
    provenance = node.get("provenance")
    provenance = provenance if isinstance(provenance, Mapping) else {}
    return (
        provenance.get("active") is not False
        and properties.get("source_status") != "orphaned"
        and (include_stale or properties.get("curation_status") != "needs-review")
    )


def _lane(
    lane: str,
    score: float,
    reason: str,
    *,
    match_kind: str | None = None,
    matched_fields: Sequence[str] = (),
    matched_terms: Sequence[str] = (),
    scope: str | None = None,
    seed: str | None = None,
    path: Sequence[dict[str, str]] = (),
) -> dict[str, Any]:
    return {
        "lane": lane,
        "rank": 1,
        "score": round(float(score), 12),
        "reason": reason,
        "match_kind": match_kind,
        "matched_fields": list(matched_fields),
        "matched_terms": list(matched_terms),
        "scope": scope,
        "seed": seed,
        "path": copy.deepcopy(list(path)),
    }


def _validate_lane_semantics(record: Mapping[str, Any]) -> bool:
    lane = record.get("lane")
    reason = record.get("reason")
    match_kind = record.get("match_kind")
    fields = record.get("matched_fields") or []
    terms = record.get("matched_terms") or []
    scope = record.get("scope")
    seed = record.get("seed")
    path = record.get("path") or []
    if lane == "identity":
        return (
            (reason, match_kind) in {
                ("exact-id", "id"),
                ("exact-label", "label"),
                ("reviewed-alias", "alias"),
            }
            and not fields and not terms and scope is None and seed is None and not path
        )
    if lane == "taxonomy":
        return (
            reason == "scope-member" and match_kind is None and not fields
            and not terms and scope is not None and seed is None
            and all(step.get("relation") == "contains" for step in path)
        )
    if lane == "lexical":
        return (
            reason in {"token-overlap", "phrase-match"}
            and match_kind is None and bool(fields) and bool(terms)
            and scope is None and seed is None and not path
        )
    if lane == "graph":
        cursor = seed
        connected = cursor is not None
        for step in path:
            if step.get("source") == cursor:
                cursor = step.get("target")
            elif step.get("target") == cursor:
                cursor = step.get("source")
            else:
                connected = False
                break
        return (
            reason in {"trusted-seed", "trusted-edge"}
            and match_kind is None and not fields and not terms
            and scope is None and seed is not None
            and (
                (reason == "trusted-seed" and not path)
                or (reason == "trusted-edge" and bool(path))
            )
            and connected
        )
    return False


def _offer(
    candidates: dict[str, dict[str, dict[str, Any]]],
    handle: str,
    record: dict[str, Any],
) -> None:
    if not _validate_lane_semantics(record):
        raise RecallError("invalid-lane-evidence", "recall lane evidence is inconsistent")
    if record["lane"] == "taxonomy":
        path = record["path"]
        if not (
            (not path and record["scope"] == handle)
            or (
                path
                and path[0]["source"] == record["scope"]
                and path[-1]["target"] == handle
                and all(
                    previous["target"] == following["source"]
                    for previous, following in zip(path, path[1:])
                )
            )
        ):
            raise RecallError("invalid-lane-evidence", "taxonomy lane path is disconnected")
    if record["lane"] == "graph":
        cursor = record["seed"]
        for step in record["path"]:
            cursor = step["target"] if step["source"] == cursor else step["source"]
        if cursor != handle:
            raise RecallError("invalid-lane-evidence", "graph lane path does not reach its result")
    lanes = candidates.setdefault(handle, {})
    lane = str(record["lane"])
    previous = lanes.get(lane)
    key = (-float(record["score"]), canonical_json(record))
    if previous is None or key < (-float(previous["score"]), canonical_json(previous)):
        lanes[lane] = record


def _rank_lanes(candidates: Mapping[str, dict[str, dict[str, Any]]]) -> None:
    for lane in LANE_ORDER:
        ranked = sorted(
            (
                (handle, records[lane])
                for handle, records in candidates.items()
                if lane in records
            ),
            key=lambda item: (-float(item[1]["score"]), item[0]),
        )
        for rank, (_, record) in enumerate(ranked, start=1):
            record["rank"] = rank


def _rerank_result_lanes(result: Mapping[str, Any]) -> None:
    """Bind every final lane rank to deterministic score/handle fusion order."""

    nodes = result.get("nodes") or []
    for lane in LANE_ORDER:
        ranked = sorted(
            (
                (str(node["handle"]), record)
                for node in nodes
                for record in node.get("lane_evidence") or []
                if record.get("lane") == lane
            ),
            key=lambda item: (-float(item[1]["score"]), item[0]),
        )
        for rank, (_, record) in enumerate(ranked, start=1):
            record["rank"] = rank


def _node_dto(
    federated: FederatedVault,
    node_id: str,
    *,
    result: dict[str, Any],
    lanes: Mapping[str, dict[str, Any]] | None = None,
    include_text: bool = False,
    depth: int | None = None,
) -> dict[str, Any]:
    node = federated.view.nodes[node_id]
    properties = node.get("properties")
    properties = properties if isinstance(properties, Mapping) else {}
    provenance = node.get("provenance")
    provenance = provenance if isinstance(provenance, Mapping) else {}
    handle = qualified_handle(federated.vault.id, node_id)
    aliases: list[str] = []
    for alias in sorted(
        {str(item) for item in properties.get("aliases", []) if str(item).strip()}
    ):
        if len(alias) > 1024:
            _add_omission(result, kind="node", identifier=handle, reason="limit")
            continue
        aliases.append(alias)
    if len(aliases) > MAX_NODE_ALIASES:
        aliases = aliases[:MAX_NODE_ALIASES]
        _add_omission(result, kind="node", identifier=handle, reason="limit")
    parents = [
        qualified_handle(federated.vault.id, parent)
        for parent in federated.index.parents.get(node_id, ())
    ]
    if len(parents) > MAX_NODE_PARENTS:
        parents = parents[:MAX_NODE_PARENTS]
        _add_omission(result, kind="node", identifier=handle, reason="limit")
    text: str | None = str(node.get("text", "")) if include_text else None
    if text is not None and len(text) > MAX_NODE_TEXT_CHARACTERS:
        text = None
        _add_omission(result, kind="node", identifier=handle, reason="limit")
    lane_rows = [copy.deepcopy(lanes[name]) for name in LANE_ORDER if lanes and name in lanes]
    if len({row["lane"] for row in lane_rows}) != len(lane_rows) or any(
        not _validate_lane_semantics(row) for row in lane_rows
    ):
        raise RecallError("invalid-lane-evidence", "recall lane evidence is inconsistent")
    authority = provenance.get("authority")
    authority = str(authority) if isinstance(authority, str) and authority else None
    return {
        "handle": handle,
        "vault_id": federated.vault.id,
        "node_id": node_id,
        "type": str(node["type"]),
        "label": str(node.get("label", "")),
        "aliases": aliases,
        "text": text,
        "curation_status": str(properties.get("curation_status", "not-applicable")),
        "source_status": str(properties.get("source_status", "not-applicable")),
        "authority": authority,
        "parents": parents,
        "score": (
            round(sum(float(row["score"]) for row in lane_rows), 12)
            if lane_rows else None
        ),
        "lane_evidence": lane_rows,
        "depth": depth,
    }


def _edge_dto(
    federated: FederatedVault,
    edge: Mapping[str, Any],
    *,
    depth: int | None = None,
) -> dict[str, Any]:
    return {
        "source": qualified_handle(federated.vault.id, str(edge["source"])),
        "relation": str(edge["relation"]),
        "target": qualified_handle(federated.vault.id, str(edge["target"])),
        "evidence": str(edge["evidence"])[:4096] if edge.get("evidence") else None,
        "curation_status": str(edge.get("curation_status", "not-applicable")),
        "depth": depth,
    }


def _identity_hits(
    snapshot: FederationSnapshot,
    query: str,
    *,
    include_stale: bool,
    allowed: set[str] | None = None,
    cap: int = MAX_IDENTITY_HITS,
    work_cap: int = MAX_IDENTITY_WORK,
) -> tuple[list[tuple[str, str, float]], int, set[str], bool, int]:
    raw = query.strip()
    normalized = normalize_text(raw)
    hits: list[tuple[str, str, float]] = []
    total = 0
    kinds: set[str] = set()
    work = 0
    bounded = False
    seen: set[str] = set()

    def retain(
        federated: FederatedVault,
        node_id: str,
        kind: str,
        score: float,
    ) -> None:
        nonlocal total
        handle = qualified_handle(federated.vault.id, node_id)
        if handle in seen or (allowed is not None and handle not in allowed):
            return
        node = federated.view.nodes.get(node_id)
        if node is None or not _node_allowed(node, include_stale=include_stale):
            return
        seen.add(handle)
        total += 1
        kinds.add(kind)
        hits.append((handle, kind, score))
        if len(hits) >= max(2, cap * 2):
            hits.sort(key=lambda item: (-item[2], item[0]))
            del hits[cap:]

    # Exact IDs are O(number of Vaults), outrank every other identity kind,
    # and are never starved by a very large label/alias posting in one Vault.
    for federated in snapshot.vaults:
        view = federated.view
        if raw in view.nodes:
            retain(federated, raw, "id", 1000.0)

    # Label and alias postings are consumed round-robin across Vaults. This
    # keeps the global work bound fair and deterministic without materializing
    # every same-label match.
    streams: deque[tuple[FederatedVault, str, float, Iterable[str]]] = deque()
    for federated in snapshot.vaults:
        view = federated.view
        if normalized in view.labels:
            streams.append((federated, "label", 900.0, iter(view.labels[normalized])))
        if normalized in view.aliases:
            streams.append((federated, "alias", 800.0, iter(view.aliases[normalized])))
    while streams and work < work_cap:
        federated, kind, score, stream = streams.popleft()
        try:
            node_id = next(stream)
        except StopIteration:
            continue
        work += 1
        retain(federated, node_id, kind, score)
        streams.append((federated, kind, score, stream))
    if streams:
        bounded = True
    hits.sort(key=lambda item: (-item[2], item[0]))
    return hits[:cap], total, kinds, bounded, work


def _identity_lane(kind: str, score: float) -> dict[str, Any]:
    reason = {"id": "exact-id", "label": "exact-label", "alias": "reviewed-alias"}[kind]
    return _lane("identity", score, reason, match_kind=kind)


def _roots(snapshot: FederationSnapshot, request: Mapping[str, Any]) -> dict[str, Any]:
    result = _empty_result()
    limit = int(request["limit"])
    rows: list[tuple[str, FederatedVault, str]] = []
    overflow = False
    for federated in snapshot.vaults:
        for node_id in federated.index.roots:
            node = federated.view.nodes[node_id]
            if not _node_allowed(node, include_stale=bool(request["include_stale"])):
                continue
            if len(rows) >= limit:
                overflow = True
                break
            rows.append((qualified_handle(federated.vault.id, node_id), federated, node_id))
    for _, federated, node_id in rows:
        result["nodes"].append(_node_dto(federated, node_id, result=result))
    if overflow:
        _add_omission(result, kind="node", identifier="taxonomy-roots", reason="limit")
    return result


def _children(snapshot: FederationSnapshot, request: Mapping[str, Any]) -> dict[str, Any]:
    result = _empty_result()
    parent_handle = str(request["handle"])
    try:
        federated, parent = _lookup(snapshot, parent_handle)
    except RecallError as error:
        if error.code == "incomplete-vault":
            _add_omission(result, kind="node", identifier=parent_handle, reason="incomplete-vault")
            return result
        raise
    if parent.get("type") not in {"field", "topic"}:
        raise RecallError(
            "invalid-taxonomy-scope", "children requires a field or topic handle",
            operation="children", vault_id=federated.vault.id, generation=snapshot.generation,
        )
    if not _node_allowed(parent, include_stale=bool(request["include_stale"])):
        raise RecallError("stale-node", "taxonomy parent is excluded by freshness policy")
    child_ids = federated.index.children.get(str(parent["id"]), ())
    limit = int(request["limit"])
    selected: list[str] = []
    overflow = False
    for child_id in child_ids:
        child = federated.view.nodes[child_id]
        if not _node_allowed(child, include_stale=bool(request["include_stale"])):
            continue
        if len(selected) >= limit:
            overflow = True
            break
        selected.append(child_id)
    for rank, child_id in enumerate(selected, start=1):
        path = [{"source": parent_handle, "relation": "contains", "target": qualified_handle(federated.vault.id, child_id)}]
        lane = _lane("taxonomy", 200.0, "scope-member", scope=parent_handle, path=path)
        lane["rank"] = rank
        result["nodes"].append(
            _node_dto(federated, child_id, result=result, lanes={"taxonomy": lane}, depth=1)
        )
    selected_set = set(selected)
    selected_edges: dict[str, Mapping[str, Any]] = {}
    for edge in federated.view.outgoing.get(str(parent["id"]), ()):
        target = str(edge.get("target", ""))
        if edge.get("relation") == "contains" and target in selected_set:
            selected_edges[target] = edge
            if len(selected_edges) == len(selected_set):
                break
    for child_id in selected:
        edge = selected_edges.get(child_id)
        if edge is None:
            raise RecallError("invalid-taxonomy", "taxonomy edge is missing from graph view")
        result["edges"].append(_edge_dto(federated, edge, depth=1))
    if overflow:
        _add_omission(result, kind="node", identifier=parent_handle, reason="limit")
    return result


def _resolve(snapshot: FederationSnapshot, request: Mapping[str, Any]) -> dict[str, Any]:
    result = _empty_result()
    detailed: set[str] = set()
    remaining_details = MAX_RESULT_NODES
    next_identity_rank = 1
    remaining_work = MAX_RESOLVE_IDENTITY_WORK
    cached_queries: dict[
        str, tuple[list[tuple[str, str, float]], int, set[str], bool]
    ] = {}
    for query in request["queries"]:
        cache_key = str(query).strip()
        cached = cached_queries.get(cache_key)
        if cached is None:
            hits, total, kinds, bounded, used = _identity_hits(
                snapshot,
                str(query),
                include_stale=bool(request["include_stale"]),
                cap=MAX_IDENTITY_HITS,
                work_cap=remaining_work,
            )
            remaining_work = max(0, remaining_work - used)
            cached = (hits, total, kinds, bounded)
            cached_queries[cache_key] = cached
        hits, total, kinds, bounded = cached
        status = (
            "ambiguous" if bounded else
            "missing" if total == 0 else
            "ambiguous" if total > 1 else
            "alias" if hits[0][1] == "alias" else "exact"
        )
        if status in {"exact", "alias"}:
            # A singleton identity remains a complete resolution even after
            # the separately bounded node-detail budget is exhausted.
            selected = hits[:1]
        else:
            selected = hits[: min(int(request["limit"]), remaining_details)]
        match_kind = next(iter(kinds)) if len(kinds) == 1 else "mixed" if kinds else None
        resolution_overflow = bounded or total > len(selected)
        result["resolutions"].append(
            {
                "query": str(query).strip(),
                "status": status,
                "match_kind": match_kind,
                "matches": [handle for handle, _, _ in selected],
                "overflow": resolution_overflow,
            }
        )
        for handle, kind, score in selected:
            if handle in detailed:
                continue
            if remaining_details <= 0:
                _add_omission(result, kind="node", identifier=handle, reason="limit")
                continue
            federated, node = _lookup(snapshot, handle)
            lane = _identity_lane(kind, score)
            lane["rank"] = next_identity_rank
            next_identity_rank += 1
            result["nodes"].append(
                _node_dto(federated, str(node["id"]), result=result, lanes={"identity": lane})
            )
            detailed.add(handle)
            remaining_details -= 1
        if resolution_overflow:
            result["truncated"] = True
            _add_omission(result, kind="node", identifier=str(query), reason="limit")
    return result


def _taxonomy_frontier(
    snapshot: FederationSnapshot,
    scopes: Sequence[str],
    *,
    include_stale: bool,
    result: dict[str, Any],
) -> dict[str, tuple[str, int, list[dict[str, str]]]]:
    frontier: dict[str, tuple[str, int, list[dict[str, str]]]] = {}
    for scope in sorted(scopes):
        try:
            federated, node = _lookup(snapshot, scope)
        except RecallError as error:
            if error.code in {"incomplete-vault", "unknown-vault", "unknown-node"}:
                reason = "incomplete-vault" if error.code == "incomplete-vault" else "scope"
                _add_omission(result, kind="node", identifier=scope, reason=reason)
                continue
            raise
        if node.get("type") not in {"field", "topic"}:
            raise RecallError(
                "invalid-taxonomy-scope",
                "recall scope must identify a field or topic",
                vault_id=federated.vault.id,
                generation=snapshot.generation,
            )
        if not _node_allowed(node, include_stale=include_stale):
            _add_omission(result, kind="node", identifier=scope, reason="stale")
            continue
        queue: deque[tuple[str, list[dict[str, str]]]] = deque([(str(node["id"]), [])])
        visited: set[str] = set()
        while queue:
            node_id, path = queue.popleft()
            if node_id in visited:
                continue
            visited.add(node_id)
            current = federated.view.nodes[node_id]
            if not _node_allowed(current, include_stale=include_stale):
                continue
            handle = qualified_handle(federated.vault.id, node_id)
            candidate = (scope, len(path), path)
            previous = frontier.get(handle)
            if previous is None or (candidate[1], candidate[0], canonical_json(candidate[2])) < (
                previous[1], previous[0], canonical_json(previous[2])
            ):
                frontier[handle] = candidate
                if len(frontier) >= MAX_TAXONOMY_FRONTIER:
                    _add_omission(result, kind="node", identifier="taxonomy-frontier", reason="limit")
                    return frontier
            if len(path) >= MAX_LANE_PATH:
                if federated.index.children.get(node_id):
                    _add_omission(result, kind="node", identifier=scope, reason="limit")
                continue
            for child in federated.index.children.get(node_id, ()):
                if len(queue) + len(frontier) >= MAX_TAXONOMY_FRONTIER:
                    _add_omission(result, kind="node", identifier="taxonomy-frontier", reason="limit")
                    return frontier
                step = {
                    "source": handle,
                    "relation": "contains",
                    "target": qualified_handle(federated.vault.id, child),
                }
                queue.append((child, [*path, step]))
    return frontier


def _graph_lane(
    snapshot: FederationSnapshot,
    seeds: Sequence[str],
    *,
    direction: str,
    edge_types: set[str],
    max_depth: int,
    include_stale: bool,
    allowed: set[str] | None,
) -> tuple[
    dict[str, tuple[str, int, list[dict[str, str]]]],
    dict[tuple[str, str, str], tuple[FederatedVault, dict[str, Any], int]],
    bool,
]:
    reached: dict[str, tuple[str, int, list[dict[str, str]]]] = {}
    traversed: dict[tuple[str, str, str], tuple[FederatedVault, dict[str, Any], int]] = {}
    work = 0
    edge_work = 0
    truncated = False
    ordered_seeds = sorted(set(seeds))
    if len(ordered_seeds) > MAX_GRAPH_SEEDS:
        ordered_seeds = ordered_seeds[:MAX_GRAPH_SEEDS]
        truncated = True
    for seed in ordered_seeds:
        federated, seed_node = _lookup(snapshot, seed)
        if (
            (allowed is not None and seed not in allowed)
            or not _node_allowed(seed_node, include_stale=include_stale)
        ):
            continue
        queue: deque[tuple[str, list[dict[str, str]]]] = deque([(str(seed_node["id"]), [])])
        visited: set[str] = set()
        while queue:
            if work >= MAX_GRAPH_WORK:
                truncated = True
                break
            node_id, path = queue.popleft()
            if node_id in visited:
                continue
            visited.add(node_id)
            work += 1
            handle = qualified_handle(federated.vault.id, node_id)
            node = federated.view.nodes[node_id]
            if (
                (allowed is not None and handle not in allowed)
                or not _node_allowed(node, include_stale=include_stale)
            ):
                continue
            candidate = (seed, len(path), path)
            previous = reached.get(handle)
            if previous is None or (candidate[1], candidate[0], canonical_json(candidate[2])) < (
                previous[1], previous[0], canonical_json(previous[2])
            ):
                if previous is None and len(reached) >= MAX_GRAPH_REACHED:
                    truncated = True
                    break
                reached[handle] = candidate
            if len(path) >= max_depth:
                continue
            adjacent: list[Iterable[tuple[dict[str, Any], str]]] = []
            if direction in {"outgoing", "both"}:
                adjacent.append(
                    ((edge, str(edge["target"])) for edge in federated.view.outgoing.get(node_id, ()))
                )
            if direction in {"incoming", "both"}:
                adjacent.append(
                    ((edge, str(edge["source"])) for edge in federated.view.incoming.get(node_id, ()))
                )
            for edge, neighbor in itertools.chain.from_iterable(adjacent):
                edge_work += 1
                if edge_work > MAX_GRAPH_EDGE_WORK:
                    truncated = True
                    break
                relation = str(edge.get("relation", ""))
                if relation not in edge_types or relation == "contains":
                    continue
                if not include_stale and edge.get("curation_status") != "current":
                    continue
                neighbor_handle = qualified_handle(federated.vault.id, neighbor)
                neighbor_node = federated.view.nodes[neighbor]
                if (
                    (allowed is not None and neighbor_handle not in allowed)
                    or not _node_allowed(neighbor_node, include_stale=include_stale)
                ):
                    continue
                step = {
                    "source": qualified_handle(federated.vault.id, str(edge["source"])),
                    "relation": relation,
                    "target": qualified_handle(federated.vault.id, str(edge["target"])),
                }
                key = (step["source"], relation, step["target"])
                if key not in traversed:
                    if len(traversed) >= MAX_GRAPH_TRAVERSED:
                        truncated = True
                        break
                    traversed[key] = (federated, dict(edge), len(path) + 1)
                if work + len(queue) >= MAX_GRAPH_WORK:
                    truncated = True
                    break
                queue.append((neighbor, [*path, step]))
            if truncated and (
                work >= MAX_GRAPH_WORK
                or len(reached) >= MAX_GRAPH_REACHED
                or len(traversed) >= MAX_GRAPH_TRAVERSED
                or edge_work >= MAX_GRAPH_EDGE_WORK
            ):
                break
        if truncated and (work >= MAX_GRAPH_WORK or edge_work >= MAX_GRAPH_EDGE_WORK):
            break
    return reached, traversed, truncated


def _search(snapshot: FederationSnapshot, request: Mapping[str, Any]) -> dict[str, Any]:
    query = str(request["query"])
    result = _empty_result(query)
    include_stale = bool(request["include_stale"])
    candidates: dict[str, dict[str, dict[str, Any]]] = {}
    frontier = _taxonomy_frontier(
        snapshot,
        list(request["scopes"]),
        include_stale=include_stale,
        result=result,
    )
    allowed = set(frontier) if request["scopes"] else None
    identity_hits, identity_total, _, identity_bounded, _ = _identity_hits(
        snapshot,
        query,
        include_stale=include_stale,
        allowed=allowed,
        cap=MAX_IDENTITY_HITS,
    )
    for handle, kind, score in identity_hits:
        _offer(candidates, handle, _identity_lane(kind, score))
    if identity_bounded or identity_total > len(identity_hits):
        _add_omission(result, kind="node", identifier="identity-candidates", reason="limit")

    terms = query_terms(query)
    normalized_query = normalize_text(query)
    lexical_rows: list[tuple[str, dict[str, Any]]] = []
    lexical_total = 0
    lexical_work = 0
    lexical_bounded = False
    for federated in snapshot.vaults:
        posting_streams = [
            iter(federated.index.postings.get(term, ()))
            for term in sorted(terms)
            if federated.index.postings.get(term)
        ]
        if not posting_streams:
            continue
        previous_node: str | None = None
        for node_id in heapq.merge(*posting_streams):
            lexical_work += 1
            if lexical_work > MAX_LEXICAL_WORK:
                lexical_bounded = True
                break
            if node_id == previous_node:
                continue
            previous_node = node_id
            handle = qualified_handle(federated.vault.id, node_id)
            if allowed is not None and handle not in allowed:
                continue
            node = federated.view.nodes[node_id]
            if not _node_allowed(node, include_stale=include_stale):
                continue
            document = federated.index.documents[node_id]
            label_hits = sorted(terms & document.label_terms)
            alias_hits = sorted(terms & document.alias_terms)
            body_hits = sorted(terms & document.body_terms)
            score = 8 * len(label_hits) + 6 * len(alias_hits) + len(body_hits)
            phrase = (
                (8 if normalized_query and normalized_query in document.normalized_label else 0)
                + (6 if normalized_query and normalized_query in document.normalized_aliases else 0)
            )
            score += phrase
            if score <= 0:
                continue
            fields = [name for name, values in (("label", label_hits), ("alias", alias_hits), ("body", body_hits)) if values]
            matched = sorted(set(label_hits + alias_hits + body_hits))
            record = _lane(
                    "lexical", float(score), "phrase-match" if phrase else "token-overlap",
                    matched_fields=fields, matched_terms=matched,
            )
            lexical_rows.append((handle, record))
            lexical_total += 1
            if len(lexical_rows) >= 2 * MAX_INTERNAL_CANDIDATES:
                lexical_rows.sort(key=lambda item: (-float(item[1]["score"]), item[0]))
                del lexical_rows[MAX_INTERNAL_CANDIDATES:]
        if lexical_bounded:
            break
    lexical_rows.sort(key=lambda item: (-float(item[1]["score"]), item[0]))
    del lexical_rows[MAX_INTERNAL_CANDIDATES:]
    for handle, record in lexical_rows:
        _offer(candidates, handle, record)
    if lexical_bounded or lexical_total > len(lexical_rows):
        _add_omission(result, kind="node", identifier="lexical-candidates", reason="limit")

    requested_edges = set(request["edge_types"])
    semantic = requested_edges & DEFAULT_SEMANTIC_RELATIONS
    if not requested_edges:
        semantic = set(DEFAULT_SEMANTIC_RELATIONS)
    seeds = [handle for handle, _, _ in identity_hits]
    if semantic:
        reached, traversed, graph_truncated = _graph_lane(
            snapshot,
            seeds,
            direction=str(request["direction"]),
            edge_types=semantic,
            max_depth=int(request["max_depth"]),
            include_stale=include_stale,
            allowed=allowed,
        )
    else:
        reached, traversed, graph_truncated = {}, {}, False
    for handle, (seed, depth, path) in reached.items():
        _offer(
            candidates,
            handle,
            _lane(
                "graph", max(1.0, 100.0 - depth * 10.0),
                "trusted-seed" if depth == 0 else "trusted-edge",
                seed=seed, path=path,
            ),
        )
    if graph_truncated:
        _add_omission(result, kind="node", identifier="graph-expansion", reason="limit")

    # Taxonomy is a scope filter and a visible boost for candidates established
    # by identity, lexical, or graph lanes. It never manufactures search hits.
    for handle in list(candidates):
        member = frontier.get(handle)
        if member is None:
            continue
        scope, depth, path = member
        _offer(
            candidates,
            handle,
            _lane(
                "taxonomy",
                max(1.0, 200.0 - depth * 10.0),
                "scope-member",
                scope=scope,
                path=path,
            ),
        )
    ranked = sorted(
        candidates,
        key=lambda handle: (
            -sum(float(row["score"]) for row in candidates[handle].values()),
            handle,
        ),
    )
    limit = min(int(request["limit"]), MAX_RESULT_NODES)
    selected = ranked[:limit]
    selected_candidates = {handle: candidates[handle] for handle in selected}
    _rank_lanes(selected_candidates)
    for handle in selected:
        federated, node = _lookup(snapshot, handle)
        graph_depth = selected_candidates[handle].get("graph", {}).get("path")
        taxonomy_depth = selected_candidates[handle].get("taxonomy", {}).get("path")
        depth = len(graph_depth) if graph_depth is not None else len(taxonomy_depth) if taxonomy_depth is not None else None
        result["nodes"].append(
            _node_dto(
                federated,
                str(node["id"]),
                result=result,
                lanes=selected_candidates[handle],
                depth=depth,
            )
        )
    selected_set = set(selected)
    for key in sorted(traversed):
        if key[0] in selected_set and key[2] in selected_set:
            federated, edge, depth = traversed[key]
            if len(result["edges"]) >= MAX_RESULT_EDGES:
                _add_omission(result, kind="edge", identifier="graph-expansion", reason="limit")
                break
            result["edges"].append(_edge_dto(federated, edge, depth=depth))
    if len(ranked) > limit:
        _add_omission(result, kind="node", identifier="fused-candidates", reason="limit")
    return result


def _get(snapshot: FederationSnapshot, request: Mapping[str, Any]) -> dict[str, Any]:
    result = _empty_result()
    handle = str(request["handle"])
    try:
        federated, node = _lookup(snapshot, handle)
    except RecallError as error:
        if error.code == "incomplete-vault":
            _add_omission(result, kind="node", identifier=handle, reason="incomplete-vault")
            return result
        raise
    if not _node_allowed(node, include_stale=bool(request["include_stale"])):
        raise RecallError(
            "stale-node", "requested node is excluded by freshness policy",
            operation="get", vault_id=federated.vault.id, generation=snapshot.generation,
        )
    lane = _identity_lane("id", 1000.0)
    result["nodes"].append(
        _node_dto(federated, str(node["id"]), result=result, lanes={"identity": lane}, include_text=True)
    )
    unique: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    overflow = False
    edge_work = 0
    streams = (
        federated.view.incoming.get(str(node["id"]), ()),
        federated.view.outgoing.get(str(node["id"]), ()),
    )
    for edge in itertools.chain.from_iterable(streams):
        edge_work += 1
        if edge_work > MAX_GRAPH_EDGE_WORK:
            overflow = True
            break
        if not (
            edge.get("relation") == "contains"
            or bool(request["include_stale"])
            or edge.get("curation_status") == "current"
        ):
            continue
        key = (str(edge["source"]), str(edge["relation"]), str(edge["target"]))
        unique[key] = edge
        if len(unique) >= 2 * MAX_RESULT_EDGES:
            unique = {
                item: unique[item]
                for item in sorted(unique)[:MAX_RESULT_EDGES]
            }
            overflow = True
    keys = sorted(unique)[:MAX_RESULT_EDGES]
    for key in keys:
        result["edges"].append(_edge_dto(federated, unique[key]))
    if overflow or len(unique) > MAX_RESULT_EDGES:
        _add_omission(result, kind="edge", identifier=handle, reason="limit")
    return result


def _expand(snapshot: FederationSnapshot, request: Mapping[str, Any]) -> dict[str, Any]:
    result = _empty_result()
    relations = set(request["edge_types"])
    if not relations:
        relations = set(DEFAULT_SEMANTIC_RELATIONS)
    reached: dict[str, tuple[str, int, list[dict[str, str]]]] = {}
    traversed: dict[tuple[str, str, str], tuple[FederatedVault, dict[str, Any], int]] = {}
    include_stale = bool(request["include_stale"])
    work = 0
    edge_work = 0
    bounded = False
    for seed in request["handles"]:
        resolved = _lookup_multi(snapshot, str(seed), result)
        if resolved is None:
            continue
        federated, seed_node = resolved
        if not _node_allowed(seed_node, include_stale=include_stale):
            _add_omission(result, kind="node", identifier=str(seed), reason="stale")
            continue
        queue: deque[tuple[str, list[dict[str, str]]]] = deque([(str(seed_node["id"]), [])])
        visited: set[str] = set()
        while queue:
            if work >= MAX_GRAPH_WORK:
                bounded = True
                break
            node_id, path = queue.popleft()
            if node_id in visited:
                continue
            visited.add(node_id)
            work += 1
            handle = qualified_handle(federated.vault.id, node_id)
            node = federated.view.nodes[node_id]
            if not _node_allowed(node, include_stale=include_stale):
                continue
            if handle not in reached and len(reached) >= MAX_GRAPH_REACHED:
                bounded = True
                break
            reached.setdefault(handle, (str(seed), len(path), path))
            if len(path) >= int(request["max_depth"]):
                continue
            adjacent: list[Iterable[tuple[dict[str, Any], str]]] = []
            if request["direction"] in {"outgoing", "both"}:
                adjacent.append(
                    ((edge, str(edge["target"])) for edge in federated.view.outgoing.get(node_id, ()))
                )
            if request["direction"] in {"incoming", "both"}:
                adjacent.append(
                    ((edge, str(edge["source"])) for edge in federated.view.incoming.get(node_id, ()))
                )
            for edge, neighbor in itertools.chain.from_iterable(adjacent):
                edge_work += 1
                if edge_work > MAX_GRAPH_EDGE_WORK:
                    bounded = True
                    break
                relation = str(edge["relation"])
                if relation not in relations:
                    continue
                if relation != "contains" and not include_stale and edge.get("curation_status") != "current":
                    continue
                neighbor_node = federated.view.nodes[neighbor]
                if not _node_allowed(neighbor_node, include_stale=include_stale):
                    continue
                step = {
                    "source": qualified_handle(federated.vault.id, str(edge["source"])),
                    "relation": relation,
                    "target": qualified_handle(federated.vault.id, str(edge["target"])),
                }
                edge_key = (step["source"], relation, step["target"])
                if edge_key not in traversed:
                    if len(traversed) >= MAX_GRAPH_TRAVERSED:
                        bounded = True
                        break
                    traversed[edge_key] = (federated, dict(edge), len(path) + 1)
                if work + len(queue) >= MAX_GRAPH_WORK:
                    bounded = True
                    break
                queue.append((neighbor, [*path, step]))
            if bounded:
                break
        if bounded:
            break
    if bounded:
        _add_omission(result, kind="node", identifier="expanded-frontier", reason="limit")
    candidates: dict[str, dict[str, dict[str, Any]]] = {}
    for handle, (seed, depth, path) in reached.items():
        # Taxonomy-only paths remain graph expansion output, not search graph-lane evidence.
        reason = "trusted-seed" if depth == 0 else "trusted-edge"
        semantic_path = [step for step in path if step["relation"] != "contains"]
        seed_federated, seed_node = _lookup(snapshot, seed)
        taxonomy_descendant = (
            depth > 0
            and not semantic_path
            and request["direction"] == "outgoing"
            and seed_node.get("type") in {"field", "topic"}
            and bool(path)
            and path[0]["source"] == seed
            and path[-1]["target"] == handle
            and all(
                previous["target"] == following["source"]
                for previous, following in zip(path, path[1:])
            )
            and seed_federated.vault.id == handle.partition(":")[0]
        )
        if taxonomy_descendant:
            lane = _lane("taxonomy", max(1.0, 200.0 - depth * 10.0), "scope-member", scope=seed, path=path)
        else:
            lane = _lane("graph", max(1.0, 100.0 - depth * 10.0), reason, seed=seed, path=path)
        _offer(candidates, handle, lane)
    ranked = sorted(reached, key=lambda handle: (reached[handle][1], handle))
    limit = min(int(request["limit"]), MAX_RESULT_NODES)
    selected = ranked[:limit]
    selected_candidates = {handle: candidates[handle] for handle in selected}
    _rank_lanes(selected_candidates)
    for handle in selected:
        federated, node = _lookup(snapshot, handle)
        result["nodes"].append(
            _node_dto(
                federated,
                str(node["id"]),
                result=result,
                lanes=selected_candidates[handle],
                depth=reached[handle][1],
            )
        )
    selected_set = set(selected)
    for key in sorted(traversed):
        if key[0] in selected_set and key[2] in selected_set:
            federated, edge, depth = traversed[key]
            if len(result["edges"]) >= MAX_RESULT_EDGES:
                _add_omission(result, kind="edge", identifier="expanded-edges", reason="limit")
                break
            result["edges"].append(_edge_dto(federated, edge, depth=depth))
    if len(ranked) > limit:
        _add_omission(result, kind="node", identifier="expanded-nodes", reason="limit")
    return result


def _effective_derivations(
    ledger: Any,
) -> Iterable[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    versions = {str(row["version_id"]): row for row in ledger.versions}
    effective = {
        str(row["version_id"]): row
        for row in ledger.derivations
        if row["status"] in {"committed", "reviewed-empty", "carried-forward"}
    }
    for document in ledger.documents:
        current = str(document["current_version_id"])
        seen: set[str] = set()
        for _ in range(len(versions) + 1):
            if current in seen:
                raise RecallError("invalid-source-ledger", "source derivation inheritance is invalid")
            seen.add(current)
            row = effective.get(current)
            if row is None:
                break
            if row["status"] in {"committed", "reviewed-empty"}:
                yield document, versions[str(row["version_id"])], row
                break
            current = str(row["inherited_from_version_id"])


def _iter_evidence_for_handles(
    selected: Sequence[str],
    snapshot: FederationSnapshot,
    *,
    edge_types: set[str],
) -> Iterable[tuple[dict[str, Any] | None, str, str]]:
    selected_set = set(selected)
    groups: dict[
        tuple[str, str],
        list[tuple[FederatedVault, dict[str, Any], dict[str, Any], dict[str, Any]]],
    ] = {}
    source_bytes = 0
    version_count = 0
    bounded = False
    for federated in snapshot.vaults:
        selected_ids = {
            handle.split(":", 1)[1]
            for handle in selected_set
            if handle.startswith(f"{federated.vault.id}:")
        }
        if not selected_ids:
            continue
        for document, version, derivation in _effective_derivations(federated.ledger):
            if derivation["status"] != "committed":
                continue
            relevant_concept = any(
                str(item["concept_id"]) in selected_ids
                for item in derivation["concept_evidence"]
            )
            relevant_relation = any(
                qualified_handle(federated.vault.id, str(item["source"])) in selected_set
                and qualified_handle(federated.vault.id, str(item["target"])) in selected_set
                and str(item["relation"]) in edge_types
                for item in derivation["relation_evidence"]
            )
            if not relevant_concept and not relevant_relation:
                continue
            if version_count >= MAX_CONTEXT_VERSIONS:
                bounded = True
                break
            raw_sha256 = str(version["raw_sha256"])
            key = (federated.vault.id, raw_sha256)
            if key not in groups:
                byte_count = int(version["byte_count"])
                if source_bytes + byte_count > MAX_CONTEXT_SOURCE_BYTES:
                    bounded = True
                    break
                source_bytes += byte_count
                groups[key] = []
            groups[key].append((federated, document, version, derivation))
            version_count += 1
        if bounded:
            break

    for key in sorted(groups):
        rows = groups[key]
        first_federated, _, first_version, _ = rows[0]
        expected_metadata = (
            int(first_version["byte_count"]),
            str(first_version["normalized_text_sha256"]),
            str(first_version["blob_path"]),
        )
        if any(
            (
                int(version["byte_count"]),
                str(version["normalized_text_sha256"]),
                str(version["blob_path"]),
            )
            != expected_metadata
            for _, _, version, _ in rows
        ):
            raise RecallError(
                "invalid-source-ledger",
                "shared source blob metadata is inconsistent",
            )
        try:
            text = verified_version_text(first_federated.ledger, first_version)
        except SourceArchiveError:
            yield None, first_federated.vault.id, "incomplete-vault"
            continue
        for federated, document, version, derivation in rows:
            version_id = str(version["version_id"])
            for concept in derivation["concept_evidence"]:
                concept_id = str(concept["concept_id"])
                handle = qualified_handle(federated.vault.id, concept_id)
                if handle not in selected_set:
                    continue
                for span in concept["spans"]:
                    try:
                        excerpt = verify_evidence_span(
                            text, span, expected_version_id=version_id
                        )
                    except SourceArchiveError:
                        yield None, federated.vault.id, "incomplete-vault"
                        continue
                    if len(excerpt) > MAX_EVIDENCE_EXCERPT_CHARACTERS:
                        yield None, handle, "limit"
                        continue
                    yield {
                            "kind": "concept",
                            "handle": handle,
                            "source": None,
                            "relation": None,
                            "target": None,
                            "document_id": str(document["document_id"]),
                            "version_id": version_id,
                            "source_path": str(version["captured_path"]),
                            "format": str(version["format"]),
                            "start_line": int(span["start_line"]),
                            "end_line": int(span["end_line"]),
                            "start_column": int(span["start_column"]) if "start_column" in span else None,
                            "end_column": int(span["end_column"]) if "end_column" in span else None,
                            "excerpt": excerpt,
                            "excerpt_sha256": str(span["excerpt_sha256"]),
                        }, handle, "ok"
            for relation_row in derivation["relation_evidence"]:
                if str(relation_row["relation"]) not in edge_types:
                    continue
                source = qualified_handle(federated.vault.id, str(relation_row["source"]))
                target = qualified_handle(federated.vault.id, str(relation_row["target"]))
                if source not in selected_set or target not in selected_set:
                    continue
                for span in relation_row["spans"]:
                    try:
                        excerpt = verify_evidence_span(
                            text, span, expected_version_id=version_id
                        )
                    except SourceArchiveError:
                        yield None, federated.vault.id, "incomplete-vault"
                        continue
                    identifier = f"{source}:{relation_row['relation']}:{target}"
                    if len(excerpt) > MAX_EVIDENCE_EXCERPT_CHARACTERS:
                        yield None, identifier, "limit"
                        continue
                    yield {
                            "kind": "relation",
                            "handle": source,
                            "source": source,
                            "relation": str(relation_row["relation"]),
                            "target": target,
                            "document_id": str(document["document_id"]),
                            "version_id": version_id,
                            "source_path": str(version["captured_path"]),
                            "format": str(version["format"]),
                            "start_line": int(span["start_line"]),
                            "end_line": int(span["end_line"]),
                            "start_column": int(span["start_column"]) if "start_column" in span else None,
                            "end_column": int(span["end_column"]) if "end_column" in span else None,
                            "excerpt": excerpt,
                            "excerpt_sha256": str(span["excerpt_sha256"]),
                        }, identifier, "ok"
        text = ""
    if bounded:
        yield None, "source-evidence", "limit"


class _ContextBudget:
    """Incrementally pack canonical array members under one byte budget."""

    def __init__(self, result: dict[str, Any], budget: int) -> None:
        self.result = result
        self.budget = budget
        # Using the budget itself is conservative: the final fixed-point byte
        # count cannot have more decimal digits than this value.
        result["estimated_tokens"] = budget
        self.used = len(canonical_json(result).encode("utf-8"))
        result["estimated_tokens"] = 0
        if self.used > budget:
            raise RecallError("token-budget-too-small", "context token budget is too small")

    def add(self, field: str, row: dict[str, Any]) -> bool:
        values = self.result[field]
        delta = len(canonical_json(row).encode("utf-8")) + (1 if values else 0)
        if self.used + delta > self.budget:
            return False
        values.append(row)
        self.used += delta
        return True

    def omit(self, *, kind: str, identifier: str, reason: str) -> None:
        record = {"kind": kind, "id": identifier[:4096], "reason": reason}
        self.result["truncated"] = True
        if record in self.result["omissions"]:
            return
        if len(self.result["omissions"]) >= MAX_RESULT_OMISSIONS:
            return
        self.add("omissions", record)

    def finish(self) -> None:
        if _finalize_estimate(self.result) > self.budget:
            raise RecallError("token-budget-too-small", "context token budget is too small")


def _iter_context_edges(
    snapshot: FederationSnapshot,
    included: Sequence[str],
    *,
    edge_types: set[str],
    include_stale: bool,
) -> Iterable[tuple[dict[str, Any] | None, str]]:
    included_set = set(included)
    seen: set[tuple[str, str, str]] = set()
    work = 0
    for handle in sorted(included_set):
        federated, node = _lookup(snapshot, handle)
        for edge in federated.view.outgoing.get(str(node["id"]), ()):
            work += 1
            if work > MAX_GRAPH_EDGE_WORK:
                yield None, "context-edges"
                return
            source = qualified_handle(federated.vault.id, str(edge["source"]))
            target = qualified_handle(federated.vault.id, str(edge["target"]))
            key = (source, str(edge["relation"]), target)
            if key in seen or target not in included_set:
                continue
            if str(edge.get("relation", "")) not in edge_types:
                continue
            if (
                edge.get("relation") != "contains"
                and not include_stale
                and edge.get("curation_status") != "current"
            ):
                continue
            seen.add(key)
            yield _edge_dto(federated, edge), f"{source}:{edge['relation']}:{target}"


def _context(snapshot: FederationSnapshot, request: Mapping[str, Any]) -> dict[str, Any]:
    explicit_handle_overflow = False
    if request["handles"]:
        selected = list(request["handles"])
        explicit_handle_overflow = len(selected) > int(request["limit"])
        search_result = _empty_result(str(request["query"]) if request["query"] else None)
        lane_by_handle: dict[str, dict[str, dict[str, Any]]] = {}
        for rank, handle in enumerate(selected, start=1):
            lane = _identity_lane("id", 1000.0)
            lane["rank"] = rank
            lane_by_handle[str(handle)] = {"identity": lane}
    else:
        search_request = dict(request)
        search_request["operation"] = "search"
        search_request["handles"] = []
        search_result = _search(snapshot, search_request)
        selected = [node["handle"] for node in search_result["nodes"]]
        lane_by_handle = {
            node["handle"]: {row["lane"]: row for row in node["lane_evidence"]}
            for node in search_result["nodes"]
        }
    result = _empty_result(str(request["query"]) if request["query"] else None)
    result["truncated"] = bool(search_result["truncated"])
    result["omissions"] = copy.deepcopy(search_result["omissions"])
    budget = int(request["token_budget"])
    packer = _ContextBudget(result, budget)
    if explicit_handle_overflow:
        packer.omit(kind="node", identifier="context-handles", reason="limit")
    for handle in selected[: int(request["limit"])]:
        lookup_result = _empty_result()
        resolved = _lookup_multi(snapshot, str(handle), lookup_result)
        if resolved is None:
            for omission in lookup_result["omissions"]:
                packer.omit(
                    kind=str(omission["kind"]),
                    identifier=str(omission["id"]),
                    reason=str(omission["reason"]),
                )
            continue
        federated, node = resolved
        if not _node_allowed(node, include_stale=bool(request["include_stale"])):
            packer.omit(kind="node", identifier=str(handle), reason="stale")
            continue
        scratch = _empty_result()
        row = _node_dto(
            federated,
            str(node["id"]),
            result=scratch,
            lanes=lane_by_handle.get(str(handle)),
            include_text=True,
        )
        if packer.add("nodes", row):
            for omission in scratch["omissions"]:
                packer.omit(
                    kind=str(omission["kind"]),
                    identifier=str(omission["id"]),
                    reason=str(omission["reason"]),
                )
        else:
            packer.omit(kind="node", identifier=str(handle), reason="token-budget")
    included = [node["handle"] for node in result["nodes"]]
    requested_edges = set(str(item) for item in request["edge_types"])
    context_edges = requested_edges or set(DEFAULT_SEMANTIC_RELATIONS)
    edge_count = 0
    for row, identifier in _iter_context_edges(
        snapshot,
        included,
        edge_types=context_edges,
        include_stale=bool(request["include_stale"]),
    ):
        if row is None:
            packer.omit(kind="edge", identifier=identifier, reason="limit")
            break
        if edge_count >= MAX_RESULT_EDGES:
            packer.omit(kind="edge", identifier="context-edges", reason="limit")
            break
        if not packer.add("edges", row):
            packer.omit(kind="edge", identifier=identifier, reason="token-budget")
            break
        edge_count += 1
    evidence_count = 0
    evidence_work = 0
    for row, identifier, reason in _iter_evidence_for_handles(
        included, snapshot, edge_types=context_edges
    ):
        evidence_work += 1
        if evidence_work > MAX_RESULT_EVIDENCE:
            packer.omit(kind="evidence", identifier="source-evidence", reason="limit")
            break
        if row is None:
            packer.omit(
                kind="vault" if reason == "incomplete-vault" else "evidence",
                identifier=identifier,
                reason=reason,
            )
            continue
        if not packer.add("evidence", row):
            packer.omit(kind="evidence", identifier=identifier, reason="token-budget")
            break
        evidence_count += 1
    packer.finish()
    return result


def _operation_result(
    snapshot: FederationSnapshot, request: Mapping[str, Any]
) -> dict[str, Any]:
    operation = str(request["operation"])
    if operation == "status":
        return _empty_result()
    if operation == "roots":
        return _roots(snapshot, request)
    if operation == "children":
        return _children(snapshot, request)
    if operation == "resolve":
        return _resolve(snapshot, request)
    if operation == "search":
        return _search(snapshot, request)
    if operation == "get":
        return _get(snapshot, request)
    if operation == "expand":
        return _expand(snapshot, request)
    return _context(snapshot, request)


def execute_recall_request(
    request: Mapping[str, Any],
    *,
    home: Path | str | None = None,
    snapshot: FederationSnapshot | None = None,
) -> dict[str, Any]:
    """Execute one closed request against one coherent federated snapshot."""

    try:
        encoded = canonical_json(dict(request)).encode("utf-8")
    except (ContractError, TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise RecallError("invalid-recall-request", "recall request is not bounded canonical JSON") from error
    if len(encoded) > MAX_RECALL_REQUEST_BYTES:
        raise RecallError("recall-request-too-large", "recall request exceeds its byte limit")
    try:
        validated = validate_contract(dict(request))
    except (ContractError, RecursionError) as error:
        raise RecallError("invalid-recall-request", "recall request violates the closed v1 contract") from error
    if validated.get("schema") != REQUEST_SCHEMA:
        raise RecallError("invalid-recall-request", "expected qlkg-recall-request-v1")
    operation = str(validated["operation"])
    if operation in {"search", "context"} and validated.get("query") is not None:
        try:
            query_terms(str(validated["query"]))
        except FederationError as error:
            raise RecallError(error.code, error.message, operation=operation) from error
    if operation == "context":
        _ContextBudget(
            _empty_result(
                str(validated["query"]) if validated.get("query") is not None else None
            ),
            int(validated["token_budget"]),
        )
    try:
        captured = project_federation(
            snapshot
            or capture_federation(home=home, vault_ids=validated["vault_ids"]),
            validated["vault_ids"],
        )
        result = _operation_result(captured, validated)
        _rerank_result_lanes(result)
        _finalize_estimate(result)
        if operation == "context" and result["estimated_tokens"] > int(
            validated["token_budget"]
        ):
            raise RecallError(
                "token-budget-too-small",
                "context token budget is too small",
                operation=operation,
                generation=captured.generation,
            )
        report = {
            "schema": REPORT_SCHEMA,
            "operation": operation,
            "status": "partial" if captured.incomplete_vaults else "complete",
            "generation": captured.generation,
            "registry_generation": captured.registry_generation,
            "vaults": [copy.deepcopy(item.card) for item in captured.vaults],
            "incomplete_vaults": copy.deepcopy(list(captured.incomplete_vaults)),
            "result": result,
        }
        checked = validate_contract(report)
        if len(canonical_json(checked).encode("utf-8")) > MAX_RECALL_REPORT_BYTES:
            raise RecallError(
                "recall-report-too-large",
                "recall report exceeds its byte limit",
                operation=operation,
                generation=captured.generation,
            )
        return checked
    except RecallError:
        raise
    except FederationError as error:
        raise RecallError(error.code, error.message, operation=operation) from error
    except (SourceArchiveError, OSError, UnicodeError, ValueError, RecursionError) as error:
        raise RecallError(
            "recall-operation-failed",
            "recall could not produce a coherent bounded result",
            operation=operation,
        ) from error


def recall_status(*, home: Path | str | None = None, vault_ids: Sequence[str] = ()) -> dict[str, Any]:
    return execute_recall_request(make_recall_request("status", vault_ids=vault_ids), home=home)


def recall_roots(*, home: Path | str | None = None, vault_ids: Sequence[str] = (), limit: int = 500, include_stale: bool = False) -> dict[str, Any]:
    return execute_recall_request(make_recall_request("roots", vault_ids=vault_ids, limit=limit, include_stale=include_stale), home=home)


def recall_children(handle: str, *, home: Path | str | None = None, limit: int = 500, include_stale: bool = False) -> dict[str, Any]:
    return execute_recall_request(make_recall_request("children", handle=handle, limit=limit, include_stale=include_stale), home=home)


def recall_resolve(queries: Sequence[str], *, home: Path | str | None = None, vault_ids: Sequence[str] = (), limit: int = 20, include_stale: bool = False) -> dict[str, Any]:
    return execute_recall_request(make_recall_request("resolve", vault_ids=vault_ids, queries=queries, limit=limit, include_stale=include_stale), home=home)


def recall_search(query: str, *, home: Path | str | None = None, vault_ids: Sequence[str] = (), scopes: Sequence[str] = (), direction: str = "both", edge_types: Sequence[str] = (), max_depth: int = 1, limit: int = 20, include_stale: bool = False) -> dict[str, Any]:
    return execute_recall_request(make_recall_request("search", vault_ids=vault_ids, query=query, scopes=scopes, direction=direction, edge_types=edge_types, max_depth=max_depth, limit=limit, include_stale=include_stale), home=home)


def recall_get(handle: str, *, home: Path | str | None = None, include_stale: bool = False) -> dict[str, Any]:
    return execute_recall_request(make_recall_request("get", handle=handle, include_stale=include_stale), home=home)


def recall_expand(handles: Sequence[str], *, home: Path | str | None = None, direction: str = "both", edge_types: Sequence[str] = (), max_depth: int = 1, limit: int = 50, include_stale: bool = False) -> dict[str, Any]:
    return execute_recall_request(make_recall_request("expand", handles=handles, direction=direction, edge_types=edge_types, max_depth=max_depth, limit=limit, include_stale=include_stale), home=home)


def recall_context(query: str | None = None, *, handles: Sequence[str] = (), home: Path | str | None = None, vault_ids: Sequence[str] = (), scopes: Sequence[str] = (), direction: str = "both", edge_types: Sequence[str] = (), max_depth: int = 1, limit: int = 20, token_budget: int = 6000, include_stale: bool = False) -> dict[str, Any]:
    return execute_recall_request(make_recall_request("context", vault_ids=vault_ids, query=query, handles=handles, scopes=scopes, direction=direction, edge_types=edge_types, max_depth=max_depth, limit=limit, token_budget=token_budget, include_stale=include_stale), home=home)


__all__ = [
    "ERROR_SCHEMA", "REPORT_SCHEMA", "REQUEST_SCHEMA", "RecallError",
    "execute_recall_request", "make_recall_request", "recall_children",
    "recall_context", "recall_expand", "recall_get", "recall_resolve",
    "recall_roots", "recall_search", "recall_status",
]
