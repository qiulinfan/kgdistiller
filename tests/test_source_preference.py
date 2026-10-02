from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kgdistiller import cli as knowledge
from kgdistiller.ingest import IngestPaths, _prepare_shadow
from kgdistiller.obsidian_export import (
    ObsidianExportError,
    _current_authority_hashes,
    _require_fresh_authorities,
)
from kgdistiller.query import load_graph_view
from kgdistiller.static_export import StaticExportError, build_site_graph
from kgdistiller.store import StoreError, _document_inventory


class PairedSourcePreferenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-source-pair-")
        self.root = Path(self.temporary.name).resolve()
        self.sources = self.root / "notes"
        self.sources.mkdir()
        self.registry = self.root / "knowledge/sources.json"
        self.registry.parent.mkdir()
        self.graph = self.root / "knowledge/graph"
        self.identities = self.root / "knowledge/identities.json"
        self.generated = self.root / "knowledge/build/knowledge-registry.typ"
        self.write_registry(["**/*.typ", "**/*.tex", "**/*.md"])
        def labels(state, _root):
            for node in state.nodes.values():
                if node["type"] == "knowledge":
                    node["properties"]["label_html"] = "<span>Fixture label</span>"

        self.labels = patch("kgdistiller.cli.render_source_labels", side_effect=labels)
        self.labels.start()
        self.addCleanup(self.labels.stop)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_registry(self, patterns: list[str]) -> None:
        self.registry.write_text(
            json.dumps({
                "schema": knowledge.SOURCE_SCHEMA,
                "fields": [{"id": "math", "label": "Mathematics"}],
                "sources": [{"id": "notes", "root": "notes", "files": patterns, "fields": ["math"]}],
            }),
            encoding="utf-8",
        )

    def write(self, name: str, content: str) -> Path:
        path = self.sources / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def sync(self, *files: Path):
        return knowledge.synchronize(
            self.root, self.registry, self.graph, self.generated,
            identities=self.identities, files=list(files), course=None, subject=None, write=True,
        )

    def spec(self):
        return knowledge.load_sources(self.root, self.registry)[0]

    def knowledge_ids(self, state):
        return {node_id for node_id, node in state.nodes.items() if node["type"] == "knowledge"}

    def test_paired_authority_and_references_use_tex_independent_of_pattern_order(self) -> None:
        typst = self.write("chapter.typ", "#kn[shared]\n#kn[ignored Typst]\n#ref[shared]\n")
        latex = self.write("chapter.tex", "\\section{Not an identity}\n\\kn{shared}\n\\knref{shared}\n")
        for patterns in (["**/*.typ", "**/*.tex"], ["**/*.tex", "**/*.typ"]):
            with self.subTest(patterns=patterns):
                self.write_registry(patterns)
                self.assertEqual([latex], knowledge.expand_source(self.spec()))
                state, _, report = self.sync()
                self.assertEqual(1, report["definitions"])
                self.assertEqual({"shared"}, self.knowledge_ids(state))
                self.assertEqual("notes/chapter.tex", state.nodes["shared"]["provenance"]["authority"])
                self.assertEqual(1, len(state.references))
                self.assertEqual("latex", state.references[0]["source_format"])
                self.assertEqual({"notes/chapter.tex"}, set(state.manifest["source_hashes"]))
                self.assertFalse(knowledge.source_matches_path(self.spec(), typst))
                with self.assertRaisesRegex(knowledge.KnowledgeError, "superseded"):
                    knowledge.unique_source_for_path([self.spec()], typst)

    def test_typ_only_tex_only_markdown_and_same_stem_in_other_directory_stay_independent(self) -> None:
        typst = self.write("left/chapter.typ", "#kn[Typst only]\n")
        latex = self.write("right/chapter.tex", "\\kn{TeX only}\n")
        markdown = self.write("right/chapter.md", "--[[Markdown authority]]--\n")
        state, _, report = self.sync()
        self.assertEqual([typst, markdown, latex], knowledge.expand_source(self.spec()))
        self.assertEqual(3, report["definitions"])
        self.assertEqual({"typst-only", "tex-only", "markdown-authority"}, self.knowledge_ids(state))

    def test_unregistered_tex_sibling_does_not_expand_registry_bounds(self) -> None:
        typst = self.write("chapter.typ", "#kn[registered]\n")
        latex = self.write("chapter.tex", "\\kn{unregistered}\n")
        self.write_registry(["**/*.typ"])
        self.assertEqual([typst], knowledge.expand_source(self.spec()))
        state, _, _ = self.sync()
        self.assertEqual({"registered"}, self.knowledge_ids(state))
        with self.assertRaisesRegex(knowledge.KnowledgeError, "not admitted"):
            knowledge.unique_source_for_path([self.spec()], latex)

    def test_explicit_typst_tex_and_directory_scopes_choose_one_tex_authority(self) -> None:
        typst = self.write("chapter.typ", "#kn[shared]\n")
        latex = self.write("chapter.tex", "\\kn{shared}\n")
        for files in ([typst], [latex], [typst, latex], [latex, typst], [self.sources]):
            with self.subTest(files=files):
                pairs, keys, full = knowledge.select_scope(self.root, [self.spec()], files, None, None)
                self.assertEqual([latex], [path for _, path in pairs])
                self.assertEqual({"notes/chapter.tex"}, keys)
                self.assertFalse(full)

    def test_full_and_incremental_switch_retire_old_typst_hash_and_references(self) -> None:
        for requested in ("all", "typst", "tex"):
            with self.subTest(requested=requested), tempfile.TemporaryDirectory() as nested:
                # Each migration starts from the same Typst-only generation.
                self.graph = Path(nested) / "graph"
                typst = self.write("chapter.typ", "#kn[shared]\n#ref[shared]\n")
                latex = self.sources / "chapter.tex"
                latex.unlink(missing_ok=True)
                before, _, _ = self.sync()
                latex.write_text("\\kn{shared}\n\\knref{shared}\n", encoding="utf-8")
                files = {"all": (), "typst": (typst,), "tex": (latex,)}[requested]
                after, _, report = self.sync(*files)
                self.assertEqual(set(before.nodes), set(after.nodes))
                self.assertEqual([], report["orphaned"])
                self.assertEqual("notes/chapter.tex", after.nodes["shared"]["provenance"]["authority"])
                self.assertEqual({"notes/chapter.tex"}, set(after.manifest["source_hashes"]))
                self.assertEqual(["notes/chapter.tex"], [item["authority"] for item in after.references])
                self.assertEqual(after.manifest["source_hashes"], _current_authority_hashes(self.root, self.registry))
                documents, _ = _document_inventory(self.root, self.registry, after)
                self.assertEqual(["notes/chapter.tex"], [item["authority"] for item in documents])

    def test_deleted_tex_falls_back_to_registered_typst_on_incremental_sync(self) -> None:
        self.write("chapter.typ", "#kn[shared]\n#ref[shared]\n")
        latex = self.write("chapter.tex", "\\kn{shared}\n\\knref{shared}\n")
        self.sync()
        latex.unlink()
        state, _, report = self.sync(latex)
        self.assertEqual([], report["orphaned"])
        self.assertEqual("notes/chapter.typ", state.nodes["shared"]["provenance"]["authority"])
        self.assertEqual({"notes/chapter.typ"}, set(state.manifest["source_hashes"]))

    def test_converted_math_marker_requires_explicit_alias_before_writing_generation(self) -> None:
        original_name = "$L^(+)$ space"
        converted_name = "$L^{+}$ space"
        self.write("chapter.typ", f"#kn[{original_name}]\n#ref[{original_name}]\n")
        before, _, _ = self.sync()
        old_id = next(iter(self.knowledge_ids(before)))
        self.write("chapter.tex", f"\\kn{{{converted_name}}}\n\\knref{{{converted_name}}}\n")
        before_files = {path.relative_to(self.graph): path.read_bytes() for path in self.graph.rglob("*") if path.is_file()}
        generated = self.generated.read_bytes()
        with self.assertRaisesRegex(knowledge.KnowledgeError, "explicit alias"):
            self.sync()
        self.assertEqual(before_files, {path.relative_to(self.graph): path.read_bytes() for path in self.graph.rglob("*") if path.is_file()})
        self.assertEqual(generated, self.generated.read_bytes())

        self.identities.write_text(json.dumps({
            "schema": knowledge.IDENTITY_SCHEMA,
            "identities": [{
                "id": old_id,
                "canonical_name": knowledge.strip_typst(original_name),
                "aliases": [knowledge.strip_latex_name(converted_name)],
            }],
        }), encoding="utf-8")
        after, _, report = self.sync()
        self.assertEqual({old_id}, self.knowledge_ids(after))
        self.assertEqual([], report["orphaned"])
        self.assertEqual([old_id], [item["target"] for item in after.references])
        self.assertEqual("notes/chapter.tex", after.nodes[old_id]["provenance"]["authority"])
        self.assertIn(knowledge.strip_typst(original_name), after.nodes[old_id]["properties"]["aliases"])

    def test_literal_caret_tex_escape_preserves_a_plain_typst_marker_identity(self) -> None:
        name = "Riemann integrability in R^n"
        self.write("chapter.typ", f"#kn[{name}]\n#ref[{name}]\n")
        before, _, _ = self.sync()
        old_id = next(iter(self.knowledge_ids(before)))
        native = r"Riemann integrability in R\^{}n"
        self.write("chapter.tex", f"\\kn{{{native}}}\n\\knref{{{native}}}\n")
        after, _, report = self.sync()
        self.assertEqual({old_id}, self.knowledge_ids(after))
        self.assertEqual([], report["orphaned"])
        self.assertEqual([old_id], [item["target"] for item in after.references])
        self.assertEqual("notes/chapter.tex", after.nodes[old_id]["provenance"]["authority"])

    def test_shadowed_typst_change_does_not_change_projection_inventory(self) -> None:
        typst = self.write("chapter.typ", "#kn[shared]\n")
        self.write("chapter.tex", "\\kn{shared}\n")
        state, _, _ = self.sync()
        typst.write_text("#kn[ignored change]\n", encoding="utf-8")
        self.assertEqual(state.manifest["source_hashes"], _current_authority_hashes(self.root, self.registry))
        documents, _ = _document_inventory(self.root, self.registry, state)
        self.assertEqual(["notes/chapter.tex"], [item["authority"] for item in documents])

    def test_exports_reject_old_typst_generation_until_pair_is_synchronized(self) -> None:
        self.write("chapter.typ", "#kn[shared]\n#ref[shared]\n")
        before, _, _ = self.sync()
        self.write("chapter.tex", "\\kn{shared}\n\\knref{shared}\n")
        with self.assertRaisesRegex(ObsidianExportError, "run kgdistiller sync"):
            _require_fresh_authorities(self.root, self.registry, load_graph_view(self.graph))
        with self.assertRaisesRegex(StoreError, "run kgdistiller sync"):
            _document_inventory(self.root, self.registry, before)
        with self.assertRaisesRegex(StaticExportError, "authority.*registered"):
            build_site_graph(self.root, self.registry, before)
        after, _, _ = self.sync()
        _require_fresh_authorities(self.root, self.registry, load_graph_view(self.graph))
        build_site_graph(self.root, self.registry, after)

    def test_ingest_shadow_preserves_both_registered_variants_for_fallback(self) -> None:
        self.write("chapter.typ", "#kn[shared]\n")
        self.write("chapter.tex", "\\kn{shared}\n")
        paths = IngestPaths(
            self.root, self.registry, self.graph,
            self.root / "knowledge/identities.json", self.root / "knowledge/alignments.json",
            self.generated,
        )
        with tempfile.TemporaryDirectory(prefix="kgdistiller-pair-shadow-") as directory:
            shadow = _prepare_shadow(paths, Path(directory).resolve())
            self.assertTrue((shadow.repo_root / "notes/chapter.typ").is_file())
            self.assertTrue((shadow.repo_root / "notes/chapter.tex").is_file())
            self.assertEqual(
                [shadow.repo_root / "notes/chapter.tex"],
                knowledge.expand_source(knowledge.load_sources(shadow.repo_root, shadow.registry)[0]),
            )


if __name__ == "__main__":
    unittest.main()
