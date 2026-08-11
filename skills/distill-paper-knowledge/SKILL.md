---
name: distill-paper-knowledge
description: Turn a validated qlpaper Markdown package into a source-grounded candidate graph, deterministic isolated snapshot, personal-graph alignment, and human-readable federated paper graph without mutating either the paper package or the target knowledge project. Use after extract-paper-markdown for paper concept extraction, claim and assumption mapping, gap analysis, and default read-only paper federation.
---

# Distill paper knowledge

Produce an isolated paper graph. This Skill owns semantic graph extraction, not
PDF recovery and not personal knowledge ingestion.

## Validate the handoff

Require a complete `qlpaper-markdown-v1` package with `paper.md`, `source.json`,
all declared attachments, and the canonical PDF when declared. Run the validator
from `$extract-paper-markdown`. Stop if unresolved core text or an object summary
blocks a central claim.

Read [references/research-paper-contract.md](references/research-paper-contract.md)
before selecting candidates. Treat the paper Markdown and attachments as
immutable source evidence.

## Recover the argument and build candidates

Read the complete package in this order:

`problem -> setup and assumptions -> mechanism -> results -> evidence -> limitations`

Select independently searchable concepts plus paper-specific assumptions,
methods, metrics, results, and boundaries required to recover that chain. Record
page, section, equation, theorem, figure, or table locations. Exclude passing
terms, authors, headings, bibliography-only names, and local symbols.

Write `knowledge/paper.candidate.json` as
`qlkg-candidate-graph-v1` in an isolated namespace such as
`paper:<source-digest-prefix>`. Build and validate the snapshot with the engine:

```sh
kgdistiller candidate build PAPER_PACKAGE/knowledge/paper.candidate.json \
  --output PAPER_PACKAGE/knowledge/paper.snapshot.json
kgdistiller candidate validate PAPER_PACKAGE/knowledge/paper.snapshot.json
```

Do not hand-write snapshot digests.

## Align once, then explain by status

Pass the complete snapshot to `$query-kgdistiller` in one bounded call. Require
one status per node and retain the target graph, snapshot, and alignment digests.

- `known`: keep only the paper-local role, locations, and exact bridge;
- `partial`: explain only the missing condition, claim, relation, or role;
- `new`: write a complete source-grounded paper dossier;
- `conflict`: retain both claims and provenance without a bridge;
- `uncertain`: retain candidate senses and needed review evidence.

Write the candidate, snapshot, alignment response, and `paper-graph.md` below
`PAPER_PACKAGE/knowledge/` unless another output root is requested. Keep paper
semantic edges separate from cross-namespace bridges.

## Preserve default isolation

Record the personal graph, snapshot, and alignment digests before and after the
workflow and require them to remain unchanged. Never invoke
`$ingest-kgdistiller`, edit personal markers, register a research authority, or
turn a similarity into identity. Import is a separate, explicitly authorized
`$import-paper-knowledge` workflow.

Return package provenance, validation evidence, candidate and snapshot digests,
alignment target digests, node/edge/bridge counts, learning order, status-aware
explanations, and unresolved records.
