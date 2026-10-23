"""Knowledge markers: scanning, name resolution, ``kgd mark``, and following records with ``check`` and ``relocate``."""

from __future__ import annotations

import unittest
from pathlib import Path

from kgdistiller.home import KnowledgeError
from kgdistiller.locate import relocate
from kgdistiller.markers import mark, markers, scan
from kgdistiller.records import check, fix_lines, parse_record
from tests.knowledge_fixture import make_record_home

LINES = [
    "Title line",
    "A measure space is a triple (X, M, mu).",
    "It extends a measurable space.",
    "A subspace is closed under addition.",
]


def node(label: str, source: str, lines: str, *, kind: str = "definition", extra: str = "") -> str:
    return f"label: {label}\nkind: {kind}\nsource: {source}\nlines: {lines}\n{extra}".rstrip("\n")


class ScanTest(unittest.TestCase):
    def test_markdown_and_text_markers(self) -> None:
        found = scan(["See --[[Measure space]]-- and --[[transformer·Head ablation|heads]]--.", "--[[ ]]-- [[link]]"], "md")
        self.assertEqual([(1, "Measure space", "Measure space"), (1, "transformer·Head ablation", "heads")],
                         [(line, name, text) for line, _, _, name, text in found])
        self.assertEqual(scan(["--[[A]]--"], "txt")[0][3], "A")

    def test_tex_markers_skip_comments_and_keep_nested_braces(self) -> None:
        found = scan([r"\begin{definition}{\kn{由 $\mathit{n}$ 个 random variable 构成的 vector}}",
                      r"\kn[Measure space]{} text % \kn{commented}", r"50\% then \kn{Kept}"], "tex")
        self.assertEqual([(1, r"由 $\mathit{n}$ 个 random variable 构成的 vector"), (2, "Measure space"), (3, "Kept")],
                         [(line, name) for line, _, _, name, _ in found])


class MarkerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.kb = make_record_home(self, bases=("kb", "notes"))
        self.kb.write_source("notes/a.txt", "\n".join(LINES) + "\n")
        self.measure = self.kb.write_record(
            "measure-space", node("Measure space", "notes/a.txt", "2"), "A triple.",
            ["A measure space is a triple (X, M, mu)."])
        self.draft = self.kb.write_record(
            "subspace", node("Linear·Subspace", "notes/a.txt", "4"), "Closed.",
            ["A subspace is closed under addition."], folder="drafts")


class ResolveTest(MarkerTestCase):
    def test_names_resolve_by_label_unscoped_label_alias_and_id(self) -> None:
        self.kb.write_source("notes/b.md", "--[[measure space]]-- --[[Subspace]]-- --[[measure-space]]-- --[[Nothing]]--\n")
        listed = markers([self.kb.root / "notes/b.md"])["markers"]
        self.assertEqual(["kb:measure-space", "kb:subspace", "kb:measure-space", None], [item["uid"] for item in listed])
        self.assertEqual(str(self.draft), listed[1]["path"])
        self.assertEqual(["Nothing"], [item["name"] for item in markers(bases=["kb"], orphans=True)["markers"]])

    def test_the_marker_file_breaks_a_tie_between_bases(self) -> None:
        self.kb.write_source("notes/a.txt", "\n".join(LINES) + "\n", base="notes")
        self.kb.write_record("measure-space", node("Measure space (notes)", "notes/a.txt", "2",
                                                   extra="aliases: [Measure space]"),
                             "Copy.", ["A measure space is a triple (X, M, mu)."], base="notes")
        self.kb.write_source("notes/b.txt", "--[[Measure space]]--\n", base="notes")
        [listed] = markers([self.kb.roots["notes"] / "notes/b.txt"])["markers"]
        self.assertEqual("notes:measure-space", listed["uid"])
        self.assertEqual(["kb:measure-space", "notes:measure-space"], listed["candidates"])


class MarkTest(MarkerTestCase):
    def test_mark_writes_before_the_first_quote_and_keeps_the_line_count(self) -> None:
        report = mark(self.draft)
        self.assertEqual(("kb:subspace", 4, "--[[Linear·Subspace]]--:"), (report["marked"], report["line"], report["marker"]))
        lines = (self.kb.root / "notes/a.txt").read_text(encoding="utf-8").split("\n")
        self.assertEqual("--[[Linear·Subspace]]--: A subspace is closed under addition.", lines[3])
        self.assertEqual(len(LINES) + 1, len(lines))
        self.assertEqual("already", next(iter(set(mark(self.draft)) - {"marked"})))
        self.assertEqual({"errors": [], "stale": [], "moved": [], "follow": []}, check())

    def test_markdown_blocks_take_the_marker_on_the_blank_line_above(self) -> None:
        self.kb.write_source("notes/m.md", "Intro.\n\n$$\nE = mc^2\n$$\n")
        draft = self.kb.write_record("energy", node("Energy", "notes/m.md", "3-5"), "Mass.", ["E = mc^2"], folder="drafts")
        self.assertEqual(2, mark(draft)["line"])
        self.assertEqual("Intro.\n--[[Energy]]--:\n$$\nE = mc^2\n$$\n", (self.kb.root / "notes/m.md").read_text(encoding="utf-8"))

    def test_tex_takes_a_named_empty_marker(self) -> None:
        self.kb.write_source("notes/t.tex", "\\section{A}\nA field is a ring.\n")
        draft = self.kb.write_record("field", node("Field", "notes/t.tex", "2"), "Ring.", ["A field is a ring."], folder="drafts")
        mark(draft)
        self.assertEqual("\\section{A}\n\\kn[Field]{}A field is a ring.\n", (self.kb.root / "notes/t.tex").read_text(encoding="utf-8"))

    def test_mark_refuses_foreign_and_accepted_records(self) -> None:
        with self.assertRaisesRegex(KnowledgeError, "is not a draft"):
            mark(self.measure)
        foreign = self.kb.write_record("web", "label: Web\nkind: concept\nsource: https://example.com/web", "W.",
                                       ["Quoted."], folder="drafts")
        with self.assertRaisesRegex(KnowledgeError, "foreign"):
            mark(foreign)


class FollowTest(MarkerTestCase):
    def mark_and_move(self, to: str, base: str = "kb") -> None:
        source = self.kb.root / "notes/a.txt"
        source.write_text("--[[Measure space]]-- " + source.read_text(encoding="utf-8"), encoding="utf-8")
        destination = self.kb.roots[base] / to
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("Moved first line.\n" + source.read_text(encoding="utf-8"), encoding="utf-8")
        source.unlink()

    def test_check_follows_a_renamed_file_and_fix_lines_rewrites_source_and_lines(self) -> None:
        self.mark_and_move("chapters/renamed.txt")
        report = check()
        self.assertEqual([{"path": str(self.measure), "round": "base", "base": "kb", "source": "chapters/renamed.txt",
                           "lines": "3-3"}], [item for item in report["follow"] if item["path"] == str(self.measure)])
        fixed = fix_lines()
        self.assertIn(str(self.measure), fixed["fixed"])
        record = parse_record(self.measure, "kb")
        self.assertEqual(("chapters/renamed.txt", ((3, 3),)), (record.source, record.lines))

    def test_a_marker_whose_quotes_are_gone_needs_a_passage(self) -> None:
        source = self.kb.root / "notes/a.txt"
        source.write_text("--[[Measure space]]-- A measure space is now a quadruple.\n", encoding="utf-8")
        [followed] = [item for item in check()["follow"] if item["path"] == str(self.measure)]
        self.assertEqual(("source", "absent"), (followed["round"], followed["evidence"]))
        refused = relocate("kb:measure-space")
        self.assertIn("pass the new passage's text", refused["refused"]["message"])
        moved = relocate("kb:measure-space", "A measure space is now a quadruple.")
        self.assertEqual("replaced", moved["evidence"])

    def test_relocate_follows_markers_into_another_base(self) -> None:
        self.mark_and_move("elsewhere/a.txt", base="notes")
        self.assertNotIn(str(self.measure), fix_lines()["fixed"])
        report = relocate("kb:measure-space")
        self.assertEqual(("notes:measure-space", "elsewhere/a.txt", "3-3", "kept"),
                         (report["relocated"], report["to"]["source"], report["to"]["lines"], report["evidence"]))
        self.assertTrue(Path(report["path"]).is_file())

    def test_relocate_without_a_marker_or_passage_is_refused(self) -> None:
        self.assertIn("no marker names the record", relocate("kb:measure-space")["refused"]["message"])


class ForeignRelocationTest(MarkerTestCase):
    def test_url_makes_a_record_foreign_with_the_page_text(self) -> None:
        report = relocate("kb:measure-space", "A measure space is a set with a σ-algebra and a measure.",
                          url="https://en.wikipedia.org/wiki/Measure_space")
        self.assertEqual(("https://en.wikipedia.org/wiki/Measure_space", "", "replaced"),
                         (report["to"]["source"], report["to"]["lines"], report["evidence"]))
        record = parse_record(self.measure, "kb")
        self.assertEqual(((), ("A measure space is a set with a σ-algebra and a measure.",)), (record.lines, record.evidence))
        self.assertTrue(record.foreign)
        self.assertNotIn(str(self.measure), [item["path"] for item in check()["errors"]])

    def test_url_needs_a_web_address_and_text(self) -> None:
        with self.assertRaisesRegex(KnowledgeError, "--url takes a web address"):
            relocate("kb:measure-space", None, url="https://example.com/a")
        with self.assertRaisesRegex(KnowledgeError, "--url takes a web address"):
            relocate("kb:measure-space", "Text.", url="notes/a.txt")


if __name__ == "__main__":
    unittest.main()
