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

Use lowercase ASCII slugs and stable IDs. Every retained node has a concrete
paper use and either a new dossier or an exact applicable existing entry link;
do not create a generic foundation placeholder or copy matched entries.

## `knowledge-graph.md`

Include, in order:

1. paper title, stable identifier, exact version, source path and full source digest;
2. scope and the user's explicit preferences, with no presumed subject mastery;
3. a legend for node classes and relation types;
4. one Mermaid `flowchart LR`;
5. a linked node index;
6. an evidence table for every nontrivial edge;
7. a concrete dependency and personal-entry link ledger;
8. unresolved graph decisions, source gaps or lookup failures.

The node index uses:

| ID | Scoped method or concrete prerequisite | Kind | Why retained | Dossier |
|---|---|---|---|---|

Use `method` or `mechanism` for paper methods. Concrete dependencies may be
`definition`, `operation`, `theorem` or `inequality` when their exact statement
is needed by an identified paper step. These are learning-index kinds, not new
engine enum values. Preserve the paper-local use and the dependency's actual
source authority; a theorem used by this paper is not automatically its invention.
No problem, result, metric, limitation or discussion nodes. Attach relevant
conditions and evidence to a mechanism's prose. Report any experimental reading
sections separately from the graph and method counts.

The edge evidence table uses:

| From | Relation | To | Meaning in this paper | Evidence or inference |
|---|---|---|---|---|

Use only the relation vocabulary in `SKILL.md`. Cite primary or authoritative
sources for mechanism-defining or historical edges. Cite exact paper
locations for `paper-instantiates` and paper-local dependency edges.

For every prerequisite dependency include:

| Dependency ID | Paper location | Exact form and conditions | Used at which step | Checked personal handle | Lookup evidence/status |
|---|---|---|---|---|---|

Query the configured personal base through `$query-kgdistiller` by default.
Inspect the returned definitions and conditions before accepting a handle; keep
the source-backed result's handle and generation/digests unchanged. Under the
user's convention, an exact applicable match means this concrete prerequisite
is mastered: keep its link and use step without reteaching or copying the entry.
This does not imply subject-wide mastery or permission to merge paper identities.
Retain local semantic edges separately from these cross-source links. For an
unresolved, unavailable or user-excluded lookup, record that state without
inventing a handle or dropping the local explanation. No personal writes.

The full graph need not be acyclic. Its `prerequisite-for` subgraph must be
acyclic.

## `reading-route.md`

Derive the full route from a topological ordering of only
`prerequisite-for` edges. Begin with actual concrete prerequisites; do not add
an assumed mathematical-foundation stage. Group concepts into stages that may
be read in parallel. Include:

- a fast route containing only concepts required for the main contribution;
- a full route containing all retained concepts;
- the purpose and expected paper payoff of each stage;
- links to new dossiers and verified existing prerequisite entries.

Use:

| Order | ID | Concept | Why now | Paper payoff | File |
|---|---|---|---|---|---|

Do not place a concept before a retained prerequisite. Contrast-only,
historical, and optional nodes may appear in side branches.
Mark exact applicable matched prerequisites as mastered linked references with
no required rereading. Keep them visible in the dependency order and explain
unresolved prerequisites. A name match alone never establishes this status.

## Worker prompt contract

Give a worker a prompt equivalent to:

```text
Use $trace-concept-lineage in worker mode.
Research node <ID>: <scoped method or concrete prerequisite>, meaning <selected sense>.
Owning paper identifier, version and source digest: <paper scope>.
Local terms and aliases: <terminology; not global identity aliases>.
Target paper: <absolute path>.
Paper locations/excerpts: <locations or excerpts>.
Direct prerequisite/neighbor IDs: <IDs>.
Dependency records: <paper location, exact form/conditions, use step,
checked personal knowledge handle and lookup evidence/status>.
Exact applicable matched prerequisites are mastered under the user's convention;
link them and their use step without reteaching or copying their entries.
Do not generalize a concrete match into whole-subject mastery.
Explain only mathematical dependencies actually used in this paper's steps.
Follow <absolute path to dossier-contract.md>.
Browse authoritative web sources and cite them near claims.
Stop searching once every dossier section has adequate evidence and the
technical core has a primary or authoritative source. If a facet is still
unresolved after two different searches, record it under uncertainty and
finish the file.
Write only <absolute output path>.
Other workers share the workspace; preserve their edits.
Do not edit the paper, other dossiers, knowledge-graph.md, or
reading-route.md. Do not ask understanding questions.
Return only: written: <absolute output path>
Add one short blocker or uncertainty line only if necessary.
```

If a worker lacks enough paper context, the root agent must supply a narrower
excerpt or exact source locations before retrying.

## Integration checks

Before delivery:

- every new/unmatched node has one dossier; exact applicable matched prerequisites
  have verified existing links and no duplicate dossiers;
- no generic foundation placeholder, subject inventory or whole-subject mastery assumption appears;
- every dossier uses the same node ID and canonical name as the index;
- methods retain owning paper/version and mechanism; prerequisites retain their
  exact statement, conditions, source authority and paper-local use;
- every dependency has a checked handle or explicit unresolved lookup state;
- personal queries stayed read-only; exact applicable matches avoid reteaching,
  while unresolved or inapplicable dependencies receive explanations;
- cross-paper names are compared by definitions and operations, never merged by spelling;
- experimental data and discussion topics have no graph nodes or dossier IDs;
- all relative file links resolve;
- no proposed worker edge is accepted without direction checking;
- the prerequisite graph is acyclic;
- both fast and full routes respect prerequisites;
- no file contains a placeholder, unfinished section, or interactive quiz.
