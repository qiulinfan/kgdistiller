---
name: curate-kgdistiller-notes
description: Extract and review source-grounded knowledge from registered Markdown, Typst, or LaTeX notes, resolve existing identities through query-kgdistiller, preserve native authority and reference markers, and hand one bounded update to ingest-kgdistiller. Use for raw-note ingestion, changed-note curation, missing entries or direct relations, marker cleanup, and note-to-static-export workflows in any kgdistiller knowledge project.
---

# Curate kgdistiller notes

Turn authored notes into a reviewed update. Treat source prose as authority,
kgdistiller as the deterministic transaction boundary, and generated graphs as
opaque derived data.

## Establish the bounded source scope

Start with:

```sh
kgdistiller --repo-root PROJECT agent status
kgdistiller --repo-root PROJECT scan --file RELATIVE_AUTHORITY
```

Require each input to match exactly one pattern in `knowledge/sources.json`.
Use the smallest coherent registered file set, including both paths of a known
rename. If a document is unregistered, propose the source ID, root, format glob,
field classification, and destination; obtain review before moving it or
expanding a glob.

Read [references/curation-contract.md](references/curation-contract.md) before
extracting. Never infer graph identity from headings, order, syntax wrappers,
keywords, embeddings, or co-occurrence.

## Extract before explaining

Read each selected authority completely. Preserve existing native markers:

- Markdown: `--[[Concept]]--` and `[[Concept]]`;
- Typst: `#kn[Concept]` and `#ref[Concept]`;
- LaTeX: `\kn{Concept}` and `\knref{Concept}`.

Build one bounded candidate batch containing names, aliases, source locations,
short evidence, and direct source-supported relations. Do not write entries or
choose identity from similarity yet.

Pass the whole batch to `$query-kgdistiller`. Require one `known`, `partial`,
`new`, `conflict`, or `uncertain` decision per candidate and retain the target
graph, snapshot, and alignment digests. Stop on conflicts and unresolved
identities.

## Prepare one reviewed update

Use established identities as refs. Add an authority marker only for a reviewed
new identity. Author only the missing portion for a partial identity. Write a
compact source-grounded entry for every active authority in the selected scope
and add only direct semantic edges with concrete evidence.

Prepare the source patch, complete post-patch marker/ref state, and one
`qlkg-agent-delta-v2`. Do not open or edit graph JSONL, entry shards, SQLite, or
alignment files directly. Hand the reviewed artifacts and query digests to
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
  --product-commit FULL_PRODUCT_COMMIT
```

If `EXPORT_DIR` is an already verified adopted bundle, add `--replace` to
build and verify its successor before an atomic, rollback-safe directory swap.
Never delete the prior bundle as a preparation step.

The export receipt is derived data. It does not authorize Git operations or
replace the authored notes.

## Deliver

Return the registered source scope, query digests, reviewed identities, source
and delta paths, committed ingest receipt, validation results, and optional
static-export receipt. Report deferred conflicts and uncertainties explicitly.
Do not claim publication, remote synchronization, or retrieval readiness unless
the corresponding verified receipt proves it.
