from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kgdistiller.latex_syntax import (
    STATEMENT_KINDS,
    find_group_end,
    mask_latex,
    statement_ranges,
)


class LatexMaskTest(unittest.TestCase):
    def assert_positions_preserved(self, source: str, visible: str) -> None:
        self.assertEqual(len(source), len(visible))
        self.assertEqual(
            [(index, value) for index, value in enumerate(source) if value in "\r\n"],
            [(index, value) for index, value in enumerate(visible) if value in "\r\n"],
        )

    def test_comments_respect_tex_control_symbols_and_preserve_positions(self) -> None:
        source = (
            "% \\kn{comment}\r\n"
            "\\% \\kn{visible-after-percent}\n"
            "\\\\% \\kn{comment-after-newline-command}\n"
            "\\\\\\% \\kn{visible-after-three-slashes}\n"
            "\\kn{实际节点} % \\kn{trailing-comment}\n"
        )
        visible = mask_latex(source)
        self.assert_positions_preserved(source, visible)
        self.assertIn(r"\kn{visible-after-percent}", visible)
        self.assertIn(r"\kn{visible-after-three-slashes}", visible)
        self.assertIn(r"\kn{实际节点}", visible)
        for hidden in ("comment}", "comment-after-newline-command", "trailing-comment"):
            self.assertNotIn(hidden, visible)

    def test_literal_environments_hide_markers_and_fake_statement_boundaries(self) -> None:
        for environment in ("verbatim", "verbatim*", "Verbatim", "lstlisting", "minted", "comment", "filecontents*"):
            with self.subTest(environment=environment):
                source = (
                    "\\begin{" + environment + "}\n"
                    "\\kn{literal} \\begin{lemma} % this is literal\n"
                    "\\end{" + environment + "}\n"
                    "\\begin{lemma}\\kn{real}\\end{lemma}"
                )
                visible = mask_latex(source)
                self.assert_positions_preserved(source, visible)
                self.assertNotIn("literal}", visible)
                self.assertIn(r"\kn{real}", visible)
                ranges = statement_ranges(source)
                self.assertEqual(len(ranges), 1)
                self.assertEqual(source[ranges[0].start:ranges[0].end], r"\begin{lemma}\kn{real}\end{lemma}")

    def test_inline_literals_are_hidden_without_treating_their_percent_as_comment(self) -> None:
        forms = (
            r"\verb|% \kn{literal} { }|",
            r"\verb*+\kn{literal}+",
            r"\Verb!\kn{literal}!",
            r"\lstinline[language={[LaTeX]TeX}]|% \kn{literal}|",
            r"\lstinline{\kn{literal} %}",
            r"\mintinline[breaklines]{latex}{\kn{literal} %}",
            r"\mintinline{latex}|% \kn{literal}|",
        )
        for form in forms:
            with self.subTest(form=form):
                source = form + r" \kn{real}"
                visible = mask_latex(source)
                self.assert_positions_preserved(source, visible)
                self.assertNotIn("literal", visible)
                self.assertIn(r"\kn{real}", visible)

    def test_iffalse_skips_nested_conditionals_and_retains_else_branch(self) -> None:
        source = (
            "\\newif\\ifcustom\n"
            "\\iffalse\n"
            "\\kn{disabled}\n"
            "\\iftrue \\kn{nested} \\else \\kn{nested-else} \\fi\n"
            "\\ifcustom \\kn{custom} \\fi\n"
            "% \\fi this is a comment\n"
            "\\else\n"
            "\\kn{enabled}\n"
            "\\fi\n"
            "\\kn{after}\n"
        )
        visible = mask_latex(source)
        self.assert_positions_preserved(source, visible)
        for hidden in ("disabled", "nested", "custom}"):
            self.assertNotIn(hidden, visible)
        self.assertIn(r"\kn{enabled}", visible)
        self.assertIn(r"\kn{after}", visible)

    def test_iffalse_without_else_hides_unclosed_statement_example(self) -> None:
        source = r"\iffalse \begin{lemma}\kn{disabled} \fi \kn{real}"
        self.assertNotIn("disabled", mask_latex(source))
        self.assertEqual(statement_ranges(source), [])

    def test_macro_definitions_hide_unexpanded_marker_bodies(self) -> None:
        definitions = (
            r"\newcommand{\example}[1][\kn{default}]{\kn{literal} #1}",
            r"\renewcommand*\example[1]{\kn{literal} #1}",
            r"\providecommand\example{\kn{literal}}",
            r"\DeclareRobustCommand{\example}{\kn{literal}}",
            r"\def\example#1, #2{\kn{literal} #1 #2}",
            r"\gdef\example{\kn{literal}}",
            r"\edef\example{\kn{literal}}",
            r"\xdef\example{\kn{literal}}",
            r"\newenvironment{example}[1]{\begin{lemma}\kn{literal}}{\end{lemma}}",
            r"\renewenvironment{example}{\kn{literal}}{}",
            r"\NewDocumentCommand{\example}{O{\kn{default}}m}{\kn{literal}}",
            r"\RenewDocumentCommand\example{m}{\kn{literal}}",
            r"\ProvideDocumentCommand\example{m}{\kn{literal}}",
            r"\DeclareDocumentCommand\example{m}{\kn{literal}}",
            r"\NewDocumentEnvironment{example}{m}{\kn{literal}}{\knref{literal}}",
            r"\DeclareMathOperator*{\example}{\kn{literal}}",
            r"\newtheorem{example}{\kn{literal}}",
        )
        for definition in definitions:
            with self.subTest(definition=definition):
                source = definition + "\n" + r"\kn{real}"
                visible = mask_latex(source)
                self.assert_positions_preserved(source, visible)
                self.assertNotIn("literal", visible)
                self.assertNotIn("default", visible)
                self.assertIn(r"\kn{real}", visible)

    def test_malformed_hidden_syntax_reports_original_line(self) -> None:
        for source, message in (
            ("\n\\begin{verbatim}\n\\kn{literal}", "unclosed literal environment"),
            ("\n\\verb|\\kn{literal}", "unclosed \\verb literal"),
            ("\n\\iffalse \\kn{literal}", "unclosed \\iffalse"),
            ("\n\\newcommand{\\example}{\\kn{literal}", "unclosed '{'"),
        ):
            with self.subTest(source=source):
                with self.assertRaises(ValueError) as context:
                    mask_latex(source)
                self.assertIn(message, str(context.exception))
                self.assertIn("line 2", str(context.exception))


class LatexGroupTest(unittest.TestCase):
    def test_quotes_and_math_do_not_hide_tex_group_boundaries(self) -> None:
        for source in (
            '{"Quoted theorem"}',
            '{"Unpaired quote}',
            '{"Nested {group}"}',
            r"{$\mathbb{R}$-space}",
        ):
            with self.subTest(source=source):
                self.assertEqual(find_group_end(source, 0), len(source) - 1)

    def test_escaped_braces_and_comments_do_not_change_depth(self) -> None:
        source = "{\\{ unbalanced escaped open % } ignored close\n nested {group} \\}}tail"
        self.assertEqual(find_group_end(source, 0), source.index("tail") - 1)
        source = r"{\\}tail"
        self.assertEqual(find_group_end(source, 0), source.index("tail") - 1)

    def test_inline_verbatim_can_contain_unbalanced_braces(self) -> None:
        source = r"{Name \verb|}{| and \lstinline|{|}tail"
        self.assertEqual(find_group_end(source, 0), source.index("tail") - 1)

    def test_missing_open_or_close_is_an_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "expected"):
            find_group_end("name", 0)
        with self.assertRaisesRegex(ValueError, "unclosed"):
            find_group_end("{name", 0)


class LatexStatementTest(unittest.TestCase):
    def test_every_standard_statement_and_starred_variant(self) -> None:
        for kind in sorted(STATEMENT_KINDS):
            for suffix in ("", "*"):
                with self.subTest(kind=kind, suffix=suffix):
                    environment = kind + suffix
                    source = "before\n\\begin{" + environment + "}[Name]\n\\kn{Node}\n\\end{" + environment + "}\nafter"
                    ranges = statement_ranges(source)
                    self.assertEqual(len(ranges), 1)
                    self.assertEqual(ranges[0].kind, kind)
                    self.assertEqual(ranges[0].start, source.index(r"\begin"))
                    self.assertEqual(ranges[0].end, source.index("\nafter"))

    def test_same_name_nesting_retains_outer_body_after_inner_end(self) -> None:
        source = (
            "\\begin{theorem}\n\\kn{Outer}\n"
            "\\begin{theorem}\n\\kn{Inner}\n\\end{theorem}\n"
            "outer-tail-A\n\\knref{Dependency}\n\\end{theorem}"
        )
        outer, inner = statement_ranges(source)
        self.assertEqual(outer.end, len(source))
        self.assertLess(inner.end, outer.end)
        self.assertLess(outer.start, inner.start)
        self.assertIn(r"\knref{Dependency}", source[outer.start:outer.end])
        changed = source.replace("outer-tail-A", "outer-tail-B")
        changed_outer, changed_inner = statement_ranges(changed)
        digest = lambda text, item: hashlib.sha256(text[item.start:item.end].encode()).hexdigest()
        self.assertNotEqual(digest(source, outer), digest(changed, changed_outer))
        self.assertEqual(digest(source, inner), digest(changed, changed_inner))

    def test_non_statement_environments_are_included_in_outer_range(self) -> None:
        source = r"\begin{document}\begin{lemma}\kn{Node}\begin{align}a&=b\end{align}\end{lemma}\end{document}"
        ranges = statement_ranges(source)
        self.assertEqual(len(ranges), 1)
        self.assertTrue(source[ranges[0].start:ranges[0].end].endswith(r"\end{lemma}"))

    def test_local_newtheorem_declarations_support_explicit_names_and_titles(self) -> None:
        source = (
            "\\newtheorem{thm}{Theorem}[section]\n"
            "\\newtheorem{lem}[thm]{Lemma}\n"
            "\\newtheorem*{observation}{Observation}\n"
            "\\begin{thm}\\kn{Theorem node}\\end{thm}\n"
            "\\begin{lem}\\kn{Lemma node}\\end{lem}\n"
            "\\begin{observation}\\kn{Concept node}\\end{observation}"
        )
        self.assertEqual([item.kind for item in statement_ranges(source)], ["theorem", "lemma", "concept"])
        self.assertNotIn(r"\newtheorem", mask_latex(source))

    def test_shared_counter_and_unexpanded_heading_do_not_imply_semantic_kind(self) -> None:
        source = r"\newtheorem{claim}[theorem]{\theoremname}\begin{claim}\kn{Node}\end{claim}"
        self.assertEqual(statement_ranges(source)[0].kind, "concept")

    def test_newtheorem_comments_do_not_change_explicit_title_type(self) -> None:
        source = "\\newtheorem{thm}{% ignore title comment\nTheorem}\n\\begin{thm}\\kn{Node}\\end{thm}"
        self.assertEqual(statement_ranges(source)[0].kind, "theorem")

    def test_comments_literals_and_macro_definitions_cannot_declare_custom_statement(self) -> None:
        source = (
            "% \\newtheorem{commented}{Lemma}\n"
            "\\newcommand{\\setup}{\\newtheorem{macro}{Lemma}}\n"
            "\\begin{verbatim}\\newtheorem{literal}{Lemma}\\end{verbatim}\n"
            "\\begin{commented}\\kn{A}\\end{commented}\n"
            "\\begin{macro}\\kn{B}\\end{macro}\n"
            "\\begin{literal}\\kn{C}\\end{literal}\n"
        )
        self.assertEqual(statement_ranges(source), [])

    def test_escaped_command_does_not_start_statement(self) -> None:
        self.assertEqual(statement_ranges(r"\\begin{lemma}\\end{lemma}"), [])

    def test_missing_end_mismatched_end_and_orphan_end_are_errors(self) -> None:
        sources = (
            r"\begin{lemma}\kn{Node}",
            r"\begin{lemma}\begin{theorem}\end{lemma}\end{theorem}",
            r"\begin{theorem}\begin{align}x\end{theorem}",
            r"\begin{lemma*}\end{lemma}",
            r"\end{lemma}",
            r"\newtheorem{thm}{Theorem}\begin{thm}\kn{Node}",
        )
        for source in sources:
            with self.subTest(source=source), self.assertRaises(ValueError):
                statement_ranges(source)

    def test_error_line_uses_unmasked_source_offsets(self) -> None:
        source = "% ignored \\end{lemma}\n\\begin{lemma}\n\\end{theorem}"
        with self.assertRaises(ValueError) as context:
            statement_ranges(source)
        self.assertIn("line 3", str(context.exception))

    def test_unrelated_partial_environment_does_not_reject_source_fragment(self) -> None:
        self.assertEqual(statement_ranges(r"\begin{document}\kn{Node}"), [])


if __name__ == "__main__":
    unittest.main()
