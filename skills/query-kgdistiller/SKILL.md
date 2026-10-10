---
name: query-kgdistiller
description: Query a kgdistiller external-brain knowledge base read-only. Use when an Agent must search records across every registered base, resolve names to their senses, mentions and pending uses, read complete records with their links and cited source lines, or classify a candidate's identity before authoring, including when the user asks to search or recall concepts from their kgdistiller (kgd/kgdt) knowledge base.
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
- Never promote lexical, name, translation, acronym or link similarity into
  identity.

## Tools

Use the MCP tools of `kgd mcp` when they are available: `kg_search`,
`kg_resolve` and `kg_get`. The server is read-only, serves the whole home and
opens a fresh connection per call. Otherwise use the CLI; both print the same
JSON.

```sh
kgd search "QUESTION OR TERMS" [--limit 40] [filters]
kgd resolve "TERM" ... [filters]
kgd get UID ... [--source-lines N]
```

Filters, repeatable with OR within one filter and AND across filters:
`--base B`, `--kind K`, `--class node|relation`, `--source PREFIX` (a
base-relative path prefix, such as one paper's folder) and
`--understanding unknown|not-yet-understood|understood`.

**search** ranks records by two lanes fused with reciprocal rank fusion:

- lexical: FTS5 over each record's text (label, aliases, kind, body, search
  terms, participant and prerequisite labels, evidence and source path), with
  CJK text indexed as characters and adjacent pairs, so `测度` finds `测度论`;
- name: exact and contained label and alias keys of the query.

Each result carries `uid`, `class`, `kind`, `label`, `base`, `source`,
`lines`, `understanding`, `epistemic`, `gloss`, `ranks` per lane, `requires`,
`participants` (for relations, by role), `in` (records that cite or require
it) and `truncated` counts for those lists. Put source-language forms in the
query when the owner reads in another language.

**resolve** returns, for each term, `senses` (label or alias equal to the term
after normalization), `mentions` (containing it as a phrase, or as a substring
for CJK) and `pending` (records using it as an unlinked term). It is unranked
and unlimited.

**get** returns complete records: fixed fields, `body`, `search_terms`,
`evidence`, `out` links by role and position (with labels, or the pending
term, and whether the target exists), `in` links, and `missing` uids. A bare id
works when exactly one base has it. `--source-lines N` adds `source_text`, the
cited range ±N lines read live from the source with line numbers.

## Lag

Every result has `lag`: `changed_files`, `unavailable_bases`, `unembedded`,
`embedding_changed`. When `lag.changed_files` is above 0, record files changed
since the last index: run `kgd index` and repeat the query. Report
`unavailable_bases` as missing coverage.

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
understanding, direct pending terms, the lag state and any `kgd index` run.
Report omitted context and ambiguity. Make no other changes.
