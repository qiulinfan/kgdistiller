"""Deterministic projection of user-declared topic/organization roles.

Generation belongs to the organization Skill. This module infers no topics,
identities or scientific claims, and never writes records or the database.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

from .home import Home, KnowledgeError, load_home
from .records import Record


def _glob_matches(source: str, pattern: str) -> bool:
    """Match registered path segments, including recursive ** and absent files.

    This is registry matching, not source-format parsing. Keeping absent
    sources matchable preserves navigation over stale indexed records.
    """
    parts, patterns = source.split("/"), pattern.split("/")
    if any(part.startswith(".") for part in parts):
        return False
    positions = {0}
    for segment in patterns:
        if segment == "**":
            positions = {number for start in positions for number in range(start, len(parts) + 1)}
        else:
            positions = {start + 1 for start in positions
                         if start < len(parts) and fnmatchcase(parts[start], segment)}
    return len(parts) in positions


def source_profiles(home: Home, sources: Iterable[tuple[str, str]] | None = None) -> dict[tuple[str, str], dict[str, str]]:
    """Navigation role conventions of registered sources; read no source text."""
    profiles = {}
    if sources is not None:
        for name, source in set(sources):
            base = home.bases.get(name)
            if base is None:
                continue
            matches = {type_name for pattern, type_name in base.sources.items() if _glob_matches(source, pattern)}
            if len(matches) == 1:
                navigation = home.types[next(iter(matches))].navigation
                if navigation is not None:
                    profiles[name, source] = navigation
        return profiles
    for base in home.bases.values():
        if not base.root.is_dir():
            continue
        mapping, _ = base.source_types()
        for source, name in mapping.items():
            navigation = home.types[name].navigation
            if navigation is not None:
                profiles[base.name, source] = navigation
    return profiles


def record_view(record: Record) -> dict[str, Any]:
    return {
        "uid": record.uid, "base": record.base, "source": record.source,
        "kind": record.kind, "class": record.class_,
        "out": [{"role": role, "uid": value.uid, "term": value.term}
                for role, _, value in record.values()],
    }


@dataclass
class Projection:
    topics: set[str]
    organizations: set[str]
    edges: list[tuple[str, str, str]]
    children: dict[str, set[str]]
    parents: dict[str, set[str]]
    malformed: list[dict[str, str]]
    dangling: list[dict[str, str]]
    cyclic: list[str]

    def roots(self, allowed: set[str]) -> list[str]:
        return sorted(uid for uid in self.topics & allowed if not (self.parents.get(uid, set()) & allowed))

    def reachable(self, allowed: set[str]) -> set[str]:
        seen = set(self.roots(allowed))
        queue = deque(seen)
        while queue:
            for child in self.children.get(queue.popleft(), set()) & allowed:
                if child not in seen:
                    seen.add(child)
                    queue.append(child)
        return seen

    def diagnostics(self, allowed: set[str], limit: int = 40) -> dict[str, Any]:
        scientific = allowed - self.topics - self.organizations
        unreachable = sorted(scientific - self.reachable(allowed))
        empty = sorted(uid for uid in self.topics & allowed if not self.children.get(uid))
        return {
            "cyclic_topics": self.cyclic[:limit], "cyclic_topic_count": len(self.cyclic),
            "dangling": self.dangling[:limit], "dangling_count": len(self.dangling),
            "malformed": self.malformed[:limit], "malformed_count": len(self.malformed),
            "uncovered": unreachable[:limit], "uncovered_count": len(unreachable),
            "empty_topics": empty[:limit], "empty_topic_count": len(empty),
            "knowledge_count": len(scientific), "covered_count": len(scientific) - len(unreachable),
            "topic_count": len(self.topics & allowed),
            "organization_count": len(self.organizations & allowed),
        }


def project(
    records: Mapping[str, Mapping[str, Any]],
    profiles: Mapping[tuple[str, str], Mapping[str, str]],
) -> Projection:
    topics, organizations = set(), set()
    for uid, row in records.items():
        profile = profiles.get((row["base"], row["source"]))
        if profile is None:
            continue
        if row["class"] == "node" and row["kind"] == profile["topic_kind"]:
            topics.add(uid)
        elif row["class"] == "relation" and row["kind"] == profile["relation_kind"]:
            organizations.add(uid)
    edges, malformed, dangling = [], [], []
    children: dict[str, set[str]] = defaultdict(set)
    parents: dict[str, set[str]] = defaultdict(set)
    for uid in sorted(organizations):
        row = records[uid]
        profile = profiles[row["base"], row["source"]]
        bound = {}
        for key in ("parent_role", "child_role"):
            values = [link for link in row["out"] if link["role"] == profile[key]]
            bound[key] = list(dict.fromkeys(link["uid"] for link in values if link.get("uid")))
            if not values or any(not link.get("uid") for link in values):
                malformed.append({"uid": uid, "message": f"{profile[key]} needs linked participants"})
        for parent in bound["parent_role"]:
            if parent not in records:
                dangling.append({"uid": uid, "target": parent})
            elif parent not in topics:
                malformed.append({"uid": uid, "message": f"parent {parent} is not a navigation topic"})
        for child in bound["child_role"]:
            if child not in records:
                dangling.append({"uid": uid, "target": child})
            elif child in organizations:
                malformed.append({"uid": uid, "message": f"child {child} is an organization relation"})
        for parent in bound["parent_role"]:
            for child in bound["child_role"]:
                if parent in topics and child in records and child not in organizations:
                    edges.append((parent, child, uid))
                    children[parent].add(child)
                    parents[child].add(parent)
    # Kahn's algorithm avoids a depth cap and recursion limits. Residual topics
    # include cycle members and their downstream topics; all need inspection.
    indegree = {uid: len(parents.get(uid, set()) & topics) for uid in topics}
    queue = deque(sorted(uid for uid, degree in indegree.items() if degree == 0))
    while queue:
        for child in sorted(children.get(queue.popleft(), set()) & topics):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    return Projection(topics, organizations, edges, dict(children), dict(parents), malformed, dangling,
                      sorted(uid for uid, degree in indegree.items() if degree))


def navigate(
    uids: Iterable[str] = (), *, bases: Iterable[str] = (), limit: int = 40, offset: int = 0,
) -> dict[str, Any]:
    """Bounded root discovery or one semantic hop, preserving base-qualified identity."""
    from .index import lag
    from .retrieve import _links_out, _rows, _session, _start_uids, _summary

    if limit < 1 or offset < 0:
        raise KnowledgeError("limit must be positive and offset must not be negative")
    connection, registered = _session()
    try:
        # Profiles are home metadata, not retrieved knowledge. Record bodies and
        # links still come from one read-only database snapshot.
        home = load_home(registered.directory)
        scope = set(bases)
        unknown = scope - set(home.bases)
        if unknown:
            raise KnowledgeError("unknown bases: " + ", ".join(sorted(unknown)))
        rows = {row["uid"]: row for row in connection.execute("SELECT uid, base, source, kind, class FROM record")}
        profiles = source_profiles(home, ((row["base"], row["source"]) for row in rows.values()))
        organization_uids = [uid for uid, row in rows.items()
                             if row["class"] == "relation" and (row["base"], row["source"]) in profiles
                             and row["kind"] == profiles[row["base"], row["source"]]["relation_kind"]]
        links = _links_out(connection, organization_uids)
        views = {uid: {**dict(row), "out": [{"role": link["role"], "uid": link["dst"], "term": link["term"]}
                                           for link in links[uid]]} for uid, row in rows.items()}
        graph = project(views, profiles)
        allowed = {uid for uid, row in rows.items() if not scope or row["base"] in scope}
        arguments = list(uids)
        starts, missing = _start_uids(connection, arguments)
        visible = set(starts) & allowed
        chosen = (set().union(*(graph.children.get(uid, set()) for uid in visible)) & allowed
                  if arguments else set(graph.roots(allowed)))
        ordered = sorted(chosen, key=lambda uid: (uid not in graph.topics, uid))
        page = ordered[offset:offset + limit]
        page_edges = [(parent, child, relation) for parent, child, relation in graph.edges
                      if parent in visible and child in page]
        relation_uids = sorted({relation for _, _, relation in page_edges})
        # Body/summary reads are bounded to the page. Navigation never loads
        # scientific bodies or vectors for every unvisited branch.
        summary_rows = _rows(connection, list(dict.fromkeys([*page, *relation_uids[:limit], *sorted(visible)])))

        def summary(uid: str) -> dict[str, Any]:
            return {**_summary(summary_rows[uid]), "navigation": "topic" if uid in graph.topics else "organization" if uid in graph.organizations else "leaf",
                    "child_count": len(graph.children.get(uid, set()) & allowed),
                    "parent_count": len(graph.parents.get(uid, set()) & allowed)}

        return {
            "records": [summary(uid) for uid in page],
            "relations": [_summary(summary_rows[uid]) for uid in relation_uids[:limit]],
            "edges": [{"parent": parent, "child": child, "relation": relation}
                      for parent, child, relation in page_edges[:limit]],
            "truncated": {"relations": max(0, len(relation_uids) - limit), "edges": max(0, len(page_edges) - limit)},
            "starts": [summary(uid) for uid in starts if uid in allowed],
            "missing": missing, "outside_scope": sorted(set(starts) - allowed),
            "profiles": [{"base": base, "source": source, **profile}
                         for (base, source), profile in sorted(profiles.items()) if not scope or base in scope],
            "page": {"offset": offset, "limit": limit, "total": len(ordered),
                     "next_offset": offset + limit if offset + limit < len(ordered) else None},
            "diagnostics": graph.diagnostics(allowed, limit), "lag": lag(connection, registered),
        }
    finally:
        connection.close()
