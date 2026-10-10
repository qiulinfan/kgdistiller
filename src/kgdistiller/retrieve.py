"""Read primitives over the derived database: ``search``, ``resolve`` and ``get``.

Every primitive opens the database read-only, is global across bases unless
filtered, and reports ``lag``. ``search`` fuses a lexical lane (FTS5 over the
unified text) and a name lane (label and alias keys) with reciprocal rank
fusion; there are no boosts, intent rules or thresholds. Only ``get
--source-lines`` reads live source text.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from . import records
from .home import Home, KnowledgeError, home_directory, load_home
from .index import is_cjk, lag, name_key, open_read_only, tokens
from .records import UNDERSTANDING, fold, gloss

LANE_LIMIT = 200
RRF_K = 60
LINK_CAP = 12
NAME_RUN_LIMIT = 12
CLASSES = ("node", "relation")
LANES = ("lexical", "name")
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


def _session() -> tuple[sqlite3.Connection, Home]:
    home = load_home(home_directory(), types=False)
    connection = open_read_only()
    connection.row_factory = sqlite3.Row
    return connection, home


def _lines(row: sqlite3.Row) -> str:
    return f"{row['line_start']}-{row['line_end']}"


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


def _capped(items: list[Any]) -> tuple[list[Any], int]:
    return items[:LINK_CAP], max(0, len(items) - LINK_CAP)


def _links_out(connection: sqlite3.Connection, uids: Sequence[str]) -> dict[str, list[sqlite3.Row]]:
    out: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for start in range(0, len(uids), _CHUNK):
        chunk = list(uids[start : start + _CHUNK])
        for row in connection.execute(
            f"SELECT src, role, pos, dst, term FROM link WHERE src IN ({', '.join('?' for _ in chunk)}) "
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


def search(query: str, limit: int = 40, filters: Filters = NO_FILTERS) -> dict[str, Any]:
    """Fuse the lexical and name lanes with RRF (k=60); ties break by uid."""
    if limit < 1:
        raise KnowledgeError("limit must be at least 1")
    connection, home = _session()
    try:
        lanes = {"lexical": _lexical(connection, query, filters), "name": _name(connection, query, filters)}
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
                "ranks": {lane: ranks[uid].get(lane) for lane in LANES},
                "requires": requires,
                "participants": participants,
                "in": incoming,
                "truncated": {"requires": requires_dropped, "participants": dropped, "in": incoming_dropped},
            })
        return {"query": query, "lanes": list(LANES), "lag": lag(connection, home), "results": results}
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
        out_links = _links_out(connection, found)
        in_links = _links_in(connection, found)
        labels = _labels(connection, (link["dst"] for links in out_links.values() for link in links if link["dst"]))
        found_records = []
        for uid in found:
            row = connection.execute("SELECT * FROM record WHERE uid = ?", (uid,)).fetchone()
            out = []
            for link in out_links[uid]:
                if link["dst"] is None:
                    out.append({"role": link["role"], "pos": link["pos"], "term": link["term"]})
                else:
                    out.append({
                        "role": link["role"], "pos": link["pos"], "uid": link["dst"],
                        "label": labels.get(link["dst"]), "exists": link["dst"] in labels,
                    })
            record = {
                "uid": uid,
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
                "in": in_links[uid],
            }
            if source_lines is not None:
                record["source_text"] = _source_text(home, row, source_lines)
            found_records.append(record)
        return {"records": found_records, "missing": missing, "lag": lag(connection, home)}
    finally:
        connection.close()
