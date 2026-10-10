---
name: query-kgdistiller
description: Query a kgdistiller external-brain knowledge base read-only. Use when an Agent must search records across every registered base, resolve names to their senses, mentions and pending uses, read complete records with their links and cited source lines, follow dependency and claim links, browse bases, source trees and kinds, pack budgeted evidence with its gaps, or classify a candidate's identity before authoring, including when the user asks to search or recall concepts from their kgdistiller (kgd/kgdt) knowledge base.
---

# Query kgdistiller

Treat kgdistiller as a read-only external brain. Return a bounded,
evidence-backed answer; never load the whole knowledge base into context.

Match the owner's language. Keep commands, uids, keys and raw errors
unchanged.

## What is searched

Knowledge is records: nodes (concepts and precisely stated results) and
relations (statements binding records to named roles; applications and
examples are relations too). Every record has a uid `<base>:<id>`, a `source`
path, a `lines` range and verbatim Evidence quotes. Pending terms are plain
values a record uses without a link. Every read goes through the derived
database `$KGDISTILLER_HOME/index.sqlite`, covers every registered base unless
filtered, and reports `lag`.

## Boundary

- Never edit a source, a record, a draft, a sheet or the home's `config.json`
  and types.
- Never run `kgd accept`, `kgd harvest`, `kgd check --fix-lines` or
  `kgd base add|rm`, and never write drafts.
- The one write allowed is the derived refresh `kgd index` when results report
  lag (below).
- Never promote lexical, dense (embedding), name, translation, acronym or link
  similarity into identity.

## Tools

Recommended order: `search` for candidates, then `browse` or `resolve` to see
the source tree and every sense of a name, `neighbors` to follow dependency
and claim links, `get` to read complete records, and `pack` to deliver
budgeted evidence with its gaps.

Use the MCP tools of `kgd mcp` when they are available: `kg_search`,
`kg_resolve`, `kg_get`, `kg_neighbors`, `kg_browse` and `kg_pack`. The server
is read-only, serves the whole home and opens a fresh connection per call.
Prefer it for repeated queries: it keeps the embedding model resident after
the first search, while every cold CLI `kgd search` loads the model again
(several seconds with `BAAI/bge-m3` on Apple silicon). Otherwise use the CLI;
both print the same JSON.

```sh
kgd search "QUESTION OR TERMS" [--limit 40] [--no-dense] [filters]
kgd resolve "TERM" ... [filters]
kgd get UID ... [--source-lines N]
kgd neighbors UID ... [--role R]... [--dir out|in|both] [--depth N] [filters]
kgd browse [HANDLE] [filters]
kgd pack UID ... [--budget BYTES] [--requires-depth N] [filters]
```

Every command but `get` takes the filters, repeatable with OR within one
filter and AND across filters: `--base B`, `--kind K`,
`--class node|relation`, `--source PREFIX` (a base-relative path prefix, such
as one paper's folder) and
`--understanding unknown|not-yet-understood|understood`.

**search** ranks records by three lanes fused with reciprocal rank fusion:

- lexical: FTS5 over each record's text (label, aliases, kind, body, search
  terms, participant and prerequisite labels, evidence and source path), with
  CJK text indexed as characters and adjacent pairs, so `测度` finds `测度论`;
- dense: semantic and cross-language matches against each record's stored
  vector; the query is encoded with the model that built the vectors. It runs
  when `meta.embedding` is set (an embedding model built the index);
- name: exact and contained label and alias keys of the query.

`lanes` lists the lanes that ran. `--no-dense` (MCP: `no_dense`) skips the
dense lane and loads no model. If search fails because the retrieval extra is
missing or the model cannot be loaded (the error names `--no-dense`), rerun it
with `--no-dense` and report the reduced `lanes` and the error.

Each result carries `uid`, `class`, `kind`, `label`, `base`, `source`,
`lines`, `understanding`, `epistemic`, `gloss`, `ranks` for each lane that ran
(`null` when that lane missed the record), `requires`, `participants` (for
relations, by role), `in` (records that cite or require it) and `truncated`
counts for those lists. Put source-language forms in the query when the owner
reads in another language; the dense lane helps across languages but does not
replace them.

**resolve** returns, for each term, `senses` (label or alias equal to the term
after normalization), `mentions` (containing it as a phrase, or as a substring
for CJK) and `pending` (records using it as an unlinked term). It is unranked
and unlimited.

**get** returns complete records: fixed fields, `body`, `search_terms`,
`evidence`, `out` links by role and position (with labels, or the pending
term, and whether the target exists), `in` links, and `missing` uids. A bare id
works when exactly one base has it. `--source-lines N` adds `source_text`, the
cited range ±N lines read live from the source with line numbers.

**neighbors** follows stored links from the given records and infers nothing.
It returns `edges` (`{from, role, to|term, depth}`, always in the link's own
direction), `records` (every reached uid with label, kind, class and whether
it exists) and `missing` (unknown starts). `--role` restricts every hop, not
only the first, and `--depth` (default 1) bounds the walk, so cycles end there
and each record keeps its smallest depth. Filters prune the walk at every hop.
Three walks cover most needs:

- dependency closure: `--role requires --dir out --depth N`;
- claim closure: `--dir in` lists the relations citing a node; `--dir both
  --depth 2` adds their co-participants, including relations that take part
  in other relations (witnesses, debate positions);
- applications: `--dir in --kind example`.

**browse** lists what is indexed. No handle lists every base with its record,
relation and pending counts; `base` and `base:dir/` list directories and
sources with counts; `base:path/file` lists that source's records grouped by
kind, with its pending terms; `--kind K` at any scope lists every record of
that kind with all its links, uncapped. Stay bounded: start at a base or one
source, narrow with the handle and filters, and never list a whole large base
or kind into context.

**pack** returns whole records, never truncated, for the given uids, their
`requires` closure (`--requires-depth`, default 1) and the relations shared by
at least two packed records, while the compact JSON of `records` stays within
`--budget` bytes (default 60000). `gaps` lists what the packet lacks:
`unknown-uid`, `over-budget`, `pending` terms, `missing-target` uids and
`not-packed` links. Report gaps as gaps; never fill one by guessing.

## Lag

Every result has `lag`: `changed_files`, `unavailable_bases`, `unembedded`,
`embedding_changed`. When `lag.changed_files` is above 0, record files changed
since the last index: run `kgd index` (the one allowed write) and repeat the
query. `lag.unembedded` above 0 means some records have no vector yet, so the
dense lane misses them; `lag.embedding_changed` means the configured model
differs from the one that built the vectors, and the dense lane keeps using
the previous model until re-embedding. For either, also run `kgd index` and
repeat the query. A model change re-embeds every record, which takes minutes
(about two per 500 records with `BAAI/bge-m3` on Apple silicon), so report the
wall time. Report `unavailable_bases` as missing coverage.

## Deliver evidence

Answer from returned records, citing each as `source:lines` with its uid and
the relevant evidence quote; use `get --source-lines N` when the answer needs
the surrounding source text. A record whose evidence `kgd check` reports as
stale is still returned; say so when the answer depends on the cited passage.

## Prerequisite lookup while reading

For a prerequisite used at a specific source step, resolve the precise names,
then inspect bounded content for applicability: the required statement, domain
and conditions. Return the use site, the record found and whether it supplies
the needed step. Distinguish, for example, an Lp from an ℓp setting, or a weak
from a strong law of large numbers, when the source needs that distinction.
To see the recorded chain behind a record, use
`neighbors UID --role requires --dir out` or `pack UID --requires-depth N`;
still surface only the direct pending terms and judge applicability from the
content, not from the links.

Available knowledge is not personal mastery. Report each record's
`understanding` separately; skip an explanation only when the owner has stated
`understood`. Surface direct pending terms without expanding their ancestry. A
failed query is not a missing concept, and a missing record is not proof the
owner lacks the subject.

## Identity classification for authoring

When a capture or compile asks whether candidates already exist, classify
each:

- `matched`: `resolve` lists a sense whose content has the same meaning and
  conditions; return its uid;
- `ambiguous`: several plausible senses or records remain; return them with
  non-authoritative reasons;
- `unmatched`: no record has this meaning; the author may write a new draft.

Names, translations and aliases are retrieval evidence only. Read the bounded
definitions before calling a hit `matched`. Homonyms stay separate records:
same-named mechanisms of two papers, or of two bases, are distinct unless an
explicit relation says they are equivalent.

## Handoff

Return one entry per question or candidate: the query, uids found with
`source:lines` and quotes, the classification when asked, the owner's
understanding, direct pending terms, the gaps of any packet used, the lag
state and any `kgd index` run. Report omitted context and ambiguity. Make no
other changes.
