---
name: query-kgdistiller
description: Query a kgdistiller external-brain knowledge base read-only. Use when an Agent must search records across every registered base, resolve names to their senses and mentions, read complete records with their links and cited source lines, follow dependency links and the statements and relations linking a record, browse bases, source trees and kinds, pack budgeted evidence with its gaps, or classify a candidate's identity before authoring, including when the user asks to search or recall concepts from their kgdistiller (kgd/kgdt) knowledge base.
---

# Query kgdistiller

Treat kgdistiller as a read-only external brain. Return a bounded,
evidence-backed answer; never load the whole knowledge base into context.

Match the owner's language. Keep commands, uids, keys and raw errors
unchanged.

## What is searched

Knowledge is records: nodes, the concepts and the results a source states
(theorems, findings, examples), whose roles link what they are about, and
relations, connections between records bound to named roles (an implication
between results, a comparison). Every record has a uid `<base>:<id>`, a `source`
and verbatim Evidence quotes: a local record's `source` is a base-relative path
with a `lines` range, and a foreign record's is the web address of the outside
work it quotes, with no lines (`foreign: true`). Every value is a link to
another record. Every read goes through the derived
database `$KGDISTILLER_HOME/index.sqlite`, covers every registered base unless
filtered, and reports `lag`.

## Boundary

- Never edit a source, a record, a draft or the home's `config.json` and
  types.
- Never run `kgd accept`, `kgd check --fix-lines` or
  `kgd base add|rm`, and never write drafts.
- The one write allowed is the derived keyword refresh `kgd index --no-embed`
  when results report changed files (below). Never re-embed: that is the
  owner's call.
- Never promote lexical, dense (embedding), name, translation, acronym or link
  similarity into identity.

## Tools

Recommended order: `search` for candidates, then `browse` or `resolve` to see
the source tree and every sense of a name, `neighbors` to follow dependency
links and the records linking one, `get` to read complete records, and `pack`
to deliver budgeted evidence with its gaps. `drafts` lists the drafts awaiting
the owner, which are not knowledge, `find` where a passage or a name
occurs in the sources, and `markers` where the sources name records with
knowledge markers (`kgd markers SOURCE` or `kgd markers --uid UID`, CLI only).

Use the MCP tools of `kgd mcp` when they are available: `kg_search`,
`kg_resolve`, `kg_get`, `kg_neighbors`, `kg_browse`, `kg_pack`, `kg_drafts`
and `kg_find`. The server
is read-only, serves the whole home and opens a fresh connection per call.
Otherwise use the CLI; both print the same JSON and share the user's one
embedding process, so only the first dense search after 15 idle minutes waits
for the model.

```sh
kgd search "QUESTION OR TERMS" [--limit 40] [--no-dense] [filters]
kgd resolve "TERM" ... [filters]
kgd get UID ... [--source-lines N]
kgd neighbors UID ... [--role R]... [--dir out|in|both] [--depth N] [filters]
kgd browse [HANDLE] [filters]
kgd pack UID ... [--budget BYTES] [--requires-depth N] [filters]
kgd drafts [--base B]... [--source PREFIX]... [--name NAME]...
kgd find "TEXT" [--base B]... [--source PREFIX]...
```

Every command but `get`, `drafts` and `find` takes the filters, repeatable with OR within one
filter and AND across filters: `--base B`, `--kind K`,
`--class node|relation`, `--source PREFIX` (a base-relative path prefix, such
as one paper's folder) and
`--understanding yes|no` (what the owner marked as not understood is `no`).

**search** ranks records by three lanes fused with reciprocal rank fusion:

- lexical: FTS5 over each record's text (label, aliases, kind, body, search
  terms, participant and prerequisite labels, and evidence), with
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
`lines`, `foreign`, `understanding`, `epistemic`, `gloss`, `degree` (how many
distinct other existing records it links or is linked by: how central it is),
`ranks` for each lane that ran (`null` when that lane missed the record),
`requires`, `participants` (every role's values, for every record with role
values: a relation's participants or what a statement node is about), `in`
(records that link or require it) and `truncated` counts for those lists. Put source-language forms in the query when the owner
reads in another language; the dense lane helps across languages but does not
replace them.

**resolve** returns, for each term, `senses` (label or alias equal to the term
after normalization), and `mentions` (containing it as a phrase, or as a
substring for CJK). It is unranked and unlimited.

**get** returns complete records: fixed fields, `body`, `search_terms`,
`evidence`, `out` links by role and position (with labels and whether the
target exists), `in` links, and `missing` uids. A bare id
works when exactly one base has it. `--source-lines N` adds `source_text`, the
cited range ±N lines read live from the source with line numbers (`null` for
a foreign record, whose source is not read).

**neighbors** follows stored links from the given records and infers nothing.
It returns `edges` (`{from, role, to, depth}`, always in the link's own
direction), `records` (every reached uid with label, kind, class and whether
it exists) and `missing` (unknown starts). `--role` restricts every hop, not
only the first, and `--depth` (default 1) bounds the walk, so cycles end there
and each record keeps its smallest depth. Filters prune the walk at every hop.
Three walks cover most needs:

- dependency closure: `--role requires --dir out --depth N`;
- what links a record: `--dir in` lists the statement nodes and relations
  that link it; `--dir both --depth 2` adds what else they link, including
  relations that take part in other relations (witnesses, debate positions);
- examples: `--dir in --kind example`.

**browse** lists what is indexed. No handle lists every base with its record and
relation counts; `base` and `base:dir/` list directories and sources with
counts, and at the base root the web addresses of foreign records;
`base:path/file` or `base:<web address>` lists that source's records grouped
by kind; `--kind K` at any scope lists every record of
that kind with all its links, uncapped. Stay bounded: start at a base or one
source, narrow with the handle and filters, and never list a whole large base
or kind into context.

**pack** returns whole records, never truncated, for the given uids, their
`requires` closure (`--requires-depth`, default 1) and every record,
relation or statement node alike, with role links to at least two packed
records, while the compact JSON of `records` stays within
`--budget` bytes (default 60000). `gaps` lists what the packet lacks:
`unknown-uid`, `over-budget`, `missing-target` uids and
`not-packed` links. Report gaps as gaps; never fill one by guessing.

## Lag

Every result has `lag`: `changed_files`, `unavailable_bases`, `unembedded`,
`embedding_changed`. When `lag.changed_files` is above 0, record files changed
since the last index: run `kgd index --no-embed` (the one allowed write) and
repeat the query. `lag.unembedded` above 0 means some records have no current
vector, because their text changed since they were embedded, so the dense lane
misses or misranks them; `lag.embedding_changed` means the configured model
differs from the one that built the vectors, and the dense lane keeps using
the previous model. Report either as weaker dense coverage and leave
re-embedding (`kgd index`) to the owner. Report `unavailable_bases` as missing
coverage.

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
judge applicability from the content, not from the links.

Available knowledge is not personal mastery. Report each record's
`understanding` separately: `no` is what the owner marked as not understood,
so explain those first. A failed query is not a missing concept, and a missing record is not proof the
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
definitions before calling a hit `matched`. Each concept has one record, owned
by the source that defines or originates it, and a label names one record: a
concept of another meaning with the same name carries its source's name,
`Label (source)`. Aliases are shared freely. A source
that only uses a concept links that record; a concept nothing records may be
captured as foreign knowledge from the work that originates it.

## Handoff

Return one entry per question or candidate: the query, uids found with
`source:lines` and quotes, the classification when asked, the owner's
understanding, the gaps of any packet used, the lag
state and any `kgd index` run. Report omitted context and ambiguity. Make no
other changes.
