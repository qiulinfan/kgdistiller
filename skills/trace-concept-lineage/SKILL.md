---
name: trace-concept-lineage
description: Batch-distill a technical paper or concept inventory into source-backed concept dossiers, a typed knowledge graph, and a prerequisite-ordered reading route. Collapse generic calculus, linear algebra, and probability foundations into one assumed MATH-FOUNDATION node, and delegate independent domain concepts to parallel subagents that write disjoint files. Use when Codex must turn a paper into a one-session learning map, research many related concepts with authoritative web sources, or prepare non-interactive study material grounded in a target paper.
---

# Trace Concept Lineage

Distill a paper into a browsable concept library rather than teaching it
interactively. Browsing is mandatory for every explained concept. Keep generic
mathematical foundations compressed into one assumed node.

## Inputs and output

Use:

- a paper repository, paper file, or complete paper text;
- an optional concept inventory, especially one produced by
  `$distill-paper-knowledge` candidate graph from an
  `$extract-paper-markdown` package;
- the learner profile, defaulting to familiarity with foundational
  mathematics;
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

Create stable node IDs and canonical names before delegation. Keep concepts
that are necessary to understand:

- the motivating problem;
- the model, mechanism, objective, or algorithm;
- the paper's assumptions and proof strategy;
- architectural or data constraints;
- the main results, comparisons, and failure modes.

Do not create nodes merely because a term appears. Merge aliases and paper-
local renamings into one node unless their distinction matters to a result.

## 2. Collapse foundational mathematics

Create exactly one assumed node:

`MATH-FOUNDATION — calculus, linear algebra, and probability foundations`

Do not create dossiers for generic foundations such as:

- limits, derivatives, gradients, Jacobians, integrals, or routine ODE
  manipulation;
- vectors, matrices, linear maps, subspaces, projections, eigenvalues, rank,
  norms, positive semidefiniteness, or Kronecker products;
- random variables, distributions, densities, expectation, variance,
  covariance, Gaussian variables, conditioning, Bayes' rule, or basic
  asymptotics.

Treat this node as an already-satisfied prerequisite. Use the mathematics
compactly inside domain dossiers. Define only paper-local or domain-specific
notation, not foundational operations.

Do not collapse a domain mechanism merely because it is expressed
mathematically. Keep concepts such as diffusion processes, Brownian motion in
diffusion modeling, stochastic differential equations, reverse-time dynamics,
score matching, denoisers, samplers, probability-flow ODEs, Wasserstein
objectives, spectral obstructions, or architecture-specific invariants when
they matter to the paper.

For a borderline term, keep it as a separate node if at least one is true:

- the paper's contribution or theorem depends on its domain-specific meaning;
- changing it would change the model, algorithm, or conclusion;
- it has a meaningful technical lineage beyond foundational mathematics;
- its name is a useful standalone research query in the paper's field.

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

Delegate each explainable node to a subagent. Use all safe available
concurrency and refill slots until every node is complete. Assign one concept
per subagent; assign a tightly coupled pair only when separating them would
make either dossier incoherent.

Give every worker:

- the node ID, canonical name, aliases, and intended sense;
- the exact target file under `concepts/`;
- the paper path and exact relevant locations or excerpts;
- direct prerequisite and neighboring node IDs;
- the learner assumption that `MATH-FOUNDATION` is mastered;
- the dossier contract at
  [references/dossier-contract.md](references/dossier-contract.md);
- an instruction to browse authoritative sources;
- an instruction to write only its assigned file.

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
- explain generic calculus, linear algebra, or probability;
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

Explain domain ideas from first principles while assuming the collapsed math
node. Define each paper-local symbol before use, but do not reteach the
mathematical operation that the symbol participates in.

## 6. Integrate and validate

After all workers finish, read every dossier and reconcile:

- duplicate or inconsistent concepts;
- aliases, notation, and node IDs;
- proposed edges and their directions;
- conflicting historical claims;
- missing citations or paper locations;
- accidental foundational-math tutorials;
- unresolved references to nonexistent nodes.

The root agent decides which proposed edges enter the global graph. Mark an
edge `inference` when it is a synthesis rather than an explicit source claim.
Read each edge literally as `<from> <relation> <to>` to verify direction.

Write `knowledge-graph.md` and `reading-route.md` only after dossier
integration. The full graph may contain contrast or generalization cycles.
Derive the reading route only from the acyclic `prerequisite-for` subgraph.
Place `MATH-FOUNDATION` at stage 0 and mark it as assumed, with no dossier.

## 7. Deliver

Return links to:

- `knowledge-graph.md`;
- `reading-route.md`;
- the `concepts/` directory or its index.

Report the number of explained nodes, the collapsed math node, and any
material source gaps. Do not paste the dossiers or conduct a quiz unless the
user explicitly asks.

## Quality gate

Before delivery, verify:

- every retained concept has a paper-grounded reason to exist;
- generic mathematics appears only as `MATH-FOUNDATION`;
- every explained node links to one complete dossier;
- every dossier used authoritative web research and cites the paper location;
- each dossier covers motivation, lineage, mechanism, formalism, contrasts,
  role in the paper, assumptions, and failure modes;
- parallel workers wrote only disjoint assigned files;
- global edges use the allowed vocabulary and have defensible directions;
- the prerequisite subgraph is acyclic;
- the reading route is a valid topological order;
- all local links resolve and no placeholder remains.
