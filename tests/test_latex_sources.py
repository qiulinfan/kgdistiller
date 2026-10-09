"""Native TeX marker scanning and identity resolution."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kgdistiller.cli import (
    GraphState,
    KnowledgeError,
    SourceSpec,
    apply_delta,
    build_identity_index,
    latex_name_key,
    scan_latex,
    synchronize,
)
from kgdistiller.project import initialize_project


def node(node_id: str, name: str, authority: str = "notes/source.tex") -> dict:
    return {
        "id": node_id,
        "type": "knowledge",
        "label": "σ-algebra",
        "properties": {"source_format": "latex", "source_name": name},
        "provenance": {"authority": authority},
    }


class LatexSourceIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgd-latex-core-")
        self.root = Path(self.temporary.name).resolve()
        self.registry = self.root / ".knowledge/sources.json"
        self.graph = self.root / ".knowledge/graph"
        initialize_project(self.root, self.registry, source_root=Path("notes"), alignments=self.root / ".knowledge/alignments.json")
        self.source = self.root / "notes/source.tex"
        self.spec = SourceSpec("test", self.source.parent, ("*.tex",))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def sync(self):
        return synchronize(self.root, self.registry, self.graph, files=[], write=True)

    def test_literal_markers_do_not_create_identities_and_quotes_are_not_group_delimiters(self) -> None:
        self.source.write_text(
            '% \\kn{comment}\n\\newcommand{\\alias}[1]{\\kn{#1}}\n'
            '\\begin{verbatim}\n\\kn{literal}\n\\end{verbatim}\n'
            '\\iffalse\\kn{disabled}\\fi\n'
            '\\\\kn{escaped}\n\\kn{A "quoted name}\n', encoding="utf-8",
        )
        result = scan_latex(self.root, self.spec, self.source, {})
        self.assertEqual([], result.errors)
        self.assertEqual(['A "quoted name'], [item.label for item in result.definitions])

    def test_missing_environment_end_blocks_sync_without_writing_graph(self) -> None:
        self.source.write_text('\\begin{theorem}\n\\kn{Unclosed theorem}\n', encoding="utf-8")
        with self.assertRaisesRegex(KnowledgeError, "unclosed"):
            self.sync()
        self.assertFalse((self.graph / "manifest.json").exists())

    def test_opaque_math_commands_do_not_collapse_distinct_marker_names(self) -> None:
        self.source.write_text(r"\kn{$\alpha$} \kn{$\beta$} \kn{$\S$-algebra} \kn{$\T$-algebra}" + "\n", encoding="utf-8")
        result = scan_latex(self.root, self.spec, self.source, {})
        self.assertEqual([], result.errors)
        self.assertEqual({"alpha", "beta", "s-algebra", "t-algebra"}, {item.id for item in result.definitions})

    def test_unchanged_native_marker_keeps_preexisting_id_when_label_normalization_improves(self) -> None:
        previous = node("legacy-algebra", r"$\S$-algebra")
        previous["label"] = "-algebra"
        identities = build_identity_index(GraphState({previous["id"]: previous}, {}, [], {}))
        self.source.write_text(r"\kn{$\S$-algebra} and \knref{$\S$-algebra}" + "\n", encoding="utf-8")
        result = scan_latex(self.root, self.spec, self.source, identities)
        self.assertEqual("legacy-algebra", result.definitions[0].id)
        self.assertEqual("legacy-algebra", result.references[0].target)
        self.assertEqual("S-algebra", result.definitions[0].label)

    def test_authored_reference_keeps_preexisting_target_when_its_normalization_improves(self) -> None:
        previous = node("sigma", r"$\sigma$")
        previous["label"] = "σ"
        previous["properties"]["source_status"] = "active"
        previous["provenance"]["active"] = True
        reference_name = r"$\ensuremath{\sigma}$"
        previous_reference = {
            "origin": "authored", "source_format": "latex",
            "source_name": reference_name, "target": "sigma",
        }
        state = GraphState({"sigma": previous}, {}, [previous_reference], {})
        identities = build_identity_index(state)
        content = r"\kn{$\sigma$} and \knref{$\ensuremath{\sigma}$}" + "\n"
        self.source.write_text(content, encoding="utf-8")

        result = scan_latex(self.root, self.spec, self.source, identities)

        self.assertEqual([], result.errors)
        self.assertEqual("sigma", result.definitions[0].id)
        self.assertEqual("sigma", result.references[0].target)
        self.assertEqual("ensuremath σ", result.references[0].label)
        self.assertEqual(reference_name, result.references[0].source_name)
        self.assertEqual(reference_name, previous_reference["source_name"])
        self.assertEqual(content, self.source.read_text(encoding="utf-8"))

    def test_existing_reference_spellings_require_authored_active_knowledge_targets(self) -> None:
        active = node("active", "Active")
        active["label"] = "Active"
        active["properties"]["source_status"] = "active"
        active["provenance"]["active"] = True
        orphan = node("orphan", "Orphan")
        orphan["label"] = "Orphan"
        orphan["properties"]["source_status"] = "orphaned"
        orphan["provenance"]["active"] = False
        references = [
            {"origin": "authored", "source_format": "latex", "source_name": "valid ref", "target": "active"},
            {"origin": "authored", "source_format": "latex", "source_name": "orphan ref", "target": "orphan"},
            {"origin": "authored", "source_format": "latex", "source_name": "dangling ref", "target": "missing"},
            {"origin": "imported", "source_format": "latex", "source_name": "unreviewed ref", "target": "active"},
            {"origin": "authored", "source_format": "typst", "source_name": "other format ref", "target": "active"},
        ]
        identities = build_identity_index(GraphState(
            {"active": active, "orphan": orphan}, {}, references, {},
        ))

        self.assertEqual("active", identities[latex_name_key("valid ref")])
        for name in ("orphan ref", "dangling ref", "unreviewed ref", "other format ref"):
            self.assertNotIn(latex_name_key(name), identities)

    def test_conflicting_existing_reference_spelling_is_rejected(self) -> None:
        first = node("first", "First")
        second = node("second", "Second")
        for item in (first, second):
            item["label"] = item["id"]
            item["properties"]["source_status"] = "active"
            item["provenance"]["active"] = True
        references = [
            {"origin": "authored", "source_format": "latex", "source_name": "same raw spelling", "target": target}
            for target in ("first", "second")
        ]
        with self.assertRaisesRegex(KnowledgeError, "ambiguous explicit LaTeX marker spelling"):
            build_identity_index(GraphState({"first": first, "second": second}, {}, references, {}))

    def test_outer_theorem_edit_after_nested_end_marks_only_outer_entry_for_review(self) -> None:
        content = '\\begin{theorem}\n\\kn{Outer}\n\\begin{theorem}\n\\kn{Inner}\n\\end{theorem}\nOuter condition.\n\\end{theorem}\n'
        self.source.write_text(content, encoding="utf-8")
        self.sync()
        derived = self.root / ".knowledge/derived/by-source/notes/source.tex.md"
        derived.parent.mkdir(parents=True)
        derived.write_text("# Reviewed source\nOuter and inner conditions.\n", encoding="utf-8")
        delta = self.root / "delta.json"
        delta.write_text(json.dumps({"schema": "kgdistiller-agent-delta-v1", "nodes": [{"id": "outer", "text": "Reviewed outer theorem."}, {"id": "inner", "text": "Reviewed inner theorem."}]}), encoding="utf-8")
        apply_delta(self.graph, delta, repo_root=self.root)
        self.source.write_text(content.replace("Outer condition.", "Changed outer condition."), encoding="utf-8")
        state, _, _ = self.sync()
        self.assertEqual("needs-review", state.nodes["outer"]["properties"]["curation_status"])
        self.assertEqual("current", state.nodes["inner"]["properties"]["curation_status"])


if __name__ == "__main__":
    unittest.main()
