# Upstream-to-knowledge synchronization

## Direction and target

The reviewed sheets feed the caller's canonical `knowledge/`; they are
upstream artifacts rather than a reverse export. The owner retains the visible `knowledge/` data root for Obsidian viewing.
Do not create a parallel `.kgdistiller/` knowledge store or relocate product
files during compilation.
Do not assume a prior paper-reading vault exists, or automatically create,
bootstrap, register or migrate a knowledge project.

Keep both Markdown sheets authoritative. Adapters may produce derived request
data, but no separately edited JSON library becomes a second source of truth.
The canonical target must retain the accepted scientific content and its
provenance, including unresolved dependency state where supported.

## Prepare a bounded, reviewable change

Identify the exact target project, registered authority scope, sheet revision,
selected definition references, accepted factual relations and pending-state
changes. Include edits and withdrawals, not just additions. Review the
before/after effect on the paper's previously synchronized contributions.
Use existing source ownership and references to find them; never identify a
contribution by display name alone.

Preserve full definitions, conditions, formulas, inputs/outputs, notation,
role bindings, unary/n-ary/self-relation structure, measurements, evidence,
epistemic qualifications and pending resolution history. Each write needs an
explicit identity decision. A same-named entry from another paper is not an
automatic match; ambiguity blocks that item rather than merging it.

Changes are limited to the selected paper's owned content. Preserve other
papers, shared content and user annotations. A withdrawal retracts only this
paper's contribution unless broader removal is explicitly authorized. Do not
leave stale duplicates after an edit, rename or retraction. If ownership or
replacement behavior cannot be established, keep that operation unapplied and
report the specific uncertainty.

Compilation and source review do not authorize live writes. Obtain user
confirmation for the concrete selected content and live target immediately
before apply, unless the user already explicitly authorized that exact scope.
Existing authorization is not a reason to ask again.

## Product boundary and adapter gaps

Use supported product adapters and the existing transactional ingest API when
they can preserve the reviewed content. Follow `$query-kgdistiller` for
identity/digest review and `$ingest-kgdistiller` for its current request,
plan/apply, concurrency, validation and receipt contracts. Do not create a
parallel writer, hand-edit entries as a transaction substitute, or edit raw
graph files, identity registries or alignment files.

For graph-v1, identity still requires one registered native Markdown, Typst or
LaTeX definition marker. A sheet `ref` is a navigation handle, not an existing
canonical node ID. The reviewed adapter must establish that binding explicitly;
headings and sheet order cannot create it.

The compiled library described in `docs/compiled-retrieval.md` is a
caller-supplied read-only retrieval input. It does not create canonical
identities or provide a write API. The current `kgdistiller-graph-v1` ingest
contract cannot be assumed to represent every compiled-library field or the
proposed first-class n-ary factual relation model. Check actual request/delta
capabilities before constructing a plan.

If the adapter cannot losslessly represent a field, relation, pending state,
or retraction, retain it in the upstream sheets and report:

- The exact item/reference and scientific content that cannot be represented.
- The unsupported target field or operation and the observed capability gap.
- The prepared portion, the unapplied portion, and the adapter work needed.

Never drop fields, flatten relations, hide unsupported assertions in unrelated
nodes, or report whole-workflow success because a smaller request committed.
A partial write requires an explicitly reviewed partial scope; report the
remaining gap and never mark an unsupported pending item resolved. A prepared
request or readable compiled projection is not a synchronized knowledge base.

## Apply, verify and retain revision state

Plan before apply using the ingest Skill's public API. Review the exact
predicted changes against the selected sheet content. Stale target/source
preconditions require refreshing the comparison and plan, not bypassing them.
Accept a write only from a committed canonical ingest receipt with its
after-digests matching fresh status and the required product checks.

Record the actual target handle, authority path, receipt reference and
item-level synchronized/unapplied state in
the upstream revision record without changing the scientific assertion or
pretending a receipt proves its truth. Subsequent edits and withdrawals must
update the same owned contributions, preserve other annotations and receive
their own reviewed synchronization. If an earlier contribution cannot be
located safely, report the unresolved stale-copy risk and leave the write
unapplied.

Return the sheet paths, reviewed change scope, plan/receipt paths, verified
target state and precise remaining gaps. Git backup, exports and publication
are separate actions with their existing authorization boundaries.

## Format evolution

When upstream scientific fields or relationship representation change, update
kgdistiller's consuming data model/adapter and round-trip checks in the same
integration. Preserve the previous committed knowledge version until a complete
supported update succeeds, and report unsynchronized or needs-review content.
A Skill wording change alone is not a data-format update.
