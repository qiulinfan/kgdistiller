# Reviewed metadata update and projection refresh

## Authority and review staging

Accepted node content and semantic state belong in the caller's canonical
`.knowledge/`. Keep complete proposed content in `.knowledge/build/reviews/`
until a supported, authorized transaction commits it. The review area is
transient proposal storage, not an alternate accepted knowledge base. Preserve
the accepted entry contract: each entry cites one registered source path and line
range and quotes those lines verbatim as Evidence.

The repository's [shared model](../../../docs/concepts-and-relations.md)
generalizes across source types. The essentials below are bundled so installed
Skills do not require a repository checkout.

## Knowledge model and complete proposals

Read the selected source's `document_type` profile from `scan --file` before
extracting. The profile's `node_kinds` and `extraction_guidance` determine what
knowledge to propose; no document-class enum is built into this workflow. The
following are examples of the shared model, not a substitute for the caller's
profile. Cite source documents directly by path and line range; they are plain
text to kgdistiller, whatever their format, and are never converted.

- Mathematical definitions, axioms and theorems/lemmas can be knowledge nodes;
  computer-science algorithms, architectures and independently defined methods,
  quantities or components can also be nodes. Retain complete meanings,
  conditions, notation, formulas, inputs/outputs and source evidence.
- Propositions and remarks express factual or logical relations rather than
  becoming nodes merely through their labels. Preserve existing accepted
  entries until an explicit identity review decides otherwise.
- Examples and experiments express typed use/application relations, rather than
  creating concepts solely to hold instances. Retain applied knowledge
  references, input/context, conditions, steps, result, evidence and epistemic
  status together.

A heading, label, citation or nominalized claim alone is insufficient for a new
identity. Preserve same-named source-scoped meanings until explicit identity
review; an explaining source does not establish first origin. Around twenty
concepts is a paper-extraction diagnostic only, not a quota or a count limit for
mathematical notes, blogs or projects.

Each proposed assertion retains a review reference, a declared predicate/type,
statement, participant-role bindings, conditions, measurements, evidence,
epistemic status and review status. Preserve unary, binary and n-ary arity,
repeated targets in different roles/configurations and self-relations. Dynamic
input/output states remain explicit. Do not flatten one comparison into
unrelated edges; split independent findings when their conditions or epistemic
states differ. Do not promote a hypothesis to fact or association to causation.

Atomic entries support `understanding` (`unknown`, `not-yet-understood`,
`understood`) and `pending_prerequisites` (direct source-grounded gap strings).
Absence means unknown; preserve omitted learning fields on updates. Only the
user's stated understanding changes that status. A definition being found or
an entry being current does not establish mastery.

Pending proposals preserve the required meaning, precise use sites and any
already known defining source. Record one dependency layer and stop. Inspect a
primary defining source when the user chooses to resolve that gap, reviewing
conditions before linking. Its newly exposed prerequisites belong to its own
entry. Do not recursively expand a bibliography or conflate finding a definition
with understanding it. Partial sheets retain their selected coverage and
unselected existing rows; full-source distillation is explicitly requested.

## Bounded change and current adapters

Identify the exact target project, registered source scope and revision,
selected identities, complete content changes, direct relations, applications
and pending-state changes. Include edits and withdrawals, not only additions.
Review their before/after effect against accepted knowledge. Use explicit source
ownership and identity bindings, never display names alone. Preserve other
sources, shared content and user annotations; withdrawing one source's
contribution does not authorize deleting a shared identity.

Use `$query-kgdistiller` for identity review and `$capture-kgdistiller` /
`$ingest-kgdistiller` for deltas, plan/apply and receipts. A sheet navigation
link is not a canonical entry id. Do not substitute hand edits of entries for a
reviewed transaction, and never hand-edit `.knowledge/edges.jsonl`.

The current `kgdistiller-agent-delta-v1` direct-edge adapter does not losslessly support full n-ary
relations, application records or rich dependency-gap history. Simple direct
gaps and understanding use the atomic-entry fields above. The
caller-supplied compiled library is a read-only retrieval input, not a write
API. Check actual request/delta capabilities before constructing a plan.

For unsupported content, keep the full proposal in `.knowledge/build/reviews/`
and report the exact item, scientific fields/operation, observed adapter gap and
unapplied scope. Do not invent a storage format or command, drop fields, flatten
relations or hide assertions in unrelated nodes. Apply a representable subset
only when that partial scope has been explicitly reviewed and authorized; a
smaller committed delta does not synchronize the full proposal.

## Apply and refresh

Compilation or source verification alone does not authorize a live write.
Obtain confirmation for the concrete content and target immediately before
apply unless that exact scope is already explicitly authorized. Existing
authorization is not a reason to ask again.

Plan through the ingest Skill's public API and review its predicted changes.
A refused apply (`stale-evidence`, `label-mismatch`, `identity-collision`)
requires a refreshed preparation and plan. Accept completion only from a
committed receipt followed by a passing `check` and fresh `agent status` counts.
If contribution ownership or replacement behavior cannot be established, leave
the operation unapplied.

After verification, refresh the lightweight definition and pending projections
with real links to the actual committed metadata. Record receipt/revision and
item-level linked/unapplied state without implying that a receipt proves a
scientific claim. Preserve authored references and unrelated source files.
Return proposal, receipt and sheet paths, verified target state and remaining
gaps. Git backup retains its existing boundary.

This contract describes authoring and projections. New canonical fields or
adapters require separately scoped product work and round-trip validation; the
Skill does not implement those APIs or choose a new graph storage format.
