---
name: compile-paper-sheets
description: Compile or revise a paper's upstream definition sheet and pending-dependency sheet, preserving paper-scoped meanings and complete factual relations. Use when the user requests def-sheet or pending-sheet compilation, review, or synchronization; ordinary paper reading does not require this Skill.
---

# Compile paper sheets

Create two readable, source-grounded upstream artifacts:
`paper-def-sheet.md` and `paper-pending-sheet.md`. These are inputs to a
reviewed knowledge update, not exports of the canonical knowledge base.
Keep this workflow separate from `$distill-paper` and `$harvest-paper`;
their invocation and import policies remain unchanged.

Match conversation and handoffs to the user's language. Write sheet
explanations in Chinese while retaining English technical terms, `type` and
`status` values, identifiers, formulas and raw errors, unless the user
explicitly requests another language.

## Compile or revise

Read [references/sheet-contract.md](references/sheet-contract.md) before
authoring. Establish the caller's paper, version, source URL, source material,
output location and requested coverage. Reuse existing sheets and authored
references when revising. Use caller-selected sources; do not embed a fixed
paper corpus or assume a previous paper-reading vault exists.

Default to the complete paper and substantive appendices unless the caller
explicitly requests a bounded section. The concept-count diagnostic never
limits source reading or factual coverage.

Read the source within that scope, including figures, formulas, proofs and
appendices needed for its definitions and assertions. Preserve precise source
locators and report unavailable parts in both sheets' coverage. A partial
reading must not receive a full-paper coverage status.

- Admit independently meaningful noun-like definitions only when this paper
  substantively explains them. A precise, important theorem is the stated
  exception; empirical claims and hypotheses remain factual relations.
- Put the complete source-grounded factual relation section in the definition
  sheet. Keep participant roles, unary and self-relations, conditions,
  measurements, evidence and epistemic qualifications together. Never
  replace an n-ary assertion with disconnected binary edges.
- Put unexpanded mentions in the pending sheet with their actual use sites
  and required meanings. Trace a needed definition through citations or
  bounded search to a primary defining source; check its conditions and
  applicability before resolving the gap. Keep unavailable or ambiguous
  definitions pending.

Around twenty concepts is a diagnostic for over-extraction, not a quota.
Review nominalized facts, redundant components and configurations when the
count is large; retain substantive definitions and all factual support.
Same-named definitions from different papers stay separate until an explicit
identity review. An explaining source does not establish first origin.

Each sheet is its own authored Markdown authority. Machine-readable
projections may be derived for an adapter, but must not become independently
edited parallel truths. Preserve explicit, stable references and existing
annotations; headings and document order never create identity.

## Review and synchronize

Compilation alone does not authorize a live knowledge write. Prepare a
reviewable change containing the selected definitions, accepted relations,
pending state and any edits or retractions.

When synchronization is requested, read
[references/sync-contract.md](references/sync-contract.md). Target the caller's
canonical `knowledge/` through supported product adapters and
`$ingest-kgdistiller` when they can preserve the reviewed content. Obtain user
confirmation for the specific content and live target unless that exact apply
is already explicitly authorized.

The caller-supplied compiled library is currently a read-only retrieval input,
not a canonical write API. Do not claim graph-v1 ingest supports the whole
compiled schema. Preserve unsupported content in the sheets, identify the
precise adapter gap and report any unapplied portion. Do not bootstrap or
migrate a vault, create a knowledge directory automatically, or edit raw graph
files.

Return the two sheet paths, coverage and review state, definition/relation/
pending counts, and remaining source or adapter gaps. Report a live update as
complete only with the verified transaction receipt required by the sync
contract.
