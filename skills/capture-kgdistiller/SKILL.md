---
name: capture-kgdistiller
description: Save or update one source-backed knowledge item while reading, preserving personal understanding and direct pending prerequisites. Use for requests to capture this concept, remember this definition, or update one entry; full-source distillation belongs to compile-knowledge-sheets.
---

# Capture one knowledge item

Save the selected knowledge to the caller's established `knowledge/` through one
bounded transaction. Use the current passage and just enough nearby context to
preserve its definition and conditions. Do not turn this into a full-source read,
a survey, a recursive prerequisite search or a new knowledge project.

Match the user's language. Retain technical names, formulas, type/status labels,
structured keys and raw errors.

## Select and understand the item

Establish the target project, registered source, selected concept and exact use
or definition location from the current conversation. Ask only for an essential
missing target or a genuinely ambiguous meaning. Read additional local context
only when a defining condition or source statement is incomplete.

Keep the full definition and essential assumptions in the entry. Definitions,
axioms, precise theorems, algorithms and architectures may be nodes. A claim or
example belongs to a relation/application; do not manufacture a concept merely
to fit the entry API. Preserve unsupported complete assertions as review
proposals and identify the remaining adapter gap.

If a selected term is only mentioned and not explained, record it as a direct
pending prerequisite of the current owning knowledge entry. Do not invent its
definition or a canonical target. If no owner is established, keep the gap as a
source-scoped review proposal until that ownership is resolved.

## Preserve learning state and stop at one layer

Read any existing `entry.understanding`. The values are `unknown`,
`not-yet-understood` and `understood`; absence means unknown. Record the user's
explicit statement and preserve existing state otherwise. Saving a definition,
reading its source or matching an entry never establishes understanding.

`pending_prerequisites` holds concise direct gaps: term, required meaning and
source/use context, including whether the gap is a missing definition or a
known definition the user still needs to learn. A verified existing entry may be
linked while remaining a learning gap. Add no target node for an unresolved term.
When the user later chooses to study that prerequisite, its own entry can record
its next layer. Stop here during this capture.

Read existing learning fields before changing their lists. Omitted fields are
preserved by ingest; an explicitly supplied pending list replaces that list, and
`[]` clears it. Resolve only the item the user has actually understood or asked
to update. Finding an external source alone does not clear a learning gap.

## Prepare and apply one bounded update

Use the product's [capture preparation contract](references/capture-contract.md) for the
compact payload and CLI. The deterministic helper builds the candidate,
comparison and finalized plan/apply requests; do not hand-create their internal
identifiers or digests. Keep prepared artifacts in the caller's
`knowledge/build/reviews/` or another explicit project review directory.

Supply a source-grounded entry and explicit reviewed add/update intent. A new
entry needs a reviewed native authority marker at its real definition; an update
reuses an established identity. Preserve unrelated source prose and markers.
The helper performs one candidate comparison. Inspect its bounded evidence;
name similarity alone cannot establish semantic identity. Use
`$query-kgdistiller` only for a concrete ambiguity or missing identity evidence,
then prepare again if the reviewed decision changed.

Hand the generated requests to `$ingest-kgdistiller`: plan, inspect the selected
source/entry changes, and apply the authorized scope. A user's concrete request
to save this item is authorization for that item; do not ask for the same
permission again. Preparation does not itself commit knowledge. An ambiguous
identity or stale source needs a corrected comparison, not an overridden gate.

The transaction can leave other entries in the source uncurated. Verify the
committed receipt and return a link to the accepted entry plus its understanding
and direct remaining gaps. If a source def/pending sheet exists or was requested,
refresh only its affected rows and retain `partial` coverage. Preserve unrelated
rows and annotations. A partial sheet need not be filled before capture ends.
