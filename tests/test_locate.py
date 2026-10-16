"""Finding text in the registered sources and relocating a record to a passage: ``find`` and ``relocate``."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest

from kgdistiller.home import KnowledgeError, add_base
from kgdistiller.locate import find, match_tokens, relocate
from kgdistiller.mcp import call_tool
from kgdistiller.records import check, parse_record
from tests.knowledge_fixture import make_record_home
from tests.test_cli import kgdistiller, output

PAPER = "notes/paper.txt"
PAPER_LINES = [
    "# Method",
    "",
    "**Masking.** Following ViT \\[[16](#^reference-16)\\], we divide an image into regular",
    "non-overlapping patches and sample a subset of them uniformly without replacement.",
    "Each mask token \\[[14](#^reference-14)\\] is a shared, learned vector.",
    "每个 mask token 是共享的 learned vector。",
]
SHOWN_PASSAGE = "Masking. Following ViT [16], we divide an image into regular non-overlapping patches and sample a subset of them uniformly without replacement."


def node(label: str, source: str, lines: str | None) -> str:
    return f"label: {label}\nkind: definition\nsource: {source}\n" + (f"lines: {lines}\n" if lines else "")


class LocateTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.kb = make_record_home(self, bases=("kb", "notes"))
        self.kb.write_source(PAPER, "\n".join(PAPER_LINES) + "\n")


class FindTest(LocateTestCase):
    def test_tokens_are_casefolded_words_and_single_cjk_characters(self) -> None:
        self.assertEqual(["mask", "token", "是", "共", "享", "的"], match_tokens("ＭＡＳＫ token 是共享的"))

    def test_a_passage_copied_from_reading_view_finds_its_lines(self) -> None:
        found = find(SHOWN_PASSAGE)
        self.assertEqual(1, found["total"])
        match = found["matches"][0]
        self.assertEqual(("kb", PAPER, "3-4", False), (match["base"], match["source"], match["lines"], match["exact"]))
        self.assertTrue(match["text"].startswith("Masking.** Following ViT \\[[16]"))
        self.assertTrue(match["text"].endswith("without replacement."))

    def test_a_name_matches_word_for_word_across_cjk(self) -> None:
        found = find("mask token")
        self.assertEqual(2, found["total"])
        self.assertEqual(["5-5", "6-6"], [match["lines"] for match in found["matches"]])
        self.assertTrue(all(match["exact"] for match in found["matches"]))
        self.assertEqual(0, find("mask vector")["total"], "a short text skips no words")

    def test_limit_base_and_source_narrow_the_search(self) -> None:
        self.kb.write_source("notes/other.txt", "mask token here\n", base="notes")
        self.assertEqual(3, find("mask token")["total"])
        self.assertEqual(1, len(find("mask token", limit=1)["matches"]))
        self.assertEqual(1, find("mask token", bases=["notes"])["total"])
        self.assertEqual(2, find("mask token", sources=["notes/paper"])["total"])
        with self.assertRaises(KnowledgeError):
            find("mask token", bases=["nope"])
        with self.assertRaises(KnowledgeError):
            find("…")

    def test_kg_find_and_the_cli_return_the_same_matches(self) -> None:
        expected = find("learned vector")
        self.assertEqual(expected, call_tool("kg_find", {"text": "learned vector"}))
        self.assertEqual(expected, output(kgdistiller("find", "learned vector", cwd=self.kb.root)))


class RelocateTest(LocateTestCase):
    def test_a_renamed_and_shifted_source_moves_the_citation_and_keeps_the_quotes(self) -> None:
        self.kb.write_source("notes/old.txt", "Each mask token is a shared, learned vector.\n")
        path = self.kb.write_record("mask-token", node("Mask token", "notes/old.txt", "1"), "A mask token.",
                                    ["Each mask token", "is a shared, learned vector."])
        (self.kb.root / "notes/old.txt").unlink()
        report = relocate("kb:mask-token", "Each mask token [14] is a shared, learned vector.")
        self.assertEqual(("kept", PAPER, "5-5"), (report["evidence"], report["to"]["source"], report["to"]["lines"]))
        self.assertEqual({"source": "notes/old.txt", "lines": "1-1"}, report["from"])
        self.assertIn("index", report)
        text = path.read_text(encoding="utf-8")
        self.assertIn(f'source: "{PAPER}"\nlines: 5-5\n', text)
        self.assertTrue(text.endswith("## Evidence\n\n> Each mask token\n\n> is a shared, learned vector.\n"))
        self.assertEqual([], check()["errors"])

    def test_a_foreign_record_becomes_local_quoting_the_matched_span(self) -> None:
        path = self.kb.write_record("vit-patching", node("ViT patching", "https://arxiv.org/abs/2010.11929", None),
                                    "Patches.", ["We split an image into fixed-size patches"])
        report = relocate("kb:vit-patching", "we divide an image into regular non-overlapping patches")
        self.assertEqual(("replaced", "3-4"), (report["evidence"], report["to"]["lines"]))
        record = parse_record(path, "kb")
        self.assertFalse(record.foreign)
        self.assertEqual(("we divide an image into regular\nnon-overlapping patches",), record.evidence)
        self.assertEqual([], check()["errors"])

    def test_a_draft_is_relocated_by_its_path_without_indexing(self) -> None:
        path = self.kb.write_record("token", node("Token", "https://example.com/token", None), "T.", ["a token of BERT"], folder="drafts")
        report = relocate(str(path), "每个 mask token 是共享的 learned vector")
        self.assertEqual("6-6", report["to"]["lines"])
        self.assertNotIn("index", report)
        self.assertIn("> 每个 mask token 是共享的 learned vector。", path.read_text(encoding="utf-8"))

    def test_several_or_no_occurrences_are_refused_without_writing(self) -> None:
        path = self.kb.write_record("token", node("Token", "https://example.com/token", None), "T.", ["token"])
        before = path.read_bytes()
        several = relocate("kb:token", "mask token")
        self.assertIn("2 times", several["refused"]["message"])
        self.assertEqual(2, len(several["refused"]["matches"]))
        none = relocate("kb:token", "No source holds this sentence.")
        self.assertEqual("the passage does not occur in a registered source", none["refused"]["message"])
        self.assertEqual(before, path.read_bytes())
        narrowed = relocate("kb:token", "mask token", sources=["notes/paper.txt"], dry_run=True)
        self.assertIn("refused", narrowed)

    def test_a_passage_in_another_base_moves_the_record_there_and_relinks_it(self) -> None:
        self.kb.write_source("notes/elsewhere.txt", "Only another base holds this sentence.\n", base="notes")
        self.kb.write_record("mask", node("Mask", PAPER, "5"), "M.", ["Each mask token"])
        self.kb.write_record("x", node("X", "notes/elsewhere.txt", "1"), "X.", ["Only another base"], base="notes")
        path = self.kb.write_record(
            "token", node("Token", "https://example.com/token", None)
            + 'requires:\n  - "[[mask]]"\n  - "[[notes:x|the X]]"\n', "T.", ["a token"])
        local = self.kb.write_record("a", node("A", PAPER, "6") + 'requires: ["[[token]]"]\n', "A.", ["每个 mask token"])
        other = self.kb.write_record("b", node("B", "notes/elsewhere.txt", "1") + 'requires:\n  - "[[kb:Token.md]]"\n',
                                     "B.", ["holds this sentence"], base="notes")
        report = relocate("kb:token", "Only another base holds this sentence.")
        self.assertEqual({"from": "kb:token", "to": "notes:token"}, report["moved"])
        self.assertEqual("notes:token", report["relocated"])
        self.assertEqual(sorted([str(local), str(other)]), sorted(report["relinked"]))
        self.assertFalse(path.exists())
        moved = self.kb.path("token", base="notes").read_text(encoding="utf-8")
        self.assertIn('requires:\n  - "[[kb:mask]]"\n  - "[[x|the X]]"\n', moved)
        self.assertIn('requires: ["[[notes:token]]"]', local.read_text(encoding="utf-8"))
        self.assertIn('requires:\n  - "[[token]]"\n', other.read_text(encoding="utf-8"))
        self.assertEqual([], check()["errors"])

    def test_a_move_across_bases_is_refused_when_the_id_is_taken_or_a_replacement_waits(self) -> None:
        self.kb.write_source("notes/elsewhere.txt", "Only another base holds this sentence.\n", base="notes")
        passage = "Only another base holds this sentence."
        self.kb.write_record("token", node("Token", "https://example.com/token", None), "T.", ["a token"])
        self.kb.write_record("token", node("Token", "https://example.com/token", None), "T.", ["a token"], folder="drafts")
        with self.assertRaisesRegex(KnowledgeError, "replacement draft of kb:token waits"):
            relocate("kb:token", passage)
        self.kb.path("token", folder="drafts").unlink()
        self.kb.write_record("token", node("Other token", "notes/elsewhere.txt", "1"), "O.", ["Only"],
                             folder="drafts", base="notes")
        with self.assertRaisesRegex(KnowledgeError, "base notes already has a record or draft token"):
            relocate("kb:token", passage)
        self.assertTrue(self.kb.path("token").exists())

    def test_a_draft_moves_across_bases_with_the_drafts_that_link_it_relinked(self) -> None:
        self.kb.write_source("notes/elsewhere.txt", "Only another base holds this sentence.\n", base="notes")
        draft = self.kb.write_record("loose", node("Loose", "https://example.com/loose", None), "L.", ["loose"], folder="drafts")
        user = self.kb.write_record("user", node("User", PAPER, "5") + 'requires: ["[[loose]]"]\n', "U.", ["Each mask token"],
                                    folder="drafts")
        report = relocate(str(draft), "Only another base holds this sentence.")
        self.assertEqual([str(user)], report["relinked"])
        self.assertNotIn("index", report)
        self.assertIn('requires: ["[[notes:loose]]"]', user.read_text(encoding="utf-8"))
        self.assertTrue(self.kb.path("loose", folder="drafts", base="notes").exists())
        self.assertEqual([], check()["errors"])

    def test_a_replacement_draft_stays_in_its_records_base(self) -> None:
        self.kb.write_source("notes/elsewhere.txt", "Only another base holds this sentence.\n", base="notes")
        self.kb.write_record("token", node("Token", "https://example.com/token", None), "T.", ["a token"])
        user = self.kb.write_record("user", node("User", PAPER, "5") + 'requires: ["[[token]]"]\n', "U.", ["Each mask token"])
        before = user.read_bytes()
        draft = self.kb.write_record("token", node("Token", "https://example.com/token", None), "T2.", ["a token"],
                                     folder="drafts")
        with self.assertRaisesRegex(KnowledgeError, "replaces the accepted record kb:token and stays in its base"):
            relocate(str(draft), "Only another base holds this sentence.")
        self.assertEqual(before, user.read_bytes())
        self.assertTrue(draft.exists())

    def test_a_move_across_bases_needs_every_base_and_skips_files_check_reports(self) -> None:
        self.kb.write_source("notes/elsewhere.txt", "Only another base holds this sentence.\n", base="notes")
        self.kb.write_record("token", node("Token", "https://example.com/token", None), "T.", ["a token"])
        broken = self.kb.path("broken", folder="drafts", base="notes")
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_text('---\nlabel: Broken\nrequires: ["[[kb:token]]"]\n', encoding="utf-8")
        latin = self.kb.path("latin", folder="drafts", base="notes")
        latin.write_bytes("---\nlabel: token \xe9\n---\n".encode("latin-1"))
        report = relocate("kb:token", "Only another base holds this sentence.", dry_run=True)
        self.assertEqual([], report["relinked"])
        third = self.kb.home.parent / "third"
        third.mkdir()
        add_base(third)
        third.rename(self.kb.home.parent / "third-away")
        with self.assertRaisesRegex(KnowledgeError, "base third is unavailable; its links to kb:token could not be rewritten"):
            relocate("kb:token", "Only another base holds this sentence.")

    def test_dry_run_writes_nothing_and_unknown_targets_are_errors(self) -> None:
        path = self.kb.write_record("token", node("Token", "https://example.com/token", None), "T.", ["token"])
        before = path.read_bytes()
        report = relocate("kb:token", "Each mask token", dry_run=True)
        self.assertTrue(report["dry_run"])
        self.assertEqual(before, path.read_bytes())
        for target in ("kb:missing", "nope:token", "token"):
            with self.subTest(target=target), self.assertRaises(KnowledgeError):
                relocate(target, "Each mask token")

    def test_the_cli_reads_the_passage_from_standard_input(self) -> None:
        self.kb.write_record("token", node("Token", "https://example.com/token", None), "T.", ["token"])
        result = subprocess.run(
            [sys.executable, "-m", "kgdistiller", "relocate", "kb:token", "-"], input="Each mask token\n",
            capture_output=True, text=True, encoding="utf-8", cwd=self.kb.root, check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("5-5", json.loads(result.stdout)["to"]["lines"])
        refused = kgdistiller("relocate", "kb:token", "mask token", cwd=self.kb.root)
        self.assertEqual(1, refused.returncode)


if __name__ == "__main__":
    unittest.main()
