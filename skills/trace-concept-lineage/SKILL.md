---
name: trace-concept-lineage
description: Explicit command only. Research paper-scoped methods and their concretely used prerequisites into source-backed dossiers and a reading route, with default read-only links to verified personal knowledge entries. Treat exact applicable matches as mastered and explain unmatched definitions, operations, theorems and inequalities without assuming whole-subject mastery; delegate new dossiers in parallel. Use for non-interactive learning maps and method-lineage research.
disable-model-invocation: true
---

Run only when the user explicitly invokes `$trace-concept-lineage` or `/trace-concept-lineage`.
Do not start this workflow from an ordinary paper-reading request or another paper command.

# Trace Concept Lineage

Distill a paper into a browsable concept library rather than teaching it
interactively. Browsing is mandatory for every explained concept. Trace the
specific prerequisites used by the paper and link verified personal entries
through read-only queries. Per the user's convention, an exact applicable entry
is mastered: link it and its use here without reteaching or copying the entry.

## Align language and source

Match explanations and handoffs to the user's language. Preserve commands and
identifiers. Read LaTeX entrypoints and includes directly. Use source files, line
ranges, sections and labels for provenance. Do not acquire/compile/render PDFs
or create evidence directories. Source-only validation is sufficient to start;
do not wait for the independent explanation or section guide. Describe
figures from source text and captions; state specific visual gaps.

## Inputs and output

Use:

- a validated arXiv LaTeX package (`source/`, `source.json`, `link.txt`);
- an optional concept inventory, especially one produced by
  `$distill-paper-knowledge` candidate graph from an
  `$distill-paper` package;
- the user's stated scope and explanation preferences, without assumed subject mastery;
- an optional output directory.

Default the output directory to `<paper-root>/learning/`. Follow
[references/distillation-contract.md](references/distillation-contract.md)
for its layout and the two global deliverables.

If the user explicitly requests only one concept, write one dossier using the
same rules. Do not switch to conversational tutoring unless explicitly asked.

## 1. Ground the concept set in the paper

Read the complete source, or consume a complete source-grounded inventory.
Inspect enough surrounding LaTeX, prose, equations, theorems, and experiments
to determine what each term means in this paper.

Read `references/research-paper-contract.md` from the installed
`$distill-paper-knowledge` Skill and apply its method/mechanism admission and
paper-scoped identity rules before delegation, including for existing inventories. Retain
concrete methods, algorithms and mechanisms with identifiable objects,
operations or structure, and defining conditions. Also retain concrete definitions,
operations, theorems and inequalities actually required by a specific paper step,
with their exact form and assumptions. Attach conditions to their owning knowledge
unit. Experimental data, evaluation settings, performance explanations,
concept comparisons and review judgments belong in reading notes, not nodes.

Do not promote a discussion heading such as "how bottleneck reduces computation"
or "optimization versus generalization" into a node or dossier. A useful teaching
topic alone is insufficient. Recheck legacy inventories instead of expanding
every existing candidate automatically.

Create stable IDs and paper-qualified canonical names before delegation. Preserve
the paper identifier, version, full source digest and local terminology in every
dossier. Merge aliases only after checking that they name the same mechanism
within the same paper and version. Same-name mechanisms in other papers retain
their source scope and require explicit comparison; never silently fold later
variants into the target paper's definition.

## 2. Trace concrete dependencies and resolve existing entries

For each method, follow its actual computations and derivations. Retain the
specific convolution operation, the particular inequality on Lp or ell-p spaces,
or the exact law-of-large-numbers version used at that step, including shapes,
exponent ranges, independence, integrability or other applicable conditions.
These are examples of specificity, not a checklist to add to every paper.
Never substitute a broad subject label or an assumed-foundation placeholder.
Do not assume mastery of calculus, linear algebra or probability.

For every dependency record the paper location, exact form and conditions,
the step it enables, and its source-backed explanation. Distinguish a dependency
explicitly invoked by the author from one inferred by your derivation. Do not
claim a named theorem was used unless the source or a checked derivation supports
that attribution. Expand only as needed to explain the retained paper steps;
do not traverse an entire textbook or add nodes for subject coverage.

By default use `$query-kgdistiller` to resolve these dependencies in a bounded
read-only batch against the configured personal knowledge base. Inspect the
returned source-backed entries and compare exact definitions, forms and
conditions. Preserve the verified knowledge handles and query generation/digests.
A name match is a candidate for inspection, not a verified conceptual link.
An exact matched entry whose meaning and conditions apply to this step is treated
as mastered. Retain its handle and explain where it is used, without reteaching it
or creating a duplicate dossier. A partial, ambiguous or inapplicable match needs
local explanation. Never generalize one match into mastery of a whole subject.
Preserve paper-scoped method
identities and keep personal-entry links distinct from local semantic edges.

If the user excludes the personal base, honor that scope. If access is unavailable
or an entry is unmatched, ambiguous or unsuitable, record that state and retain
the local explanation; do not invent handles, write to the base, or block ordinary
reading. Reuse the
parent's current verified batch where available instead of repeating the lookup.

## 3. Plan the global graph before writing

The root agent owns:

- node IDs, canonical names, and slugs;
- the set of dossier output paths;
- all cross-concept edges;
- `knowledge-graph.md`;
- `reading-route.md`.

Subagents must never edit these global artifacts.

Use only these edge types:

- `prerequisite-for`
- `motivates`
- `implemented-by`
- `special-case-of`
- `generalizes`
- `contrasts-with`
- `used-by`
- `produces`
- `paper-instantiates`

Draft prerequisite edges first. They determine scheduling and the reading
route. Other edge types may be added after dossiers are available. Never turn
mere co-occurrence into a relationship.

## 4. Delegate concept dossiers in parallel

Delegate each new or unmatched explainable node to a subagent. Verified mastered
prerequisites stay linked in the index and dependency ledger, without duplicate
dossiers or extra workers. Use all safe available
concurrency and refill slots until every node is complete. Assign one concept
per subagent; assign a tightly coupled pair only when separating them would
make either dossier incoherent.

Give every worker:

- the node ID, paper-qualified canonical name, local aliases, and intended sense;
- the owning paper identifier, version and full source digest;
- the exact target file under `concepts/`;
- the paper path and exact relevant locations or excerpts;
- direct prerequisite and neighboring node IDs;
- each concrete dependency's paper location, form/conditions, use step and
  checked knowledge handle, or the explicit unresolved lookup state;
- the dossier contract at
  [references/dossier-contract.md](references/dossier-contract.md);
- an instruction to browse authoritative sources;
- an instruction that other workers share the workspace, to preserve their edits
  and write only its assigned file.

Use minimal or no inherited conversation context when the orchestration
environment supports it. Disjoint output paths are mandatory.

Each worker must:

1. inspect the paper-local context;
2. research the concept by definition, motivation, lineage, mechanism,
   variants, and limitations;
3. prefer original papers, peer-reviewed surveys, monographs, university
   notes, and official specifications;
4. stop expanding the search once every required dossier section has at least
   one adequate source and the technical core has a primary or authoritative
   source;
5. write a complete dossier to the exact path;
6. propose only evidence-backed graph edges inside that dossier;
7. return only `written: <path>` plus a one-line blocker or uncertainty when
   needed.

Each worker must not:

- edit the paper source;
- edit another concept dossier;
- edit either global deliverable;
- expand mathematical subjects beyond the specific dependencies used here;
- ask understanding questions or wait for learner interaction;
- paste the dossier into its agent response.

If a worker fails, retry with a narrower prompt or complete that dossier at
the root. Do not leave placeholders.

Research is coverage-bounded, not exhaustive. Start drafting after the paper
sense and technical core are verified. If one facet remains unresolved after
two meaningfully different searches, record the gap under uncertainty and
finish the file. Do not delay the whole batch for an optional historical
detail or additional explanatory source.

## 5. Research and writing standard

Follow [references/dossier-contract.md](references/dossier-contract.md).
Browsing is mandatory; memory alone is insufficient.

Search separately for:

1. canonical definition and terminology;
2. the motivating problem and predecessor methods;
3. seminal formulation and dated refinements;
4. stepwise mechanism or algorithm;
5. variants and neighboring concepts;
6. assumptions, limitations, and known failure modes;
7. the target paper's specific use.

Follow citation chains backward from surveys and forward from seminal work.
Verify historical priority with the original work and, when possible, an
independent retrospective source. Open every cited source. Cite the supporting
page, not a search-results page. Prefer paraphrase to quotation and represent
disagreements explicitly.

Explain the paper's ideas and the concrete mathematical steps they require.
Define notation before use and explain an unmatched or inapplicable prerequisite
where it is needed, with its conditions. For exact applicable personal matches,
show the link and its role in this paper's step without re-explaining the entry.

## 6. Integrate and validate

After all workers finish, read every dossier and reconcile:

- duplicate or inconsistent concepts;
- aliases, notation, and node IDs;
- proposed edges and their directions;
- conflicting historical claims;
- missing citations or paper locations;
- broad subject inventories or ungrounded prerequisite expansion;
- whole-subject mastery inferred from a name match or one concrete matched entry;
- duplicate explanations of verified mastered prerequisites;
- dependency records missing forms, conditions, use steps or checked link states;
- accidental result, evaluation or discussion nodes;
- lost paper scope or same-name mechanisms merged across sources;
- unresolved references to nonexistent nodes.

The root agent decides which proposed edges enter the global graph. Mark an
edge `inference` when it is a synthesis rather than an explicit source claim.
Read each edge literally as `<from> <relation> <to>` to verify direction.

Write `knowledge-graph.md` and `reading-route.md` only after dossier
integration. The full graph may contain contrast or generalization cycles.
Derive the reading route only from the acyclic `prerequisite-for` subgraph.
Begin with the actual prerequisite nodes needed by this paper. Preserve verified
mastered entries in the dependency order as linked references with no required
rereading; explain only unresolved prerequisites before their dependent methods.

## 7. Deliver

Return links to:

- `knowledge-graph.md`;
- `reading-route.md`;
- the `concepts/` directory or its index.

Report the explained methods and concrete prerequisites, verified personal links,
and material source or lookup gaps. Do not paste the dossiers or conduct a quiz unless the
user explicitly asks.

## Quality gate

Before delivery, verify:

- every retained concept has a paper-grounded reason to exist;
- each method retains its paper scope and each prerequisite has a concrete paper use;
- observations and discussion topics remain prose, not graph nodes;
- no generic foundation placeholder or whole-subject mastery assumption appears;
- every dependency has a location, exact form/conditions, use step and checked
  knowledge handle or explicit unresolved lookup state;
- personal queries were read-only; exact applicable matches are linked as mastered
  without duplicate explanation, and unresolved dependencies are explained;
- every explained node links to one complete dossier;
- every dossier used authoritative web research and cites the paper location;
- each dossier covers motivation, lineage, mechanism, formalism, contrasts,
  role in the paper, assumptions, and failure modes;
- parallel workers wrote only disjoint assigned files;
- global edges use the allowed vocabulary and have defensible directions;
- the prerequisite subgraph is acyclic;
- the reading route is a valid topological order;
- all local links resolve and no placeholder remains.
