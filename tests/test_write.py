"""The write path: ``accept``, ``sheet`` (file and JSON) and ``harvest``."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from kgdistiller import write
from kgdistiller.home import KnowledgeError, LockConflict, lock
from kgdistiller.write import accept, harvest, sheet, sheet_json
from tests.knowledge_fixture import make_record_home

SOURCE = "notes/a.txt"
LINES = [
    "Title line",
    "A measure space is a triple (X, M, mu).",
    "It extends a measurable space.",
    "A subspace is closed under addition.",
    "The sum of two subspaces is a subspace.",
    "Unique sentence about dimension.",
]


def node(label: str, lines: str, *, kind: str = "definition", extra: str = "") -> str:
    return f"label: {label}\nkind: {kind}\nsource: {SOURCE}\nlines: {lines}\n{extra}".rstrip("\n")


class WriteTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.kb = make_record_home(self, bases=("kb", "notes"))
        self.kb.write_source(SOURCE, "\n".join(LINES) + "\n")
        self.kb.write_record(
            "subspace", node("Subspace", "4", extra='understanding: understood\nrequires: ["vector space"]'),
            "A subspace is closed under addition. More text.", ["A subspace is closed under addition."],
        )

    def draft(self, identifier: str, frontmatter: str, quote: str = "Unique sentence about dimension.", body: str = "Text.") -> Path:
        return self.kb.write_record(identifier, frontmatter, body, [quote], folder="drafts")

    def snapshot(self) -> dict[str, bytes]:
        knowledge = self.kb.root / ".knowledge"
        return {str(path.relative_to(knowledge)): path.read_bytes() for path in sorted(knowledge.rglob("*.md"))}


class AcceptTest(WriteTestCase):
    def test_accept_moves_the_draft_into_entries(self) -> None:
        draft = self.draft("dimension", node("Dimension", "6", extra="understanding: not-yet-understood"))
        content = draft.read_bytes()
        receipt = accept([draft])
        self.assertEqual(
            {"created": ["kb:dimension"], "understanding_set": [{"uid": "kb:dimension", "value": "not-yet-understood"}]},
            receipt,
        )
        self.assertFalse(draft.exists())
        self.assertEqual(content, self.kb.path("dimension").read_bytes())

    def test_unknown_understanding_is_not_listed(self) -> None:
        draft = self.draft("dimension", node("Dimension", "6"))
        self.assertEqual({"created": ["kb:dimension"], "understanding_set": []}, accept([str(draft)]))

    def test_dry_run_writes_nothing(self) -> None:
        draft = self.draft("dimension", node("Dimension", "6"))
        before = self.snapshot()
        self.assertEqual({"would_create": ["kb:dimension"]}, accept([draft], dry_run=True))
        self.assertEqual(before, self.snapshot())

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

    def test_an_existing_id_is_refused(self) -> None:
        draft = self.draft("subspace", node("Other subspace", "6"))
        self.assertIn("already exists in entries/", accept([draft])["refused"][0]["message"])

    def test_identical_entry_finishes_an_interrupted_accept(self) -> None:
        draft = self.draft("dimension", node("Dimension", "6", extra="understanding: understood"))
        os.link(draft, self.kb.path("dimension"))
        self.assertEqual(
            {"created": ["kb:dimension"], "understanding_set": [{"uid": "kb:dimension", "value": "understood"}]},
            accept([draft]),
        )
        self.assertFalse(draft.exists())
        self.assertTrue(self.kb.path("dimension").exists())

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


EXPECTED_SHEET = """<!-- generated by kgd sheet; only draft checkboxes are read back -->
# notes/a.txt

[[notes/a.txt]] · type math · 4 accepted · 2 drafts

## definition
- [[.knowledge/entries/measure-space|Measure space]] · L2-3 — 三元组。
- [[.knowledge/entries/subspace|Subspace]] · L4-4 · understood — A subspace is closed under addition.

## implies
- [[.knowledge/entries/sum-is-subspace|Sum  is  a subspace]] · L5-5 · premise: Subspace · conclusion: Sum of subspaces, Vector Space, missing — Sum | is [closed].

## other
- [[.knowledge/entries/odd|Odd]] · L6-6 — Odd.

## Pending
- measurable space — Measure space (requires)
- vector space — Subspace (requires), Sum  is  a subspace (conclusion)

## Drafts
- [ ] [[.knowledge/drafts/direct-sum|Direct sum]] · definition · L5-5 — Direct.
- [ ] [[.knowledge/drafts/criterion|Criterion]] · implies · L6-6 · premise: Direct sum · conclusion: dimension — Crit.
"""


class SheetTestCase(WriteTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.kb.write_record(
            "measure-space", node("Measure space", "2-3", extra='aliases: [测度空间]\nrequires: ["[[subspace]]", measurable space]'),
            "三元组。其余。", ["A measure space is a triple (X, M, mu)."],
        )
        self.kb.write_record(
            "sum-is-subspace",
            node("Sum [is] a|subspace", "5", kind="implies",
                 extra='premise: ["[[subspace]]"]\nconclusion: ["[[notes:sum]]", Vector Space, "[[missing]]"]'),
            "Sum | is [closed].", ["The sum of two subspaces is a subspace."],
        )
        self.kb.write_record("odd", node("Odd", "6", kind="lemma"), "Odd.", ["Unique sentence about dimension."])
        self.kb.write_source(SOURCE, "\n".join(LINES) + "\n", base="notes")
        self.kb.write_record("sum", node("Sum of subspaces", "5"), "Sum.", ["The sum of two"], base="notes")
        self.draft("direct-sum", node("Direct sum", "5"), quote="The sum of two subspaces", body="Direct.")
        self.draft("criterion", node("Criterion", "6", kind="implies", extra='premise: ["[[direct-sum]]"]\nconclusion: [dimension]'), body="Crit.")
        self.kb.write_record("elsewhere", node("Elsewhere", "1").replace(SOURCE, "notes/b.txt"), "E.", ["x"])
        self.source = self.kb.root / SOURCE
        self.sheet = self.kb.root / ".knowledge/sheets/notes/a.txt.md"

    def tick(self, identifier: str) -> None:
        text = self.sheet.read_text(encoding="utf-8")
        self.sheet.write_text(text.replace(f"- [ ] [[.knowledge/drafts/{identifier}|", f"- [x] [[.knowledge/drafts/{identifier}|"), encoding="utf-8")


class SheetTest(SheetTestCase):
    def test_exact_sheet(self) -> None:
        self.assertEqual({"sheet": str(self.sheet)}, sheet(self.source))
        self.assertEqual(EXPECTED_SHEET, self.sheet.read_text(encoding="utf-8"))

    def test_display_text_is_sanitized_in_roles_and_pending(self) -> None:
        self.kb.write_record("weird", node('"Weird [w] | x"', "6"), "W.", ["Unique sentence about dimension."])
        self.kb.write_record(
            "weird-rel",
            node("Weird rel", "6", kind="implies", extra='premise: ["[[weird]]"]\nconclusion: ["odd|term"]'),
            "R.", ["Unique sentence about dimension."],
        )
        sheet(self.source)
        text = self.sheet.read_text(encoding="utf-8")
        self.assertIn("- [[.knowledge/entries/weird-rel|Weird rel]] · L6-6 · premise: Weird  w    x · conclusion: odd term — R.", text)
        self.assertIn("- odd term — Weird rel (conclusion)", text)
        self.assertNotIn("[w]", text)
        self.assertNotIn("odd|term", text)

    def test_ticks_survive_regeneration_while_their_draft_exists(self) -> None:
        sheet(self.source)
        self.tick("criterion")
        self.tick("direct-sum")
        self.kb.path("direct-sum", "drafts").unlink()
        self.draft("later", node("Later", "1"), quote="Title line")
        sheet(self.source)
        text = self.sheet.read_text(encoding="utf-8")
        self.assertIn("- [x] [[.knowledge/drafts/criterion|Criterion]]", text)
        self.assertIn("- [ ] [[.knowledge/drafts/later|Later]]", text)
        self.assertNotIn("drafts/direct-sum", text)
        self.assertIn("premise: direct-sum ·", text)

    def test_json_profile_and_inventory_write_nothing(self) -> None:
        profile = sheet_json(self.source)
        self.assertFalse(self.sheet.exists())
        self.assertEqual(
            ["base", "source", "type", "node_kinds", "relation_kinds", "epistemic", "guidance", "line_count",
             "records", "drafts", "pending"],
            list(profile),
        )
        self.assertEqual(("kb", SOURCE, "math", 6), (profile["base"], profile["source"], profile["type"], profile["line_count"]))
        self.assertEqual(["premise", "conclusion"], profile["relation_kinds"]["implies"])
        self.assertEqual(
            {"uid": "kb:subspace", "label": "Subspace", "kind": "definition", "class": "node", "lines": "4-4",
             "understanding": "understood", "gloss": "A subspace is closed under addition."},
            profile["records"][1],
        )
        self.assertEqual(
            [{"id": "direct-sum", "label": "Direct sum", "kind": "definition", "class": "node", "lines": "5-5"},
             {"id": "criterion", "label": "Criterion", "kind": "implies", "class": "relation", "lines": "6-6"}],
            profile["drafts"],
        )
        self.assertEqual(
            {"term": "vector space", "owners": [{"uid": "kb:subspace", "role": "requires"},
                                               {"uid": "kb:sum-is-subspace", "role": "conclusion"}]},
            profile["pending"][1],
        )

    def test_line_count_reads_bom_and_crlf_like_check(self) -> None:
        self.source.write_bytes(b"\xef\xbb\xbf" + "\r\n".join(LINES).encode() + b"\r\n")
        self.assertEqual(6, sheet_json(self.source)["line_count"])

    def test_unregistered_source_asks_for_a_glob(self) -> None:
        other = self.kb.write_source("other/c.md", "text\n")
        with self.assertRaises(KnowledgeError) as caught:
            sheet(other)
        self.assertIn("add a glob", str(caught.exception))
        outside = self.kb.home.parent / "x.txt"
        outside.write_text("x", encoding="utf-8")
        with self.assertRaises(KnowledgeError):
            sheet_json(outside)


class HarvestTest(SheetTestCase):
    def test_harvest_accepts_ticked_drafts_and_regenerates(self) -> None:
        sheet(self.source)
        self.tick("direct-sum")
        self.tick("criterion")
        receipt = harvest(self.sheet)
        self.assertEqual({"created": ["kb:direct-sum", "kb:criterion"], "understanding_set": []}, receipt)
        text = self.sheet.read_text(encoding="utf-8")
        self.assertIn("· 6 accepted · 0 drafts", text)
        self.assertIn("- [[.knowledge/entries/direct-sum|Direct sum]] · L5-5 — Direct.", text)
        self.assertNotIn("## Drafts", text)

    def test_unticked_drafts_keep_their_state(self) -> None:
        sheet(self.source)
        self.tick("direct-sum")
        harvest(self.sheet)
        text = self.sheet.read_text(encoding="utf-8")
        self.assertIn("- [ ] [[.knowledge/drafts/criterion|Criterion]]", text)

    def test_refusal_and_dry_run_change_nothing(self) -> None:
        sheet(self.source)
        self.tick("criterion")
        before = self.snapshot()
        refused = harvest(self.sheet)
        self.assertEqual("premise[0]: select [[direct-sum]] too", refused["refused"][0]["message"])
        self.assertEqual(before, self.snapshot())
        self.tick("direct-sum")
        before = self.snapshot()
        self.assertEqual({"would_create": ["kb:direct-sum", "kb:criterion"]}, harvest(self.sheet, dry_run=True))
        self.assertEqual(before, self.snapshot())

    def test_ticked_rows_whose_draft_is_gone_are_skipped(self) -> None:
        sheet(self.source)
        self.tick("direct-sum")
        self.kb.path("direct-sum", "drafts").unlink()
        self.assertEqual({"created": [], "understanding_set": []}, harvest(self.sheet))

    def test_only_rows_under_drafts_count(self) -> None:
        sheet(self.source)
        text = self.sheet.read_text(encoding="utf-8")
        self.sheet.write_text(text.replace("## Pending", "- [x] [[.knowledge/drafts/direct-sum]]\n\n## Pending"), encoding="utf-8")
        self.assertEqual({"created": [], "understanding_set": []}, harvest(self.sheet))

    def test_the_sheet_must_live_under_sheets(self) -> None:
        with self.assertRaises(KnowledgeError):
            harvest(self.source)

    def test_an_unregistered_source_is_refused_before_accepting(self) -> None:
        sheet(self.source)
        self.tick("direct-sum")
        self.kb.sources["kb"] = {}
        self.kb.write_config()
        before = self.snapshot()
        with self.assertRaises(KnowledgeError):
            harvest(self.sheet)
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
