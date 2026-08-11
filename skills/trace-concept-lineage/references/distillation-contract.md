# Paper distillation contract

The root agent owns the global output. Workers own only their assigned concept
files.

## Output topology

```text
<output-root>/
├── knowledge-graph.md
├── reading-route.md
└── concepts/
    ├── d01-<concept-slug>.md
    ├── d02-<concept-slug>.md
    └── ...
```

Use lowercase ASCII slugs and stable IDs. `MATH-FOUNDATION` has no concept
file.

## `knowledge-graph.md`

Include, in order:

1. paper title, source path, and inspected revision or date;
2. scope and learner assumptions;
3. a legend for node classes and relation types;
4. one Mermaid `flowchart LR`;
5. a linked node index;
6. an evidence table for every nontrivial edge;
7. unresolved graph decisions or source gaps.

The node index uses:

| ID | Concept | Kind | Why retained | Dossier |
|---|---|---|---|---|

Kinds may include `assumed-foundation`, `problem`, `mechanism`,
`representation`, `objective`, `algorithm`, `theory`, `architecture`,
`limitation`, and `paper-construction`.

The edge evidence table uses:

| From | Relation | To | Meaning in this paper | Evidence or inference |
|---|---|---|---|---|

Use only the relation vocabulary in `SKILL.md`. Cite primary or authoritative
sources for mechanism-defining or historical edges. Cite exact paper
locations for `paper-instantiates` and paper-local dependency edges.

The full graph need not be acyclic. Its `prerequisite-for` subgraph must be
acyclic.

## `reading-route.md`

Derive the full route from a topological ordering of only
`prerequisite-for` edges. Begin with:

`Stage 0 — MATH-FOUNDATION (assumed; no reading required)`

Then group concepts into stages that may be read in parallel. Include:

- a fast route containing only concepts required for the main contribution;
- a full route containing all retained concepts;
- the purpose and expected paper payoff of each stage;
- relative links to every dossier.

Use:

| Order | ID | Concept | Why now | Paper payoff | File |
|---|---|---|---|---|---|

Do not place a concept before a retained prerequisite. Contrast-only,
historical, and optional nodes may appear in side branches.

## Worker prompt contract

Give a worker a prompt equivalent to:

```text
Use $trace-concept-lineage in worker mode.
Research node <ID>: <canonical concept>, meaning <selected sense>.
Target paper: <absolute path>.
Paper locations/excerpts: <locations or excerpts>.
Known prerequisite/neighbor IDs: <IDs>.
Assume MATH-FOUNDATION is mastered.
Follow <absolute path to dossier-contract.md>.
Browse authoritative web sources and cite them near claims.
Stop searching once every dossier section has adequate evidence and the
technical core has a primary or authoritative source. If a facet is still
unresolved after two different searches, record it under uncertainty and
finish the file.
Write only <absolute output path>.
Do not edit the paper, other dossiers, knowledge-graph.md, or
reading-route.md. Do not ask understanding questions.
Return only: written: <absolute output path>
Add one short blocker or uncertainty line only if necessary.
```

If a worker lacks enough paper context, the root agent must supply a narrower
excerpt or exact source locations before retrying.

## Integration checks

Before delivery:

- every explainable node has exactly one existing dossier;
- `MATH-FOUNDATION` is the only generic math node and has no dossier;
- every dossier uses the same node ID and canonical name as the index;
- all relative file links resolve;
- no proposed worker edge is accepted without direction checking;
- the prerequisite graph is acyclic;
- both fast and full routes respect prerequisites;
- no file contains a placeholder, unfinished section, or interactive quiz.
