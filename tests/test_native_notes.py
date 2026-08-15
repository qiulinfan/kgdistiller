from __future__ import annotations

import ast
import unittest
from pathlib import Path

from kgdistiller.native_notes import (
    ConceptNote,
    MAX_NOTE_BYTES,
    NativeNoteError,
    TaxonomyNote,
    parse_native_markdown,
    parse_note_link,
    render_concept_note,
    render_taxonomy_note,
)


def concept_bytes(*, newline: str = "\n", blank: bool = True) -> bytes:
    between = newline if blank else ""
    return (
        "---"
        + newline
        + "kgd_schema: qlkg-concept-v1"
        + newline
        + "kgd_id: measure-space"
        + newline
        + "aliases: [\"Measure space\"]"
        + newline
        + "tags: [\"kgdistiller/concept\"]"
        + newline
        + "kgd_fields: [\"[[Knowledge/Fields/Measure Theory]]\"]"
        + newline
        + "kgd_topics: []"
        + newline
        + "kgd_prerequisites: []"
        + newline
        + "kgd_implies: []"
        + newline
        + "kgd_generalizes: []"
        + newline
        + "kgd_contrasts_with: []"
        + newline
        + "kgd_derived_from: []"
        + newline
        + "---"
        + newline
        + between
        + "# Measure space"
        + newline
        + newline
        + "A measure space is curated."
        + newline
    ).encode("utf-8")


class NativeNoteTests(unittest.TestCase):
    def test_lf_and_crlf_have_exact_provenance_lines(self) -> None:
        without_blank = parse_native_markdown(
            concept_bytes(newline="\n", blank=False),
            authority="Knowledge/Concepts/Measure Space.md",
        )
        with_blank = parse_native_markdown(
            concept_bytes(newline="\r\n", blank=True),
            authority="Knowledge/Concepts/Measure Space.md",
        )
        self.assertIsInstance(without_blank, ConceptNote)
        self.assertEqual((14, 16), (without_blank.h1_line, without_blank.end_line))
        self.assertEqual((15, 17), (with_blank.h1_line, with_blank.end_line))
        self.assertEqual(without_blank.definition_sha256, with_blank.definition_sha256)
        self.assertNotIn("\r", with_blank.normalized_text)

    def test_renderer_round_trips_identity_links_display_and_body(self) -> None:
        note = parse_native_markdown(
            concept_bytes(), authority="Knowledge/Concepts/Measure Space.md"
        )
        note = ConceptNote(
            **{
                **note.__dict__,
                "fields": (
                    parse_note_link(
                        "[[Knowledge/Fields/Measure Theory|Measure theory]]"
                    ),
                ),
            }
        )
        rendered = render_concept_note(note)
        reparsed = parse_native_markdown(
            rendered.encode("utf-8"), authority=note.authority
        )
        self.assertEqual(note.id, reparsed.id)
        self.assertEqual(note.label, reparsed.label)
        self.assertEqual(note.body, reparsed.body)
        self.assertEqual("Measure theory", reparsed.fields[0].display)

    def test_taxonomy_renderer_round_trips_field_and_multi_parent_topic(self) -> None:
        field = parse_native_markdown(
            (
                "---\n"
                "kgd_schema: qlkg-taxonomy-v1\n"
                "kgd_id: measure-theory\n"
                "kgd_kind: field\n"
                "aliases: [\"Measure Theory\"]\n"
                "kgd_parents: []\n"
                "---\n\n"
                "# Measure theory\n\n"
                "A root field.\n"
            ).encode("utf-8"),
            authority="Knowledge/Fields/Measure Theory.md",
        )
        self.assertIsInstance(field, TaxonomyNote)
        reparsed_field = parse_native_markdown(
            render_taxonomy_note(field).encode("utf-8"),
            authority=field.authority,
        )
        self.assertEqual((field.id, field.kind, field.body), (
            reparsed_field.id,
            reparsed_field.kind,
            reparsed_field.body,
        ))

        topic = parse_native_markdown(
            (
                "---\n"
                "kgd_schema: qlkg-taxonomy-v1\n"
                "kgd_id: integration\n"
                "kgd_kind: topic\n"
                "aliases: [\"Integration\"]\n"
                "kgd_parents:\n"
                "  - \"[[Knowledge/Fields/Measure Theory]]\"\n"
                "  - \"[[Knowledge/Fields/Functional Analysis|Analysis]]\"\n"
                "---\n\n"
                "# Integration\n\n"
                "A topic with two parent fields.\n"
            ).encode("utf-8"),
            authority="Knowledge/Topics/Integration.md",
        )
        self.assertIsInstance(topic, TaxonomyNote)
        reparsed_topic = parse_native_markdown(
            render_taxonomy_note(topic).encode("utf-8"),
            authority=topic.authority,
        )
        self.assertEqual(topic.parents, reparsed_topic.parents)
        self.assertEqual(topic.body, reparsed_topic.body)

    def test_unicode_alias_and_user_property_preserve_raw_crlf_frontmatter(self) -> None:
        data = concept_bytes(newline="\r\n").replace(
            b'aliases: ["Measure space"]\r\n',
            'aliases: ["测度空间"]\r\nuser_note: "保留原样"\r\n'.encode("utf-8"),
        )
        note = parse_native_markdown(
            data, authority="Knowledge/Concepts/Measure Space.md"
        )
        expected_raw = data.split(b"---\r\n", 2)[1]
        self.assertEqual(("测度空间",), note.aliases)
        self.assertEqual("保留原样", note.frontmatter["user_note"])
        self.assertEqual(expected_raw, note.raw_frontmatter)
        self.assertIn(b"\r\n", note.raw_frontmatter)

    def test_yaml_rejects_duplicate_anchor_alias_tag_nested_and_extra_document(self) -> None:
        cases = {
            "duplicate": (
                "kgd_schema: qlkg-concept-v1\nkgd_id: one\nkgd_id: two\n"
            ),
            "anchor": "kgd_schema: &schema qlkg-concept-v1\nkgd_id: one\n",
            "alias": "kgd_schema: qlkg-concept-v1\nkgd_id: &id one\naliases: [*id]\n",
            "tag": "kgd_schema: qlkg-concept-v1\nkgd_id: !custom one\n",
            "nested": "kgd_schema: qlkg-concept-v1\nkgd_id: one\nuser: {nested: value}\n",
            "deep": "kgd_schema: qlkg-concept-v1\nkgd_id: one\nuser: "
            + "[" * 70
            + "x"
            + "]" * 70
            + "\n",
            "document": "kgd_schema: qlkg-concept-v1\nkgd_id: one\n...\nmore: value\n",
        }
        for name, frontmatter in cases.items():
            data = f"---\n{frontmatter}---\n# One\nBody\n".encode("utf-8")
            with self.subTest(case=name):
                with self.assertRaises(NativeNoteError):
                    parse_native_markdown(
                        data, authority="Knowledge/Concepts/One.md"
                    )

    def test_links_are_qualified_portable_and_control_free(self) -> None:
        valid = parse_note_link(
            "[[Knowledge/Concepts/Sigma Algebra.md|Sigma algebra]]"
        )
        self.assertEqual("Knowledge/Concepts/Sigma Algebra", valid.target)
        invalid = (
            "[[Sigma Algebra]]",
            "[[Knowledge/Concepts/../Sigma]]",
            "[[Knowledge/Concepts/CON]]",
            "[[Knowledge/Concepts/A\x01B]]",
            "[[Knowledge/Concepts/A\x7fB]]",
            "[[Knowledge/Concepts/A?B]]",
            "[[C:/Knowledge/Concepts/A]]",
            "[[ Knowledge/Concepts/A]]",
            "[[Knowledge/Concepts/A.MD]]",
        )
        for value in invalid:
            with self.subTest(value=repr(value)):
                with self.assertRaises(NativeNoteError):
                    parse_note_link(value)

    def test_unknown_kgd_property_fails_but_body_legacy_markers_are_plain_text(self) -> None:
        data = concept_bytes().replace(
            b"kgd_id: measure-space\n",
            b"kgd_id: measure-space\nkgd_surprise: true\n",
        )
        with self.assertRaisesRegex(NativeNoteError, "unknown kgdistiller"):
            parse_native_markdown(
                data, authority="Knowledge/Concepts/Measure Space.md"
            )
        legacy = concept_bytes().replace(
            b"A measure space is curated.",
            b"#kn[ghost] --[[ghost]]-- \\kn{ghost}",
        )
        note = parse_native_markdown(
            legacy, authority="Knowledge/Concepts/Measure Space.md"
        )
        self.assertIn("#kn[ghost]", note.body)
        self.assertEqual((), note.implies)

    def test_missing_h1_and_oversized_note_fail_closed(self) -> None:
        missing_h1 = concept_bytes().replace(b"# Measure space", b"Measure space")
        with self.assertRaisesRegex(NativeNoteError, "must contain an H1"):
            parse_native_markdown(
                missing_h1, authority="Knowledge/Concepts/Measure Space.md"
            )
        oversized = concept_bytes() + b"x" * (MAX_NOTE_BYTES + 1)
        with self.assertRaisesRegex(NativeNoteError, "exceeds"):
            parse_native_markdown(
                oversized, authority="Knowledge/Concepts/Measure Space.md"
            )

    def test_new_modules_parse_as_python_39_and_alias_is_not_pep604_runtime(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for relative in (
            "src/kgdistiller/native_notes.py",
            "src/kgdistiller/native_compiler.py",
        ):
            source = (root / relative).read_text(encoding="utf-8")
            with self.subTest(module=relative):
                ast.parse(source, filename=relative, feature_version=(3, 9))
        import kgdistiller.native_notes as notes

        self.assertEqual("typing.Union", str(notes.NativeNote).split("[")[0])


if __name__ == "__main__":
    unittest.main()
