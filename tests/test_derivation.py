from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kgdistiller.derivation import (
    DERIVED_SCHEMA,
    DerivationError,
    install_derivation,
    plan_derivation,
)
from kgdistiller.cli import synchronize
from kgdistiller.project import initialize_project


class DerivationPlacementTest(unittest.TestCase):
    def make_vault(self, root: Path) -> None:
        initialize_project(
            root,
            root / "knowledge/sources.json",
            source_root=Path("notes"),
        )

    def test_nearest_enclosing_vault_owns_internal_derivation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgd-derive-") as temporary:
            outer = Path(temporary) / "outer"
            outer.mkdir()
            self.make_vault(outer)
            inner = outer / "nested"
            inner.mkdir()
            self.make_vault(inner)
            source = inner / "drafts/chapter.typ"
            source.parent.mkdir()
            source.write_text("= Chapter\n", encoding="utf-8")

            plan = plan_derivation(source)

            self.assertEqual(str(inner.resolve()), plan["vault"])
            self.assertEqual(
                "knowledge/derived/by-source/drafts/chapter.typ.md", plan["output"]
            )
            self.assertFalse(plan["external_source"])

    def test_internal_install_records_upstream_without_touching_editor_tree(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgd-derive-") as temporary:
            vault = Path(temporary) / "vault"
            vault.mkdir()
            self.make_vault(vault)
            source = vault / "notes/chapter.tex"
            source.write_text("\\section{Chapter}\n", encoding="utf-8")
            converted = Path(temporary) / "converted.md"
            converted.write_text("# Chapter\n\nConverted content.\n", encoding="utf-8")

            result = install_derivation(source, converted)

            destination = Path(result["destination"])
            content = destination.read_text(encoding="utf-8")
            self.assertEqual(
                (vault / "knowledge/derived/by-source/notes/chapter.tex.md").resolve(),
                destination,
            )
            self.assertIn(f'kgd_schema: "{DERIVED_SCHEMA}"', content)
            self.assertIn('kgd_source: "notes/chapter.tex"', content)
            self.assertIn('kgd_source_format: "latex"', content)
            self.assertFalse((vault / "notes/derived").exists())
            self.assertEqual("\\section{Chapter}\n", source.read_text(encoding="utf-8"))

    def test_external_source_requires_explicit_vault_and_becomes_new_origin(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgd-derive-") as temporary:
            root = Path(temporary)
            vault = root / "vault"
            vault.mkdir()
            self.make_vault(vault)
            external = root / "outside/paper.pdf"
            external.parent.mkdir()
            external.write_bytes(b"%PDF-fixture")
            converted = root / "paper.md"
            converted.write_text("# Paper\n\n--[[Paper concept]]--\n", encoding="utf-8")

            with self.assertRaisesRegex(DerivationError, "specify --repo-root or --vault"):
                plan_derivation(external)

            result = install_derivation(
                external,
                converted,
                target_vault=vault,
            )
            content = Path(result["destination"]).read_text(encoding="utf-8")
            metadata = content.split("---", 2)[1]
            self.assertTrue(result["external_source"])
            self.assertEqual("knowledge/derived/imports/paper.md", result["output"])
            self.assertIn("kgd_schema", metadata)
            self.assertNotIn("kgd_source:", metadata)
            self.assertNotIn("kgd_source_sha256", metadata)
            self.assertNotIn(str(external), content)
            state, _, _ = synchronize(
                vault,
                vault / "knowledge/sources.json",
                vault / "knowledge/graph",
                vault / "knowledge/build/knowledge-registry.typ",
                identities=vault / "knowledge/identities.json",
                alignments=vault / "knowledge/alignments.json",
                files=[],
                course=None,
                subject=None,
                write=True,
            )
            self.assertEqual(
                "knowledge/derived/imports/paper.md",
                state.nodes["paper-concept"]["provenance"]["authority"],
            )

    def test_output_cannot_escape_managed_derived_tree(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgd-derive-") as temporary:
            vault = Path(temporary) / "vault"
            vault.mkdir()
            self.make_vault(vault)
            source = vault / "notes/chapter.typ"
            source.write_text("= Chapter\n", encoding="utf-8")

            with self.assertRaisesRegex(DerivationError, "knowledge/derived"):
                plan_derivation(source, output=Path("notes/chapter.md"))


if __name__ == "__main__":
    unittest.main()
