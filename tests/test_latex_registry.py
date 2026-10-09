from __future__ import annotations

import copy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kgdistiller.latex_registry import (
    latex_registry_text,
    marker_registry,
)


def knowledge_node(
    node_id: str,
    label: str,
    *,
    source_name: str = "",
    source_format: str = "latex",
    aliases: tuple[str, ...] = (),
    web: str = "",
    active: bool = True,
) -> dict:
    return {
        "id": node_id,
        "type": "knowledge",
        "label": label,
        "properties": {
            "source_status": "active" if active else "orphaned",
            "source_format": source_format,
            "source_name": source_name,
            "aliases": list(aliases),
        },
        "provenance": {"active": active, "web": web},
    }


def graph_state(*nodes: dict, references: list[dict] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        nodes={node["id"]: node for node in nodes},
        references=references or [],
        edges={},
        manifest={},
    )


class LatexMarkerRegistryTest(unittest.TestCase):
    def test_preserves_explicit_raw_latex_authority_and_reference_spellings(self) -> None:
        definition = r"$\sigma$-algebra"
        reference = r"\(\sigma\)-algebra"
        source = knowledge_node(
            "sigma-algebra", "σ-algebra", source_name=definition,
            aliases=("sigma algebra",), web="https://example.test/math/#kn-sigma-algebra",
        )
        state = graph_state(source, references=[{
            "target": "sigma-algebra", "source_format": "latex", "origin": "authored",
            "source_name": reference, "display_markup": "A displayed alias is not an identity",
        }])
        rows = marker_registry(state)
        self.assertEqual({row["name"] for row in rows}, {"σ-algebra", "sigma algebra", definition, reference})
        self.assertEqual({row["id"] for row in rows}, {"sigma-algebra"})
        self.assertEqual({row["url"] for row in rows}, {"https://example.test/math/#kn-sigma-algebra"})

    def test_ignores_unrelated_formats_unreviewed_reference_names_and_inactive_targets(self) -> None:
        source = knowledge_node("active", "Active", source_format="typst", source_name="#strong[Active]")
        orphan = knowledge_node("orphan", "Orphan", source_name="Orphan raw", active=False)
        taxonomy = {"id": "field", "type": "field", "label": "Taxonomy"}
        references = [
            {"target": "active", "source_format": "typst", "origin": "authored", "source_name": "#text[wrong]"},
            {"target": "active", "source_format": "latex", "origin": "imported", "source_name": "Unreviewed name"},
            {"target": "orphan", "source_format": "latex", "origin": "authored", "source_name": "Orphan ref"},
            {"target": "missing", "source_format": "latex", "origin": "authored", "source_name": "Missing ref"},
        ]
        self.assertEqual(marker_registry(graph_state(source, orphan, taxonomy, references=references)), [{
            "name": "Active", "id": "active", "url": "/knowledge/#node=active",
        }])

    def test_an_explicit_inactive_provenance_is_excluded_even_if_status_is_active(self) -> None:
        node = knowledge_node("hidden", "Hidden")
        node["provenance"]["active"] = False
        self.assertEqual(marker_registry(graph_state(node)), [])

    def test_exact_name_conflicts_are_errors_and_same_identity_duplicates_are_deduplicated(self) -> None:
        first = knowledge_node("first", "Shared", source_name="Shared", aliases=("Shared",))
        self.assertEqual(len(marker_registry(graph_state(first))), 1)
        second = knowledge_node("second", "Other", aliases=("Shared",))
        with self.assertRaisesRegex(ValueError, "ambiguous LaTeX marker name"):
            marker_registry(graph_state(first, second))

    def test_authored_reference_name_conflicts_are_not_silently_overwritten(self) -> None:
        state = graph_state(
            knowledge_node("first", "First"), knowledge_node("second", "Second"),
            references=[
                {"target": target, "source_format": "latex", "origin": "authored", "source_name": "Same raw name"}
                for target in ("first", "second")
            ],
        )
        with self.assertRaisesRegex(ValueError, "first.*second"):
            marker_registry(state)

    def test_registry_is_deterministic_and_does_not_mutate_state(self) -> None:
        first = knowledge_node("a", "A", source_name=r"\textbf{A}", aliases=("Alias A",))
        second = knowledge_node("b", "B")
        state = graph_state(second, first)
        original = copy.deepcopy(state)
        rows = marker_registry(state)
        self.assertEqual(rows, marker_registry(graph_state(first, second)))
        self.assertEqual(state, original)
        self.assertEqual(latex_registry_text(state), latex_registry_text(graph_state(first, second)))

    def test_generated_file_defines_native_markers_and_contains_only_explicit_identity_data(self) -> None:
        state = graph_state(knowledge_node("node", "A { literal % # }", source_name=r"A \{ literal \% \# \}"))
        generated = latex_registry_text(state)
        self.assertIn(r"\RequirePackage{hyperref}", generated)
        self.assertIn(r"\DeclareRobustCommand{\kn}[1]", generated)
        self.assertIn(r"\DeclareRobustCommand{\knref}[1]", generated)
        self.assertIn(r"\detokenize{#1}", generated)
        self.assertIn(r"\hypertarget{kn-\kgd@nodeid}", generated)
        self.assertIn(r"\hyperlink{kn-\kgd@nodeid}", generated)
        self.assertIn(r"\href{\kgd@nodeurl}", generated)
        self.assertIn("Unknown knowledge marker", generated)
        self.assertIn("A { literal % # }", generated)
        self.assertIn(r"A \{ literal \% \# \}", generated)
        self.assertNotIn("typst", generated.casefold())

    def test_data_delimiter_is_chosen_to_avoid_literal_name_collision(self) -> None:
        generated = latex_registry_text(graph_state(knowledge_node("node", "|KGDISTILLERBOUNDARY0|")))
        self.assertIn(r"\kgdistillerRegister|KGDISTILLERBOUNDARY1|", generated)
        self.assertIn("|KGDISTILLERBOUNDARY0|", generated)

    def test_authored_tex_comments_are_removed_only_from_native_token_keys(self) -> None:
        raw = "Theo% comment\nrem"
        state = graph_state(knowledge_node("node", "Theorem", source_name=raw))
        self.assertIn(raw, {row["name"] for row in marker_registry(state)})
        generated = latex_registry_text(state)
        self.assertNotIn("Theo% comment", generated)
        self.assertIn("Theorem", generated)
        self.assertIn("Literal %", latex_registry_text(graph_state(knowledge_node("percent", "Literal %"))))

    def test_control_characters_cannot_break_registry_data_rows(self) -> None:
        with self.assertRaisesRegex(ValueError, "control character"):
            latex_registry_text(graph_state(knowledge_node("node", "unsafe\x00name")))
        with self.assertRaisesRegex(ValueError, "control character"):
            latex_registry_text(graph_state(knowledge_node("node", "Name", web="https://example.test/\nextra")))


class NativeLatexRegistryTest(unittest.TestCase):
    def compile_document(self, engine: str, state: SimpleNamespace, document: str) -> tuple[str, bytes]:
        executable = shutil.which(engine)
        if executable is None:
            self.skipTest(f"{engine} is not installed")
        with tempfile.TemporaryDirectory(prefix="kgdistiller-native-latex-") as temporary:
            root = Path(temporary)
            (root / "registry.tex").write_text(latex_registry_text(state), encoding="utf-8")
            source = root / "document.tex"
            source.write_text(document, encoding="utf-8")
            result = subprocess.run(
                [executable, "-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape", source.name],
                cwd=root, check=False, capture_output=True, text=True, timeout=30,
            )
            log_path = root / "document.log"
            log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, log[-12000:])
            self.assertTrue((root / "document.pdf").is_file(), log)
            return log, (root / "document.pdf").read_bytes()

    def test_pdflatex_math_spellings_ids_external_urls_and_local_targets(self) -> None:
        state = graph_state(
            knowledge_node(
                "sigma-algebra", "σ-algebra", source_name=r"$\sigma$-algebra",
                web="https://example.test/math/#kn-sigma-algebra",
            ),
            knowledge_node(
                "escaped", "percent % hash # unbalanced {", source_name=r"percent \% hash \# unbalanced \{",
                web="https://example.test/a%20b?x=1&y=2#kn-escaped",
            ),
            knowledge_node("multiline", "concept one, two", source_name="concept \\textbf{one,\ntwo}"),
            knowledge_node("macro-token", "Macro token name", source_name=r"\typeout{KGD-MUST-NOT-EXECUTE}"),
            references=[
                {
                    "target": "sigma-algebra", "source_format": "latex", "origin": "authored",
                    "source_name": r"\(\sigma\)-algebra",
                },
                {
                    "target": "multiline", "source_format": "latex", "origin": "authored",
                    "source_name": r"concept \textbf{one, two}",
                },
            ],
        )
        log, pdf = self.compile_document("pdflatex", state, r"""\documentclass{article}
\pdfcompresslevel=0
\pdfobjcompresslevel=0
\input{registry.tex}
\begin{document}
\typeout{KGD-ID=\kgdistillerNodeId{$\sigma$-algebra}}
\typeout{KGD-REF-ID=\kgdistillerNodeId{\(\sigma\)-algebra}}
\typeout{KGD-ESCAPED-ID=\kgdistillerNodeId{percent \% hash \# unbalanced \{}}
\typeout{KGD-URL=\kgdistillerNodeUrl{percent \% hash \# unbalanced \{}}
\typeout{KGD-MULTILINE-ID=\kgdistillerNodeId{concept \textbf{one,
two}}}
\typeout{KGD-MULTILINE-REF-ID=\kgdistillerNodeId{concept \textbf{one, two}}}
\typeout{KGD-INERT-MACRO-ID=\kgdistillerNodeId{\typeout{KGD-MUST-NOT-EXECUTE}}}
\typeout{KGD-UNKNOWN-ID=[\kgdistillerNodeId{Unknown theorem}]}
External: \knref{$\sigma$-algebra}. \knref{percent \% hash \# unbalanced \{}.
Definition: \kn{$\sigma$-algebra}.
Local: \knref{\(\sigma\)-algebra}.
Repeated definition: \kn{\(\sigma\)-algebra}.
Unknown: \knref{Unknown theorem}.
\end{document}
""")
        self.assertIn("KGD-ID=sigma-algebra", log)
        self.assertIn("KGD-REF-ID=sigma-algebra", log)
        self.assertIn("KGD-ESCAPED-ID=escaped", log)
        self.assertIn("KGD-URL=https://example.test/a%20b?x=1&y=2#kn-escaped", log)
        self.assertIn("KGD-MULTILINE-ID=multiline", log)
        self.assertIn("KGD-MULTILINE-REF-ID=multiline", log)
        self.assertIn("KGD-INERT-MACRO-ID=macro-token", log)
        self.assertNotIn("KGD-MUST-NOT-EXECUTE", log)
        self.assertIn("KGD-UNKNOWN-ID=[]", log)
        self.assertIn("Unknown knowledge marker: Unknown theorem", log)
        self.assertNotIn("Unknown knowledge marker: $", log)
        self.assertNotIn("destination with the same identifier", log)
        self.assertIn(b"https://example.test/math/#kn-sigma-algebra", pdf)
        self.assertIn(b"https://example.test/a%20b?x=1&y=2#kn-escaped", pdf)
        self.assertIn(b"kn-sigma-algebra", pdf)

    def test_xelatex_unicode_names_preserve_stable_identity_and_links(self) -> None:
        if shutil.which("xelatex") is None:
            self.skipTest("xelatex is not installed")
        kpsewhich = shutil.which("kpsewhich")
        if kpsewhich is None:
            self.skipTest("cannot locate the Chinese fixture font")
        font_result = subprocess.run([kpsewhich, "FandolSong-Regular.otf"], check=False, capture_output=True, text=True, timeout=10)
        if font_result.returncode != 0 or not font_result.stdout.strip():
            self.skipTest("FandolSong-Regular.otf is not installed")
        state = graph_state(knowledge_node("measurable-function", "可测函数", source_name="可测函数", aliases=("可测映射",)))
        log, _ = self.compile_document("xelatex", state, r"""\documentclass{article}
\usepackage{fontspec}
\setmainfont{FandolSong-Regular.otf}
\input{registry.tex}
\begin{document}
\typeout{KGD-CHINESE-ID=\kgdistillerNodeId{可测函数}}
\typeout{KGD-CHINESE-ALIAS-ID=\kgdistillerNodeId{可测映射}}
\typeout{KGD-CHINESE-URL=\kgdistillerNodeUrl{可测函数}}
\knref{可测函数}。\kn{可测函数}。\knref{可测映射}。
\end{document}
""")
        self.assertIn("KGD-CHINESE-ID=measurable-function", log)
        self.assertIn("KGD-CHINESE-ALIAS-ID=measurable-function", log)
        self.assertIn("KGD-CHINESE-URL=/knowledge/#node=measurable-function", log)
        self.assertNotIn("Unknown knowledge marker", log)
        self.assertNotIn("Missing character", log)


if __name__ == "__main__":
    unittest.main()
