---
name: curate-kgdistiller-notes
description: Extract and review source-grounded knowledge from registered Markdown, Typst, or LaTeX notes, resolve existing identities through query-kgdistiller, preserve native authority and reference markers, and hand one bounded update to ingest-kgdistiller. Use for raw-note ingestion, changed-note curation, missing entries or direct relations, marker cleanup, and note-to-static-export workflows in any kgdistiller knowledge project, including when the user asks to file or ingest a document into their kgdistiller (kgd/kgdt) knowledge base.
---

# Curate kgdistiller notes

Turn authored notes into a reviewed update to the caller's canonical
`knowledge/`, which retains accepted content and semantic state. Treat
registered native markers as identity authority, source-grounded Markdown
atomic entries as content authority, and kgdistiller as the deterministic
transaction boundary. Source-scoped review projections can inform an update;
definition and pending sheets are lightweight links to committed metadata.
Full proposals belong in `knowledge/build/reviews/` until accepted; sheets do
not override these authorities. Generated graphs remain opaque derived data.

Match user-facing explanations, prompts, and handoffs to the user's language
unless the user requests another language. Keep commands, identifiers,
structured keys and action codes, and raw errors unchanged.

For a request to save one selected item while reading, use
`$capture-kgdistiller`; it shares the same entries and transaction boundary while
leaving other concepts uncurated. The whole-file extraction below applies only
to a requested source-level curation scope. A sheet may remain partial.

## Establish the bounded source scope

Start with:

```sh
kgdistiller --repo-root PROJECT agent status
kgdistiller --repo-root PROJECT scan --file RELATIVE_AUTHORITY
```

Require status to report a `kgdistiller-graph-v1` graph before curation. If the project still
uses a superseded core discriminator, stop and hand it to
`$deploy-kgdistiller` for the explicit Git-backed registry update and rebuild;
never migrate or relabel the old graph inside a curation transaction.

Require each input to match exactly one pattern in `knowledge/sources.json`.
Use the smallest coherent registered file set, including both paths of a known
rename. If a document is unregistered, propose the source ID, root, format glob,
field classification, optional registered `document_type`, and destination;
obtain review before moving it or expanding a glob. `scan --file` returns
`sources[].document_type` and the selected `document_types` profiles. Follow
the user's `node_kinds` and `extraction_guidance`; document type is separate from
file format and knowledge domain. Do not hardcode document classes or infer
extraction policy from an extension. An absent profile makes no classification
claim; use the explicitly requested scope without silently adding a profile.

Read [references/curation-contract.md](references/curation-contract.md) before
extracting. Never infer graph identity from headings, order, syntax wrappers,
keywords, embeddings, or co-occurrence.

## Extract before explaining

Read each selected `.md`, `.typ` or `.tex` authority directly and completely;
no converted Markdown source is required. Knowledge entries themselves remain
Markdown and link to their native evidence. Preserve existing native markers:

- Markdown: `--[[Concept]]--` and `[[Concept]]`;
- Typst: `#kn[Concept]` and `#ref[Concept]`;
- LaTeX: `\kn{Concept}` and `\knref{Concept}`.

Build one bounded candidate batch containing names, aliases, source locations,
short evidence, and supported direct relations. Preserve complete
relation/application proposals alongside this batch in
`knowledge/build/reviews/`, using the distinction in the curation contract.
Preserve existing user-marked identities pending explicit review. Do not write
entries or choose identity from similarity yet.

Pass the whole batch to `$query-kgdistiller`. Require
`kgdistiller-graph-comparison-v1` with one `matched`, `ambiguous`, or `unmatched`
identity decision per candidate and retain the target graph, snapshot, and
alignment digests. Stop on ambiguous identities. The v1 comparison contract does not assess
partial entries or semantic claim conflicts; do not infer either from ranked
retrieval evidence.

## Prepare one reviewed update

Use matched identities as refs. Add an authority marker only for a reviewed
unmatched identity. Any enrichment of a matched identity needs a separate,
source-grounded human review because the v1 comparison contract does not identify a missing
portion. Write a compact source-grounded entry for every active authority in
the selected scope and add only direct semantic edges with concrete evidence.
Retain complete role bindings, arity, states, conditions and evidence while
reviewing assertions. Graph-v1 direct edges cannot represent full n-ary or
application records: retain unsupported proposals in `knowledge/build/reviews/`,
report the adapter gap and defer their persistence. Never flatten or omit them to
make a smaller delta appear complete.

Treat `kgdistiller-agent-proposal-v1` only as a digest-bound review package. Its
`delta_ready` may be false, so build and review the required
`kgdistiller-agent-delta-v1` independently from native source evidence; never apply a
proposal operation or empty `delta_preview` as a write delta.

Prepare the source patch, complete post-patch marker/ref state, and one
`kgdistiller-agent-delta-v1`. Let ingest create or update
`knowledge/entries/<node-id>.md`; do not hand-edit those files as a substitute
for a reviewed transaction. Do not open or edit graph JSONL, derived entry
shards, or alignment files directly. Hand the reviewed artifacts and query digests to
`$ingest-kgdistiller`; accept completion only from a committed canonical
receipt whose after-digests match a fresh status call.

Run the scoped deterministic gates:

```sh
kgdistiller --repo-root PROJECT curate-check --file RELATIVE_AUTHORITY
kgdistiller --repo-root PROJECT check
```

If the caller needs host-consumable data, create a separate static export only
after these gates pass:

```sh
kgdistiller --repo-root PROJECT export site --output EXPORT_DIR \
  --product-commit FULL_PRODUCT_COMMIT \
  --source-repository SOURCE_REPOSITORY
```

If `EXPORT_DIR` is an already verified adopted bundle, add `--replace` to
build and verify its successor before an atomic, rollback-safe directory swap.
Never delete the prior bundle as a preparation step.

The export receipt is derived data. It does not authorize Git operations or
replace the authored notes.

## Deliver

Return the registered source scope, query digests, reviewed identities, source
and delta paths, committed ingest receipt, validation results, and optional
static-export receipt. Report deferred ambiguity and separately reviewed
content conflicts explicitly.
Identify unsupported relation/application records and any unapplied scope.
Refresh source-scoped link sheets only from verified committed metadata; never
fabricate accepted entries or links for deferred proposals.
Do not claim publication, remote synchronization, or retrieval readiness unless
the corresponding verified receipt proves it.
