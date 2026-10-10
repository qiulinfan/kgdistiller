"""The derived database: schema, ``kgd index`` invariants, read-only opens, lag and tokens."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from kgdistiller import index as index_module
from kgdistiller.home import KnowledgeError, load_home
from kgdistiller.index import (
    base_status,
    database_path,
    index,
    index_clean,
    lag,
    name_key,
    open_read_only,
    tokens,
)
from tests.knowledge_fixture import FakeEncoder, fake_encoder, make_record_home

SOURCE = "notes/a.txt"
LINES = [
    "Title line",
    "A measure space is a triple (X, M, mu).",
    "A subspace is closed under addition.",
    "The sum of two subspaces is a subspace.",
    "测度论研究可测空间。",
]
SRC = Path(__file__).resolve().parents[1] / "src" / "kgdistiller"


def node(label: str, lines: str, *, kind: str = "definition", extra: str = "") -> str:
    return f"label: {label}\nkind: {kind}\nsource: {SOURCE}\nlines: {lines}\n{extra}".rstrip("\n")


def dump() -> dict[str, Any]:
    """Every derived row compared by invariant 1 of docs/retrieval.md, keyed by uid instead of rowid."""
    connection = open_read_only()
    try:
        columns = [
            "uid", "base", "id", "class", "kind", "label", "aliases", "source", "line_start", "line_end",
            "understanding", "epistemic", "body", "search_terms", "evidence", "text", "vec",
        ]
        return {
            "record": connection.execute(f"SELECT {', '.join(columns)} FROM record ORDER BY uid").fetchall(),
            "link": connection.execute("SELECT * FROM link ORDER BY src, role, pos").fetchall(),
            "name": connection.execute("SELECT * FROM name ORDER BY key, uid").fetchall(),
            "fts": connection.execute(
                "SELECT r.uid, f.tokens FROM fts f JOIN record r ON r.rowid = f.rowid ORDER BY r.uid"
            ).fetchall(),
            "fts_rows": connection.execute("SELECT count(*) FROM fts").fetchone()[0],
            "meta": connection.execute("SELECT * FROM meta").fetchall(),
        }
    finally:
        connection.close()


class TokenizeTest(unittest.TestCase):
    def test_ascii_words_are_normalized_unicode_words(self) -> None:
        self.assertEqual(["cross", "entropy", "σ", "algebra"], tokens("Ｃｒｏｓｓ-Entropy σ-algebra"))
        self.assertEqual(["l2", "space"], tokens("L2_space"))

    def test_cjk_runs_yield_unigrams_and_bigrams(self) -> None:
        self.assertEqual(["测", "度", "论", "测度", "度论"], tokens("测度论"))
        self.assertEqual(["borel", "测", "度", "测度"], tokens("Borel测度"))

    def test_sub_word_query_tokens_are_contained_in_longer_terms(self) -> None:
        document = set(tokens("测度论研究可测空间"))
        for query in ("测度", "度论", "可测"):
            with self.subTest(query=query):
                self.assertTrue(set(tokens(query)) <= document)

    def test_name_key(self) -> None:
        self.assertEqual("measure space", name_key("  Measure-Space! "))
        self.assertEqual("σ algebra", name_key("Σ-algebra"))
        self.assertEqual("测度 空间", name_key("测度 空间"))
        self.assertEqual("", name_key("∑ + ∏"))

    def test_name_key_fixture(self) -> None:
        # The Obsidian plugin's nameKey reads the same fixture, so both stay in step.
        rows = json.loads((Path(__file__).parent / "fixtures" / "name-key.json").read_text(encoding="utf-8"))
        self.assertTrue(rows)
        for row in rows:
            with self.subTest(text=row["input"]):
                self.assertEqual(row["key"], name_key(row["input"]))


class IndexTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.kb = make_record_home(self, bases=("kb", "notes"))
        for base in ("kb", "notes"):
            self.kb.write_source(SOURCE, "\n".join(LINES) + "\n", base=base)
        self.subspace = self.kb.write_record(
            "subspace", node("Subspace", "3", extra="aliases: [Linear subspace, 子空间]"),
            "A subspace is closed under addition.", ["A subspace is closed under addition."],
        )
        self.sum = self.kb.write_record(
            "sum-is-subspace",
            node("Sum is a subspace", "4", kind="implies",
                 extra='premise: ["[[subspace]]", "[[subspace]]"]\nconclusion: ["[[notes:measure]]", dimension]\n'
                       'requires: ["[[missing]]", vector space]\nepistemic: stated'),
            "The sum is a subspace.\n\n## Search terms\n\nwhy is a sum closed", ["The sum of two subspaces"],
        )
        self.measure = self.kb.write_record(
            "measure", node("Measure", "2"), "测度论研究可测空间。", ["A measure space"], base="notes",
        )

    def edit(self, path: Path, old: str, new: str) -> None:
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new), encoding="utf-8")
        self.bump(path)

    def bump(self, path: Path) -> None:
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


class SchemaTest(IndexTestCase):
    def test_fresh_database(self) -> None:
        report = index()
        self.assertTrue(report["created"])
        connection = sqlite3.connect(database_path())
        try:
            self.assertEqual(1, connection.execute("PRAGMA user_version").fetchone()[0])
            self.assertEqual("wal", connection.execute("PRAGMA journal_mode").fetchone()[0])
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'fts_%'")}
            self.assertEqual({"meta", "record", "link", "name", "fts"}, tables)
            self.assertEqual([("embedding", "")], connection.execute("SELECT * FROM meta").fetchall())
            self.assertEqual([None, None, None], [row[0] for row in connection.execute("SELECT vec FROM record")])
        finally:
            connection.close()
        self.assertEqual(self.kb.home / "index.sqlite", database_path())

    def test_report_shape(self) -> None:
        report = index()
        self.assertEqual(
            ["created", "rebuild", "bases", "unavailable", "understanding_changed", "reused", "embedded",
             "unembedded", "truncated"],
            list(report),
        )
        self.assertEqual({"kb": {"parsed": 2, "deleted": 0, "unparseable": []},
                          "notes": {"parsed": 1, "deleted": 0, "unparseable": []}}, report["bases"])
        self.assertEqual((0, 0, 0, []), (report["reused"], report["embedded"], report["unembedded"], report["truncated"]))
        self.assertTrue(index_clean(report))
        second = index()
        self.assertFalse(second["created"])
        self.assertEqual(0, second["bases"]["kb"]["parsed"])
        json.dumps(second)

    def test_unified_text_and_rows(self) -> None:
        index()
        connection = open_read_only()
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute("SELECT * FROM record WHERE uid = 'kb:sum-is-subspace'").fetchone()
            self.assertEqual(
                "Sum is a subspace\nimplies · stated\nThe sum is a subspace.\nwhy is a sum closed\n"
                "conclusion: Measure; dimension\npremise: Subspace; Subspace\nrequires: kb:missing; vector space\n"
                "> The sum of two subspaces\nnotes/a.txt",
                row["text"],
            )
            self.assertEqual(("relation", "unknown", 4, 4), (row["class"], row["understanding"], row["line_start"], row["line_end"]))
            links = connection.execute("SELECT role, pos, dst, term, term_key FROM link WHERE src = 'kb:sum-is-subspace' ORDER BY role, pos").fetchall()
            self.assertEqual(
                [("conclusion", 0, "notes:measure", None, None), ("conclusion", 1, None, "dimension", "dimension"),
                 ("premise", 0, "kb:subspace", None, None), ("premise", 1, "kb:subspace", None, None),
                 ("requires", 0, "kb:missing", None, None), ("requires", 1, None, "vector space", "vector space")],
                [tuple(link) for link in links],
            )
            names = connection.execute("SELECT key, is_label FROM name WHERE uid = 'kb:subspace' ORDER BY key").fetchall()
            self.assertEqual([("linear subspace", 0), ("subspace", 1), ("子空间", 0)], [tuple(name) for name in names])
        finally:
            connection.close()


class InvariantTest(IndexTestCase):
    def test_incremental_equals_rebuild_equals_fresh(self) -> None:
        index()
        self.edit(self.subspace, "label: Subspace", "label: Linear space part")
        self.edit(self.measure, "测度论研究可测空间。", "测度论研究可测空间。Measure text.")
        self.kb.write_record("new-record", node("New record", "1"), "New.", ["Title line"])
        self.kb.path("sum-is-subspace").rename(self.kb.path("sum-closed"))
        self.kb.write_record("broken", "label: Broken", "Text.", ["Title line"])
        incremental = index()
        self.assertFalse(index_clean(incremental))
        first = dump()
        index(rebuild=True)
        second = dump()
        os.remove(database_path())
        for suffix in ("-wal", "-shm"):
            Path(f"{database_path()}{suffix}").unlink(missing_ok=True)
        fresh = index()
        self.assertTrue(fresh["created"])
        third = dump()
        self.assertEqual(first, second)
        self.assertEqual(first, third)
        uids = [row[0] for row in first["record"]]
        self.assertEqual(["kb:new-record", "kb:subspace", "kb:sum-closed", "notes:measure"], uids)
        self.assertEqual(len(uids), first["fts_rows"])

    def test_incremental_equals_rebuild_equals_fresh_with_vectors(self) -> None:
        self.kb.embedding = "fake/model"
        self.kb.write_config()
        with fake_encoder():
            index()
            self.edit(self.subspace, "label: Subspace", "label: Linear space part")
            self.edit(self.measure, "测度论研究可测空间。", "测度论研究可测空间。Measure text.")
            self.kb.write_record("new-record", node("New record", "1"), "New.", ["Title line"])
            self.kb.path("sum-is-subspace").rename(self.kb.path("sum-closed"))
            self.kb.write_record("broken", "label: Broken", "Text.", ["Title line"])
            index()
            first = dump()
            index(rebuild=True)
            second = dump()
            for suffix in ("", "-wal", "-shm"):
                Path(f"{database_path()}{suffix}").unlink(missing_ok=True)
            self.assertTrue(index()["created"])
            third = dump()
        self.assertEqual(first, second)
        self.assertEqual(first, third)
        self.assertEqual([("embedding", "fake/model")], first["meta"])
        for row in first["record"]:
            self.assertEqual(FakeEncoder.vector(row[15]), row[16], row[0])

    def rowids(self) -> dict[str, int]:
        connection = open_read_only()
        try:
            return dict(connection.execute("SELECT uid, rowid FROM record").fetchall())
        finally:
            connection.close()

    def test_rowids_are_stable_across_updates(self) -> None:
        index()
        before = self.rowids()
        self.edit(self.subspace, "label: Subspace", "label: Subspace again")
        index()
        after = self.rowids()
        self.assertEqual(before, after)

    def test_label_change_cascades_into_linking_texts(self) -> None:
        index()
        self.edit(self.subspace, "label: Subspace", "label: Untergruppe")
        report = index()
        self.assertEqual(1, report["bases"]["kb"]["parsed"])
        connection = open_read_only()
        try:
            text = connection.execute("SELECT text FROM record WHERE uid = 'kb:sum-is-subspace'").fetchone()[0]
            self.assertIn("premise: Untergruppe; Untergruppe", text)
            hits = connection.execute(
                "SELECT r.uid FROM fts JOIN record r ON r.rowid = fts.rowid WHERE fts MATCH '\"untergruppe\"' ORDER BY r.uid"
            ).fetchall()
            self.assertEqual([("kb:subspace",), ("kb:sum-is-subspace",)], hits)
        finally:
            connection.close()

    def test_unparseable_file_keeps_no_row_until_fixed(self) -> None:
        index()
        self.edit(self.subspace, "label: Subspace", "label: [broken")
        report = index()
        self.assertEqual([str(self.subspace)], [item["path"] for item in report["bases"]["kb"]["unparseable"]])
        self.assertFalse(index_clean(report))
        uids = [row[0] for row in dump()["record"]]
        self.assertNotIn("kb:subspace", uids)
        again = index()
        self.assertEqual(1, len(again["bases"]["kb"]["unparseable"]))
        self.edit(self.subspace, "label: [broken", "label: Subspace")
        fixed = index()
        self.assertTrue(index_clean(fixed))
        self.assertIn("kb:subspace", [row[0] for row in dump()["record"]])

    def test_understanding_changes_are_reported(self) -> None:
        index()
        self.edit(self.subspace, "lines: 3", "lines: 3\nunderstanding: understood")
        self.assertEqual([{"uid": "kb:subspace", "from": "unknown", "to": "understood"}], index()["understanding_changed"])

    def test_deleted_files_and_unregistered_bases_lose_their_rows(self) -> None:
        index()
        self.kb.path("measure", base="notes").unlink()
        self.assertEqual(1, index()["bases"]["notes"]["deleted"])
        self.kb.write_record("measure", node("Measure", "2"), "Text.", ["A measure space"], base="notes")
        index()
        del self.kb.roots["notes"]
        self.kb.write_config()
        index()
        self.assertEqual(["kb:subspace", "kb:sum-is-subspace"], [row[0] for row in dump()["record"]])

    def test_unavailable_base_is_left_untouched(self) -> None:
        index()
        before = dump()
        moved = self.kb.home.parent / "notes-away"
        self.kb.roots["notes"].rename(moved)
        report = index()
        self.assertEqual(["notes"], report["unavailable"])
        self.assertNotIn("notes", report["bases"])
        self.assertFalse(index_clean(report))
        self.assertEqual(before, dump())
        moved.rename(self.kb.roots["notes"])

    def test_symlinked_knowledge_tree_is_refused(self) -> None:
        root = self.kb.roots["notes"]
        target = self.kb.home.parent / "elsewhere"
        (root / ".knowledge").rename(target)
        (root / ".knowledge").symlink_to(target, target_is_directory=True)
        with self.assertRaises(KnowledgeError):
            index()

    def test_symlinked_record_files_are_unparseable_and_entries_must_be_a_folder(self) -> None:
        index()
        outside = self.kb.home.parent / "outside.md"
        outside.write_text(self.subspace.read_text(encoding="utf-8"), encoding="utf-8")
        linked = self.kb.root / ".knowledge/entries/linked.md"
        linked.symlink_to(outside)
        report = index()
        self.assertEqual([str(linked)], [item["path"] for item in report["bases"]["kb"]["unparseable"]])
        self.assertFalse(index_clean(report))
        self.assertNotIn("kb:linked", [row[0] for row in dump()["record"]])
        entries = self.kb.roots["notes"] / ".knowledge/entries"
        target = self.kb.home.parent / "entries-elsewhere"
        entries.rename(target)
        entries.symlink_to(target, target_is_directory=True)
        with self.assertRaises(KnowledgeError):
            index()

    def test_no_module_imports_hashlib(self) -> None:
        pattern = re.compile(r"^\s*(import hashlib|from hashlib)", re.MULTILINE)
        offenders = [str(path) for path in SRC.rglob("*.py") if pattern.search(path.read_text(encoding="utf-8"))]
        self.assertEqual([], offenders)

    def test_no_pinned_commit_ids_or_revisions(self) -> None:
        pattern = re.compile(r"\b[0-9a-f]{40}\b|\brevision", re.IGNORECASE)
        offenders = [str(path) for path in SRC.rglob("*.py") if pattern.search(path.read_text(encoding="utf-8"))]
        self.assertEqual([], offenders)


class OpeningTest(IndexTestCase):
    def test_read_only_open_requires_an_index(self) -> None:
        with self.assertRaises(KnowledgeError) as caught:
            open_read_only()
        self.assertIn("run `kgd index`", str(caught.exception))
        self.assertFalse(database_path().exists())

    def test_read_only_open_never_writes(self) -> None:
        index()
        before = database_path().read_bytes()
        connection = open_read_only()
        try:
            connection.execute("SELECT count(*) FROM record").fetchone()
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM record")
        finally:
            connection.close()
        self.assertEqual(before, database_path().read_bytes())
        wal = Path(f"{database_path()}-wal")
        self.assertTrue(not wal.exists() or wal.stat().st_size == 0)

    def test_other_schema_version_is_recreated(self) -> None:
        index()
        connection = sqlite3.connect(database_path())
        connection.execute("PRAGMA user_version = 7")
        connection.close()
        with self.assertRaises(KnowledgeError):
            open_read_only()
        report = index()
        self.assertTrue(report["created"])
        self.assertEqual(3, len(dump()["record"]))

    def test_unreadable_file_is_recreated(self) -> None:
        database_path().parent.mkdir(parents=True, exist_ok=True)
        database_path().write_bytes(b"not a database at all" * 100)
        self.assertTrue(index()["created"])
        self.assertEqual(3, len(dump()["record"]))

    def test_missing_fts5_fails_fast(self) -> None:
        with patch.object(index_module, "_fts5_error", lambda: "no such module: fts5"):
            with self.assertRaises(KnowledgeError) as caught:
                index()
            self.assertIn("FTS5", str(caught.exception))
            with self.assertRaises(KnowledgeError):
                open_read_only()
        self.assertFalse(database_path().exists())


class LagTest(IndexTestCase):
    def lag(self) -> dict[str, Any]:
        connection = open_read_only()
        try:
            return lag(connection, load_home(self.kb.home))
        finally:
            connection.close()

    def test_lag_counts_changed_files_by_stat(self) -> None:
        index()
        self.assertEqual({"changed_files": 0, "unavailable_bases": [], "unembedded": 0, "embedding_changed": False}, self.lag())
        self.edit(self.subspace, "label: Subspace", "label: Subspace!")
        self.kb.write_record("added", node("Added", "1"), "A.", ["Title line"])
        self.kb.path("measure", base="notes").unlink()
        self.assertEqual(3, self.lag()["changed_files"])

    def test_unavailable_and_embedding_configured(self) -> None:
        self.kb.embedding = "example/model"
        self.kb.write_config()
        report = index(embed=False)
        self.assertEqual(3, report["unembedded"])
        self.assertEqual({"changed_files": 0, "unavailable_bases": [], "unembedded": 3, "embedding_changed": False},
                         self.lag())
        self.assertEqual([("embedding", "example/model")], dump()["meta"])
        shutil.move(self.kb.roots["notes"], self.kb.home.parent / "away")
        current = self.lag()
        self.assertEqual(["notes"], current["unavailable_bases"])
        self.assertFalse(current["embedding_changed"])
        self.assertEqual(3, current["unembedded"])

    def test_base_status_tolerates_a_missing_database(self) -> None:
        home = load_home(self.kb.home)
        status = base_status(home)
        self.assertEqual({"kb", "notes"}, set(status))
        self.assertEqual(0, status["kb"]["indexed"])
        self.assertEqual(2, status["kb"]["lag"]["changed_files"])
        self.assertFalse(database_path().exists())
        index()
        status = base_status(home)
        self.assertEqual((2, 0), (status["kb"]["indexed"], status["kb"]["lag"]["changed_files"]))
        self.assertEqual({"changed_files": 0, "unavailable_bases": [], "unembedded": 0, "embedding_changed": False},
                         status["notes"]["lag"])


class EmbeddingTest(IndexTestCase):
    MODEL = "fake/model"

    def setUp(self) -> None:
        super().setUp()
        self.kb.embedding = self.MODEL
        self.kb.write_config()

    def rows(self) -> dict[str, tuple[str, bytes | None]]:
        connection = open_read_only()
        try:
            return {uid: (text, vec) for uid, text, vec in connection.execute("SELECT uid, text, vec FROM record")}
        finally:
            connection.close()

    def assert_all_encoded(self) -> None:
        for uid, (text, vec) in self.rows().items():
            self.assertEqual(FakeEncoder.vector(text), vec, uid)

    def lag(self) -> dict[str, Any]:
        connection = open_read_only()
        try:
            return lag(connection, load_home(self.kb.home))
        finally:
            connection.close()

    def test_first_embed_then_nothing_to_do(self) -> None:
        with fake_encoder() as fake:
            report = index()
            self.assertEqual((0, 3, 0, []), (report["reused"], report["embedded"], report["unembedded"], report["truncated"]))
            self.assert_all_encoded()
            self.assertEqual([("embedding", self.MODEL)], dump()["meta"])
            self.assertEqual([self.MODEL], fake.loads)
            again = index()
            self.assertEqual([self.MODEL], fake.loads)
            self.assertEqual((0, 0, 0), (again["reused"], again["embedded"], again["unembedded"]))
        self.assertEqual({"changed_files": 0, "unavailable_bases": [], "unembedded": 0, "embedding_changed": False},
                         self.lag())

    def test_text_change_without_embedding_leaves_that_row_null(self) -> None:
        with fake_encoder():
            index()
            self.edit(self.measure, "测度论研究可测空间。", "Another body.")
            report = index(embed=False)
        self.assertEqual((0, 0, 1), (report["reused"], report["embedded"], report["unembedded"]))
        nulls = [uid for uid, (_, vec) in self.rows().items() if vec is None]
        self.assertEqual(["notes:measure"], nulls)
        self.assertEqual(1, self.lag()["unembedded"])

    def test_id_rename_reuses_its_vector(self) -> None:
        with fake_encoder():
            index()
            before = self.rows()["kb:sum-is-subspace"]
            self.kb.path("sum-is-subspace").rename(self.kb.path("sum-closed"))
            report = index()
        self.assertEqual((1, 0, 0), (report["reused"], report["embedded"], report["unembedded"]))
        self.assertEqual(before, self.rows()["kb:sum-closed"])

    def test_model_change_re_embeds_everything(self) -> None:
        with fake_encoder() as fake:
            index()
            self.kb.embedding = "other/model"
            self.kb.write_config()
            self.assertTrue(self.lag()["embedding_changed"])
            report = index()
            self.assertEqual((0, 3, 0), (report["reused"], report["embedded"], report["unembedded"]))
            self.assertEqual([self.MODEL, "other/model"], fake.loads)
        self.assertEqual([("embedding", "other/model")], dump()["meta"])
        self.assert_all_encoded()

    def test_stale_write_is_a_no_op(self) -> None:
        tampered: list[str] = []

        def change_text(texts: list[str]) -> None:
            if tampered:
                return
            tampered.append("kb:subspace")
            other = sqlite3.connect(database_path())
            try:
                other.execute("UPDATE record SET text = 'changed meanwhile' WHERE uid = 'kb:subspace'")
                other.commit()
            finally:
                other.close()

        with fake_encoder(on_encode=change_text):
            report = index()
            self.assertEqual((2, 1), (report["embedded"], report["unembedded"]))
            self.assertEqual(("changed meanwhile", None), self.rows()["kb:subspace"])
            again = index()
        self.assertEqual((1, 0), (again["embedded"], again["unembedded"]))
        self.assertTrue(self.rows()["kb:subspace"][0].startswith("Subspace\n"))
        self.assert_all_encoded()

    def test_rebuild_reuses_every_vector(self) -> None:
        with fake_encoder() as fake:
            index()
            before = dump()
            report = index(rebuild=True)
            self.assertEqual([self.MODEL], fake.loads)
        self.assertEqual((3, 0, 0), (report["reused"], report["embedded"], report["unembedded"]))
        self.assertEqual(before, dump())

    def test_batches_of_64_by_rowid(self) -> None:
        for number in range(70):
            self.kb.write_record(f"extra-{number:02d}", node(f"Extra {number}", "1"), f"Body {number}.", ["Title line"])
        with fake_encoder() as fake:
            report = index()
            self.assertEqual([64, 9], fake.encoders[self.MODEL].calls)
        self.assertEqual(73, report["embedded"])
        self.assert_all_encoded()

    def test_over_limit_texts_are_reported_and_still_embedded(self) -> None:
        index(embed=False)
        lengths = sorted((len(text), uid) for uid, (text, _) in self.rows().items())
        with fake_encoder(max_chars=lengths[-2][0]):
            report = index()
        self.assertEqual([lengths[-1][1]], report["truncated"])
        self.assertEqual(3, report["embedded"])
        self.assert_all_encoded()

    def test_missing_retrieval_extra_keeps_the_lexical_commit(self) -> None:
        resident = patch("kgdistiller.adapters.sentence_transformers._resident", None)
        resident.start()
        self.addCleanup(resident.stop)
        with patch.dict(sys.modules, {"sentence_transformers": None}), self.assertRaises(KnowledgeError) as caught:
            index()
        self.assertIn("install kgdistiller[retrieval] or set embedding to null", str(caught.exception))
        self.assertIn(self.MODEL, str(caught.exception))
        self.assertEqual({"kb:subspace", "kb:sum-is-subspace", "notes:measure"}, set(self.rows()))
        self.assertEqual(3, self.lag()["unembedded"])


if __name__ == "__main__":
    unittest.main()
