from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kgdistiller.cli import KnowledgeError, apply_delta, load_state, synchronize
from kgdistiller.entry_markdown import ENTRY_SCHEMA
from kgdistiller.derivation import install_derivation
from kgdistiller.project import initialize_project


class EntryMarkdownAuthorityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgd-entry-md-")
        self.repo = Path(self.temporary.name)
        self.registry = self.repo / "knowledge/sources.json"
        self.graph = self.repo / "knowledge/graph"
        self.identities = self.repo / "knowledge/identities.json"
        self.alignments = self.repo / "knowledge/alignments.json"
        self.typst_registry = self.repo / "knowledge/build/knowledge-registry.typ"
        initialize_project(
            self.repo,
            self.registry,
            source_root=Path("notes"),
            alignments=self.alignments,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def sync(self):
        return synchronize(
            self.repo,
            self.registry,
            self.graph,
            self.typst_registry,
            identities=self.identities,
            alignments=self.alignments,
            files=[],
            course=None,
            subject=None,
            write=True,
        )

    def write_delta(self, payload: dict) -> Path:
        path = self.repo / "knowledge/build/entry.delta.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_apply_writes_obsidian_visible_markdown_authority(self) -> None:
        authority = self.repo / "notes/chapter.md"
        authority.write_text("--[[Measure space]]--\n", encoding="utf-8")
        self.sync()
        delta = self.write_delta(
            {
                "schema": "kgdistiller-agent-delta-v1",
                "nodes": [
                    {
                        "id": "measure-space",
                        "entry": {
                            "summary": "A measurable space equipped with a measure.",
                            "prerequisites": ["sigma-algebra"],
                        },
                    }
                ],
                "edges": [],
            }
        )

        apply_delta(
            self.graph,
            self.typst_registry,
            delta,
            repo_root=self.repo,
        )

        entry = self.repo / "knowledge/entries/measure-space.md"
        content = entry.read_text(encoding="utf-8")
        self.assertIn(f'kgd_schema: "{ENTRY_SCHEMA}"', content)
        self.assertIn('kgd_source: "notes/chapter.md"', content)
        self.assertIn("## Summary", content)
        self.assertIn("## Prerequisites", content)
        manifest = json.loads(
            (self.graph / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            "kgdistiller-entry-index-v1",
            manifest["entry_authorities"]["schema"],
        )
        self.assertEqual(
            "knowledge/entries/measure-space.md",
            manifest["entry_authorities"]["entries"][0]["path"],
        )
        self.assertNotIn("text", next(
            json.loads(line)
            for line in (self.graph / "nodes.jsonl").read_text(encoding="utf-8").splitlines()
            if json.loads(line)["id"] == "measure-space"
        ))

    def test_manual_entry_edit_is_the_next_graph_source(self) -> None:
        authority = self.repo / "notes/chapter.md"
        authority.write_text("--[[Measure space]]--\n", encoding="utf-8")
        self.sync()
        delta = self.write_delta(
            {
                "schema": "kgdistiller-agent-delta-v1",
                "nodes": [{"id": "measure-space", "text": "Original summary."}],
            }
        )
        apply_delta(self.graph, self.typst_registry, delta, repo_root=self.repo)
        entry = self.repo / "knowledge/entries/measure-space.md"
        entry.write_text(
            entry.read_text(encoding="utf-8").replace(
                "Original summary.", "Edited directly in Obsidian."
            ),
            encoding="utf-8",
        )

        state, _, _ = self.sync()

        self.assertEqual("Edited directly in Obsidian.", state.nodes["measure-space"]["text"])
        self.assertEqual(
            "Edited directly in Obsidian.",
            load_state(self.graph).nodes["measure-space"]["text"],
        )

    def test_typst_entry_requires_derived_markdown_and_tracks_its_hash(self) -> None:
        authority = self.repo / "notes/chapter.typ"
        authority.write_text("#definition(title: [#kn[Measure space]])[Body.]\n", encoding="utf-8")
        self.sync()
        delta = self.write_delta(
            {
                "schema": "kgdistiller-agent-delta-v1",
                "nodes": [{"id": "measure-space", "text": "Reviewed summary."}],
            }
        )
        with self.assertRaisesRegex(KnowledgeError, "derived/by-source/notes/chapter.typ.md"):
            apply_delta(self.graph, self.typst_registry, delta, repo_root=self.repo)

        derived = self.repo / "knowledge/derived/by-source/notes/chapter.typ.md"
        derived.parent.mkdir(parents=True, exist_ok=True)
        derived.write_text("# Converted chapter\n", encoding="utf-8")
        apply_delta(self.graph, self.typst_registry, delta, repo_root=self.repo)
        derived.write_text("# Changed conversion\n", encoding="utf-8")

        state, _, _ = self.sync()

        self.assertEqual(
            "needs-review",
            state.nodes["measure-space"]["properties"]["curation_status"],
        )

    def test_internal_pdf_derivation_is_scanned_and_preserves_review_chain(self) -> None:
        pdf = self.repo / "papers/paper.pdf"
        pdf.parent.mkdir()
        pdf.write_bytes(b"%PDF-version-one")
        converted = self.repo / "knowledge/build/paper.converted.md"
        converted.parent.mkdir(parents=True, exist_ok=True)
        converted.write_text("--[[PDF concept]]--\n", encoding="utf-8")
        installed = install_derivation(pdf, converted)
        self.assertEqual(
            "knowledge/derived/by-source/papers/paper.pdf.md",
            installed["output"],
        )
        self.sync()
        delta = self.write_delta(
            {
                "schema": "kgdistiller-agent-delta-v1",
                "nodes": [{"id": "pdf-concept", "text": "Reviewed PDF concept."}],
            }
        )
        apply_delta(self.graph, self.typst_registry, delta, repo_root=self.repo)

        pdf.write_bytes(b"%PDF-version-two")
        install_derivation(pdf, converted, replace=True)
        state, _, _ = self.sync()

        self.assertEqual(
            "needs-review",
            state.nodes["pdf-concept"]["properties"]["curation_status"],
        )


if __name__ == "__main__":
    unittest.main()
