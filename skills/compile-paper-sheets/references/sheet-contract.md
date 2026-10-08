# Paper sheet contract

## Authority, scope and references

The caller chooses the paper and source material. Each sheet starts with:

- The natural paper title, source URL and version when available.
- `coverage`: the portions actually read, missing material, and whether the
  requested scope is complete. Include the source format and precise locators
  for any coverage boundary.
- `review_status`: current state and what was reviewed; distinguish source
  verification from the user's acceptance for knowledge synchronization.

Use original section/paragraph anchors, equation labels, figure/table numbers,
appendix locations or PDF pages to locate supporting passages precisely. A
paper title or broad section heading alone is insufficient for an item.
Every item identifies its owning paper and evidence locations; the sheet's
header can supply the shared title and URL. An external source has its own
natural title, URL, version and locators.

Assign an explicit, readable `ref` to each definition, relation and pending
item that needs linking. Reuse authored references on revision; do not derive
identity from headings, order or keyword overlap. These are local navigation
handles, not a paper-ID or hash scheme and not canonical graph node IDs.
Qualify cross-paper targets with their natural paper title, source URL and
authored reference. A rename changes the display name, not its reference.

Use Markdown as the single authored authority for each artifact. A temporary
JSON projection must identify the source sheet and be regenerated from it;
do not independently edit both representations. No format conversion may
silently omit scientific content.

## `paper-def-sheet.md`

Keep two substantive sections: definitions and factual relations. Write
Chinese explanations with original English technical terms and English
`type`/`status` values. Preserve equations and notation exactly enough to
retain their meaning; define symbols, units and conventions.

### Definition admission and content

A definition describes an independently meaningful, noun-like method,
architecture, component, objective, operation, protocol, quantity or precisely
defined phenomenon. Require this paper to substantively explain what it is.
Mere naming, citation, usage, or turning a claim into a noun phrase does not
qualify. A precisely stated, important `theorem` or `lemma` may qualify when
its assumptions, conclusion and proof/source status are explicit; an informal
hypothesis does not.

For each admitted definition retain:

| Field | Required content |
| --- | --- |
| `ref`, name, `type`, `review_status` | Explicit stable reference, English technical name, meaningful type and current review status. |
| Definition | A self-contained Chinese explanation of what the object is and what distinguishes it in this paper. |
| Conditions and formal content | Full defining assumptions, formulas, notation, units and boundary cases needed for correct use. |
| Inputs/outputs | The actual contract when applicable, including states or interfaces rather than just input/output names. |
| Dependencies | Explicit targets or pending references, their required meanings and the definition's use of them. |
| Evidence and attribution | Precise paper locators and the distinction between source explanation, inherited definition, author claim and agent reconstruction. |

Local explanation of an inherited concept can support a paper-scoped node,
but does not turn that paper into the first source or replace the external
definition. Treat an explaining source as the explanation authority unless
first origin has separately been verified. Preserve same-named concepts in
different papers as separate scoped records; similarity and shared spelling
do not establish equivalence.

Around twenty concepts per paper is an extraction diagnostic, not a minimum,
maximum or completeness test. A larger count prompts review for redundant
components, configuration records and nominalized findings. Do not remove
real definitions to meet a count.

### Complete factual relations

Retain the substantive observations, comparisons, claims and hypotheses from
the declared coverage here, including support for admitted definitions.
Reducing definition nodes must not discard factual evidence. A missing
relation section is not repaired by hiding claims inside definition prose.

Each relation retains an explicit `ref`, an English `predicate` with a
declared meaning, a Chinese statement, participant bindings, conditions,
evidence, epistemic status and `review_status`. A relation reference does not
make that assertion a concept node. Use a readable list or table whose
content is equivalent to:

| Content | Preservation rule |
| --- | --- |
| Participants | Bind each target to its English role. Targets may be definition references or explicit pending references. Preserve repeated targets with distinct roles, configurations or states. |
| Arity | Allow one participant for a property observation, two for a binary assertion, and more for an n-ary assertion. Preserve self-relations; distinct endpoint count is not an admission rule. |
| Conditions | Keep the setting, assumptions, dataset/protocol, configuration, intervention and scope jointly attached to the assertion. |
| Measurements | Retain quantity, value, units, comparison direction, uncertainty and measurement protocol when stated. |
| Evidence | Preserve exact supporting locators, source-specific qualifications and any limits of support. |
| Epistemic state | Preserve `epistemic.status` using scientific terms such as `defined`, `design-choice`, `cited`, `empirical`, `conjectured` and `proved`. Record author attribution and agent reconstruction separately; do not promote association to causation or hypothesis to fact. |

Do not flatten a comparison into unrelated pairwise edges: its subject,
baseline, measurement and conditions jointly express one result. Split
independent findings when their conditions or epistemic states differ. Keep
dynamic input/output states explicit. A cycle does not establish a circular
proof. Declare new predicate/role meanings for review instead of inventing
sentence-specific canonical relation types.

Use `review_status` values such as `draft`, `reviewed`, `accepted`, `rejected`
or `withdrawn`, stating who accepted which scope when acceptance is claimed.
Review status is separate from scientific epistemic status.

## `paper-pending-sheet.md`

Record concepts merely named, cited or used without a sufficient local
definition. Do not manufacture a definition from familiar terminology or
attribute an external explanation to the using paper.

Each pending item retains:

- An explicit `ref`, source term/aliases and English `type`.
- Precise use sites and the owning definition/relation references.
- The required meaning: what operation, statement, conditions or interface
  the paper needs at those sites.
- Why the current source does not supply an adequate explanation, without
  treating a missing explanation as proof that the user lacks the knowledge.
- Candidate external defining sources, with natural titles, URLs and precise
  locators, and an applicability decision with its evidence.
- `resolution_status` (`open`, `source-found`, `resolved` or `not-applicable`),
  a separate review status, and the exact resolution target or remaining gap.

Follow the relevant citation or conduct a bounded search for a primary
source that actually states the intended definition, formula and conditions.
Inspect that source; a search snippet, citation, identical name or known paper
record cannot close the gap. Check the needed sense and conditions against
the original use site. Do not recursively expand a bibliography or silently
replace the required meaning with a nearby concept.

An external definition remains attributed to its external source. If it is
accepted into the workflow, link it explicitly with full provenance and keep
the using paper's pending record and resolution history. An unavailable,
ambiguous or inapplicable source remains visible as a pending gap.

## Revision and review

Preserve authored references and unrelated user annotations. Explain material
definition, condition, formula, relation or pending-state changes. Retain
reviewable evidence of rejected/withdrawn items and their former targets so
later synchronization can retract the paper's contributions without leaving
silent stale copies. Review the sheets against their declared source coverage;
counts and transport validity do not certify scientific completeness.
