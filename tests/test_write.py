"""The write path: ``drafts``, ``accept`` of new and replacing drafts, and the ``source`` profile."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from kgdistiller import write
from kgdistiller.home import KnowledgeError, LockConflict, lock
from kgdistiller.index import index, open_read_only
from kgdistiller.write import accept, drafts, source
from tests.knowledge_fixture import fake_encoder, make_record_home

SOURCE = "notes/a.txt"
LINES = [
    "Title line",
    "A measure space is a triple (X, M, mu).",
    "It extends a measurable space.",
    "A subspace is closed under addition.",
    "The sum of two subspaces is a subspace.",
    "Unique sentence about dimension.",
]


def without_index(receipt: dict[str, Any]) -> dict[str, Any]:
    """An accept receipt without the report of the index run that follows it."""
    assert "index" in receipt, receipt
    return {key: value for key, value in receipt.items() if key != "index"}


def node(label: str, lines: str, *, kind: str = "definition", extra: str = "") -> str:
    return f"label: {label}\nkind: {kind}\nsource: {SOURCE}\nlines: {lines}\n{extra}".rstrip("\n")


class WriteTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.kb = make_record_home(self, bases=("kb", "notes"))
        self.kb.write_source(SOURCE, "\n".join(LINES) + "\n")
        self.kb.write_record(
            "subspace", node("Subspace", "4", extra="understanding: no"),
            "A subspace is closed under addition. More text.", ["A subspace is closed under addition."],
        )

    def draft(self, identifier: str, frontmatter: str, quote: str = "Unique sentence about dimension.", body: str = "Text.") -> Path:
        return self.kb.write_record(identifier, frontmatter, body, [quote], folder="drafts")

    def snapshot(self) -> dict[str, bytes]:
        knowledge = self.kb.root / ".knowledge"
        return {str(path.relative_to(knowledge)): path.read_bytes() for path in sorted(knowledge.rglob("*.md"))}


class AcceptTest(WriteTestCase):
    def test_a_draft_named_like_a_record_is_refused_until_it_takes_its_source_name(self) -> None:
        draft = self.draft("subspace-2", node("subspace", "6"))
        refused = accept([str(draft)])
        self.assertIn("is already the label of kb:subspace", refused["refused"][0]["message"])
        self.assertIn("as in 'subspace (a)'", refused["refused"][0]["message"])
        renamed = self.draft("subspace-a", node("subspace (a)", "6"))
        draft.unlink()
        self.assertEqual({"created": ["kb:subspace-a"], "updated": []}, without_index(accept([str(renamed)])))

    def test_accept_moves_the_draft_into_entries(self) -> None:
        draft = self.draft("dimension", node("Dimension", "6", extra="understanding: no"))
        content = draft.read_bytes()
        receipt = accept([str(draft)])
        self.assertEqual({"created": ["kb:dimension"], "updated": []}, without_index(receipt))
        self.assertFalse(draft.exists())
        self.assertEqual(content, self.kb.path("dimension").read_bytes())

    def test_dry_run_writes_nothing(self) -> None:
        draft = self.draft("dimension", node("Dimension", "6"))
        before = self.snapshot()
        self.assertEqual({"would_create": ["kb:dimension"], "would_update": []}, accept([draft], dry_run=True))
        self.assertEqual(before, self.snapshot())

    def test_a_foreign_draft_is_accepted_without_freshness(self) -> None:
        draft = self.kb.write_record(
            "attention", "label: Attention\nkind: concept\nsource: https://example.com/paper\nrequires: [\"[[subspace]]\"]",
            "Attention.", ["Quoted from the paper, not from a local file."], folder="drafts",
        )
        self.assertEqual({"created": ["kb:attention"], "updated": []}, without_index(accept([draft])))
        self.assertTrue(self.kb.path("attention").is_file())
        lined = self.kb.write_record(
            "lined", "label: Lined\nkind: concept\nsource: https://example.com/paper\nlines: 2", "L.", ["Q."],
            folder="drafts",
        )
        self.assertIn("has no lines", accept([lined])["refused"][0]["message"])

    def test_a_plain_value_is_refused(self) -> None:
        draft = self.draft("dimension", node("Dimension", "6", extra="requires: [vector space]"))
        self.assertIn("'vector space' is not a link", accept([draft])["refused"][0]["message"])

    def test_any_refusal_writes_nothing(self) -> None:
        good = self.draft("dimension", node("Dimension", "6"))
        bad = self.draft("stale", node("Stale", "1"), quote="Not in the source.")
        before = self.snapshot()
        receipt = accept([good, bad])
        self.assertEqual(["refused"], list(receipt))
        self.assertEqual([str(bad)], [item["path"] for item in receipt["refused"]])
        self.assertIn("evidence is stale", receipt["refused"][0]["message"])
        self.assertEqual(before, self.snapshot())

    def test_moved_evidence_is_refused_with_its_range(self) -> None:
        draft = self.draft("moved", node("Moved", "1"))
        self.assertIn("found at lines 6-6", accept([draft])["refused"][0]["message"])

    def test_linked_drafts_must_be_selected_together(self) -> None:
        first = self.draft("first", node("First", "6", kind="implies", extra='premise: ["[[second]]"]'))
        second = self.draft("second", node("Second", "6"))
        receipt = accept([first])
        self.assertEqual([{"path": str(first), "message": "premise[0]: select [[second]] too"}], receipt["refused"])
        self.assertEqual(["kb:first", "kb:second"], accept([first, second])["created"])
        self.assertEqual("", "".join(path.name for path in (self.kb.root / ".knowledge/drafts").iterdir()))
        self.assertFalse(second.exists())

    def test_validation_follows_check_rules(self) -> None:
        cases = {
            "dangling": (node("D", "6", kind="implies", extra='premise: ["[[nowhere]]"]'), "dangling link"),
            "bad-kind": (node("K", "6", kind="lemma"), "not a node or relation kind"),
            "foreign-draft": (node("F", "6", kind="implies", extra='premise: ["[[notes:x]]"]'), "links a draft of base notes"),
        }
        self.kb.write_record("x", node("X", "1"), "X.", ["Title line"], folder="drafts", base="notes")
        self.kb.write_source(SOURCE, "\n".join(LINES) + "\n", base="notes")
        for identifier, (frontmatter, fragment) in cases.items():
            with self.subTest(identifier):
                path = self.draft(identifier, frontmatter)
                self.assertIn(fragment, accept([path])["refused"][0]["message"])

    def test_paths_must_be_drafts_of_a_registered_base(self) -> None:
        outside = self.kb.home.parent / "outside.md"
        outside.write_text("x", encoding="utf-8")
        cases = {
            str(self.kb.path("subspace")): "is not a .knowledge/drafts/<id>.md file",
            str(outside): "is not inside a registered base",
            str(self.kb.path("absent", "drafts")): "does not exist",
        }
        for path, fragment in cases.items():
            with self.subTest(path=path):
                self.assertIn(fragment, accept([path])["refused"][0]["message"])

    def test_identical_entry_finishes_an_interrupted_accept(self) -> None:
        draft = self.draft("dimension", node("Dimension", "6"))
        os.link(draft, self.kb.path("dimension"))
        self.assertEqual({"would_create": ["kb:dimension"], "would_update": []}, accept([draft], dry_run=True))
        self.assertEqual({"created": ["kb:dimension"], "updated": []}, without_index(accept([draft])))
        self.assertFalse(draft.exists())
        self.assertTrue(self.kb.path("dimension").exists())

    def test_accept_indexes_and_embeds_only_the_records_it_creates(self) -> None:
        self.kb.embedding = "fake-model"
        self.kb.write_config()
        with fake_encoder():
            index()
            self.kb.write_record(
                "subspace", node("Subspace", "4", extra="understanding: no"),
                "An edited body.", ["A subspace is closed under addition."],
            )
            receipt = accept([self.draft("dimension", node("Dimension", "6"))])
        self.assertEqual((1, 1), (receipt["index"]["embedded"], receipt["index"]["unembedded"]))
        connection = open_read_only()
        try:
            vectors = dict(connection.execute("SELECT uid, vec IS NOT NULL FROM record"))
        finally:
            connection.close()
        self.assertEqual({"kb:dimension": 1, "kb:subspace": 0}, vectors)

    def test_never_overwrites_a_target_that_appears(self) -> None:
        first = self.draft("first", node("First", "6"))
        second = self.draft("second", node("Second", "6"))
        real_link = os.link

        def racing_link(source: Any, target: Any) -> None:
            if Path(target).name == "second.md":
                Path(target).write_text("someone else's record\n", encoding="utf-8")
            real_link(source, target)

        with patch.object(write.os, "link", racing_link):
            receipt = accept([first, second])
        self.assertEqual(["kb:first"], receipt["created"])
        self.assertIn("appeared with different text", receipt["aborted"]["message"])
        self.assertEqual("someone else's record\n", self.kb.path("second").read_text(encoding="utf-8"))
        self.assertTrue(second.exists())
        self.assertFalse(first.exists())

    def test_holds_the_home_lock(self) -> None:
        draft = self.draft("dimension", node("Dimension", "6"))
        with lock(), self.assertRaises(LockConflict):
            accept([draft])
        self.assertTrue(draft.exists())


class DraftsTest(WriteTestCase):
    def test_lists_a_draft_with_its_fields_values_and_acceptance(self) -> None:
        path = self.draft("dimension", node("Dimension", "6", extra='aliases: [维数]\nrequires: ["[[subspace]]", "[[notes:rank]]"]'))
        self.assertEqual({"drafts": [{
            "uid": "kb:dimension", "base": "kb", "id": "dimension", "path": str(path),
            "replaces": None, "foreign": False,
            "class": "node", "kind": "definition", "label": "Dimension", "aliases": ["维数"],
            "source": SOURCE, "lines": "6-6", "understanding": "yes", "epistemic": None,
            "gloss": "Text.", "body": "Text.", "search_terms": "", "evidence": ["Unique sentence about dimension."],
            "out": [
                {"role": "requires", "pos": 0, "uid": "kb:subspace", "state": "accepted", "label": "Subspace",
                 "understanding": "no"},
                {"role": "requires", "pos": 1, "uid": "notes:rank", "state": "missing", "label": None},
            ],
            "with": [], "linked_by": [], "problems": ["requires[1]: dangling link [[notes:rank]]"],
        }], "unavailable_bases": []}, drafts())

    def test_linked_drafts_name_what_they_are_accepted_with(self) -> None:
        first = self.draft("first", node("First", "6", kind="implies", extra='premise: ["[[second]]"]'))
        second = self.draft("second", node("Second", "6", kind="implies", extra='premise: ["[[third]]"]'))
        third = self.draft("third", node("Third", "6"))
        listed = {item["id"]: item for item in drafts()["drafts"]}
        self.assertEqual((["kb:second", "kb:third"], [], []), (listed["first"]["with"], listed["first"]["linked_by"], listed["first"]["problems"]))
        self.assertEqual((["kb:third"], ["kb:first"]), (listed["second"]["with"], listed["second"]["linked_by"]))
        self.assertEqual(([], ["kb:second"]), (listed["third"]["with"], listed["third"]["linked_by"]))
        self.assertEqual({"role": "premise", "pos": 0, "uid": "kb:second", "state": "draft", "label": "Second"}, listed["first"]["out"][0])
        self.assertEqual(
            {"would_create": ["kb:first", "kb:second", "kb:third"], "would_update": []},
            accept([first, second, third], dry_run=True),
        )

    def test_problems_are_what_accept_refuses(self) -> None:
        self.draft("stale", node("Stale", "1"), quote="Not in the source.")
        self.draft("dangling", node("Dangling", "6", kind="implies", extra='premise: ["[[nowhere]]"]'))
        taken = self.draft("taken", node("Taken", "6"))
        entry = self.kb.write_record("taken", node("Taken", "6"), "Other.", ["Unique sentence about dimension."])
        later = taken.stat().st_mtime_ns + 1_000_000_000
        os.utime(entry, ns=(later, later))
        listed = {item["id"]: item for item in drafts()["drafts"]}
        for item in listed.values():
            with self.subTest(item["id"]):
                refused = accept([item["path"]], dry_run=True)["refused"]
                self.assertEqual([entry["message"] for entry in refused], item["problems"])
        self.assertIn("evidence is stale", listed["stale"]["problems"][0])
        self.assertIn("changed after this replacement was drafted", listed["taken"]["problems"][0])
        self.assertEqual({"role": "premise", "pos": 0, "uid": "kb:nowhere", "state": "missing", "label": None},
                         listed["dangling"]["out"][0])

    def test_an_unreadable_draft_is_listed_with_its_problem_unless_the_listing_is_narrowed(self) -> None:
        (self.kb.root / ".knowledge/drafts").mkdir(parents=True, exist_ok=True)
        (self.kb.root / ".knowledge/drafts/broken.md").write_text("---\nlabel: [x\n---\nText.\n", encoding="utf-8")
        [item] = drafts()["drafts"]
        self.assertEqual(("kb:broken", None, []), (item["uid"], item["label"], item["with"]))
        self.assertIn("invalid YAML", item["problems"][0])
        self.assertEqual([], drafts(sources=["notes"])["drafts"])
        self.assertEqual([], drafts(names=["broken"])["drafts"])

    def test_narrows_by_base_source_and_name_and_writes_nothing(self) -> None:
        self.draft("dimension", node("Dimension", "6", extra="aliases: [维数]"))
        self.kb.write_source(SOURCE, "\n".join(LINES) + "\n", base="notes")
        self.kb.write_record("rank", node("Rank", "6"), "R.", ["Unique sentence about dimension."], folder="drafts", base="notes")
        before = self.snapshot()
        uids = lambda listed: [item["uid"] for item in listed["drafts"]]
        self.assertEqual(["kb:dimension", "notes:rank"], uids(drafts()))
        self.assertEqual(["notes:rank"], uids(drafts(bases=["notes"])))
        self.assertEqual(["kb:dimension"], uids(drafts(names=["维数"])))
        self.assertEqual([], uids(drafts(names=["length"])))
        self.assertEqual([], uids(drafts(sources=["notes/b"])))
        self.assertEqual(["kb:dimension", "notes:rank"], uids(drafts(sources=["notes/a"])))
        self.assertEqual(before, self.snapshot())

    def test_takes_no_lock(self) -> None:
        self.draft("dimension", node("Dimension", "6"))
        with lock():
            self.assertEqual(["kb:dimension"], [item["uid"] for item in drafts()["drafts"]])


class UpdateDraftTest(WriteTestCase):
    """A draft under the id of an accepted record of its base proposes replacing that record."""

    def replacement(self, body: str = "A subspace is closed under addition and scaling.", extra: str = "understanding: yes") -> Path:
        draft = self.kb.write_record(
            "subspace", node("Subspace", "4", extra=extra), body, ["A subspace is closed under addition."], folder="drafts",
        )
        entry = self.kb.path("subspace")
        earlier = draft.stat().st_mtime_ns - 1_000_000_000
        os.utime(entry, ns=(earlier, earlier))
        return draft

    def test_accept_replaces_the_entry_and_reports_it_updated(self) -> None:
        draft = self.replacement()
        content = draft.read_bytes()
        self.assertEqual({"would_create": [], "would_update": ["kb:subspace"]}, accept([draft], dry_run=True))
        self.assertTrue(draft.exists())
        receipt = accept([draft])
        self.assertEqual({"created": [], "updated": ["kb:subspace"]}, without_index(receipt))
        self.assertFalse(draft.exists())
        self.assertEqual(content, self.kb.path("subspace").read_bytes())

    def test_new_and_replacing_drafts_are_accepted_together(self) -> None:
        replacing = self.replacement()
        new = self.draft("dimension", node("Dimension", "6", extra='requires: ["[[subspace]]"]'))
        self.assertEqual(
            {"created": ["kb:dimension"], "updated": ["kb:subspace"]}, without_index(accept([new, replacing])),
        )

    def test_a_replacement_is_validated_like_any_draft(self) -> None:
        draft = self.replacement()
        draft.write_text(draft.read_text(encoding="utf-8").replace("addition.\n", "nothing.\n"), encoding="utf-8")
        entry = self.kb.path("subspace")
        earlier = draft.stat().st_mtime_ns - 1_000_000_000
        os.utime(entry, ns=(earlier, earlier))
        before = self.snapshot()
        self.assertIn("evidence is stale", accept([draft])["refused"][0]["message"])
        self.assertEqual(before, self.snapshot())

    def test_an_entry_changed_after_the_draft_is_refused(self) -> None:
        draft = self.replacement()
        entry = self.kb.path("subspace")
        later = draft.stat().st_mtime_ns + 1_000_000_000
        os.utime(entry, ns=(later, later))
        before = self.snapshot()
        receipt = accept([draft])
        self.assertEqual([{
            "path": str(draft),
            "message": f"{entry} changed after this replacement was drafted; draft it again from the record",
        }], receipt["refused"])
        self.assertEqual(before, self.snapshot())

    def test_a_draft_identical_to_its_entry_is_removed(self) -> None:
        draft = self.kb.path("subspace", "drafts")
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_bytes(self.kb.path("subspace").read_bytes())
        content = draft.read_bytes()
        receipt = accept([draft])
        self.assertEqual(["kb:subspace"], receipt["created"])
        self.assertFalse(draft.exists())
        self.assertEqual(content, self.kb.path("subspace").read_bytes())

    def test_accept_embeds_the_replaced_record(self) -> None:
        self.kb.embedding = "fake-model"
        self.kb.write_config()
        self.kb.write_record("rank", node("Rank", "6"), "Rank.", ["Unique sentence about dimension."])
        with fake_encoder():
            index()
            self.kb.write_record("rank", node("Rank", "6"), "Rank, edited.", ["Unique sentence about dimension."])
            receipt = accept([self.replacement(body="A replaced body.")])
        self.assertEqual((1, 1), (receipt["index"]["embedded"], receipt["index"]["unembedded"]))
        connection = open_read_only()
        try:
            bodies = dict(connection.execute("SELECT uid, body FROM record"))
        finally:
            connection.close()
        self.assertEqual("A replaced body.", bodies["kb:subspace"])

    def test_drafts_lists_what_a_draft_replaces(self) -> None:
        self.replacement()
        self.draft("dimension", node("Dimension", "6"))
        listed = {item["id"]: item for item in drafts()["drafts"]}
        self.assertEqual("kb:subspace", listed["subspace"]["replaces"])
        self.assertIsNone(listed["dimension"]["replaces"])
        self.assertEqual([], listed["subspace"]["problems"])

    def test_a_link_to_a_replaced_id_targets_the_accepted_record(self) -> None:
        self.replacement()
        self.draft("dimension", node("Dimension", "6", kind="implies", extra='premise: ["[[subspace]]"]'))
        listed = {item["id"]: item for item in drafts()["drafts"]}
        self.assertEqual(
            {"role": "premise", "pos": 0, "uid": "kb:subspace", "state": "accepted", "label": "Subspace",
             "understanding": "no"},
            listed["dimension"]["out"][0],
        )
        self.assertEqual(([], []), (listed["dimension"]["with"], listed["subspace"]["linked_by"]))
        self.assertEqual([], listed["dimension"]["problems"])
        self.assertEqual({"created": ["kb:dimension"], "updated": []},
                         without_index(accept([listed["dimension"]["path"]])))

    def test_drafts_marks_foreign_drafts(self) -> None:
        self.kb.write_record(
            "attention", "label: Attention\nkind: concept\nsource: https://example.com/paper", "A.", ["Q."], folder="drafts",
        )
        [item] = drafts()["drafts"]
        self.assertEqual((True, "", [], None), (item["foreign"], item["lines"], item["problems"], item["replaces"]))


class SourceTest(WriteTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.kb.write_record(
            "measure-space", node("Measure space", "2-3", extra='aliases: [测度空间]\nrequires: ["[[subspace]]"]'),
            "三元组。其余。", ["A measure space is a triple (X, M, mu)."],
        )
        self.draft("direct-sum", node("Direct sum", "5"), quote="The sum of two subspaces", body="Direct.")
        self.draft("criterion", node("Criterion", "6", kind="implies", extra='premise: ["[[direct-sum]]"]'), body="Crit.")
        self.kb.write_record("elsewhere", node("Elsewhere", "1").replace(SOURCE, "notes/b.txt"), "E.", ["x"])
        self.source = self.kb.root / SOURCE

    def test_profile_and_inventory_write_nothing(self) -> None:
        before = self.snapshot()
        profile = source(self.source)
        self.assertEqual(before, self.snapshot())
        self.assertFalse((self.kb.root / ".knowledge/sheets").exists())
        self.assertEqual(
            ["base", "source", "type", "node_kinds", "relation_kinds", "epistemic", "guidance", "line_count",
             "records", "drafts"],
            list(profile),
        )
        self.assertEqual(("kb", SOURCE, "math", 6), (profile["base"], profile["source"], profile["type"], profile["line_count"]))
        self.assertEqual(["definition", "theorem", "concept"], profile["node_kinds"])
        self.assertEqual(["premise", "conclusion"], profile["relation_kinds"]["implies"])
        self.assertEqual(["proved", "stated"], profile["epistemic"])
        self.assertEqual(
            [{"uid": "kb:measure-space", "label": "Measure space", "kind": "definition", "class": "node", "lines": "2-3",
              "understanding": "yes", "gloss": "三元组。"},
             {"uid": "kb:subspace", "label": "Subspace", "kind": "definition", "class": "node", "lines": "4-4",
              "understanding": "no", "gloss": "A subspace is closed under addition."}],
            profile["records"],
        )
        self.assertEqual(
            [{"id": "direct-sum", "label": "Direct sum", "kind": "definition", "class": "node", "lines": "5-5"},
             {"id": "criterion", "label": "Criterion", "kind": "implies", "class": "relation", "lines": "6-6"}],
            profile["drafts"],
        )

    def test_line_count_reads_bom_and_crlf_like_check(self) -> None:
        self.source.write_bytes(b"\xef\xbb\xbf" + "\r\n".join(LINES).encode() + b"\r\n")
        self.assertEqual(6, source(self.source)["line_count"])

    def test_unregistered_source_asks_for_a_glob(self) -> None:
        other = self.kb.write_source("other/c.md", "text\n")
        with self.assertRaises(KnowledgeError) as caught:
            source(other)
        self.assertIn("add a glob", str(caught.exception))
        outside = self.kb.home.parent / "x.txt"
        outside.write_text("x", encoding="utf-8")
        with self.assertRaises(KnowledgeError):
            source(outside)
        with self.assertRaises(KnowledgeError):
            source(self.kb.root / "notes/missing.txt")

    def test_sheet_and_harvest_are_gone(self) -> None:
        for name in ("sheet", "sheet_json", "harvest", "render_sheet"):
            self.assertFalse(hasattr(write, name), name)

if __name__ == "__main__":
    unittest.main()
