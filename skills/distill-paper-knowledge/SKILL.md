---
name: distill-paper-knowledge
description: Explicit command only. Extract paper-scoped methods and mechanisms, trace exact prerequisites used in their operations and derivations, and link them to the personal knowledge base by default through read-only queries. Produce an isolated graph and readable entries directly from TeX, independently of the section guide or the normal paper explanation.
disable-model-invocation: true
---

Run only when the user explicitly invokes `$distill-paper-knowledge` or `/distill-paper-knowledge`.
Do not start this workflow from an ordinary paper-reading request or another paper command.

# Distill paper knowledge

Produce an isolated graph of the methods and mechanisms defined or used by the
paper. This Skill owns semantic extraction, not source acquisition or personal
knowledge ingestion. Keep the source package immutable; write generated outputs
only under the requested output root or its `knowledge/` directory.

## Align language

Match user-facing explanations and handoffs to the user's language. Keep
commands, identifiers, structured keys and raw errors unchanged.

## Validate the handoff

Require a `qlpaper-latex-source-v1` package with `source/`, `source.json`, and
`link.txt`. Run the validator from `$distill-paper` with `--source-only`.
Read the original TeX and includes directly. No full-text Markdown transcription
or translation is required. Do not wait for the independent explanation or
section guide, and do not edit their files.
Stop only the claims whose necessary source text is unresolved.

Read [references/research-paper-contract.md](references/research-paper-contract.md)
before selecting candidates. Treat the retained LaTeX as immutable source authority. Do not fetch or use PDFs,
create evidence directories, or require visual inspection. Use caption/text-supported
figure descriptions and retain specific interpretation gaps.

## Read the paper, then select methods and mechanisms

Read the complete LaTeX source, local includes and supplements in this order:

`problem -> setup and assumptions -> mechanism -> results -> evidence -> limitations`

Reading the whole argument does not make every part of it a knowledge node.
Retain a concrete method or mechanism only when its objects, operations or
structure, and defining conditions can be explained from the source. Record
those details and precise source locations. Keep required background mechanisms
only when they meet the same rule.

Do not create nodes for experimental results, evaluation protocols, performance
explanations, optimization/generalization comparisons, paper limitations, or
review judgments. A reusable observation or an important claim is insufficient.
Attach defining conditions to the relevant mechanism; keep empirical evidence
and interpretation in unmarked paper notes. Do not rename a result or a
discussion topic to make it look like a mechanism.

## Establish paper-scoped identity before alignment

Follow the identity format in the contract: a source-digest namespace, a stable
local ID prefixed by the source digest, a canonical label qualified by the paper identifier and version, and
structured paper provenance. Retain the unqualified term and aliases as local
terminology for retrieval, never as global identity aliases. A paper tag used
only for display is insufficient.

Same-name terms in different papers or versions remain distinct. Cross-paper
equivalence requires a source-backed comparison of definitions, operations,
formulas and conditions; even an exact bridge preserves the scoped nodes.

Write `knowledge/paper.candidate.json` as
`kgdistiller-candidate-graph-v1` in an isolated namespace such as
`paper:<source-digest-prefix>`. Build and validate the snapshot with the engine:

```sh
kgdistiller candidate build PAPER_PACKAGE/knowledge/paper.candidate.json \
  --output PAPER_PACKAGE/knowledge/paper.snapshot.json
kgdistiller candidate validate PAPER_PACKAGE/knowledge/paper.snapshot.json
```

Do not hand-write snapshot digests.

## Trace concrete dependencies and write readable entries

Follow the dependency-use procedure in the contract. For each necessary operation
or derivation step, identify the exact operator, definition, inequality or theorem
form used, its conditions, and the source location where it does work. General
labels such as linear algebra, multivariable calculus, probability or a bundled
math-foundation node are not dependencies and imply nothing about mastery.

Concrete prerequisite knowledge may be retained with `paper_role: prerequisite`
when a source-backed use requires it. This includes a specific theorem or
inequality; it does not admit empirical results or review conclusions as nodes.
Expand only the prerequisites needed to explain or verify this use. Stop at a
verified existing entry or a sufficiently explained dependency, not at an
arbitrary subject boundary; do not recursively enumerate an entire discipline.

Prepare each paper-local definition/use before lookup. After lookup write
`knowledge/paper-graph.md`, linking accurately matched existing entries and
recording their exact use in the paper. Under the user's convention these
specific matches are mastered: do not create duplicate explanatory dossiers.
For unmatched knowledge or a genuinely different formulation, write the needed
explanation in `knowledge/methods/<node-id>.md` using the contract. A classification
alone is not an explanation of a gap. These files are isolated paper artifacts,
not registered personal knowledge authorities.

## Query and link the personal knowledge base by default

Resolve the established target from the request/session, otherwise inspect
`kgdistiller vault list` and use its registered default. Do not assume the paper
package or product repository is the personal knowledge project. If targets are
ambiguous or unavailable, continue the local graph, report linking as incomplete,
and request only the missing target information. Do not silently disable the lane,
invent unmatched results, create a vault, or delay the independent reader.

Use `$query-kgdistiller` read-only. Batch exact prerequisite names with their
required senses separately from the paper-scoped candidate alignment. Inspect
the retrieved definitions/statements and their conditions before accepting a
usage link. For instance, distinguish discrete/continuous convolution, a named
Lp/ell-p inequality and its exponent conditions, or the required weak/strong law
of large numbers and assumptions. Name similarity alone does not verify a use.

For every dependency record the paper location and step, required formulation,
the verified existing handle and relevant content, and what that knowledge
justifies. If a specific entry is found and applicable, treat it as mastered and
link it without reteaching or copying it. This convention applies only to the
matched knowledge, not an entire discipline. Unresolved senses remain unresolved;
explain the unresolved part rather than falsely marking it mastered. Honor an
explicit user opt-out, reporting the lane as omitted.
Ordinary reading remains independent of lookup and its results.

Pass the complete snapshot to `$query-kgdistiller` in one bounded batch. Require
one engine status (`matched`, `ambiguous`, or `unmatched`) per node and retain
the target graph, snapshot, and alignment digests. Qualified identities go to
identity resolution; unqualified terms go to lexical retrieval.

Keep engine output unchanged. Separately review any proposed cross-paper bridge
against the source definitions. A name match alone cannot establish equivalent
mechanisms or authorize a ref/import. Record insufficient evidence as needing
review, without a bridge. Content gaps and conflicts require their own evidence;
they are not extra statuses emitted by the comparison API.

For every node retain the paper's own use, defining conditions and locations,
including when a bridge exists. A matched entry needs only this paper-local use
record and the link, not another full explanation. A different paper-local
mechanism still needs its own explanation; shared spelling does not make it mastered.

Write any requested alignment response below `PAPER_PACKAGE/knowledge/` unless
another output root is requested. Keep paper semantic edges separate from
cross-namespace bridges.

## Preserve default isolation

Record the personal graph, snapshot, and alignment digests before and after
lookup and require them to remain unchanged. When lookup was explicitly omitted
or unavailable, report that state rather than claiming linking is complete. Never invoke
`$ingest-kgdistiller`, edit personal markers, register a research authority, or
turn a similarity into identity. Import is a separate, explicitly authorized
`$import-paper-knowledge` workflow.

Return links to the graph and readable method entries, concise source/validation
evidence, method/mechanism counts and actual gaps. Include comparison results and
target digests only when comparison ran. Keep technical digests in artifacts
instead of leading a reading answer with them. Count paper notes separately;
there is no requirement to turn each result or limitation into a node.
