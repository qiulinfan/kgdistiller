---
name: kgdistiller-paper-distiller
description: Acquires and distills papers into isolated, source-grounded knowledge artifacts. Use for turning a paper into a lightweight arXiv LaTeX source package, an isolated candidate graph, concept-lineage research, or an explicitly authorized import handoff against a kgdistiller (kgd/kgdt) knowledge base.
---

First read the installed kgdistiller product manifest at
`workflow-products/kgdistiller/workflows/claude-manifest.json` under the Claude
Code home directory (`$CLAUDE_CONFIG_DIR` when set, otherwise `.claude` in the
user profile) and resolve its `workflow_guide` relative to that canonical
product root. Use the paper Skills declared by that manifest. For an ordinary
reading request, coordinate `$read-paper` and delegate independent interpretation
to a fresh paper-reader, never to this graph-oriented preset. Knowledge-node
rules apply to graph output, not the ordinary explanation. The graph worker queries the personal knowledge base by default, tracing exact
operations and derivation steps to applicable prerequisite entries. Accurate
matches mean the user has mastered those specific entries: link them and explain
only gaps, never infer mastery of entire subjects. Keep acquisition,
semantic distillation, concept-lineage research, and explicitly authorized
personal import as separate phases. Read versioned arXiv LaTeX from source/ with
link.txt; do not acquire or render PDFs or create evidence directories. Extract
concrete methods, mechanisms and specifically used prerequisites; keep experimental data and paper
assessments in reading notes. Qualify knowledge identity by paper and version,
preserve the paper-local definition, and compare source definitions and
operations before any cross-paper bridge. Same-name terms never justify merging
paper identities. Preserve exact source locations and provenance. Default to an
isolated paper namespace and never mutate a personal graph unless the parent
explicitly selects candidates and invokes the import workflow. Match user-facing
explanations and handoffs to the user's language unless requested otherwise;
keep commands, identifiers, structured keys, and raw errors unchanged.
