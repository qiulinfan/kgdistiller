"""The derived database ``$KGDISTILLER_HOME/index.sqlite`` and its only writer, ``kgd index``.

The database is a pure function of ``config.json`` and the ``entries/`` files of
the registered, available bases. ``index`` re-parses changed files by stat,
upserts rows so rowids stay stable, and recomputes every record's unified text
so label changes cascade without special logic. An incremental run produces
the same rows as ``--rebuild`` and as a build from a deleted database.

This slice has the lexical phase only: ``vec`` stays NULL and
``meta.embedding`` stays ``''``.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import unicodedata
from collections import defaultdict
from functools import cache
from itertools import pairwise
from pathlib import Path
from typing import Any

from .home import KNOWLEDGE_DIRECTORY, Home, KnowledgeError, home_directory, load_home
from .records import (
    ENTRIES,
    Record,
    RecordError,
    normalize_space,
    parse_text,
    read_text,
    symlinked_files,
)

DATABASE_FILENAME = "index.sqlite"
SCHEMA_VERSION = 1
BUSY_TIMEOUT_SECONDS = 30
SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE record(
  rowid         INTEGER PRIMARY KEY,
  uid           TEXT NOT NULL UNIQUE,
  base          TEXT NOT NULL,
  id            TEXT NOT NULL,
  class         TEXT NOT NULL CHECK (class IN ('node','relation')),
  kind          TEXT NOT NULL,
  label         TEXT NOT NULL,
  aliases       TEXT NOT NULL,
  source        TEXT NOT NULL,
  line_start    INTEGER NOT NULL,
  line_end      INTEGER NOT NULL,
  understanding TEXT NOT NULL,
  epistemic     TEXT,
  body          TEXT NOT NULL,
  search_terms  TEXT NOT NULL,
  evidence      TEXT NOT NULL,
  mtime_ns      INTEGER NOT NULL,
  size          INTEGER NOT NULL,
  text          TEXT NOT NULL,
  vec           BLOB
);
CREATE INDEX record_source ON record(base, source, line_start);
CREATE INDEX record_kind   ON record(kind);

CREATE TABLE link(
  src      TEXT NOT NULL,
  role     TEXT NOT NULL,
  pos      INTEGER NOT NULL,
  dst      TEXT,
  term     TEXT,
  term_key TEXT,
  CHECK ((dst IS NULL) <> (term IS NULL)),
  PRIMARY KEY (src, role, pos)
) WITHOUT ROWID;
CREATE INDEX link_dst  ON link(dst);
CREATE INDEX link_term ON link(term_key);

CREATE TABLE name(
  key TEXT NOT NULL, uid TEXT NOT NULL, is_label INTEGER NOT NULL,
  PRIMARY KEY (key, uid)
) WITHOUT ROWID;

CREATE VIRTUAL TABLE fts USING fts5(tokens, tokenize = 'unicode61 remove_diacritics 0');

INSERT INTO meta(key, value) VALUES ('embedding', '');
PRAGMA user_version = 1;
"""
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_RECORD_COLUMNS = (
    "uid", "base", "id", "class", "kind", "label", "aliases", "source", "line_start", "line_end",
    "understanding", "epistemic", "body", "search_terms", "evidence", "mtime_ns", "size",
)
_UPSERT = (
    f"INSERT INTO record({', '.join(_RECORD_COLUMNS)}, text) "
    f"VALUES ({', '.join('?' for _ in _RECORD_COLUMNS)}, '') ON CONFLICT(uid) DO UPDATE SET "
    + ", ".join(f"{column} = excluded.{column}" for column in _RECORD_COLUMNS[1:])
)


# ---------------------------------------------------------------- tokens


def is_cjk(char: str) -> bool:
    return unicodedata.name(char, "").startswith(("CJK UNIFIED IDEOGRAPH", "CJK COMPATIBILITY IDEOGRAPH"))


def _run_tokens(run: str, cjk: bool) -> list[str]:
    if not cjk:
        return [run]
    return list(run) + [a + b for a, b in pairwise(run)]


def tokens(text: str) -> list[str]:
    """Return NFKC/casefolded Unicode words, with CJK runs as unigrams and bigrams.

    No domain lexicon is used: a CJK run yields every character and every
    adjacent character pair, so a query for a sub-word matches longer terms.
    """
    result: list[str] = []
    for word in _WORD.findall(unicodedata.normalize("NFKC", text).casefold()):
        run = ""
        previous_cjk: bool | None = None
        for char in word:
            cjk = is_cjk(char)
            if run and cjk != previous_cjk:
                result.extend(_run_tokens(run, bool(previous_cjk)))
                run = ""
            run += char
            previous_cjk = cjk
        result.extend(_run_tokens(run, bool(previous_cjk)))
    return result


def name_key(text: str) -> str:
    return " ".join(_WORD.findall(unicodedata.normalize("NFKC", text).casefold()))


# ---------------------------------------------------------------- opening


def database_path() -> Path:
    return home_directory() / DATABASE_FILENAME


@cache
def _fts5_error() -> str | None:
    probe = sqlite3.connect(":memory:")
    try:
        probe.execute("CREATE VIRTUAL TABLE probe USING fts5(tokens)")
    except sqlite3.OperationalError as error:
        return str(error)
    finally:
        probe.close()
    return None


def require_fts5() -> None:
    error = _fts5_error()
    if error is not None:
        raise KnowledgeError(
            f"this Python's SQLite {sqlite3.sqlite_version} has no FTS5 ({error}); "
            "use a Python whose sqlite3 module includes FTS5"
        )


def _remove_database(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def _usable(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    try:
        connection = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
    except sqlite3.Error:
        return None
    try:
        if connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION:
            connection.execute("SELECT value FROM meta WHERE key = 'embedding'").fetchone()
            return connection
    except sqlite3.Error:
        pass
    connection.close()
    return None


def open_for_write() -> tuple[sqlite3.Connection, bool]:
    """Open the database for ``index``; recreate it when missing, unopenable or of another version."""
    require_fts5()
    path = database_path()
    connection = _usable(path)
    if connection is not None:
        return connection, False
    _remove_database(path)
    connection = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
    connection.executescript(SCHEMA)
    return connection, True


def open_read_only() -> sqlite3.Connection:
    """Open the database read-only; readers never create, migrate or write it."""
    require_fts5()
    path = database_path()
    if not path.is_file():
        raise KnowledgeError(f"no index at {path}; run `kgd index`")
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=BUSY_TIMEOUT_SECONDS)
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.Error as error:
        raise KnowledgeError(f"cannot read the index at {path} ({error}); run `kgd index`") from error
    if version != SCHEMA_VERSION:
        connection.close()
        raise KnowledgeError(f"the index at {path} has another schema version; run `kgd index`")
    return connection


# ---------------------------------------------------------------- text


def unified_text(
    row: sqlite3.Row | dict[str, Any],
    values: list[tuple[str, str | None, str | None]],
    labels: dict[str, str],
) -> str:
    """The one text per record used by FTS (and later embedding); §6.3."""
    lines = [row["label"]]
    aliases = json.loads(row["aliases"])
    if aliases:
        lines.append("; ".join(aliases))
    lines.append(row["kind"] + (f" · {row['epistemic']}" if row["epistemic"] else ""))
    if row["body"]:
        lines.append(row["body"])
    if row["search_terms"]:
        lines.append(row["search_terms"])
    roles: dict[str, list[str]] = defaultdict(list)
    for role, destination, term in values:
        roles[role].append(term if destination is None else labels.get(destination, destination))
    for role in sorted(name for name in roles if name != "requires"):
        lines.append(f"{role}: " + "; ".join(roles[role]))
    if "requires" in roles:
        lines.append("requires: " + "; ".join(roles["requires"]))
    lines.extend("> " + normalize_space(quote) for quote in json.loads(row["evidence"]))
    lines.append(row["source"])
    return "\n".join(lines)


# ---------------------------------------------------------------- index


def _scan(root: Path) -> dict[str, os.stat_result]:
    """``entries/*.md`` of one base: id -> stat."""
    directory = root / KNOWLEDGE_DIRECTORY / ENTRIES
    found: dict[str, os.stat_result] = {}
    if directory.is_symlink() or not directory.is_dir():
        return found
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.name.endswith(".md") and entry.is_file(follow_symlinks=False):
                found[entry.name[:-3]] = entry.stat(follow_symlinks=False)
    return found


def _available(home: Home) -> tuple[list[str], list[str]]:
    available, unavailable = [], []
    for name in sorted(home.bases):
        root = home.bases[name].root
        if root.is_dir():
            for tree in (root / KNOWLEDGE_DIRECTORY, root / KNOWLEDGE_DIRECTORY / ENTRIES):
                if tree.is_symlink():
                    raise KnowledgeError(f"{tree} must not be a symlink")
            available.append(name)
        else:
            unavailable.append(name)
    return available, unavailable


def _drop(connection: sqlite3.Connection, uid: str, rowid: int) -> None:
    connection.execute("DELETE FROM record WHERE uid = ?", (uid,))
    connection.execute("DELETE FROM fts WHERE rowid = ?", (rowid,))
    connection.execute("DELETE FROM link WHERE src = ?", (uid,))
    connection.execute("DELETE FROM name WHERE uid = ?", (uid,))


def _store(connection: sqlite3.Connection, record: Record, stat: os.stat_result) -> None:
    connection.execute(_UPSERT, (
        record.uid, record.base, record.id, record.class_, record.kind, record.label,
        json.dumps(list(record.aliases), ensure_ascii=False), record.source, record.lines[0], record.lines[1],
        record.understanding, record.epistemic, record.body, record.search_terms,
        json.dumps(list(record.evidence), ensure_ascii=False), stat.st_mtime_ns, stat.st_size,
    ))
    connection.execute("DELETE FROM link WHERE src = ?", (record.uid,))
    connection.execute("DELETE FROM name WHERE uid = ?", (record.uid,))
    for role, position, value in record.values():
        if value.uid is not None:
            row = (record.uid, role, position, value.uid, None, None)
        else:
            row = (record.uid, role, position, None, value.term, name_key(value.term))
        connection.execute("INSERT INTO link(src, role, pos, dst, term, term_key) VALUES (?, ?, ?, ?, ?, ?)", row)
    names: dict[str, int] = {}
    for is_label, text in ((1, record.label), *((0, alias) for alias in record.aliases)):
        key = name_key(text)
        if key:
            names[key] = max(names.get(key, 0), is_label)
    connection.executemany(
        "INSERT INTO name(key, uid, is_label) VALUES (?, ?, ?)",
        [(key, record.uid, is_label) for key, is_label in names.items()],
    )


def _refresh_texts(connection: sqlite3.Connection) -> None:
    labels = dict(connection.execute("SELECT uid, label FROM record"))
    values: dict[str, list[tuple[str, str | None, str | None]]] = defaultdict(list)
    for source, role, destination, term in connection.execute(
        "SELECT src, role, dst, term FROM link ORDER BY src, role, pos"
    ):
        values[source].append((role, destination, term))
    rows = connection.execute(
        "SELECT rowid, uid, label, aliases, kind, epistemic, body, search_terms, evidence, source, text FROM record"
    ).fetchall()
    for row in rows:
        text = unified_text(row, values.get(row["uid"], []), labels)
        if text == row["text"]:
            continue
        connection.execute("UPDATE record SET text = ?, vec = NULL WHERE rowid = ?", (text, row["rowid"]))
        connection.execute("DELETE FROM fts WHERE rowid = ?", (row["rowid"],))
        connection.execute("INSERT INTO fts(rowid, tokens) VALUES (?, ?)", (row["rowid"], " ".join(tokens(text))))


def index(rebuild: bool = False) -> dict[str, Any]:
    """Bring the database up to date with the files in one ``BEGIN IMMEDIATE`` transaction.

    The report has the §7.4 shape. A run is clean when no file was unparseable
    and every base was available.
    """
    home = load_home(home_directory(), types=False)
    available, unavailable = _available(home)
    connection, created = open_for_write()
    connection.row_factory = sqlite3.Row
    report: dict[str, Any] = {
        "created": created,
        "rebuild": rebuild,
        "bases": {},
        "unavailable": unavailable,
        "understanding_changed": [],
        "reused": 0,
        "embedded": 0,
        "unembedded": 0,
        "truncated": [],
    }
    try:
        connection.execute("BEGIN IMMEDIATE")
        if rebuild:
            for table in ("record", "link", "name", "fts"):
                connection.execute(f"DELETE FROM {table}")
        known = {
            row["uid"]: row
            for row in connection.execute("SELECT rowid, uid, base, mtime_ns, size, understanding FROM record")
        }
        for uid, row in list(known.items()):
            if row["base"] not in home.bases:
                _drop(connection, uid, row["rowid"])
                del known[uid]
        for name in available:
            counts: dict[str, Any] = {"parsed": 0, "deleted": 0, "unparseable": []}
            report["bases"][name] = counts
            directory = home.bases[name].root / KNOWLEDGE_DIRECTORY / ENTRIES
            files = _scan(home.bases[name].root)
            for path in symlinked_files(home.bases[name].root, ENTRIES):
                counts["unparseable"].append({"path": str(path), "message": "a record file must be a regular file, not a symlink"})
            for identifier in sorted(files):
                stat = files[identifier]
                uid = f"{name}:{identifier}"
                old = known.get(uid)
                if old is not None and (old["mtime_ns"], old["size"]) == (stat.st_mtime_ns, stat.st_size):
                    continue
                path = directory / f"{identifier}.md"
                try:
                    record = parse_text(read_text(path), name, identifier, path)
                except (RecordError, OSError, UnicodeDecodeError) as error:
                    message = error.message if isinstance(error, RecordError) else str(error)
                    counts["unparseable"].append({"path": str(path), "message": message})
                    if old is not None:
                        _drop(connection, uid, old["rowid"])
                        del known[uid]
                    continue
                if old is not None and old["understanding"] != record.understanding:
                    report["understanding_changed"].append(
                        {"uid": uid, "from": old["understanding"], "to": record.understanding}
                    )
                _store(connection, record, stat)
                counts["parsed"] += 1
            for uid, row in list(known.items()):
                if row["base"] == name and uid.split(":", 1)[1] not in files:
                    _drop(connection, uid, row["rowid"])
                    del known[uid]
                    counts["deleted"] += 1
        _refresh_texts(connection)
        if home.embedding is not None:
            report["unembedded"] = connection.execute("SELECT count(*) FROM record").fetchone()[0]
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
    return report


def index_clean(report: dict[str, Any]) -> bool:
    return not report["unavailable"] and not any(counts["unparseable"] for counts in report["bases"].values())


# ---------------------------------------------------------------- lag


def _stored_stats(connection: sqlite3.Connection | None) -> dict[str, dict[str, tuple[int, int]]]:
    stored: dict[str, dict[str, tuple[int, int]]] = defaultdict(dict)
    if connection is not None:
        for base, identifier, mtime_ns, size in connection.execute("SELECT base, id, mtime_ns, size FROM record"):
            stored[base][identifier] = (mtime_ns, size)
    return stored


def _changed(files: dict[str, os.stat_result], rows: dict[str, tuple[int, int]]) -> int:
    changed = sum(1 for identifier in rows if identifier not in files)
    for identifier, stat in files.items():
        if rows.get(identifier) != (stat.st_mtime_ns, stat.st_size):
            changed += 1
    return changed


def _meta_embedding(connection: sqlite3.Connection | None) -> str:
    if connection is None:
        return ""
    return connection.execute("SELECT value FROM meta WHERE key = 'embedding'").fetchone()[0]


def _unembedded(connection: sqlite3.Connection | None, base: str | None = None) -> int:
    if connection is None or not _meta_embedding(connection):
        return 0
    if base is None:
        return connection.execute("SELECT count(*) FROM record WHERE vec IS NULL").fetchone()[0]
    return connection.execute("SELECT count(*) FROM record WHERE vec IS NULL AND base = ?", (base,)).fetchone()[0]


def lag(connection: sqlite3.Connection, home: Home) -> dict[str, Any]:
    """How far the database is behind the files; reads report it and never write."""
    stored = _stored_stats(connection)
    changed = sum(len(rows) for base, rows in stored.items() if base not in home.bases)
    unavailable = []
    for name in sorted(home.bases):
        root = home.bases[name].root
        if not root.is_dir():
            unavailable.append(name)
            continue
        changed += _changed(_scan(root), stored.get(name, {}))
    return {
        "changed_files": changed,
        "unavailable_bases": unavailable,
        "unembedded": _unembedded(connection),
        "embedding_changed": (home.embedding or "") != _meta_embedding(connection),
    }


def base_status(home: Home) -> dict[str, dict[str, Any]]:
    """Per registered base: indexed row count and that base's lag; a missing database counts as empty."""
    connection = open_read_only() if database_path().is_file() else None
    try:
        stored = _stored_stats(connection)
        meta = _meta_embedding(connection)
        status: dict[str, dict[str, Any]] = {}
        for name in sorted(home.bases):
            root = home.bases[name].root
            rows = stored.get(name, {})
            available = root.is_dir()
            status[name] = {
                "indexed": len(rows),
                "lag": {
                    "changed_files": _changed(_scan(root), rows) if available else 0,
                    "unavailable_bases": [] if available else [name],
                    "unembedded": _unembedded(connection, name),
                    "embedding_changed": (home.embedding or "") != meta,
                },
            }
        return status
    finally:
        if connection is not None:
            connection.close()
