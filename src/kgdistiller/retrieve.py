"""Read primitives over the derived database.

There are six: ``search``, ``resolve``, ``get``, ``neighbors``, ``browse`` and
``pack``. Each opens the database read-only, reads it in one snapshot, is
global across bases unless filtered, and reports ``lag``. ``search`` fuses
three lanes with reciprocal rank fusion:

- lexical: FTS5 over the unified text;
- dense: an exact dot product over the stored vectors, the query encoded with
  ``meta.embedding``; it runs when ``meta.embedding`` is set (an embedding
  model built the index) and the caller does not disable it;
- name: label and alias keys.

There are no boosts, intent rules or thresholds. ``neighbors`` follows links
with one recursive query, ``browse`` lists bases, source trees, a source's
records by kind or every record of a kind, and ``pack`` returns whole records
within a byte budget with shared relations and typed gaps. Only
``get --source-lines`` reads live source text.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from . import records
from .adapters import sentence_transformers as embedding_adapter
from .home import Home, KnowledgeError, home_directory, load_home
from .index import is_cjk, lag, meta_embedding, name_key, open_read_only, tokens
from .records import UNDERSTANDING, fold, gloss

LANE_LIMIT = 200
RRF_K = 60
LINK_CAP = 12
NAME_RUN_LIMIT = 12
CLASSES = ("node", "relation")
DIRECTIONS = ("out", "in", "both")
PACK_BUDGET = 60000
MAX_DEPTH = 32
_CHUNK = 500


@dataclass(frozen=True)
class Filters:
    """Repeatable filters: OR within one filter, AND across filters, applied in SQL."""

    base: tuple[str, ...] = ()
    kind: tuple[str, ...] = ()
    class_: tuple[str, ...] = ()
    source: tuple[str, ...] = ()
    understanding: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value in self.class_:
            if value not in CLASSES:
                raise KnowledgeError(f"class must be one of {', '.join(CLASSES)}, not {value!r}")
        for value in self.understanding:
            if value not in UNDERSTANDING:
                raise KnowledgeError(f"understanding must be one of {', '.join(UNDERSTANDING)}, not {value!r}")

    def sql(self, alias: str = "r") -> tuple[str, list[str]]:
        """An ``AND …`` clause over the record table aliased ``alias``."""
        clauses: list[str] = []
        parameters: list[str] = []
        for column, values in (
            ("base", self.base), ("kind", self.kind), ("class", self.class_), ("understanding", self.understanding),
        ):
            if values:
                clauses.append(f"{alias}.{column} IN ({', '.join('?' for _ in values)})")
                parameters.extend(values)
        if self.source:
            clauses.append("(" + " OR ".join(f"substr({alias}.source, 1, length(?)) = ?" for _ in self.source) + ")")
            for prefix in self.source:
                parameters.extend((prefix, prefix))
        return "".join(f" AND {clause}" for clause in clauses), parameters


NO_FILTERS = Filters()


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _session() -> tuple[sqlite3.Connection, Home]:
    home = load_home(home_directory(), types=False)
    connection = open_read_only()
    connection.row_factory = sqlite3.Row
    # One read transaction per call, so meta.embedding and the vectors come
    # from the same commit even while `kgd index` writes.
    connection.execute("BEGIN")
    return connection, home


def _lines(row: sqlite3.Row) -> str:
    return f"{row['line_start']}-{row['line_end']}"


def _rows(connection: sqlite3.Connection, uids: Sequence[str]) -> dict[str, sqlite3.Row]:
    """The record rows of ``uids`` that exist, by uid."""
    rows: dict[str, sqlite3.Row] = {}
    for start in range(0, len(uids), _CHUNK):
        chunk = list(uids[start : start + _CHUNK])
        for row in connection.execute(f"SELECT * FROM record WHERE uid IN ({', '.join('?' for _ in chunk)})", chunk):
            rows[row["uid"]] = row
    return rows


def _labels(connection: sqlite3.Connection, uids: Iterable[str]) -> dict[str, str]:
    wanted = sorted(set(uids))
    labels: dict[str, str] = {}
    for start in range(0, len(wanted), _CHUNK):
        chunk = wanted[start : start + _CHUNK]
        labels.update(connection.execute(
            f"SELECT uid, label FROM record WHERE uid IN ({', '.join('?' for _ in chunk)})", chunk
        ).fetchall())
    return labels


# ---------------------------------------------------------------- search


def _lexical(connection: sqlite3.Connection, query: str, filters: Filters) -> list[str]:
    words = list(dict.fromkeys(tokens(query)))
    if not words:
        return []
    clause, parameters = filters.sql()
    expression = " OR ".join('"' + word.replace('"', '""') + '"' for word in words)
    rows = connection.execute(
        f"SELECT r.uid FROM fts JOIN record r ON r.rowid = fts.rowid WHERE fts MATCH ?{clause} "
        f"ORDER BY bm25(fts), r.uid LIMIT {LANE_LIMIT}",
        [expression, *parameters],
    )
    return [row[0] for row in rows]


def _cjk_runs(word: str) -> list[str]:
    runs, current = [], ""
    for char in word:
        if is_cjk(char):
            current += char
        elif current:
            runs.append(current)
            current = ""
    if current:
        runs.append(current)
    return runs


def name_candidates(query: str) -> set[str]:
    """Contiguous runs of 1-12 words of the query's name key, plus CJK substrings of length 1-12."""
    words = name_key(query).split()
    candidates = {
        " ".join(words[start:end])
        for start in range(len(words))
        for end in range(start + 1, min(len(words), start + NAME_RUN_LIMIT) + 1)
    }
    for word in words:
        for run in _cjk_runs(word):
            candidates.update(
                run[start:end]
                for start in range(len(run))
                for end in range(start + 1, min(len(run), start + NAME_RUN_LIMIT) + 1)
            )
    return candidates


def _name(connection: sqlite3.Connection, query: str, filters: Filters) -> list[str]:
    candidates = sorted(name_candidates(query))
    if not candidates:
        return []
    exact = name_key(query)
    clause, parameters = filters.sql()
    hits: list[tuple[str, str, int]] = []
    for start in range(0, len(candidates), _CHUNK):
        chunk = candidates[start : start + _CHUNK]
        hits.extend(connection.execute(
            f"SELECT n.key, n.uid, n.is_label FROM name n JOIN record r ON r.uid = n.uid "
            f"WHERE n.key IN ({', '.join('?' for _ in chunk)}){clause}",
            [*chunk, *parameters],
        ).fetchall())
    hits.sort(key=lambda hit: (hit[0] != exact, -len(hit[0]), -hit[2], hit[1]))
    return list(dict.fromkeys(uid for _, uid, _ in hits))[:LANE_LIMIT]


NO_DENSE_HINT = "pass --no-dense (MCP: no_dense) to search without the dense lane"


def _dense(connection: sqlite3.Connection, query: str, filters: Filters, model: str) -> list[str]:
    """Uids by the exact dot product of their stored vector with the query encoded by ``model``."""
    clause, parameters = filters.sql()
    rows = connection.execute(
        f"SELECT r.uid, r.vec FROM record r WHERE r.vec IS NOT NULL{clause} ORDER BY r.uid", parameters
    ).fetchall()
    if not rows:
        return []
    missing = KnowledgeError(
        f"the index holds {model} vectors but the retrieval extra is missing: "
        "install kgdistiller[retrieval] or pass --no-dense (MCP: no_dense)"
    )
    try:
        import numpy
    except ImportError:
        raise missing from None
    try:
        query_vector = embedding_adapter.encoder(model).encode_query(query)
    except embedding_adapter.RetrievalExtraMissing:
        raise missing from None
    except embedding_adapter.EmbeddingError as error:
        raise KnowledgeError(f"{error}; {NO_DENSE_HINT}") from error
    matrix = numpy.frombuffer(b"".join(row[1] for row in rows), dtype="<f4").reshape(len(rows), -1)
    scores = matrix @ numpy.frombuffer(query_vector, dtype="<f4")
    order = numpy.argsort(-scores, kind="stable")[:LANE_LIMIT]
    return [rows[position][0] for position in order]


def _capped(items: list[Any]) -> tuple[list[Any], int]:
    return items[:LINK_CAP], max(0, len(items) - LINK_CAP)


def _links_out(connection: sqlite3.Connection, uids: Sequence[str]) -> dict[str, list[sqlite3.Row]]:
    out: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for start in range(0, len(uids), _CHUNK):
        chunk = list(uids[start : start + _CHUNK])
        for row in connection.execute(
            f"SELECT src, role, pos, dst, term, term_key FROM link WHERE src IN ({', '.join('?' for _ in chunk)}) "
            "ORDER BY src, role = 'requires', role, pos",
            chunk,
        ):
            out[row["src"]].append(row)
    return out


def _links_in(connection: sqlite3.Connection, uids: Sequence[str]) -> dict[str, list[dict[str, str]]]:
    incoming: dict[str, list[dict[str, str]]] = defaultdict(list)
    for start in range(0, len(uids), _CHUNK):
        chunk = list(uids[start : start + _CHUNK])
        for row in connection.execute(
            f"SELECT DISTINCT l.dst, l.src, l.role, r.kind, r.label FROM link l JOIN record r ON r.uid = l.src "
            f"WHERE l.dst IN ({', '.join('?' for _ in chunk)}) ORDER BY l.dst, l.src, l.role",
            chunk,
        ):
            incoming[row["dst"]].append({"uid": row["src"], "label": row["label"], "kind": row["kind"], "role": row["role"]})
    return incoming


def _reference(row: sqlite3.Row, labels: dict[str, str]) -> dict[str, Any]:
    if row["dst"] is None:
        return {"term": row["term"]}
    return {"uid": row["dst"], "label": labels.get(row["dst"])}


def _summary(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "uid": row["uid"],
        "label": row["label"],
        "kind": row["kind"],
        "class": row["class"],
        "base": row["base"],
        "source": row["source"],
        "lines": _lines(row),
        "gloss": gloss(row["body"]),
    }


def search(query: str, limit: int = 40, filters: Filters = NO_FILTERS, dense: bool = True) -> dict[str, Any]:
    """Fuse the lexical, dense and name lanes with RRF (k=60); ties break by uid.

    The dense lane runs when ``dense`` is true and ``meta.embedding`` is set
    (an embedding model built the index); ``lanes`` lists the lanes that ran.
    """
    if limit < 1:
        raise KnowledgeError("limit must be at least 1")
    connection, home = _session()
    try:
        lanes = {"lexical": _lexical(connection, query, filters)}
        model = meta_embedding(connection)
        if dense and model:
            lanes["dense"] = _dense(connection, query, filters, model)
        lanes["name"] = _name(connection, query, filters)
        ranks: dict[str, dict[str, int]] = defaultdict(dict)
        scores: dict[str, float] = defaultdict(float)
        for lane, ranked in lanes.items():
            for rank, uid in enumerate(ranked, 1):
                ranks[uid][lane] = rank
                scores[uid] += 1 / (RRF_K + rank)
        chosen = sorted(scores, key=lambda uid: (-scores[uid], uid))[:limit]
        rows = {
            row["uid"]: row
            for start in range(0, len(chosen), _CHUNK)
            for row in connection.execute(
                f"SELECT * FROM record WHERE uid IN ({', '.join('?' for _ in chosen[start : start + _CHUNK])})",
                chosen[start : start + _CHUNK],
            )
        }
        out_links = _links_out(connection, chosen)
        in_links = _links_in(connection, chosen)
        labels = _labels(connection, (link["dst"] for links in out_links.values() for link in links if link["dst"]))
        results = []
        for uid in chosen:
            row = rows[uid]
            requires = [_reference(link, labels) for link in out_links[uid] if link["role"] == "requires"]
            participants: dict[str, list[dict[str, Any]]] = {}
            dropped = 0
            if row["class"] == "relation":
                grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
                for link in out_links[uid]:
                    if link["role"] != "requires":
                        grouped[link["role"]].append(_reference(link, labels))
                for role in sorted(grouped):
                    participants[role], extra = _capped(grouped[role])
                    dropped += extra
            requires, requires_dropped = _capped(requires)
            incoming, incoming_dropped = _capped(
                [{"uid": item["uid"], "kind": item["kind"], "role": item["role"]} for item in in_links[uid]]
            )
            results.append({
                "uid": uid,
                "class": row["class"],
                "kind": row["kind"],
                "label": row["label"],
                "base": row["base"],
                "source": row["source"],
                "lines": _lines(row),
                "understanding": row["understanding"],
                "epistemic": row["epistemic"],
                "gloss": gloss(row["body"]),
                "ranks": {lane: ranks[uid].get(lane) for lane in lanes},
                "requires": requires,
                "participants": participants,
                "in": incoming,
                "truncated": {"requires": requires_dropped, "participants": dropped, "in": incoming_dropped},
            })
        return {"query": query, "lanes": list(lanes), "lag": lag(connection, home), "results": results}
    finally:
        connection.close()


# ---------------------------------------------------------------- resolve


def _order(row: sqlite3.Row) -> tuple[Any, ...]:
    return row["base"], row["source"], row["line_start"], row["uid"]


def resolve(terms: Iterable[str], filters: Filters = NO_FILTERS) -> dict[str, Any]:
    """Senses (exact name key), mentions (contained name key) and pending link rows for each term."""
    connection, home = _session()
    try:
        clause, parameters = filters.sql()
        answers = []
        for term in terms:
            key = name_key(term)
            senses: dict[str, sqlite3.Row] = {}
            mentions: dict[str, sqlite3.Row] = {}
            pending: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
            if key:
                cjk = any(is_cjk(char) for char in key)
                for row in connection.execute(
                    f"SELECT n.key AS name, r.* FROM name n JOIN record r ON r.uid = n.uid "
                    f"WHERE instr(n.key, ?) > 0{clause}",
                    [key, *parameters],
                ):
                    if row["name"] == key:
                        senses[row["uid"]] = row
                    elif cjk or f" {key} " in f" {row['name']} ":
                        mentions[row["uid"]] = row
                for row in connection.execute(
                    f"SELECT l.src, l.role, l.pos, l.term, r.base, r.source, r.line_start, r.uid FROM link l "
                    f"JOIN record r ON r.uid = l.src WHERE l.term_key = ?{clause}",
                    [key, *parameters],
                ):
                    pending.append((
                        (*_order(row), row["role"], row["pos"]),
                        {"owner": row["src"], "role": row["role"], "term": row["term"]},
                    ))
            answers.append({
                "term": term,
                "key": key,
                "senses": [_summary(row) for row in sorted(senses.values(), key=_order)],
                "mentions": [
                    _summary(row) for uid, row in sorted(mentions.items(), key=lambda item: _order(item[1]))
                    if uid not in senses
                ],
                "pending": [item for _, item in sorted(pending, key=lambda pair: pair[0])],
            })
        return {"terms": answers, "lag": lag(connection, home)}
    finally:
        connection.close()


# ---------------------------------------------------------------- get


def _resolve_uid(connection: sqlite3.Connection, argument: str) -> str | None:
    if ":" in argument:
        uid = fold(argument)
        return uid if connection.execute("SELECT 1 FROM record WHERE uid = ?", (uid,)).fetchone() else None
    found = [row[0] for row in connection.execute("SELECT uid FROM record WHERE id = ? ORDER BY uid", (fold(argument),))]
    if len(found) > 1:
        raise KnowledgeError(f"id {argument} exists in several bases; pass one of: {', '.join(found)}")
    return found[0] if found else None


def _source_text(home: Home, row: sqlite3.Row, around: int) -> str | None:
    """The cited range ±``around`` of a registered source inside its base root; null otherwise."""
    base = home.bases.get(row["base"])
    source = row["source"]
    if base is None or not records.valid_source_path(source) or not base.holds(source):
        return None
    if source not in base.source_types()[0]:
        return None
    try:
        lines = records.source_lines(base.root / source)
    except (OSError, UnicodeDecodeError):
        return None
    first = max(1, row["line_start"] - around)
    last = min(len(lines), row["line_end"] + around)
    if first > last:
        return None
    return "\n".join(f"{number}\t{lines[number - 1]}" for number in range(first, last + 1))


def _record(row: sqlite3.Row, out_links: Sequence[sqlite3.Row], labels: dict[str, str]) -> dict[str, Any]:
    """A complete record with its out-links, as ``get`` returns it before ``in`` and ``source_text``."""
    out: list[dict[str, Any]] = []
    for link in out_links:
        if link["dst"] is None:
            out.append({"role": link["role"], "pos": link["pos"], "term": link["term"]})
        else:
            out.append({
                "role": link["role"], "pos": link["pos"], "uid": link["dst"],
                "label": labels.get(link["dst"]), "exists": link["dst"] in labels,
            })
    return {
        "uid": row["uid"],
        "base": row["base"],
        "id": row["id"],
        "class": row["class"],
        "kind": row["kind"],
        "label": row["label"],
        "aliases": json.loads(row["aliases"]),
        "source": row["source"],
        "lines": _lines(row),
        "understanding": row["understanding"],
        "epistemic": row["epistemic"],
        "gloss": gloss(row["body"]),
        "body": row["body"],
        "search_terms": row["search_terms"],
        "evidence": json.loads(row["evidence"]),
        "out": out,
    }


def get(uids: Iterable[str], source_lines: int | None = None) -> dict[str, Any]:
    """Complete records with their out- and in-links; ``source_lines`` adds the live cited range ±N."""
    if source_lines is not None and source_lines < 0:
        raise KnowledgeError("--source-lines must not be negative")
    connection, home = _session()
    try:
        found: list[str] = []
        missing: list[str] = []
        for argument in uids:
            uid = _resolve_uid(connection, argument)
            if uid is None:
                missing.append(argument)
            elif uid not in found:
                found.append(uid)
        rows = _rows(connection, found)
        out_links = _links_out(connection, found)
        in_links = _links_in(connection, found)
        labels = _labels(connection, (link["dst"] for links in out_links.values() for link in links if link["dst"]))
        found_records = []
        for uid in found:
            record = _record(rows[uid], out_links[uid], labels)
            record["in"] = in_links[uid]
            if source_lines is not None:
                record["source_text"] = _source_text(home, rows[uid], source_lines)
            found_records.append(record)
        return {"records": found_records, "missing": missing, "lag": lag(connection, home)}
    finally:
        connection.close()


def _start_uids(connection: sqlite3.Connection, arguments: Iterable[str]) -> tuple[list[str], list[str]]:
    """Resolved uids and unknown arguments, each deduplicated in caller order; an ambiguous bare id raises."""
    found: list[str] = []
    unknown: list[str] = []
    for argument in arguments:
        uid = _resolve_uid(connection, argument)
        if uid is None:
            if argument not in unknown:
                unknown.append(argument)
        elif uid not in found:
            found.append(uid)
    return found, unknown


def _role_clause(roles: Sequence[str]) -> tuple[str, list[str]]:
    if not roles:
        return "", []
    return f" AND l.role IN ({', '.join('?' for _ in roles)})", list(roles)


def _references(links: Sequence[sqlite3.Row], labels: dict[str, str]) -> dict[str, list[dict[str, Any]]]:
    """Out-links grouped by role, in link order (``requires`` last), uncapped."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for link in links:
        grouped.setdefault(link["role"], []).append(_reference(link, labels))
    return grouped


# ---------------------------------------------------------------- neighbors


def _reach(
    connection: sqlite3.Connection, starts: list[str], roles: Sequence[str], direction: str, depth: int, filters: Filters,
) -> dict[str, int]:
    """Every uid reachable within ``depth`` hops, at its minimum depth; missing targets are leaves."""
    if not starts:
        return {}
    step = {
        "out": ("l.src = walk.uid", "l.dst"),
        "in": ("l.dst = walk.uid", "l.src"),
        "both": ("(l.src = walk.uid OR l.dst = walk.uid)", "CASE WHEN l.src = walk.uid THEN l.dst ELSE l.src END"),
    }
    join, following = step[direction]
    role_clause, role_parameters = _role_clause(roles)
    filter_clause, filter_parameters = filters.sql("r")
    rows = connection.execute(
        f"""
        WITH RECURSIVE walk(uid, depth, live) AS (
            SELECT value, 0, 1 FROM json_each(?)
            UNION
            SELECT {following}, walk.depth + 1, r.uid IS NOT NULL
            FROM walk JOIN link l ON {join}
            LEFT JOIN record r ON r.uid = {following}
            WHERE walk.live AND walk.depth < ? AND l.dst IS NOT NULL{role_clause}
              AND (r.uid IS NULL OR (1{filter_clause}))
        )
        SELECT uid, MIN(depth) FROM walk GROUP BY uid
        """,
        [json.dumps(starts), depth, *role_parameters, *filter_parameters],
    )
    return dict(rows.fetchall())


def neighbors(
    uids: Iterable[str],
    roles: Sequence[str] = (),
    direction: str = "out",
    depth: int = 1,
    filters: Filters = NO_FILTERS,
) -> dict[str, Any]:
    """Edges reachable from ``uids`` within ``depth`` hops over ``roles``; nothing is inferred.

    Filters restrict the records the walk may enter; start uids always count.
    Pending terms and missing targets are leaves. Each link is reported once,
    in its own direction, at the minimum depth it is reached from.
    """
    if direction not in DIRECTIONS:
        raise KnowledgeError(f"direction must be one of {', '.join(DIRECTIONS)}, not {direction!r}")
    if depth < 1:
        raise KnowledgeError("depth must be at least 1")
    connection, home = _session()
    try:
        starts, missing = _start_uids(connection, uids)
        visited = _reach(connection, starts, roles, direction, depth, filters)
        rows = _rows(connection, list(visited))
        expandable = sorted(uid for uid, reached in visited.items() if reached < depth and uid in rows)
        role_clause, role_parameters = _role_clause(roles)
        found: dict[tuple[str, str, int], tuple[int, dict[str, Any]]] = {}

        def edge(link: sqlite3.Row, at: int) -> None:
            key = (link["src"], link["role"], link["pos"])
            if key in found and found[key][0] <= at:
                return
            target = {"term": link["term"]} if link["dst"] is None else {"to": link["dst"]}
            found[key] = (at, {"from": link["src"], "role": link["role"], **target, "depth": at})

        for start in range(0, len(expandable), _CHUNK):
            chunk = expandable[start : start + _CHUNK]
            marks = ", ".join("?" for _ in chunk)
            if direction in ("out", "both"):
                for link in connection.execute(
                    f"SELECT src, role, pos, dst, term FROM link l WHERE src IN ({marks}){role_clause}",
                    [*chunk, *role_parameters],
                ):
                    if link["dst"] is None or link["dst"] in visited:
                        edge(link, visited[link["src"]] + 1)
            if direction in ("in", "both"):
                for link in connection.execute(
                    f"SELECT src, role, pos, dst, term FROM link l WHERE dst IN ({marks}){role_clause}",
                    [*chunk, *role_parameters],
                ):
                    if link["src"] in visited:
                        edge(link, visited[link["dst"]] + 1)
        edges = [
            item for _, (_, item) in sorted(
                found.items(), key=lambda entry: (entry[1][0], entry[0][0], entry[0][1] == "requires", *entry[0][1:]),
            )
        ]
        records = {
            uid: (
                {"label": rows[uid]["label"], "kind": rows[uid]["kind"], "class": rows[uid]["class"], "exists": True}
                if uid in rows else {"label": None, "kind": None, "class": None, "exists": False}
            )
            for uid in sorted(visited, key=lambda uid: (visited[uid], uid))
        }
        return {"edges": edges, "records": records, "missing": missing, "lag": lag(connection, home)}
    finally:
        connection.close()


# ---------------------------------------------------------------- browse


def _scope_sql(base: str | None, path: str) -> tuple[str, list[Any]]:
    """An ``AND …`` clause for every base (``base`` None), a base root (``path`` empty), a directory or a source."""
    if base is None:
        return "", []
    if path == "":
        return " AND r.base = ?", [base]
    if path.endswith("/"):
        return " AND r.base = ? AND substr(r.source, 1, length(?)) = ?", [base, path, path]
    return " AND r.base = ? AND r.source = ?", [base, path]


def _browse_bases(connection: sqlite3.Connection, home: Home, filters: Filters) -> list[dict[str, Any]]:
    clause, parameters = filters.sql()
    counts = {
        row[0]: (row[1], row[2]) for row in connection.execute(
            f"SELECT r.base, COUNT(*), SUM(r.class = 'relation') FROM record r WHERE 1{clause} GROUP BY r.base",
            parameters,
        )
    }
    pending = dict(connection.execute(
        f"SELECT r.base, COUNT(DISTINCT l.term_key) FROM link l JOIN record r ON r.uid = l.src "
        f"WHERE l.term_key IS NOT NULL{clause} GROUP BY r.base",
        parameters,
    ).fetchall())
    return [
        {
            "name": name,
            "available": home.bases[name].root.is_dir(),
            "records": counts.get(name, (0, 0))[0],
            "relations": counts.get(name, (0, 0))[1],
            "pending": pending.get(name, 0),
        }
        for name in sorted(home.bases)
        if not filters.base or name in filters.base
    ]


def _browse_directory(connection: sqlite3.Connection, base: str, prefix: str, filters: Filters) -> list[dict[str, Any]]:
    scope, scope_parameters = _scope_sql(base, prefix)
    clause, parameters = filters.sql()
    entries: dict[str, dict[str, Any]] = {}
    terms: dict[str, set[str]] = defaultdict(set)

    def entry(source: str) -> dict[str, Any]:
        rest = source[len(prefix):]
        if "/" in rest:
            path, kind = prefix + rest.split("/", 1)[0] + "/", "dir"
        else:
            path, kind = source, "source"
        return entries.setdefault(path, {"path": path, "type": kind, "records": 0, "relations": 0, "pending": 0})

    for source, class_, count in connection.execute(
        f"SELECT r.source, r.class, COUNT(*) FROM record r WHERE 1{scope}{clause} GROUP BY r.source, r.class",
        [*scope_parameters, *parameters],
    ):
        item = entry(source)
        item["records"] += count
        if class_ == "relation":
            item["relations"] += count
    for source, term_key in connection.execute(
        f"SELECT DISTINCT r.source, l.term_key FROM link l JOIN record r ON r.uid = l.src "
        f"WHERE l.term_key IS NOT NULL{scope}{clause}",
        [*scope_parameters, *parameters],
    ):
        terms[entry(source)["path"]].add(term_key)
    for path, keys in terms.items():
        entries[path]["pending"] = len(keys)
    return [entries[path] for path in sorted(entries)]


def _browse_source(connection: sqlite3.Connection, base: str, source: str, filters: Filters) -> dict[str, Any]:
    scope, scope_parameters = _scope_sql(base, source)
    clause, parameters = filters.sql()
    rows = connection.execute(
        f"SELECT * FROM record r WHERE 1{scope}{clause} ORDER BY r.line_start, r.uid", [*scope_parameters, *parameters]
    ).fetchall()
    uids = [row["uid"] for row in rows]
    out_links = _links_out(connection, uids)
    labels = _labels(connection, (link["dst"] for links in out_links.values() for link in links if link["dst"]))
    kinds: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pending: dict[str, tuple[str, list[dict[str, str]]]] = {}
    for row in rows:
        kinds[row["kind"]].append({
            "uid": row["uid"],
            "label": row["label"],
            "class": row["class"],
            "lines": _lines(row),
            "understanding": row["understanding"],
            "gloss": gloss(row["body"]),
            "links": _references(out_links[row["uid"]], labels),
        })
        for link in out_links[row["uid"]]:
            if link["dst"] is None:
                owners = pending.setdefault(link["term_key"], (link["term"], []))[1]
                owner = {"uid": row["uid"], "role": link["role"]}
                if owner not in owners:
                    owners.append(owner)
    return {
        "base": base,
        "source": source,
        "kinds": [{"kind": kind, "records": kinds[kind]} for kind in sorted(kinds)],
        "pending": [{"term": term, "owners": owners} for _, (term, owners) in sorted(pending.items())],
    }


def _browse_kind(connection: sqlite3.Connection, base: str | None, path: str, filters: Filters) -> list[dict[str, Any]]:
    scope, scope_parameters = _scope_sql(base, path)
    clause, parameters = filters.sql()
    rows = connection.execute(
        f"SELECT * FROM record r WHERE 1{scope}{clause} ORDER BY r.base, r.source, r.line_start, r.uid",
        [*scope_parameters, *parameters],
    ).fetchall()
    out_links = _links_out(connection, [row["uid"] for row in rows])
    labels = _labels(connection, (link["dst"] for links in out_links.values() for link in links if link["dst"]))
    return [
        {
            "uid": row["uid"],
            "label": row["label"],
            "kind": row["kind"],
            "class": row["class"],
            "base": row["base"],
            "source": row["source"],
            "lines": _lines(row),
            "understanding": row["understanding"],
            "epistemic": row["epistemic"],
            "gloss": gloss(row["body"]),
            "links": _references(out_links[row["uid"]], labels),
        }
        for row in rows
    ]


def _is_directory(connection: sqlite3.Connection, base: str, path: str) -> bool:
    """Whether a handle path without a trailing '/' names a directory of indexed sources rather than a source."""
    if connection.execute("SELECT 1 FROM record WHERE base = ? AND source = ? LIMIT 1", (base, path)).fetchone():
        return False
    prefix = path + "/"
    return connection.execute(
        "SELECT 1 FROM record WHERE base = ? AND substr(source, 1, length(?)) = ? LIMIT 1", (base, prefix, prefix)
    ).fetchone() is not None


def browse(handle: str | None = None, filters: Filters = NO_FILTERS) -> dict[str, Any]:
    """Bases, a base's source tree, one source's records by kind, or (with a kind filter) a record listing.

    ``handle`` is ``base``, ``base:dir/`` or ``base:path/file``; it splits on
    the first ':'. Only sources with indexed records appear.
    """
    connection, home = _session()
    try:
        base: str | None = None
        path = ""
        if handle is not None:
            base, _, path = handle.partition(":")
            if base not in home.bases:
                raise KnowledgeError(f"unknown base {base!r}")
        if filters.kind:
            result: dict[str, Any] = {"records": _browse_kind(connection, base, path, filters)}
        elif base is None:
            result = {"bases": _browse_bases(connection, home, filters)}
        elif path == "" or path.endswith("/") or _is_directory(connection, base, path):
            directory = path if path == "" or path.endswith("/") else path + "/"
            result = {"base": base, "dir": directory, "entries": _browse_directory(connection, base, directory, filters)}
        else:
            result = _browse_source(connection, base, path, filters)
        result["lag"] = lag(connection, home)
        return result
    finally:
        connection.close()


# ---------------------------------------------------------------- pack


def _passing(connection: sqlite3.Connection, uids: Sequence[str], filters: Filters) -> set[str]:
    """The ``uids`` that exist and pass ``filters``."""
    clause, parameters = filters.sql()
    return {
        row[0] for row in connection.execute(
            f"SELECT r.uid FROM record r WHERE r.uid IN (SELECT value FROM json_each(?)){clause}",
            [json.dumps(sorted(set(uids))), *parameters],
        )
    }


def pack(
    uids: Iterable[str],
    budget: int = PACK_BUDGET,
    requires_depth: int = 1,
    filters: Filters = NO_FILTERS,
) -> dict[str, Any]:
    """Whole records within ``budget`` UTF-8 bytes of compact JSON, with shared relations and typed gaps.

    The caller's uids come first, then their ``requires`` closure breadth-first
    up to ``requires_depth``, then each relation with at least two distinct
    packed role targets. Filters restrict only the records pack adds itself.
    """
    if budget < 1:
        raise KnowledgeError("budget must be at least 1")
    if requires_depth < 0:
        raise KnowledgeError("requires depth must not be negative")
    connection, home = _session()
    try:
        order, unknown = _start_uids(connection, uids)
        listed = set(order)
        layer = list(order)
        for _ in range(requires_depth):
            links = _links_out(connection, layer)
            targets = [
                link["dst"] for parent in layer for link in links[parent]
                if link["role"] == "requires" and link["dst"] is not None
            ]
            allowed = _passing(connection, targets, filters)
            layer = []
            for uid in targets:
                if uid in allowed and uid not in listed:
                    listed.add(uid)
                    layer.append(uid)
            order.extend(layer)
            if not layer:
                break

        packed: list[dict[str, Any]] = []
        packed_uids: set[str] = set()
        over: list[dict[str, Any]] = []
        size = len(compact_json([]).encode("utf-8"))
        out_links: dict[str, list[sqlite3.Row]] = {}
        labels: dict[str, str] = {}

        def add(uids_to_add: list[str]) -> None:
            nonlocal size
            rows = _rows(connection, uids_to_add)
            links = _links_out(connection, uids_to_add)
            labels.update(_labels(connection, (link["dst"] for items in links.values() for link in items if link["dst"])))
            for uid in uids_to_add:
                out_links[uid] = links[uid]
                record = _record(rows[uid], links[uid], labels)
                del record["search_terms"]
                grown = size + len(compact_json(record).encode("utf-8")) + (1 if packed else 0)
                if grown > budget:
                    over.append({"reason": "over-budget", "uid": uid})
                    continue
                packed.append(record)
                packed_uids.add(uid)
                size = grown

        add(order)
        clause, parameters = filters.sql()
        shared = [
            row[0] for row in connection.execute(
                f"SELECT l.src, COUNT(DISTINCT l.dst) AS packed FROM link l JOIN record r ON r.uid = l.src "
                f"WHERE r.class = 'relation' AND l.role != 'requires' AND l.dst IN (SELECT value FROM json_each(?)){clause} "
                f"GROUP BY l.src HAVING packed >= 2 ORDER BY packed DESC, l.src",
                [json.dumps(sorted(packed_uids)), *parameters],
            )
            if row[0] not in listed
        ]
        add(shared)

        gaps: list[dict[str, Any]] = [{"reason": "unknown-uid", "uid": argument} for argument in unknown] + over
        for record in packed:
            for link in out_links[record["uid"]]:
                where = {"from": record["uid"], "role": link["role"]}
                if link["dst"] is None:
                    gaps.append({"reason": "pending", **where, "term": link["term"]})
                elif link["dst"] not in labels:
                    gaps.append({"reason": "missing-target", **where, "uid": link["dst"]})
                elif link["dst"] not in packed_uids:
                    gaps.append({"reason": "not-packed", **where, "uid": link["dst"]})
        return {"records": packed, "bytes": size, "budget": budget, "gaps": gaps, "lag": lag(connection, home)}
    finally:
        connection.close()
