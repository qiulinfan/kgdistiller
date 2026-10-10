---
name: curate-kgdistiller-notes
description: Extract and review source-grounded knowledge from registered text documents of any format, guided by their user-registered document type, resolve existing identities through query-kgdistiller, and hand one bounded update of entries and direct relations to ingest-kgdistiller. Use for raw-note ingestion, changed-note curation, and missing entries or direct relations in any kgdistiller knowledge project, including when the user asks to file or ingest a document into their kgdistiller (kgd/kgdt) knowledge base.
---

# Curate kgdistiller notes

Turn authored notes into a reviewed update to the caller's canonical
`.knowledge/`. Accepted knowledge is two things: one Markdown entry per node in
`.knowledge/entries/<id>.md`, which cites its source path and line range and
quotes those lines verbatim as Evidence, and the accepted direct relations in
`.knowledge/edges.jsonl`. kgdistiller is the deterministic transaction boundary.
Source documents are only read; nothing in them defines a node by syntax.
Full proposals belong in `.knowledge/build/reviews/` until accepted; definition
and pending sheets are lightweight links to committed entries. `build/` is
excluded from Obsidian hidden-folder indexing by default; remove `build` from
the kgdistiller plugin's exclusion list to open drafts there.

Match user-facing explanations, prompts, and handoffs to the user's language
unless the user requests another language. Keep commands, identifiers,
structured keys and action codes, and raw errors unchanged.

For a request to save one selected item while reading, use
`$capture-kgdistiller`; it shares the same entries and transaction boundary while
leaving other knowledge in the source untouched. The whole-file extraction below
applies only to a requested source-level curation scope. A sheet may remain
partial.

## Establish the bounded source scope

Start with:

```sh
kgdistiller --repo-root PROJECT agent status
kgdistiller --repo-root PROJECT check
kgdistiller --repo-root PROJECT scan --file RELATIVE_SOURCE
```

`agent status` reports the entry and edge counts. `check` must pass before a
curation transaction, apart from entries it reports as moved (run
`check --fix-lines` for those after confirming the source edit) or stale (report
them; re-capturing them is a separate reviewed update).

Each input must be admitted by exactly one source in `.knowledge/sources.json`.
Use the smallest coherent registered file set. If a document is unregistered,
propose the source ID, root, file glob, optional registered `document_type`, and
destination; obtain review before moving it or expanding a glob. `scan --file`
returns the file's `document_type`, its `profile` (`node_kinds`,
`extraction_guidance`) and the numbered lines. Follow the user's profile;
document type is separate from file format and knowledge domain. Do not
hardcode document classes or infer extraction policy from an extension. An
absent profile makes no classification claim; use the explicitly requested
scope without silently adding a profile.

Read [references/curation-contract.md](references/curation-contract.md) before
extracting. Never infer identity from headings, order, syntax wrappers,
keywords, embeddings, or co-occurrence.

## Extract before explaining

Read each selected source completely as plain text. Whatever its format,
kgdistiller treats it identically: a path plus numbered lines. Build one
bounded candidate batch containing labels, aliases, the line range that states
each candidate, a kind from the profile, and supported direct relations with
their evidence. Preserve complete relation/application proposals alongside this
batch in `.knowledge/build/reviews/`, using the distinction in the curation
contract. Do not write entries or choose identity from similarity yet.

Resolve the whole batch with `$query-kgdistiller`:

```sh
kgdistiller --repo-root PROJECT agent resolve "LABEL" "ALIAS" ...
kgdistiller --repo-root PROJECT agent search "LABEL OR DEFINING PHRASE"
kgdistiller --repo-root PROJECT agent get ENTRY_ID
```

Decide one disposition per candidate: `add` (no existing entry has this
meaning), `update` (an existing entry is the same knowledge; name its id) or
defer (ambiguous). An exact name or alias match in `agent resolve` is an
existing identity; a lexical or embedding match is only a candidate to read.
Stop on ambiguity rather than guessing.

## Prepare one reviewed update

For a single entry, `$capture-kgdistiller`'s `capture prepare` builds the
requests. When the user wants to select items by checkbox, `harvest prepare`
puts the reviewed captures on a sheet. For several entries, edges or removals,
write one `kgdistiller-ingest-request-v1` whose
`kgdistiller-agent-delta-v1` lists `create_entries`, `update_entries` (with
`expected_label`), `remove_entries`, `add_edges` and `remove_edges`; the
transaction contract bundled with `$ingest-kgdistiller` gives the exact shape,
and [the curation contract](references/curation-contract.md) lists every entry
field. Every entry's `evidence` must equal its cited lines
exactly: copy them from `scan --file`, never paraphrase them.

Write a compact source-grounded summary for every entry and add only direct
semantic edges with concrete evidence. Retain complete role bindings, arity,
states, conditions and evidence while reviewing assertions. Current direct-edge
deltas cannot represent full n-ary or application records: retain unsupported
proposals in `.knowledge/build/reviews/`, report the adapter gap and defer their
persistence. Never flatten or omit them to make a smaller delta appear complete.

Let ingest create or update `.knowledge/entries/<id>.md`; do not hand-edit
those files as a substitute for a reviewed transaction, and never hand-edit
`.knowledge/edges.jsonl`. Hand the reviewed request to `$ingest-kgdistiller`;
accept completion only from a committed receipt followed by a passing `check`.

```sh
kgdistiller --repo-root PROJECT check
kgdistiller --repo-root PROJECT agent status
```

Publishing the notes belongs to the repository that owns them; kgdistiller has
no publishing surface.

## Deliver

Return the registered source scope, the identity dispositions with their
evidence, the request path, the committed ingest receipt, and the `check`
result. Report deferred ambiguity and separately reviewed content conflicts
explicitly. Identify unsupported relation/application records and any
unapplied scope. Refresh source-scoped link sheets only from verified committed
entries; never fabricate accepted entries or links for deferred proposals.
