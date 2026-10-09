from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kgdistiller.cli import KnowledgeError, apply_delta, load_state, synchronize
from kgdistiller.entry_markdown import (
    ENTRY_SCHEMA,
    EntryMarkdownError,
    normalize_entry,
    parse_entry,
    render_entry,
)
from kgdistiller.derivation import install_derivation
from kgdistiller.obsidian_export import build_obsidian_projection
from kgdistiller.project import initialize_project
from kgdistiller.query import get


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

    def apply_entry(self, **update) -> None:
        delta = self.write_delta(
            {
                "schema": "kgdistiller-agent-delta-v1",
                "nodes": [{"id": "measure-space", **update}],
            }
        )
        apply_delta(self.graph, self.typst_registry, delta, repo_root=self.repo)

    def test_learning_metadata_round_trips_without_creating_prerequisite_nodes(self) -> None:
        authority = self.repo / "notes/chapter.md"
        authority.write_text("--[[Measure space]]--\n", encoding="utf-8")
        self.sync()
        entry = {
            "summary": "A measurable space equipped with a measure.",
            "understanding": "not-yet-understood",
            "pending_prerequisites": [
                "sigma-algebra — which sets can receive a measure in this definition"
            ],
        }
        self.apply_entry(entry=entry)

        parsed = parse_entry(self.repo / "knowledge/entries/measure-space.md")
        self.assertEqual(entry, parsed["entry"])
        self.sync()
        queried = get(self.graph, "measure-space")["node"]
        self.assertEqual(entry, queried["entry"])
        self.assertEqual("current", queried["properties"]["curation_status"])
        self.assertNotIn("sigma-algebra", load_state(self.graph).nodes)

        output = self.repo / "knowledge/build/obsidian"
        build_obsidian_projection(
            self.repo,
            output,
            registry=self.registry,
            graph_dir=self.graph,
            identities=self.identities,
        )
        exported = (output / "concepts/Measure space.md").read_text(encoding="utf-8")
        self.assertIn("### Understanding\n\nnot-yet-understood", exported)
        self.assertIn("### Pending prerequisites", exported)
        self.assertIn(entry["pending_prerequisites"][0], exported)

    def test_absent_learning_state_stays_unknown_after_curation(self) -> None:
        authority = self.repo / "notes/chapter.md"
        authority.write_text("--[[Measure space]]--\n", encoding="utf-8")
        self.sync()
        self.apply_entry(text="A measurable space equipped with a measure.")
        self.sync()
        queried = get(self.graph, "measure-space")["node"]
        self.assertEqual("current", queried["properties"]["curation_status"])
        self.assertNotIn("understanding", queried["entry"])
        self.assertEqual("unknown", queried["entry"].get("understanding", "unknown"))
        self.assertNotIn("pending_prerequisites", queried["entry"])

    def test_entry_updates_preserve_learning_state_until_explicitly_changed(self) -> None:
        authority = self.repo / "notes/chapter.md"
        authority.write_text("--[[Measure space]]--\n", encoding="utf-8")
        self.sync()
        pending = ["sigma-algebra — collection of measurable sets"]
        self.apply_entry(entry={
            "summary": "Initial definition.",
            "understanding": "not-yet-understood",
            "pending_prerequisites": pending,
        })
        for update in (
            {"entry": {"summary": "Revised definition."}},
            {"text": "Updated source explanation."},
        ):
            with self.subTest(update=update):
                self.apply_entry(**update)
                entry = get(self.graph, "measure-space")["node"]["entry"]
                self.assertEqual("not-yet-understood", entry["understanding"])
                self.assertEqual(pending, entry["pending_prerequisites"])

        self.apply_entry(entry={
            "summary": "Reviewed definition.",
            "understanding": "understood",
            "pending_prerequisites": [],
        })
        entry = get(self.graph, "measure-space")["node"]["entry"]
        self.assertEqual("understood", entry["understanding"])
        self.assertEqual([], entry.get("pending_prerequisites", []))

    def test_invalid_understanding_is_rejected_when_written_or_manually_edited(self) -> None:
        content = render_entry(
            node_id="measure-space",
            label="Measure space",
            entry={"summary": "A measurable space with a measure.", "understanding": "unknown"},
            source="notes/chapter.md",
            source_sha256="source-version",
            definition_sha256="definition-version",
        )
        path = self.repo / "entry.md"
        for invalid in ("mastered", "", None, True):
            with self.subTest(value=invalid):
                with self.assertRaisesRegex(EntryMarkdownError, "understanding"):
                    normalize_entry({"understanding": invalid})
        for invalid in ("mastered", ""):
            with self.subTest(manual=invalid):
                path.write_text(content.replace("\nunknown\n", f"\n{invalid}\n"), encoding="utf-8")
                with self.assertRaisesRegex(EntryMarkdownError, "understanding"):
                    parse_entry(path)

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
