from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from kgdistiller.cli import KnowledgeError, apply_delta, load_state, synchronize
from kgdistiller.derivation import install_derivation
from kgdistiller.entry_markdown import (
    ENTRY_SCHEMA,
    EntryMarkdownError,
    entry_with_kind,
    normalize_entry,
    parse_entry,
    render_entry,
    resolve_entry_source,
)
from kgdistiller.obsidian_export import build_obsidian_projection
from kgdistiller.project import initialize_project
from kgdistiller.query import get


class EntryMarkdownAuthorityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgd-entry-md-")
        self.repo = Path(self.temporary.name)
        self.registry = self.repo / ".knowledge/sources.json"
        self.graph = self.repo / ".knowledge/graph"
        self.identities = self.repo / ".knowledge/identities.json"
        self.alignments = self.repo / ".knowledge/alignments.json"
        self.typst_registry = self.repo / ".knowledge/build/knowledge-registry.typ"
        initialize_project(
            self.repo,
            self.registry,
            source_root=Path("notes"),
            alignments=self.alignments,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def sync(self, *, write: bool = True):
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
            write=write,
        )

    def write_delta(self, payload: dict) -> Path:
        path = self.repo / ".knowledge/build/entry.delta.json"
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

        parsed = parse_entry(self.repo / ".knowledge/entries/measure-space.md")
        self.assertEqual(entry, parsed["entry"])
        self.sync()
        queried = get(self.graph, "measure-space")["node"]
        self.assertEqual(entry, queried["entry"])
        self.assertEqual("current", queried["properties"]["curation_status"])
        self.assertNotIn("sigma-algebra", load_state(self.graph).nodes)

        output = self.repo / ".knowledge/build/obsidian"
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
            with (
                self.subTest(value=invalid),
                self.assertRaisesRegex(EntryMarkdownError, "understanding"),
            ):
                normalize_entry({"understanding": invalid})
        for invalid in ("mastered", ""):
            with self.subTest(manual=invalid):
                path.write_text(content.replace("\nunknown\n", f"\n{invalid}\n"), encoding="utf-8")
                with self.assertRaisesRegex(EntryMarkdownError, "understanding"):
                    parse_entry(path)

    def test_reviewed_kind_is_durable_in_entry_and_survives_graph_rebuild(self) -> None:
        authority = self.repo / "notes/chapter.typ"
        authority.write_text("#definition(title: [#kn[Measure space]])[Body.]\n", encoding="utf-8")
        self.sync()
        self.apply_entry(text="Reviewed summary.", properties={"kind": "custom-concept"})
        path = self.repo / ".knowledge/entries/measure-space.md"
        self.assertEqual("custom-concept", parse_entry(path)["metadata"]["kgd_kind"])
        shutil.rmtree(self.graph)
        state, _, _ = self.sync()
        self.assertEqual("custom-concept", state.nodes["measure-space"]["properties"]["kind"])
        self.assertEqual("reviewed", state.nodes["measure-space"]["properties"]["kind_origin"])
        self.assertEqual("definition", state.nodes["measure-space"]["properties"]["source_kind"])

        self.apply_entry(properties={"kind": "reclassified-concept"})
        self.assertEqual("reclassified-concept", parse_entry(path)["metadata"]["kgd_kind"])
        self.sync()
        self.assertEqual("reclassified-concept", load_state(self.graph).nodes["measure-space"]["properties"]["kind"])

    def test_invalid_reviewed_kind_is_rejected_when_rendered_or_manually_edited(self) -> None:
        arguments = {
            "node_id": "measure-space", "label": "Measure space", "entry": {"summary": "A definition."},
            "source": "notes/chapter.typ", "source_sha256": "source", "definition_sha256": "definition",
        }
        path = self.repo / "entry.md"
        content = render_entry(**arguments, kind="custom-concept")
        for invalid in ("", "two\nlines", " padded ", 42):
            with self.subTest(kind=invalid):
                with self.assertRaises(EntryMarkdownError):
                    render_entry(**arguments, kind=invalid)
                path.write_text(content.replace('kgd_kind: "custom-concept"', f"kgd_kind: {json.dumps(invalid)}"), encoding="utf-8")
                with self.assertRaises(EntryMarkdownError):
                    parse_entry(path)

    def test_kind_only_plan_preserves_existing_entry_bytes_and_does_not_write(self) -> None:
        path = self.repo / "entry.md"
        for previous_kind in (None, "old-kind"):
            with self.subTest(previous_kind=previous_kind):
                content = render_entry(
                    node_id="measure-space", label="Measure space", entry={"summary": "An existing explanation."},
                    source="notes/chapter.typ", source_sha256="old-source", definition_sha256="old-definition",
                    kind=previous_kind,
                ).replace("\n", "\r\n")
                path.write_bytes(content.encode("utf-8"))
                planned = entry_with_kind(path, "new-kind")
                self.assertEqual(content.encode("utf-8"), path.read_bytes())
                restored = (
                    planned.replace('kgd_kind: "new-kind"', 'kgd_kind: "old-kind"')
                    if previous_kind else planned.replace('kgd_kind: "new-kind"\r\n', "")
                )
                self.assertEqual(content, restored)
        link = self.repo / "linked-entry.md"
        link.symlink_to(path)
        with self.assertRaisesRegex(EntryMarkdownError, "ordinary file"):
            entry_with_kind(link, "new-kind")

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

        entry = self.repo / ".knowledge/entries/measure-space.md"
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
            ".knowledge/entries/measure-space.md",
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
        entry = self.repo / ".knowledge/entries/measure-space.md"
        entry.write_text(
            entry.read_text(encoding="utf-8").replace(
                "Original summary.", "Edited directly in Obsidian."
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(KnowledgeError, "out of sync"):
            load_state(self.graph)
        state, _, _ = self.sync()

        self.assertEqual("Edited directly in Obsidian.", state.nodes["measure-space"]["text"])
        self.assertEqual(
            "Edited directly in Obsidian.",
            load_state(self.graph).nodes["measure-space"]["text"],
        )

    def _assert_native_entry_lifecycle(self, extension: str, content: str) -> None:
        authority = self.repo / f"notes/chapter.{extension}"
        authority.write_text(content, encoding="utf-8")
        self.sync()
        self.apply_entry(text="Reviewed summary.")
        path = self.repo / ".knowledge/entries/measure-space.md"
        self.assertEqual(f"notes/chapter.{extension}", parse_entry(path)["metadata"]["kgd_source"])
        self.assertFalse(any((self.repo / ".knowledge/derived").rglob("*.md")))

        # Editing another marked definition does not invalidate this entry.
        authority.write_text(content.replace("Unrelated.", "A revised unrelated definition."), encoding="utf-8")
        state, _, _ = self.sync()
        self.assertEqual("current", state.nodes["measure-space"]["properties"]["curation_status"])

        authority.write_text(content.replace("Body.", "A changed definition."), encoding="utf-8")
        state, _, _ = self.sync()
        self.assertEqual("needs-review", state.nodes["measure-space"]["properties"]["curation_status"])
        self.apply_entry(text="Refreshed after reviewing the native definition.")
        state, _, _ = self.sync()
        self.assertEqual("current", state.nodes["measure-space"]["properties"]["curation_status"])
        self.assertEqual("Refreshed after reviewing the native definition.", state.nodes["measure-space"]["text"])

    def test_typst_entry_uses_native_authority_and_refreshes_without_conversion(self) -> None:
        self._assert_native_entry_lifecycle("typ", (
            "#definition(title: [#kn[Measure space]])[Body.]\n\n"
            "#definition(title: [#kn[Other concept]])[Unrelated.]\n"
        ))

    def test_latex_entry_uses_native_authority_and_refreshes_without_conversion(self) -> None:
        self._assert_native_entry_lifecycle("tex", (
            "\\begin{definition}\\kn{Measure space} Body.\\end{definition}\n\n"
            "\\begin{definition}\\kn{Other concept} Unrelated.\\end{definition}\n"
        ))

    def test_explicit_historical_derived_source_is_preserved_and_tracks_its_hash(self) -> None:
        authority = self.repo / "notes/chapter.typ"
        authority.write_text("#definition(title: [#kn[Measure space]])[Body.]\n", encoding="utf-8")
        self.sync()
        delta = self.write_delta(
            {
                "schema": "kgdistiller-agent-delta-v1",
                "nodes": [{
                    "id": "measure-space", "text": "Reviewed summary.",
                    "entry_source": ".knowledge/derived/by-source/notes/chapter.typ.md",
                }],
            }
        )
        with self.assertRaisesRegex(KnowledgeError, "derived/by-source/notes/chapter.typ.md"):
            apply_delta(self.graph, self.typst_registry, delta, repo_root=self.repo)

        derived = self.repo / ".knowledge/derived/by-source/notes/chapter.typ.md"
        derived.parent.mkdir(parents=True, exist_ok=True)
        derived.write_text("# Converted chapter\n", encoding="utf-8")
        apply_delta(self.graph, self.typst_registry, delta, repo_root=self.repo)
        self.apply_entry(text="An updated summary retains its explicit evidence link.")
        parsed = parse_entry(self.repo / ".knowledge/entries/measure-space.md")
        self.assertEqual(".knowledge/derived/by-source/notes/chapter.typ.md", parsed["metadata"]["kgd_source"])
        derived.write_text("# Changed conversion\n", encoding="utf-8")

        state, _, _ = self.sync()

        self.assertEqual(
            "needs-review",
            state.nodes["measure-space"]["properties"]["curation_status"],
        )

    def test_resolve_source_has_no_implicit_derived_fallback(self) -> None:
        derived = self.repo / ".knowledge/derived/by-source/notes/missing.typ.md"
        derived.parent.mkdir(parents=True, exist_ok=True)
        derived.write_text("Old converted evidence.", encoding="utf-8")
        node = {"id": "missing", "provenance": {"authority": "notes/missing.typ"}}
        with self.assertRaisesRegex(EntryMarkdownError, "does not exist: notes/missing.typ"):
            resolve_entry_source(self.repo, node)
        self.assertEqual(derived.relative_to(self.repo).as_posix(), resolve_entry_source(
            self.repo, node, derived.relative_to(self.repo).as_posix(),
        )[0])
        node["provenance"]["authority"] = "notes/missing.pdf"
        with self.assertRaisesRegex(EntryMarkdownError, "explicit"):
            resolve_entry_source(self.repo, node)

    def test_resolve_source_rejects_unsafe_missing_and_non_file_paths(self) -> None:
        node = {"id": "test"}
        for value in ("../outside.md", str(self.repo / "notes/absolute.md"), "notes/source.pdf", "notes/a\nb.md"):
            with self.subTest(value=value), self.assertRaisesRegex(EntryMarkdownError, "vault-relative"):
                resolve_entry_source(self.repo, node, value)
        for value in ("notes/missing.typ", "notes/missing.tex"):
            with self.subTest(value=value), self.assertRaisesRegex(EntryMarkdownError, "does not exist"):
                resolve_entry_source(self.repo, node, value)
        directory = self.repo / "notes/directory.tex"
        directory.mkdir()
        with self.assertRaisesRegex(EntryMarkdownError, "does not exist"):
            resolve_entry_source(self.repo, node, "notes/directory.tex")
        target = self.repo / "notes/real.typ"
        target.write_text("#kn[Example]", encoding="utf-8")
        (self.repo / "notes/link.typ").symlink_to(target)
        with self.assertRaisesRegex(EntryMarkdownError, "symlinks"):
            resolve_entry_source(self.repo, node, "notes/link.typ")
        (self.repo / "linked").symlink_to(self.repo / "notes", target_is_directory=True)
        with self.assertRaisesRegex(EntryMarkdownError, "symlinks"):
            resolve_entry_source(self.repo, node, "linked/real.typ")

    def test_native_source_relocation_is_planned_without_writing_during_dry_run(self) -> None:
        authority = self.repo / "notes/chapter.typ"
        authority.write_text("#definition(title: [#kn[Measure space]])[Body.]\n", encoding="utf-8")
        self.sync()
        self.apply_entry(text="A durable reviewed entry.", properties={"kind": "custom-concept"})
        entry = self.repo / ".knowledge/entries/measure-space.md"
        before = entry.read_text()
        metadata = parse_entry(entry)["metadata"]
        authority.rename(self.repo / "notes/renamed.typ")
        state, _, _ = self.sync(write=False)
        self.assertEqual(before, entry.read_text())
        self.assertEqual("notes/renamed.typ", state.nodes["measure-space"]["properties"]["entry_source"])

        self.sync()
        self.assertEqual(
            before.replace('kgd_source: "notes/chapter.typ"', 'kgd_source: "notes/renamed.typ"'),
            entry.read_text(),
        )
        after = parse_entry(entry)["metadata"]
        self.assertEqual(metadata["kgd_source_sha256"], after["kgd_source_sha256"])
        self.assertEqual(metadata["kgd_definition_sha256"], after["kgd_definition_sha256"])
        state, _, _ = self.sync()
        self.assertEqual("current", state.nodes["measure-space"]["properties"]["curation_status"])

    def test_relocated_native_definition_with_changed_content_stays_stale(self) -> None:
        authority = self.repo / "notes/chapter.tex"
        authority.write_text("\\begin{definition}\\kn{Measure space} Body.\\end{definition}\n", encoding="utf-8")
        self.sync()
        self.apply_entry(text="A durable reviewed entry.")
        relocated = self.repo / "notes/renamed.tex"
        authority.rename(relocated)
        relocated.write_text(relocated.read_text().replace("Body.", "Changed scientific content."), encoding="utf-8")
        state, _, _ = self.sync()
        self.assertEqual("needs-review", state.nodes["measure-space"]["properties"]["curation_status"])
        self.assertEqual("A durable reviewed entry.", state.nodes["measure-space"]["text"])

    def test_explicit_derived_binding_does_not_follow_native_relocation(self) -> None:
        authority = self.repo / "notes/chapter.typ"
        authority.write_text("#definition(title: [#kn[Measure space]])[Body.]\n", encoding="utf-8")
        derived = self.repo / ".knowledge/derived/by-source/notes/chapter.typ.md"
        derived.parent.mkdir(parents=True, exist_ok=True)
        derived.write_text("Reviewed derived evidence.", encoding="utf-8")
        self.sync()
        self.apply_entry(text="A durable reviewed entry.", entry_source=derived.relative_to(self.repo).as_posix())
        entry = self.repo / ".knowledge/entries/measure-space.md"
        before = entry.read_text()
        authority.rename(self.repo / "notes/renamed.typ")
        self.sync()
        self.assertEqual(before, entry.read_text())

    def test_internal_pdf_derivation_is_scanned_and_preserves_review_chain(self) -> None:
        pdf = self.repo / "papers/paper.pdf"
        pdf.parent.mkdir()
        pdf.write_bytes(b"%PDF-version-one")
        converted = self.repo / ".knowledge/build/paper.converted.md"
        converted.parent.mkdir(parents=True, exist_ok=True)
        converted.write_text("--[[PDF concept]]--\n", encoding="utf-8")
        installed = install_derivation(pdf, converted)
        self.assertEqual(
            ".knowledge/derived/by-source/papers/paper.pdf.md",
            installed["output"],
        )
        self.sync()
        delta = self.write_delta(
            {
                "schema": "kgdistiller-agent-delta-v1",
                "nodes": [{
                    "id": "pdf-concept", "text": "Reviewed PDF concept.",
                    "entry_source": installed["output"],
                }],
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
