# Paper method and mechanism graph contract

## Required input

Consume a source-validated `qlpaper-latex-source-v1` package with `source/`,
`source.json` and `link.txt`. Graph extraction reads TeX directly and may run
while the translator produces paper.md and paper_ch.md. Use `--source-only`
validation for this handoff; completed translation is not a graph prerequisite.
Read the entrypoint, includes, bibliography and appendices. Cite source files,
lines, sections and labels. Do not acquire PDFs or create evidence directories;
retain specific gaps when captions and text cannot establish a visual fact.

## Node admission

A knowledge node describes a concrete method or mechanism defined or used in
this paper. Its payload must identify the objects or inputs, the operations or
structure, the resulting objects or outputs, and the defining conditions.
Include formulas or algorithmic steps where they define the mechanism. Necessary
background mechanisms use the same admission rule. Concrete mathematical
prerequisites (definitions, operators, inequalities and theorem formulations)
are also eligible when an actual operation or derivation step requires them;
record the use and applicable conditions. Merely being searchable,
reusable, important to the argument, or named in the paper is insufficient.

Use `properties.paper_role` values `method`, `mechanism`, or `prerequisite`.
The last denotes specific knowledge actually used, never a broad subject.
Keep conditions and
shape constraints in the owning mechanism's explanation. Do not create knowledge
nodes for problems, measurements, evaluation protocols, performance explanations,
concept comparisons, limitations, review judgments, authors, headings or symbols.
Read such material to understand the paper and retain useful details in unmarked
paper notes, outside the candidate nodes and semantic edges.

Examples of the admission decision:

| Proposed item | Treatment |
|---|---|
| Residual learning with its residual branch and additive computation | Mechanism node with the paper's formula and conditions |
| Identity or projection shortcut with an explicit branch operation | Mechanism node, scoped to this paper and version |
| "How bottleneck reduces computation" | Explanatory topic; do not create a node from this heading or a cost observation |
| "Optimization versus generalization" | Discussion in paper notes, not a knowledge node |
| Classification scores, crop settings, or the 1202-layer negative result | Experimental data in paper notes |
| "No universal convergence guarantee" | Evidence boundary in paper notes |
| A specific convolution operation, an Lp/ell-p inequality, or a version of a law of large numbers used in a derivation | Concrete prerequisite with its exact use, formulation and conditions; look up the existing entry |

An architecture is eligible only if the source independently defines its concrete
construction. Renaming a cost/result summary to an architecture name does not
satisfy this requirement. A metric name alone also fails the admission rule;
do not add metric nodes merely to explain reported scores.

## Trace dependencies from actual uses

Work from the paper's concrete operations, algorithms and derivation steps.
For each substantive dependency record:

| Paper location and step | Required knowledge and exact form | Conditions needed here | Existing entry and verified applicability | Action |
|---|---|---|---|---|

For example, identify which convolution is used and on what objects; which
inequality controls the indicated norm and with what exponent conditions; which
law of large numbers justifies that limit and whether the stated assumptions
support that version. These are illustrative queries, not a mandatory list for
every paper. Do not infer a named theorem merely from a keyword or silently
replace an unproved paper claim with a theorem of your own.

Use `$query-kgdistiller` by default. Check retrieved content, not just its title.
An accurate match applicable to this use means the user has mastered that
specific knowledge: retain its link and the paper's application, and skip its
tutorial. Explain only unmatched knowledge or a missing/different condition.
Do not infer that one matched item establishes mastery of linear algebra,
multivariable calculus, probability, or any other entire subject.

Never add `MATH-FOUNDATION` or generic subject placeholders. Expand dependencies
only as needed to resolve a real step, and stop at a verified applicable entry or
a sufficiently explained local gap. Do not enumerate all elementary operations
or recursively reconstruct a textbook. A matched theorem's own prerequisite
tree need not be expanded again. For an implicit dependency, label the use as an
inference and show the source expression that warrants it.

Keep experimental conclusions as use-site text rather than result nodes. A
specific mathematical theorem used to derive a statement is prerequisite
knowledge, distinct from that paper's empirical score or review conclusion.

## Paper-scoped identity format

For each node preserve:

- `namespace`: `paper:<source-digest-prefix>`; retain the full source digest below;
- `id`: `paper-<source-digest-prefix>-<local-slug>`, independent of document
  order, for example `paper-c9b29f067122aca3-identity-shortcut`; the paper prefix
  is required because the engine also probes IDs across namespaces;
- `label`: `<local name> (<stable paper identifier><version>)`, for example
  `Identity shortcut (arXiv:1512.03385v1)`;
- `properties.paper`: `identifier`, `version`, `title`, `authors`, and
  `source_sha256`, taken from the validated manifest and source text;
- `properties.local_name` and `properties.local_aliases`: the terminology used
  within the paper, including any disambiguating sense;
- `properties.paper_role`: `method`, `mechanism` or `prerequisite`;
- `provenance`: the source authority and precise location, using existing schema
  fields; the payload contains the paper-local definition and mechanism.

These are authoring conventions within the existing candidate schema, not new
engine schemas or CLI options. Node handles remain `(namespace, id)`. Qualification
in both ID and label prevents ordinary ID/name matching from discarding the paper
scope. Use a sufficiently long digest prefix to distinguish the compared packages
(at least 16 hex characters), and check the full digest when reusing an identity.
Do not set `properties.target_id` as a shortcut to a same-name entry. Do not put
bare local terms in global aliases or use them as identity
queries. Use them as lexical retrieval terms. Keep the paper/version visible in
human-readable indexes and any later native authority markers.

The same word in two papers or revisions does not establish the same mechanism.
Compare source definitions, operations, formulas, domains/shapes and defining
conditions before proposing equivalence, specialization, extension or difference.
Insufficient detail leaves the relationship unresolved. Even reviewed equivalence
is a bridge between scoped entries, never permission to merge them into a global
unqualified node. An external background explanation retains its own source;
do not silently attribute its definition to the target paper.

## Candidate graph and comparison

Represent admitted candidates as knowledge nodes. Use existing source-supported
`prerequisite-for`, `implies`, `derived-from`, `generalizes`, `contrasts-with`,
and optional `contains` edges only between admitted nodes. A concrete prerequisite
points with `prerequisite-for` to the method or mechanism that uses it; preserve
the precise derivation location as evidence, without inventing a result node.
Include evidence for
each semantic relation and read its direction literally. Performance improvement,
experimental comparison, co-occurrence and section order do not create mechanism
relations. Leave a connection in prose if the supported vocabulary cannot express
it accurately; do not overload `derived-from` to mean "scored better."

Build and validate snapshots through the engine. Query the established personal
knowledge project by default, respecting an explicit opt-out. If no unambiguous
target is available, continue local extraction and mark linking incomplete.
For completed queries preserve raw comparison statuses
`matched`, `ambiguous`, `unmatched`, plus the target digests. Review cross-paper
identity separately through `$query-kgdistiller`: a bare-name match does not
justify a bridge. Keep the original paper-specific content even for matched
nodes. A bridge never becomes a semantic edge inside the paper namespace.

## Human-readable output and completion

Write `paper-graph.md` with paper identity, methods/mechanisms, concrete
prerequisites, use-site records, source locations, semantic edges and verified
existing-entry links. Link unmatched items to `methods/<node-id>.md`. Matched
items link to the existing authority through the verified handle and bounded
source location returned by the query API, with a concise explanation of where
the paper uses them. Do not write another tutorial for a mastered match.
Include actual unresolved decisions and record unavailable lookup separately
from a completed query that found no applicable entry.

Each new method entry is an independently readable explanation of this paper's
mechanism: introduce its local terminology and purpose, identify inputs/objects,
explain operations step by step with necessary formulas, and state outputs and
defining conditions. Include a small example when useful and exact source
locations. Preserve the paper-qualified name and full provenance. Match the
user's language. Do not require exhaustive historical research, a rigid dossier
template or a separate subagent per entry for the default graph branch. A missing
prerequisite entry explains only the specific definition/statement, conditions
and application needed at the use site, not a survey of its parent discipline.

These entries supplement the independent `reading.md` in `$read-paper`; neither
graph admission nor comparison status restricts that ordinary explanation.
Keep experimental data, evaluation settings and review discussion in a clearly
separate paper-notes section of `paper-graph.md`, or another assigned file within
the graph output root. Do not add graph markers or include these records in
node/edge counts. A parallel graph worker must not edit `reading.md` or either
translator file; the parent integrates reading corrections after the reader finishes.

Completion requires node-admission and paper-scope review, verified existing-entry
links or readable gap entries, source-backed dependency uses, valid candidate and
snapshot digests, and visible source gaps. Default linking also requires reviewed
identities and unchanged target digests. Explicit opt-out or unavailable lookup
must be disclosed, not passed off as completed linking. Mechanical validation checks schema integrity;
the agent must review method/mechanism admission and cross-paper meaning.
Existing artifacts do not acquire these semantics just by validating: flag legacy
unqualified nodes or result nodes for separate regeneration, without rewriting
them or any personal graph as part of a Skill-maintenance request.
