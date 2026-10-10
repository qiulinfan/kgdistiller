"""Deterministic, read-only queries over the reviewed entry store.

A :class:`GraphView` is one complete in-memory view of the entries under
``.knowledge/entries/`` and the accepted edges in ``.knowledge/edges.jsonl``.
Callers may retain a view for a request, but should load a fresh view for each
independent CLI or MCP operation. Staleness of an entry's Evidence never hides
it from a query; ``kgd check`` reports it instead.
"""

from __future__ import annotations

import copy
import math
from collections import Counter, defaultdict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import canonical_json
from .entries import (
    LIST_SECTIONS,
    EntryError,
    entry_relative,
    identity_key,
    validate_id,
)
from .home import Base, KnowledgeError
from .knowledge_store import (
    SEMANTIC_RELATIONS,
    KnowledgeState,
    load_state,
)
from .tokens import tokenize

QUERY_STATUS_SCHEMA = "kgdistiller-query-status-v1"
CONTEXT_SCHEMA = "kgdistiller-context-bundle-v1"
MAX_LIMIT = 500
MAX_BATCH_CONCEPTS = 512
MAX_GRAPH_SEEDS = 128
MAX_GRAPH_DEPTH = 8
MAX_QUERY_LENGTH = 4096
MAX_QUERY_TERMS = 128
_BM25_K1 = 1.2
_BM25_B = 0.75
_SEARCH_TEXT_FIELDS = ("kind", "summary", "context", "role", *LIST_SECTIONS, "evidence")


class QueryError(ValueError):
    """Stable failure raised before a partial or ambiguous view is exposed."""


def normalize_text(value: str) -> str:
    """Apply the only cross-language normalization used by query operations."""
    return identity_key(value)


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        if value.strip():
            yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


@dataclass(frozen=True)
class GraphView:
    """One fully loaded entry store: nodes are entry records, edges are accepted relations."""

    repo_root: Path
    nodes: dict[str, dict[str, Any]]
    edges: tuple[dict[str, Any], ...]
    outgoing: dict[str, tuple[dict[str, Any], ...]]
    incoming: dict[str, tuple[dict[str, Any], ...]]
    labels: dict[str, tuple[str, ...]]
    aliases: dict[str, tuple[str, ...]]

    @classmethod
    def load(cls, base: Base) -> GraphView:
        """Read the store, refusing while an ingest install is in progress or interrupted."""
        from .ingest import IngestPaths, journal_path

        if journal_path(IngestPaths(base)).exists():
            raise QueryError(
                "an ingest install is in progress or was interrupted; "
                f"rerun or recover it with `kgd ingest apply REQUEST --base {base.name}` before querying"
            )
        check_hint = f"run `kgd check --base {base.name}` to list every problem"
        try:
            state = load_state(base.root)
        except (KnowledgeError, OSError, UnicodeError) as error:
            raise QueryError(f"{error}; {check_hint}") from error
        try:
            return cls.from_state(base.root, state)
        except QueryError as error:
            raise QueryError(f"{error}; {check_hint}") from error

    @classmethod
    def from_state(cls, repo_root: Path, state: KnowledgeState) -> GraphView:
        nodes = {
            entry_id: {**copy.deepcopy(record), "entry": entry_relative(entry_id).as_posix()}
            for entry_id, record in sorted(state.entries.items())
        }
        edges = tuple(copy.deepcopy(edge) for _, edge in sorted(state.edges.items()))
        outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
        incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in edges:
            for endpoint in (edge["source"], edge["target"]):
                if endpoint not in nodes:
                    raise QueryError(
                        f"edge {edge['source']} {edge['relation']} {edge['target']} "
                        f"has no entry for {endpoint}"
                    )
            outgoing[edge["source"]].append(edge)
            incoming[edge["target"]].append(edge)
        labels: dict[str, list[str]] = defaultdict(list)
        aliases: dict[str, list[str]] = defaultdict(list)
        for node_id, node in nodes.items():
            labels[normalize_text(node["label"])].append(node_id)
            for alias in node["aliases"]:
                aliases[normalize_text(alias)].append(node_id)
        return cls(
            repo_root=Path(repo_root),
            nodes=nodes,
            edges=edges,
            outgoing={key: tuple(value) for key, value in outgoing.items()},
            incoming={key: tuple(value) for key, value in incoming.items()},
            labels={key: tuple(sorted(set(value))) for key, value in labels.items()},
            aliases={key: tuple(sorted(set(value))) for key, value in aliases.items()},
        )


def load_graph_view(base: Base) -> GraphView:
    return GraphView.load(base)


def _node_id(value: Any, label: str) -> str:
    try:
        return validate_id(value)
    except EntryError as error:
        raise QueryError(f"{label} must be an entry id: {error}") from error


def _limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_LIMIT:
        raise QueryError(f"limit must be between 1 and {MAX_LIMIT}")
    return value


def _edge_policy(value: str) -> str:
    if not isinstance(value, str) or value not in {"all", "high-confidence"}:
        raise QueryError("edge_policy must be all or high-confidence")
    return value


def _finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _edge_allowed(edge: Mapping[str, Any], *, edge_policy: str = "all") -> bool:
    if edge_policy == "all":
        return True
    # Confidence is authored metadata, not proof of independent review.
    evidence = edge.get("evidence")
    return edge.get("confidence") == "high" and isinstance(evidence, str) and bool(evidence.strip())


def query_status(view: GraphView) -> dict[str, Any]:
    relations = Counter(edge["relation"] for edge in view.edges)
    return {
        "schema": QUERY_STATUS_SCHEMA,
        "counts": {"entries": len(view.nodes), "edges": len(view.edges)},
        "relations": dict(sorted(relations.items())),
    }


def resolve_concepts(
    view: GraphView,
    concepts: list[str],
    *,
    match_limit: int = MAX_LIMIT,
) -> list[dict[str, Any]]:
    match_limit = _limit(match_limit)
    if not isinstance(concepts, list) or len(concepts) > MAX_BATCH_CONCEPTS:
        raise QueryError(f"concept batch exceeds {MAX_BATCH_CONCEPTS}")
    if any(
        not isinstance(value, str)
        or not value.strip()
        or len(value) > MAX_QUERY_LENGTH
        for value in concepts
    ):
        raise QueryError(
            f"each concept must be a non-empty string of at most {MAX_QUERY_LENGTH} characters"
        )
    results: list[dict[str, Any]] = []
    for value in concepts:
        raw = value.strip()
        normalized = normalize_text(raw)
        kind: str | None = None
        candidates: list[str] = []
        if raw in view.nodes:
            kind, candidates = "id", [raw]
        elif normalized in view.labels:
            kind, candidates = "label", list(view.labels[normalized])
        elif normalized in view.aliases:
            kind, candidates = "alias", list(view.aliases[normalized])
        total = len(candidates)
        matches = [copy.deepcopy(view.nodes[node_id]) for node_id in candidates[:match_limit]]
        if total == 1:
            status = "alias" if kind == "alias" else "exact"
        elif total > 1:
            status = "ambiguous"
        else:
            status = "missing"
        results.append({
            "query": raw,
            "status": status,
            "match_kind": kind,
            "matches": matches,
            "candidate_ids": [node["id"] for node in matches],
            "overflow": total > match_limit,
            "identity_authority": total > 0,
        })
    return results


def _distinct_search_text(values: Iterable[str]) -> str:
    """Avoid indexing identical text twice."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = normalize_text(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(value)
    return "\n".join(result)


def _node_search_fields(node: Mapping[str, Any]) -> tuple[str, str, str]:
    """Project an entry's label, aliases and body text for lexical and model ranking."""
    aliases = _distinct_search_text(_strings(node.get("aliases", [])))
    body = _distinct_search_text(
        text for field in _SEARCH_TEXT_FIELDS for text in _strings(node.get(field))
    )
    return str(node.get("label", "")), aliases, body


def search(view: GraphView, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    limit = _limit(limit)
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_LENGTH:
        raise QueryError(f"query must contain 1 to {MAX_QUERY_LENGTH} characters")
    terms = set(tokenize(query)[:MAX_QUERY_TERMS])
    if not terms:
        return []
    ranked: list[tuple[float, str, list[dict[str, Any]]]] = []
    documents: dict[str, Counter[str]] = {}
    for node_id, node in view.nodes.items():
        label, aliases, body = _node_search_fields(node)
        # Fixed field repetition gives names and aliases more weight while
        # retaining standard BM25 saturation and length normalization.
        documents[node_id] = Counter(
            tokenize(label) * 3 + tokenize(aliases) * 2 + tokenize(body)
        )
    if not documents:
        return []
    count = len(documents)
    lengths = {node_id: sum(words.values()) for node_id, words in documents.items()}
    average_length = sum(lengths.values()) / count or 1.0
    frequencies = Counter(term for words in documents.values() for term in words)
    for node_id, words in documents.items():
        normalization = _BM25_K1 * (
            1 - _BM25_B + _BM25_B * lengths[node_id] / average_length
        )
        score = 0.0
        for term in sorted(terms):
            frequency = words.get(term, 0)
            if not frequency:
                continue
            inverse_frequency = math.log1p(
                (count - frequencies[term] + 0.5) / (frequencies[term] + 0.5)
            )
            score += inverse_frequency * frequency * (_BM25_K1 + 1) / (
                frequency + normalization
            )
        if score <= 0:
            continue
        reasons = [{"method": "lexical", "score": score, "identity_authority": False}]
        ranked.append((score, node_id, reasons))
    ranked.sort(key=lambda item: (-item[0], normalize_text(str(view.nodes[item[1]].get("label", ""))), item[1]))
    return [
        {
            "rank": rank,
            "node": copy.deepcopy(view.nodes[node_id]),
            "reasons": reasons,
        }
        for rank, (_, node_id, reasons) in enumerate(ranked[:limit], start=1)
    ]


def get(view: GraphView, node_id: str) -> dict[str, Any]:
    node_id = _node_id(node_id, "concept ID")
    if node_id not in view.nodes:
        raise QueryError(f"unknown concept: {node_id}")
    return {
        "node": copy.deepcopy(view.nodes[node_id]),
        "incoming": copy.deepcopy(list(view.incoming.get(node_id, ()))),
        "outgoing": copy.deepcopy(list(view.outgoing.get(node_id, ()))),
    }


def _direction(value: str) -> str:
    values = {"out": "outgoing", "in": "incoming", "outgoing": "outgoing", "incoming": "incoming", "both": "both"}
    if value not in values:
        raise QueryError("invalid graph direction")
    return values[value]


def expand(
    view: GraphView,
    seed_ids: list[str],
    *,
    direction: str = "both",
    edge_types: list[str] | None = None,
    max_depth: int = 1,
    limit: int = 50,
    edge_policy: str = "all",
) -> dict[str, Any]:
    normalized_direction = _direction(direction)
    edge_policy = _edge_policy(edge_policy)
    limit = _limit(limit)
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or not 0 <= max_depth <= MAX_GRAPH_DEPTH:
        raise QueryError(f"max_depth must be between 0 and {MAX_GRAPH_DEPTH}")
    if not isinstance(seed_ids, list) or not 1 <= len(seed_ids) <= MAX_GRAPH_SEEDS:
        raise QueryError(f"graph seed batch must contain 1 to {MAX_GRAPH_SEEDS} IDs")
    for seed in seed_ids:
        _node_id(seed, "graph seed ID")
    seeds = list(dict.fromkeys(seed_ids))
    if any(seed not in view.nodes for seed in seeds):
        unknown = next(seed for seed in seeds if seed not in view.nodes)
        raise QueryError(f"unknown graph seed: {unknown}")
    if len(seeds) > limit:
        raise QueryError("graph seed batch exceeds the result limit")
    relations = set(edge_types) if edge_types is not None else set(SEMANTIC_RELATIONS)
    visited: dict[str, tuple[int, list[dict[str, Any]], str]] = {}
    queue: deque[tuple[str, int, list[dict[str, Any]], str]] = deque()
    for seed in seeds:
        visited[seed] = (0, [], seed)
        queue.append((seed, 0, [], seed))
    traversed: dict[tuple[str, str, str], dict[str, Any]] = {}
    while queue and len(visited) < limit:
        current, depth, path, root = queue.popleft()
        if depth >= max_depth:
            continue
        candidates: list[tuple[dict[str, Any], str, str]] = []
        if normalized_direction in {"outgoing", "both"}:
            candidates.extend((edge, str(edge["target"]), "outgoing") for edge in view.outgoing.get(current, ()))
        if normalized_direction in {"incoming", "both"}:
            candidates.extend((edge, str(edge["source"]), "incoming") for edge in view.incoming.get(current, ()))
        # Contrasts are symmetric for traversal while each path step still
        # records the authored edge orientation.
        if normalized_direction == "outgoing":
            candidates.extend((edge, str(edge["source"]), "incoming") for edge in view.incoming.get(current, ()) if edge.get("relation") == "contrasts-with")
        elif normalized_direction == "incoming":
            candidates.extend((edge, str(edge["target"]), "outgoing") for edge in view.outgoing.get(current, ()) if edge.get("relation") == "contrasts-with")
        candidates.sort(key=lambda item: (str(item[0]["relation"]), item[1], item[2]))
        for edge, neighbor, edge_direction in candidates:
            if edge.get("relation") not in relations or not _edge_allowed(edge, edge_policy=edge_policy):
                continue
            key = (str(edge["source"]), str(edge["relation"]), str(edge["target"]))
            traversed[key] = edge
            if neighbor in visited:
                continue
            step = {"source": key[0], "relation": key[1], "target": key[2], "direction": edge_direction}
            next_path = [*path, step]
            visited[neighbor] = (depth + 1, next_path, root)
            queue.append((neighbor, depth + 1, next_path, root))
            if len(visited) >= limit:
                break
    rows = [
        {
            "node": copy.deepcopy(view.nodes[node_id]),
            "depth": depth,
            "path": copy.deepcopy(path),
            "seed": node_id in seeds,
            "seed_id": root,
        }
        for node_id, (depth, path, root) in visited.items()
    ]
    rows.sort(key=lambda item: (item["depth"], normalize_text(str(item["node"].get("label", ""))), item["node"]["id"]))
    return {
        "seeds": seeds,
        "policy": {
            "direction": direction,
            "edge_types": sorted(relations),
            "max_depth": max_depth,
            "limit": limit,
            "edge_policy": edge_policy,
            "confidence_gate": "declared-high-with-evidence" if edge_policy == "high-confidence" else "none",
        },
        "nodes": rows,
        "edges": [copy.deepcopy(traversed[key]) for key in sorted(traversed)],
        "allowed_edge_count": len(traversed),
    }


def personalized_pagerank(
    view: GraphView,
    seeds: Mapping[str, float],
    *,
    edge_types: list[str] | None = None,
    direction: str = "out",
    edge_policy: str = "all",
    max_depth: int | None = None,
    damping: float = 0.85,
    max_iterations: int = 256,
    tolerance: float = 1e-10,
    limit: int = 50,
) -> dict[str, Any]:
    """Rank the policy-valid seed-reachable induced graph.

    Approximate scores remain available for diagnostics when convergence fails.
    The residual is measured on the returned full vector (before result limits
    and rounding). For the stochastic transition with restart, its L1 distance
    from the stationary vector is bounded by residual / (1 - damping).
    """
    normalized_direction = _direction(direction)
    edge_policy = _edge_policy(edge_policy)
    limit = _limit(limit)
    if not isinstance(seeds, Mapping) or not 1 <= len(seeds) <= MAX_GRAPH_SEEDS:
        raise QueryError(f"PPR seed batch must contain 1 to {MAX_GRAPH_SEEDS} IDs")
    for node_id in seeds:
        _node_id(node_id, "PPR seed ID")
    if (
        not _finite_number(damping)
        or not 0 < damping < 1
        or isinstance(max_iterations, bool)
        or not isinstance(max_iterations, int)
        or not 1 <= max_iterations <= 1000
        or not _finite_number(tolerance)
        or tolerance <= 0
    ):
        raise QueryError("invalid PPR convergence policy")
    if max_depth is not None and (
        isinstance(max_depth, bool)
        or not isinstance(max_depth, int)
        or not 0 <= max_depth <= MAX_GRAPH_DEPTH
    ):
        raise QueryError(f"max_depth must be between 0 and {MAX_GRAPH_DEPTH} or None")
    relations = set(edge_types) if edge_types is not None else set(SEMANTIC_RELATIONS)
    valid_nodes = view.nodes
    positive: dict[str, float] = {}
    for node_id, raw_weight in seeds.items():
        if isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float)):
            raise QueryError("PPR seed weights must be finite non-boolean numbers")
        try:
            weight = float(raw_weight)
        except (ValueError, OverflowError) as error:
            raise QueryError("PPR seed weights must be finite non-boolean numbers") from error
        if not math.isfinite(weight):
            raise QueryError("PPR seed weights must be finite non-boolean numbers")
        if node_id in valid_nodes and weight > 0:
            positive[node_id] = weight
    if not positive:
        raise QueryError("PPR requires at least one positive graph seed")
    # Scaling first avoids overflow when several finite weights approach
    # float's maximum; normalized seed probability always has finite mass.
    weight_scale = max(positive.values())
    scaled = {node_id: weight / weight_scale for node_id, weight in positive.items()}
    scaled_total = math.fsum(scaled.values())
    valid_adjacency: dict[str, dict[str, float]] = {node_id: {} for node_id in valid_nodes}
    weights = {"prerequisite-for": 1.0, "implies": 1.0, "generalizes": 0.9, "derived-from": 0.9, "contrasts-with": 0.7}
    valid_edges: list[dict[str, Any]] = []
    for edge in view.edges:
        source_id, target_id, relation = str(edge["source"]), str(edge["target"]), str(edge["relation"])
        if (
            source_id not in valid_nodes
            or target_id not in valid_nodes
            or relation not in relations
            or not _edge_allowed(edge, edge_policy=edge_policy)
        ):
            continue
        pairs: set[tuple[str, str]] = set()
        if normalized_direction in {"outgoing", "both"}:
            pairs.add((source_id, target_id))
        if normalized_direction in {"incoming", "both"}:
            pairs.add((target_id, source_id))
        if relation == "contrasts-with":
            pairs.update({(source_id, target_id), (target_id, source_id)})
        for left, right in pairs:
            valid_adjacency[left][right] = valid_adjacency[left].get(right, 0.0) + weights.get(relation, 1.0)
        valid_edges.append(edge)
    reachable_seed: dict[str, str] = {}
    depths: dict[str, int] = {}
    reachability: deque[str] = deque()
    for seed_id in sorted(positive):
        reachable_seed[seed_id] = seed_id
        depths[seed_id] = 0
        reachability.append(seed_id)
    while reachability:
        current = reachability.popleft()
        if max_depth is not None and depths[current] >= max_depth:
            continue
        for neighbor in sorted(valid_adjacency[current]):
            if neighbor in reachable_seed:
                continue
            reachable_seed[neighbor] = reachable_seed[current]
            depths[neighbor] = depths[current] + 1
            reachability.append(neighbor)
    nodes = {node_id: valid_nodes[node_id] for node_id in sorted(reachable_seed)}
    reset = {node_id: scaled.get(node_id, 0.0) / scaled_total for node_id in nodes}
    adjacency = {
        node_id: {neighbor: weight for neighbor, weight in valid_adjacency[node_id].items() if neighbor in nodes}
        for node_id in nodes
    }
    transitions: dict[str, dict[str, float]] = {}
    for node_id, outgoing in adjacency.items():
        outgoing_total = math.fsum(outgoing.values())
        transitions[node_id] = {
            neighbor: weight / outgoing_total for neighbor, weight in outgoing.items()
        }
    edge_count = sum(
        edge["source"] in nodes and edge["target"] in nodes for edge in valid_edges
    )

    def transition(scores: Mapping[str, float]) -> dict[str, float]:
        next_scores = {node_id: (1 - damping) * reset_weight for node_id, reset_weight in reset.items()}
        dangling = math.fsum(scores[node_id] for node_id in nodes if not transitions[node_id])
        for source_id, score in scores.items():
            for target_id, probability in transitions[source_id].items():
                next_scores[target_id] += damping * score * probability
        if dangling:
            for node_id, reset_weight in reset.items():
                next_scores[node_id] += damping * dangling * reset_weight
        return next_scores

    scores = dict(reset)
    converged = False
    iterations = 0
    for iterations in range(1, max_iterations + 1):
        next_scores = transition(scores)
        delta = math.fsum(abs(next_scores[node_id] - scores[node_id]) for node_id in nodes)
        scores = next_scores
        if delta <= tolerance:
            converged = True
            break
    next_scores = transition(scores)
    residual = math.fsum(abs(next_scores[node_id] - scores[node_id]) for node_id in nodes)
    error_bound = residual / (1 - damping)
    probability_mass = math.fsum(scores.values())
    if any(not math.isfinite(value) for value in (residual, error_bound, probability_mass)):
        raise QueryError("PPR numerical result is not finite")
    ranked = sorted(
        ((node_id, score) for node_id, score in scores.items() if score > 0.0),
        key=lambda item: (-item[1], item[0]),
    )[:limit]
    return {
        "seeds": dict(sorted(positive.items())),
        "policy": {
            "damping": damping,
            "max_iterations": max_iterations,
            "tolerance": tolerance,
            "edge_types": sorted(relations),
            "direction": direction,
            "edge_policy": edge_policy,
            "confidence_gate": "declared-high-with-evidence" if edge_policy == "high-confidence" else "none",
            "max_depth": max_depth,
            "depth_boundary": "induced-subgraph-dangling-to-seeds",
        },
        "iterations": iterations,
        "used_iterations": iterations,
        "converged": converged,
        "l1_residual": residual,
        "stationary_error_bound": error_bound,
        "probability_mass": probability_mass,
        "reachable_node_count": len(nodes),
        "allowed_edge_count": edge_count,
        "results": [
            {
                "rank": rank,
                "score": round(score, 15),
                "seed_ids": [reachable_seed[node_id]],
                "node": copy.deepcopy(nodes[node_id]),
            }
            for rank, (node_id, score) in enumerate(ranked, start=1)
        ],
    }


def estimate_tokens(value: Any) -> int:
    """Return a provider-neutral upper bound using canonical UTF-8 bytes."""
    return max(1, len(canonical_json(value).encode("utf-8")))


def finalize_token_estimate(value: dict[str, Any]) -> int:
    """Set ``budget.estimated_tokens`` to its exact serialized fixed point."""
    budget = value.get("budget")
    if not isinstance(budget, dict) or "estimated_tokens" not in budget:
        raise QueryError("token estimate requires budget.estimated_tokens")
    while True:
        estimated = estimate_tokens(value)
        if budget["estimated_tokens"] == estimated:
            return estimated
        budget["estimated_tokens"] = estimated


def context(
    view: GraphView,
    node_ids: list[str],
    *,
    edge_types: list[str] | None = None,
    token_budget: int = 6000,
) -> dict[str, Any]:
    """Pack selected entries and the edges between them under a strict budget."""
    if isinstance(token_budget, bool) or not isinstance(token_budget, int) or token_budget < 1:
        raise QueryError("token_budget must be positive")
    allowed_relations = None if edge_types is None else set(edge_types)
    selected = list(dict.fromkeys(node_id for node_id in node_ids if node_id in view.nodes))
    bundle: dict[str, Any] = {
        "schema": CONTEXT_SCHEMA,
        "nodes": [],
        "edges": [],
        "omissions": [],
        "budget": {"token_budget": token_budget, "estimated_tokens": 0},
    }
    for node_id in selected:
        candidate = copy.deepcopy(bundle)
        candidate["nodes"].append(copy.deepcopy(view.nodes[node_id]))
        if finalize_token_estimate(candidate) <= token_budget:
            bundle = candidate
        else:
            bundle["omissions"].append({"kind": "node", "id": node_id, "reason": "token-budget"})
    included = {str(node["id"]) for node in bundle["nodes"]}
    for edge in view.edges:
        if (
            edge["source"] not in included
            or edge["target"] not in included
            or (allowed_relations is not None and edge["relation"] not in allowed_relations)
        ):
            continue
        candidate = copy.deepcopy(bundle)
        candidate["edges"].append(copy.deepcopy(edge))
        if finalize_token_estimate(candidate) <= token_budget:
            bundle = candidate
        else:
            identifier = f"{edge['source']}:{edge['relation']}:{edge['target']}"
            bundle["omissions"].append({"kind": "edge", "id": identifier, "reason": "token-budget"})
    while bundle["omissions"] and finalize_token_estimate(bundle) > token_budget:
        bundle["omissions"].pop()
    if finalize_token_estimate(bundle) > token_budget:
        raise QueryError("budget-too-small after context packing")
    return bundle
