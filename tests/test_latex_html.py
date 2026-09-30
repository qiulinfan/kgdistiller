from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from kgdistiller.cli import GraphState, KnowledgeError, SourceSpec, apply_delta, build_identity_index, scan_latex, synchronize
from kgdistiller.latex_html import (
    COMMAND_ENV,
    RESULT_SCHEMA,
    LatexHtmlError,
    converter_command,
    export_latex_document,
    render_latex_labels,
    run_converter,
    validate_label_html,
)
from kgdistiller.project import initialize_project


def node(node_id: str, name: str, authority: str = "notes/source.tex") -> dict:
    return {
        "id": node_id,
        "type": "knowledge",
        "label": "σ-algebra",
        "properties": {"source_format": "latex", "source_name": name, "latex_name": name},
        "provenance": {"authority": authority},
    }


class LatexHtmlProtocolTest(unittest.TestCase):
    def test_converter_argv_is_explicit_and_never_a_shell_string(self) -> None:
        with patch.dict(os.environ, {COMMAND_ENV: '["node", "some path/$tool.mjs"]'}):
            self.assertEqual(["node", "some path/$tool.mjs"], converter_command())
        for value in ['"node script"', '[]', '["node", 7]', '["node", ""]']:
            with self.subTest(value=value), patch.dict(os.environ, {COMMAND_ENV: value}):
                with self.assertRaises(LatexHtmlError):
                    converter_command()

    def test_provider_result_is_bound_to_operation(self) -> None:
        response = {"schema": RESULT_SCHEMA, "operation": "document", "html": "<html></html>"}
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps(response), "")
        with patch("kgdistiller.latex_html.subprocess.Popen", return_value=process):
            with self.assertRaisesRegex(LatexHtmlError, "another operation"):
                run_converter({"operation": "labels", "labels": []})

    def test_converter_timeout_allows_provider_to_clean_up_tex_processes(self) -> None:
        process = Mock()
        process.communicate.side_effect = [subprocess.TimeoutExpired("converter", 120), ("", "cancelled")]
        with patch("kgdistiller.latex_html.subprocess.Popen", return_value=process):
            with self.assertRaisesRegex(LatexHtmlError, "failed"):
                run_converter({"operation": "labels", "labels": []})
        process.terminate.assert_called_once()
        process.kill.assert_not_called()

    def test_rich_labels_keep_original_tex_and_project_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "notes/source.tex"
            source.parent.mkdir()
            source.write_text(r"\newcommand{\S}{\sigma}", encoding="utf-8")
            state = GraphState({"sigma": node("sigma", r"$\S$-algebra")}, {}, [], {})
            response = {"labels": [{"id": "sigma", "html": '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>σ</mi></math>-algebra'}]}
            with patch("kgdistiller.latex_html.run_converter", return_value=response) as converter:
                render_latex_labels(state, root)
            request = converter.call_args.args[0]
            self.assertEqual(str(source), request["source"])
            self.assertEqual(r"$\S$-algebra", request["labels"][0]["latex"])
            self.assertIn("<math", state.nodes["sigma"]["properties"]["label_html"])

    def test_plain_latex_sources_need_no_converter(self) -> None:
        state = GraphState({"plain": node("plain", "ordinary concept")}, {}, [], {})
        state.nodes["plain"]["label"] = "A & B"
        with patch("kgdistiller.latex_html.run_converter") as converter:
            render_latex_labels(state)
        converter.assert_not_called()
        self.assertEqual("A &amp; B", state.nodes["plain"]["properties"]["label_html"])

    def test_orphan_keeps_cached_mathematical_label_without_reloading_removed_macros(self) -> None:
        cached = '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>σ</mi></math>'
        orphan = node("sigma", r"$\projectSigma$", "notes/deleted.tex")
        orphan["properties"].update({"source_status": "orphaned", "label_html": cached})
        orphan["provenance"]["active"] = False
        state = GraphState({"sigma": orphan}, {}, [], {})
        with patch("kgdistiller.latex_html.run_converter") as converter:
            render_latex_labels(state)
        converter.assert_not_called()
        self.assertEqual(cached, state.nodes["sigma"]["properties"]["label_html"])

    def test_missing_duplicate_or_unexpected_labels_cannot_install_results(self) -> None:
        for records in [[], [{"id": "other", "html": "unexpected"}], [{"id": "sigma", "html": "one"}, {"id": "sigma", "html": "two"}]]:
            state = GraphState({"sigma": node("sigma", r"$\sigma$-algebra")}, {}, [], {})
            with self.subTest(records=records), patch("kgdistiller.latex_html.run_converter", return_value={"labels": records}):
                with self.assertRaises(LatexHtmlError):
                    render_latex_labels(state)
            self.assertNotIn("label_html", state.nodes["sigma"]["properties"])

    def test_only_passive_html_and_mathml_can_become_labels(self) -> None:
        self.assertEqual("<strong>x</strong>", validate_label_html("<strong>x</strong>"))
        for unsafe in ['<script>alert(1)</script>', '<span onclick="go()">x</span>', '<svg/>', '<math href="https://example.test">x</math>', '<style>body{}</style>', '<span style="color:red">x</span>']:
            with self.subTest(unsafe=unsafe), self.assertRaises(LatexHtmlError):
                validate_label_html(unsafe)

    def test_export_rejects_changed_included_authority_without_installing_html(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            main = root / "main.tex"
            child = root / "child.tex"
            main.write_text(r"\documentclass{article}\begin{document}\input{child}\end{document}", encoding="utf-8")
            child.write_text("Original condition.", encoding="utf-8")
            from kgdistiller.cli import sha256_authority_file
            state = GraphState({}, {}, [], {"source_hashes": {"child.tex": sha256_authority_file(child)}})
            output = root / "main.html"

            def converted(request):
                child.write_text("Changed condition.", encoding="utf-8")
                return {"html": "<html><body>Old condition.</body></html>"}

            with patch("kgdistiller.latex_html.run_converter", side_effect=converted):
                with self.assertRaisesRegex(LatexHtmlError, "authority changed"):
                    export_latex_document(root, main, output, state=state)
            self.assertFalse(output.exists())

    def test_export_does_not_overwrite_an_output_created_while_rendering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            main = root / "main.tex"
            main.write_text(r"\documentclass{article}\begin{document}Text.\end{document}", encoding="utf-8")
            state = GraphState({}, {}, [], {})
            output = root / "main.html"

            def converted(request):
                output.write_text("Another writer's output.", encoding="utf-8")
                return {"html": "<html><body>Text.</body></html>"}

            with patch("kgdistiller.latex_html.run_converter", side_effect=converted):
                with self.assertRaisesRegex(LatexHtmlError, "appeared during export"):
                    export_latex_document(root, main, output, state=state)
            self.assertEqual("Another writer's output.", output.read_text(encoding="utf-8"))


class LatexSourceIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgd-latex-core-")
        self.root = Path(self.temporary.name).resolve()
        self.registry = self.root / "knowledge/sources.json"
        self.graph = self.root / "knowledge/graph"
        self.generated = self.root / "knowledge/build/knowledge-registry.typ"
        initialize_project(self.root, self.registry, source_root=Path("notes"), alignments=self.root / "knowledge/alignments.json")
        self.source = self.root / "notes/source.tex"
        self.spec = SourceSpec("test", "", "", self.source.parent, ("*.tex",), "", "personal-note", (), ())

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def sync(self):
        return synchronize(self.root, self.registry, self.graph, self.generated, files=[], course=None, subject=None, write=True)

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

    def test_deleting_latex_authority_retains_its_id_and_cached_label(self) -> None:
        self.source.write_text(r"\newcommand{\projectSigma}{\sigma}" + '\n\\kn{$\\projectSigma$-algebra}\n', encoding="utf-8")
        cached = '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>σ</mi></math>-algebra'

        def converted(request):
            return {"labels": [{"id": item["id"], "html": cached} for item in request["labels"]]}

        with patch("kgdistiller.latex_html.run_converter", side_effect=converted):
            before, _, _ = self.sync()
        original_id = next(key for key, value in before.nodes.items() if value["type"] == "knowledge")
        self.source.unlink()
        with patch("kgdistiller.latex_html.run_converter") as converter:
            after, _, _ = self.sync()
        converter.assert_not_called()
        self.assertEqual("orphaned", after.nodes[original_id]["properties"]["source_status"])
        self.assertEqual(cached, after.nodes[original_id]["properties"]["label_html"])

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
        from kgdistiller.cli import latex_name_key

        active = node("active", "Active")
        active["label"] = "Active"
        active["properties"]["source_status"] = "active"
        active["provenance"]["active"] = True
        orphan = node("orphan", "Orphan")
        orphan["label"] = "Orphan"
        orphan["properties"]["source_status"] = "orphaned"
        orphan["provenance"]["active"] = False
        taxonomy = {"id": "field", "type": "field", "label": "Field"}
        references = [
            {"origin": "authored", "source_format": "latex", "source_name": "valid ref", "target": "active"},
            {"origin": "authored", "source_format": "latex", "source_name": "orphan ref", "target": "orphan"},
            {"origin": "authored", "source_format": "latex", "source_name": "dangling ref", "target": "missing"},
            {"origin": "authored", "source_format": "latex", "source_name": "taxonomy ref", "target": "field"},
            {"origin": "imported", "source_format": "latex", "source_name": "unreviewed ref", "target": "active"},
            {"origin": "authored", "source_format": "typst", "source_name": "other format ref", "target": "active"},
        ]
        identities = build_identity_index(GraphState(
            {"active": active, "orphan": orphan, "field": taxonomy}, {}, references, {},
        ))

        self.assertEqual("active", identities[latex_name_key("valid ref")])
        for name in ("orphan ref", "dangling ref", "taxonomy ref", "unreviewed ref", "other format ref"):
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
        derived = self.root / "knowledge/derived/by-source/notes/source.tex.md"
        derived.parent.mkdir(parents=True)
        derived.write_text("# Reviewed source\nOuter and inner conditions.\n", encoding="utf-8")
        delta = self.root / "knowledge/build/delta.json"
        delta.write_text(json.dumps({"schema": "kgdistiller-agent-delta-v1", "nodes": [{"id": "outer", "text": "Reviewed outer theorem."}, {"id": "inner", "text": "Reviewed inner theorem."}]}), encoding="utf-8")
        apply_delta(self.graph, self.generated, delta, repo_root=self.root)
        self.source.write_text(content.replace("Outer condition.", "Changed outer condition."), encoding="utf-8")
        state, _, _ = self.sync()
        self.assertEqual("needs-review", state.nodes["outer"]["properties"]["curation_status"])
        self.assertEqual("current", state.nodes["inner"]["properties"]["curation_status"])

    @unittest.skipUnless(os.environ.get(COMMAND_ENV) or shutil.which("latex-live-export"), "local LaTeX HTML converter is required")
    def test_native_math_name_sync_produces_mathml_without_typst(self) -> None:
        self.source.write_text(r"\newcommand{\mSigma}{\sigma}" + '\n\\begin{definition}\n\\kn{$\\mSigma$-algebra}\nBody.\n\\end{definition}\n', encoding="utf-8")
        with patch("kgdistiller.cli.render_typst_labels") as typst:
            state, _, _ = self.sync()
        typst.assert_called_once()
        knowledge = next(item for item in state.nodes.values() if item["type"] == "knowledge")
        self.assertIn("<math", knowledge["properties"]["label_html"])
        self.assertIn("σ", knowledge["properties"]["label_html"])
        self.assertEqual(r"$\mSigma$-algebra", knowledge["properties"]["latex_name"])


if __name__ == "__main__":
    unittest.main()
