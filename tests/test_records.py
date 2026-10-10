"""The record format: frontmatter reading, value and body grammar, gloss, ids and the class rule."""

from __future__ import annotations

import json
import tempfile
import unicodedata
import unittest
from pathlib import Path

from kgdistiller.records import (
    MAX_ID_LENGTH,
    UNDERSTANDING,
    Record,
    RecordError,
    gloss,
    parse_body,
    parse_text,
    parse_value,
    read_text,
    record_class,
    slug,
    source_lines,
    valid_id,
)
from tests.knowledge_fixture import render_record

GRAMMAR = Path(__file__).parent / "fixtures" / "link-grammar.json"
CASE_FOLDS = Path(__file__).parents[1] / "integrations" / "obsidian" / "src" / "case-folding.json"
FRONTMATTER = "label: Measure space\nkind: definition\nsource: notes/a.txt\nlines: 3-4"


def parse(text: str, identifier: str = "measure-space", base: str = "kb") -> Record:
    return parse_text(text, base, identifier, Path(f"{identifier}.md"))


def record(frontmatter: str = FRONTMATTER, body: str = "A measure space is a triple.", quotes: tuple[str, ...] = ("quoted text",)) -> Record:
    return parse(render_record(frontmatter, body, quotes))


class FrontmatterTest(unittest.TestCase):
    def test_base_loader_keeps_every_scalar_a_string(self) -> None:
        parsed = record("label: on\nkind: definition\nsource: notes/a.txt\nlines: 40-46\naliases:\n  - yes\n  - 2")
        self.assertEqual("on", parsed.label)
        self.assertEqual(("yes", "2"), parsed.aliases)
        self.assertEqual((40, 46), parsed.lines)
        self.assertEqual("40-46", parsed.lines_text)

    def test_single_line_number_is_a_range_of_one(self) -> None:
        self.assertEqual((7, 7), record(FRONTMATTER.replace("lines: 3-4", "lines: 7")).lines)
        self.assertEqual((7, 7), record(FRONTMATTER.replace("lines: 3-4", 'lines: "7"')).lines)

    def test_invalid_line_ranges_are_frontmatter_errors(self) -> None:
        for lines in ("0", "9-3", "a-b", "3-", "[3, 4]"):
            with self.subTest(lines=lines), self.assertRaises(RecordError) as caught:
                record(FRONTMATTER.replace("lines: 3-4", f"lines: {lines}"))
            self.assertEqual("frontmatter", caught.exception.rule)

    def test_empty_scalar_on_list_and_role_keys_is_an_empty_list(self) -> None:
        parsed = record(FRONTMATTER + "\naliases:\nrequires:\npremise:\nunderstanding:\nepistemic:")
        self.assertEqual((), parsed.aliases)
        self.assertEqual((), parsed.requires)
        self.assertEqual((("premise", ()),), parsed.roles)
        self.assertEqual("unknown", parsed.understanding)
        self.assertIsNone(parsed.epistemic)
        self.assertEqual("node", parsed.class_)

    def test_unquoted_wikilink_is_reported_with_the_quoting_fix(self) -> None:
        for line in ("premise: [[subspace]]", "requires:\n  - [[subspace]]"):
            with self.subTest(line=line), self.assertRaises(RecordError) as caught:
                record(FRONTMATTER + "\n" + line)
            self.assertEqual("link", caught.exception.rule)
            self.assertIn('quote wikilinks: write "[[subspace]]"', caught.exception.message)

    def test_required_keys_and_shapes(self) -> None:
        cases = {
            "missing label": FRONTMATTER.replace("label: Measure space\n", ""),
            "empty kind": FRONTMATTER.replace("kind: definition", "kind:"),
            "list label": FRONTMATTER.replace("label: Measure space", "label: [a, b]"),
            "scalar aliases": FRONTMATTER + "\naliases: measure",
            "scalar role": FRONTMATTER + '\npremise: "[[subspace]]"',
            "mapping value": FRONTMATTER + "\npremise: {a: b}",
            "bad understanding": FRONTMATTER + "\nunderstanding: mastered",
            "empty alias": FRONTMATTER + '\naliases: [""]',
        }
        for name, frontmatter in cases.items():
            with self.subTest(name), self.assertRaises(RecordError) as caught:
                record(frontmatter)
            self.assertEqual("frontmatter", caught.exception.rule)

    def test_understanding_values(self) -> None:
        for value in UNDERSTANDING:
            self.assertEqual(value, record(FRONTMATTER + f"\nunderstanding: {value}").understanding)

    def test_tags_and_cssclasses_are_accepted_and_ignored(self) -> None:
        parsed = record(FRONTMATTER + "\ntags: [a, b]\ncssclasses: wide\n")
        self.assertEqual("node", parsed.class_)
        self.assertEqual((), parsed.roles)
        self.assertEqual("node", record(FRONTMATTER + "\ntags:\n  - [[x]]").class_)

    def test_frontmatter_delimiters(self) -> None:
        for text in ("label: x\n---\nbody", "---\nlabel: x\nbody", "---\nlabel: x\n--- \nbody"):
            with self.subTest(text=text), self.assertRaises(RecordError) as caught:
                parse(text)
            self.assertEqual("frontmatter", caught.exception.rule)

    def test_the_file_stem_must_be_a_valid_id(self) -> None:
        text = render_record(FRONTMATTER, "Prose.", ("quote",))
        for stem in ("Measure-Space", "measure_space", "a--b"):
            with self.subTest(stem=stem), self.assertRaises(RecordError) as caught:
                parse(text, stem)
            self.assertEqual("id", caught.exception.rule)

    def test_values_keep_frontmatter_role_order_then_requires(self) -> None:
        parsed = record(
            "label: Sum is a subspace\nkind: implies\nsource: notes/a.txt\nlines: 3\n"
            'requires: ["[[vector-space]]", dimension]\n'
            'premise: ["[[subspace]]", "[[subspace]]"]\nconclusion: ["[[notes:sum]]"]'
        )
        self.assertEqual(
            [("premise", 0, "kb:subspace"), ("premise", 1, "kb:subspace"), ("conclusion", 0, "notes:sum"),
             ("requires", 0, "kb:vector-space"), ("requires", 1, None)],
            [(role, position, value.uid) for role, position, value in parsed.values()],
        )
        self.assertEqual("dimension", parsed.requires[1].term)
        self.assertEqual("relation", parsed.class_)


class ValueGrammarTest(unittest.TestCase):
    def test_shared_link_grammar_fixture(self) -> None:
        cases = json.loads(GRAMMAR.read_text(encoding="utf-8"))["cases"]
        self.assertGreater(len(cases), 30)
        for case in cases:
            with self.subTest(value=case["value"], base=case["base"]):
                try:
                    value = parse_value(case["value"], case["base"])
                except RecordError as error:
                    self.assertEqual("link", error.rule)
                    got = {"error": error.reason}
                else:
                    got = {"uid": value.uid} if value.uid is not None else {"term": value.term}
                self.assertEqual(case["expect"], got)

    def test_plugin_case_folding_table_is_python_casefold(self) -> None:
        # The plugin folds with this table where casefold and lowercase differ, and
        # lowercases every other character on its own, which is exactly `fold`.
        table = json.loads(CASE_FOLDS.read_text(encoding="utf-8"))
        expected = {}
        for point in range(0x110000):
            char = chr(point)
            if unicodedata.category(char) not in {"Cn", "Cs"} and char.casefold() != char.lower():
                expected[char] = char.casefold()
        assigned = {char: folded for char, folded in table.items() if unicodedata.category(char) != "Cn"}
        self.assertEqual(expected, assigned)

    def test_record_values_are_parsed_with_the_record_base(self) -> None:
        with self.assertRaises(RecordError) as caught:
            record(FRONTMATTER + '\nrequires: ["[[kb:subspace]]"]')
        self.assertEqual("own-base", caught.exception.reason)


class BodyGrammarTest(unittest.TestCase):
    def test_prose_search_terms_and_quotes(self) -> None:
        prose, search_terms, quotes = parse_body(
            "\nFirst paragraph.\n\n## Conditions\n\nBoth normed.\n\n## Search terms\n\nwhat is it\nwhy\n\n"
            "## Evidence\n\n> one\n> continued\n\n>two\n>\n> three\n"
        )
        self.assertEqual("First paragraph.\n\n## Conditions\n\nBoth normed.", prose)
        self.assertEqual("what is it\nwhy", search_terms)
        self.assertEqual(("one\ncontinued", "two\n\nthree"), quotes)

    def test_headings_inside_fences_belong_to_the_prose(self) -> None:
        prose, _, quotes = parse_body("Text.\n```\n## Evidence\n```\n~~~~\n## Search terms\n~~~~\n## Evidence\n> q\n")
        self.assertIn("## Evidence", prose)
        self.assertEqual(("q",), quotes)

    def test_errors(self) -> None:
        cases = {
            "no evidence": "Prose only.\n",
            "evidence not last": "## Evidence\n> q\n## Notes\nx\n",
            "two evidence sections": "## Evidence\n> a\n## Evidence\n> b\n",
            "search terms not before evidence": "## Search terms\nx\n## Notes\ny\n## Evidence\n> q\n",
            "prose inside evidence": "## Evidence\n> q\nnot a quote\n",
            "no quote": "## Evidence\n\n",
            "empty quote": "## Evidence\n>\n>   \n",
        }
        for name, body in cases.items():
            with self.subTest(name), self.assertRaises(RecordError) as caught:
                parse_body(body)
            self.assertEqual("body", caught.exception.rule)


class GlossTest(unittest.TestCase):
    def test_first_sentence_of_first_prose_paragraph(self) -> None:
        self.assertEqual("三元组 (X,M,μ)。", gloss("三元组 (X,M,μ)。其中 M 是 σ-algebra。"))
        self.assertEqual("A map is bounded.", gloss("A map is bounded. It is continuous."))
        self.assertEqual("A map is bounded.", gloss("A map\nis bounded.\nMore."))
        self.assertEqual("Uses e.g.", gloss("Uses e.g. a map. More."))
        self.assertEqual("The value 1.5 is fine?", gloss("The value 1.5 is fine? Yes."))
        self.assertEqual("No terminal punctuation", gloss("No terminal punctuation"))

    def test_headings_are_skipped_and_length_is_capped(self) -> None:
        self.assertEqual("Body.", gloss("### Heading\n\nBody. Rest."))
        self.assertEqual(240, len(gloss("x" * 500)))
        self.assertEqual("", gloss(""))
        self.assertEqual("", gloss("# Only a heading"))


class IdTest(unittest.TestCase):
    def test_slug(self) -> None:
        self.assertEqual("measure-space", slug("Measure space"))
        self.assertEqual("sum-of-two-subspaces-is-a-subspace", slug("Sum of two subspaces is a subspace"))
        self.assertEqual("σ-algebra", slug("Σ-algebra"))
        self.assertEqual("测度-空间", slug("测度 空间"))
        self.assertEqual("strasse", slug("Straße"))
        self.assertEqual("l2-space", slug("__L2 space__"))
        self.assertEqual("", slug("∑ + ∏"))

    def test_slug_length_cut(self) -> None:
        long = slug(" ".join(["word"] * 30))
        self.assertLessEqual(len(long), MAX_ID_LENGTH)
        self.assertFalse(long.endswith("-"))
        self.assertTrue(valid_id(long))
        self.assertEqual("x" * MAX_ID_LENGTH, slug("x" * 100))
        exact = "a" * 79 + "-" + "b" * 10
        self.assertEqual("a" * 79, slug(exact))

    def test_valid_id(self) -> None:
        for value in ("measure-space", "测度", "l2", "a-b-c"):
            self.assertTrue(valid_id(value), value)
        for value in ("Measure", "a--b", "-a", "a-", "a_b", "a b", "x" * 81, ""):
            self.assertFalse(valid_id(value), value)


class ClassRuleTest(unittest.TestCase):
    def test_relation_iff_a_non_fixed_key_holds_a_non_empty_list(self) -> None:
        self.assertEqual("node", record_class({"label": "x", "requires": ["[[a]]"], "aliases": ["b"]}))
        self.assertEqual("node", record_class({"label": "x", "premise": []}))
        self.assertEqual("relation", record_class({"label": "x", "premise": ["a"]}))
        self.assertEqual("relation", record_class({"tags": [], "side": ["[[a]]", "[[b]]"]}))
        self.assertEqual("node", record_class({"tags": ["a"], "cssclasses": ["b"]}))
        self.assertEqual("relation", record(FRONTMATTER + "\nuses: [dimension]").class_)


class TextReadingTest(unittest.TestCase):
    def test_bom_and_crlf_files_read_identically(self) -> None:
        text = render_record(FRONTMATTER, "Prose line.\nSecond.", ("quoted text",))
        with tempfile.TemporaryDirectory() as directory:
            plain = Path(directory) / "plain.md"
            windows = Path(directory) / "windows.md"
            plain.write_bytes(text.encode("utf-8"))
            windows.write_bytes(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))
            self.assertEqual(read_text(plain), read_text(windows))
            self.assertEqual(source_lines(plain), source_lines(windows))
            first, second = (parse_text(read_text(path), "kb", "x", path) for path in (plain, windows))
            self.assertEqual(
                (first.label, first.lines, first.body, first.evidence),
                (second.label, second.lines, second.body, second.evidence),
            )

    def test_source_lines_are_one_based_and_ignore_the_final_newline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "s.txt"
            path.write_bytes("﻿one\r\ntwo\rthree\n".encode())
            self.assertEqual(["one", "two", "three"], source_lines(path))
            path.write_bytes(b"")
            self.assertEqual([], source_lines(path))
            path.write_bytes(b"a\n\n")
            self.assertEqual(["a", ""], source_lines(path))


if __name__ == "__main__":
    unittest.main()
