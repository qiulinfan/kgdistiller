from __future__ import annotations

import unittest
from pathlib import Path

from kgdistiller.entries import (
    MAX_ID_LENGTH,
    EntryError,
    entry_relative,
    identity_key,
    locate_evidence,
    normalize_evidence,
    normalize_record,
    parse_entry,
    render_entry,
    slug_id,
    validate_id,
)

PATH = Path("entry.md")


def record(**changes):
    value = {
        "id": "measure-space",
        "label": "Measure space",
        "kind": "definition",
        "aliases": ["测度空间", "Maßraum"],
        "source": "notes/measure.tex",
        "line_start": 3,
        "line_end": 5,
        "understanding": "not-yet-understood",
        "summary": "A set with a sigma-algebra and a measure.\n\nSecond paragraph.",
        "context": "Chapter 1.",
        "prerequisites": ["Sigma-algebra"],
        "open_questions": ["Why countable additivity?"],
        "evidence": "\\begin{definition}\n  A ```measure``` space $(X, \\mathcal{A}, \\mu)$\n\\end{definition}",
    }
    value.update(changes)
    return value


class EntryFormatTest(unittest.TestCase):
    def test_round_trip_is_canonical(self) -> None:
        text = render_entry(record())
        parsed = parse_entry(text, PATH)
        self.assertEqual(parsed, normalize_record(record()))
        self.assertEqual(render_entry(parsed), text)
        self.assertTrue(text.startswith("---\nschema: kgdistiller-entry-v1\nid: measure-space\n"))
        self.assertIn("aliases:\n  - 测度空间\n  - Maßraum\n", text)
        self.assertIn("line_start: 3\nline_end: 5\nunderstanding: not-yet-understood\n---\n\n# Measure space\n", text)
        self.assertIn("## Evidence\n\n````\n\\begin{definition}", text)
        self.assertTrue(text.endswith("\\end{definition}\n````\n"))

    def test_section_order_and_optional_sections(self) -> None:
        text = render_entry(record(context="", open_questions=[]))
        self.assertNotIn("## Context", text)
        self.assertNotIn("## Open questions", text)
        titles = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(titles, ["## Summary", "## Prerequisites", "## Evidence"])

    def test_obsidian_property_forms_are_read(self) -> None:
        text = (
            "---\n"
            "schema: 'kgdistiller-entry-v1'\n"
            'id: "measure-space"\n'
            "label: 'Measure ''space'''\n"
            "kind: definition\n"
            "aliases:\n"
            "- first\n"
            '- "second: quoted"\n'
            "source: notes/measure.tex\n"
            "line_start: 3\n"
            "line_end: 3\n"
            "understanding: unknown\n"
            "---\n"
            "# Measure 'space'\n\n## Summary\n\nText.\n\n## Evidence\n\n```\nquoted\n```\n"
        )
        parsed = parse_entry(text, PATH)
        self.assertEqual(parsed["label"], "Measure 'space'")
        self.assertEqual(parsed["aliases"], ["first", "second: quoted"])
        empty = text.replace("aliases:\n- first\n- \"second: quoted\"\n", "aliases: []\n")
        self.assertEqual(parse_entry(empty, PATH)["aliases"], [])
        blank = text.replace("aliases:\n- first\n- \"second: quoted\"\n", "aliases:\n")
        self.assertEqual(parse_entry(blank, PATH)["aliases"], [])
        crlf = text.replace("\n", "\r\n")
        self.assertEqual(parse_entry(crlf, PATH), parsed)

    def test_unsafe_scalars_are_quoted_as_json(self) -> None:
        text = render_entry(record(label="2: odd #label", aliases=["true", "- dash"]))
        self.assertIn('label: "2: odd #label"', text)
        self.assertIn('  - "true"\n  - "- dash"', text)
        self.assertEqual(parse_entry(text, PATH)["aliases"], ["true", "- dash"])

    def test_rejects_unknown_keys_sections_and_yaml_outside_the_subset(self) -> None:
        text = render_entry(record())
        cases = {
            "unknown frontmatter key": text.replace("kind: definition\n", "kind: definition\ncolor: red\n"),
            "unknown entry section": text.replace("## Context", "## Notes"),
            "differs from label": text.replace("# Measure space", "# Measure spaces"),
            "outside the entry subset": text.replace("kind: definition", "kind: {a: b}"),
            "must be an integer": text.replace("line_start: 3", "line_start: '3'"),
            "duplicate frontmatter key": text.replace("kind: definition\n", "kind: definition\nkind: theorem\n"),
            "missing": text.replace("understanding: not-yet-understood\n", ""),
            "no Evidence": text[: text.index("## Evidence")],
            "without an info string": text.replace("````\n\\begin", "````tex\n\\begin"),
            "text outside": text.replace("# Measure space\n", "# Measure space\nstray\n"),
        }
        for message, broken in cases.items():
            with self.subTest(message=message), self.assertRaisesRegex(EntryError, message):
                parse_entry(broken, PATH)

    def test_field_validation(self) -> None:
        cases = {
            "unknown entry fields": record(color="red"),
            "entry is missing": {key: value for key, value in record().items() if key != "evidence"},
            "single-line": record(label="two\nlines"),
            "repeat its label": record(aliases=["MEASURE  space"]),
            "must be unique": record(aliases=["a", "A"]),
            "without '..'": record(source="../outside.tex"),
            "base-relative": record(source="/abs/path.tex"),
            "outside the .knowledge": record(source=".knowledge/entries/x.md"),
            "must not exceed": record(line_start=6, line_end=5),
            "positive integer": record(line_start=0),
            "understanding must": record(understanding="mastered"),
            "summary must not": record(summary="  "),
            "nonempty source text": record(evidence=" \n "),
        }
        for message, value in cases.items():
            with self.subTest(message=message), self.assertRaisesRegex(EntryError, message):
                normalize_record(value)

    def test_unrepresentable_sections_are_rejected(self) -> None:
        with self.assertRaisesRegex(EntryError, "cannot be represented"):
            render_entry(record(summary="Text\n## Evidence\nmore"))


class EntryIdTest(unittest.TestCase):
    def test_ids_are_readable_ascii_slugs(self) -> None:
        self.assertEqual(slug_id("Measure space"), "measure-space")
        self.assertEqual(slug_id("Lebesgue–Stieltjes measure"), "lebesgue-stieltjes-measure")
        self.assertEqual(slug_id("Schrödinger's equation"), "schrodinger-s-equation")
        self.assertIsNone(slug_id("测度"))
        self.assertIsNone(slug_id("CON"))
        self.assertEqual(len(slug_id("word " * 100)), MAX_ID_LENGTH - 1)
        self.assertEqual(entry_relative("measure-space").as_posix(), ".knowledge/entries/measure-space.md")

    def test_id_rules(self) -> None:
        validate_id("a" * MAX_ID_LENGTH)
        for value in ("a" * (MAX_ID_LENGTH + 1), "Upper", "trailing-", "two--dashes", "nul", "lpt1", "", 7):
            with self.subTest(value=value), self.assertRaises(EntryError):
                validate_id(value)

    def test_identity_key_normalizes_width_case_and_whitespace(self) -> None:
        self.assertEqual(identity_key(" Ｍeasure \t SPACE "), "measure space")


class EvidenceTest(unittest.TestCase):
    def test_normalize_and_locate_on_whitespace_tokens(self) -> None:
        source = "intro\nA measure\n  space is\nstuff\nA measure space is\n"
        self.assertEqual(normalize_evidence(" A\tmeasure\n space "), "A measure space")
        self.assertEqual(locate_evidence(source, "A measure space is"), [(2, 3), (5, 5)])
        self.assertEqual(locate_evidence(source, "measure spaces"), [])
        self.assertEqual(locate_evidence(source, "stuff"), [(4, 4)])
        self.assertEqual(locate_evidence(source, "   "), [])
        self.assertEqual(locate_evidence("ab c", "b c"), [])

    def test_locate_matches_whole_cited_lines_only(self) -> None:
        evidence = "A measure space is a triple\nwith countable additivity."
        source = "New\nNOTE: A measure space is a triple\nwith countable additivity. (see)\n"
        self.assertEqual(locate_evidence(source, evidence), [])
        self.assertEqual(locate_evidence("New\nA measure space is a triple\nwith countable additivity. (see)\n",
                                         evidence), [])
        self.assertEqual(locate_evidence("New\nNOTE: A measure space is a triple\nwith countable additivity.\n",
                                         evidence), [])
        self.assertEqual(locate_evidence("New\n\nA measure space is a triple\n\nwith countable additivity.\n",
                                         evidence), [(3, 5)])


if __name__ == "__main__":
    unittest.main()
