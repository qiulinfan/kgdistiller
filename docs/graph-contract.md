# kgdistiller knowledge contract

## What is stored

A knowledge project keeps its accepted knowledge in two places under
`.knowledge/`:

- `entries/<id>.md`: one reviewed entry per knowledge node;
- `edges.jsonl`: the accepted semantic edges between entries.

Nothing else is knowledge. Source documents are registered in
`.knowledge/sources.json` and only read. `.knowledge/build/` holds rebuildable
local work: ingest journals, plans and receipts, review drafts, retrieval
caches and the Obsidian graph feed. In-memory query views and the feed never
become another authority.

## Sources are format-agnostic

A knowledge source is any registered UTF-8 text document. kgdistiller reads it
as lines numbered from 1 and never parses its syntax: `.md`, `.typ`, `.tex`,
`.txt` and every other format are handled identically. No marker, heading,
environment or link inside a source defines a node, and no source is converted
into another format. An entry cites its source by project-relative path and
line range and quotes the cited lines verbatim.

Each entry's source must be admitted by exactly one registered source (its
`root` plus `files` globs). Source roots lie inside the project and outside
`.knowledge/`.

## Node selection

The [shared authoring model](concepts-and-relations.md) applies to papers,
mathematical notes, CS notes, blogs and other registered knowledge sources.
Nodes denote independently meaningful definitions, axioms, precise theorems and
lemmas, algorithms, architectures or other reusable knowledge objects.
Propositions and remarks express relations; examples and experiments express
uses/applications. Preserve complete assumptions, assertions and evidence.

Nodes come only from reviewed capture or curation through transactional
ingest, guided by the source's user-registered document type. Sections, proofs,
exercises, equations and figures do not become nodes merely because they exist,
and identity is never inferred from document order, headings or co-occurrence.

A local entry requires the source to explain its meaning, not to be its first
historical origin. Same-name source-scoped terms remain retrieval candidates
until their definitions and conditions have been compared. Unexplained external
terms remain pending dependencies. Paper extraction uses around twenty concepts
as a diagnostic, not a general node limit.

Rich n-ary relations, applications and complete gap-state records are authoring
targets, not additional `kgdistiller-agent-delta-v1` capabilities. Unsupported
updates remain review proposals rather than invented knowledge.

## Entries

An entry is Obsidian-compatible Markdown (`kgdistiller-entry-v1`). Its
frontmatter is the restricted YAML subset that Obsidian shows as properties,
with keys in this order:

````markdown
---
schema: kgdistiller-entry-v1
id: measure-space
label: Measure space
kind: definition
aliases:
  - 测度空间
source: notes/measure.tex
line_start: 42
line_end: 47
understanding: unknown
---

# Measure space

## Summary

A measurable space (X, F) equipped with a measure mu on F.

## Pending prerequisites

- sigma-algebra: the domain of a measure; not yet studied.

## Evidence

```
\begin{definition}[Measure space]
...the verbatim text of lines 42-47...
\end{definition}
```
````

| Key | Rule |
|---|---|
| `schema` | `kgdistiller-entry-v1`. |
| `id` | Readable ASCII slug `[a-z0-9]+(-[a-z0-9]+)*`, at most 200 characters, never a Windows-reserved name. The file name is `<id>.md`. |
| `label` | Single-line canonical name; the body's H1 must equal it. |
| `kind` | Nonempty single line; one of the source document type's `node_kinds` when the admitting source declares a `document_type`. |
| `aliases` | Block list or `[]`; unique, and never the entry's own label. |
| `source` | Project-relative POSIX path without `..`, outside `.knowledge/`. |
| `line_start`, `line_end` | Integers with `1 <= line_start <= line_end <=` the source's line count. |
| `understanding` | `unknown`, `not-yet-understood` or `understood`. |

The writer emits plain scalars when safe and JSON-compatible double-quoted
scalars otherwise. The reader also accepts single-quoted scalars and the forms
Obsidian's property editor writes. Unknown keys, flow mappings, multi-line
scalars, anchors and comments are rejected with a clear error.

The body holds, in this order, `## Summary` (required), `## Context`, `## Role`,
`## Prerequisites`, `## Pending prerequisites`, `## Common confusions`,
`## Open questions` and `## Evidence` (required). The list sections use `- `
items. Evidence is the verbatim text of the cited lines inside one backtick
fence longer than any backtick run in the quote, with no info string. Unknown
sections and text outside sections are rejected.

### Identifiers and names

The default id is the slug of the label: NFKD to ASCII, lowercase, runs of other
characters become `-`. When the slug is empty (for example a pure-CJK label) or
already taken, the author supplies an explicit id. There is no hash-derived or
numeric fallback.

Labels and aliases are unique across the whole store, compared after NFKC
normalization, casefolding and whitespace collapsing. A rename is an update
whose new label differs; capture keeps the old label as an alias.

### Learning state

`understanding` records the user's stated mastery and is preserved by updates
that do not change it. `pending_prerequisites` holds only the entry's direct
gaps as concise source-grounded text. Neither retrieval, a current entry nor
complete distillation promotes understanding.

## Relations

`edges.jsonl` holds one edge per line with exactly these nonempty string fields:
`source`, `relation`, `target`, `origin`, `confidence` and `evidence`. Lines are
sorted by `(source, relation, target)` with sorted keys and the file is replaced
atomically. Supported relations are:

- `prerequisite-for`: a direct learning dependency;
- `implies`: direct logical entailment;
- `generalizes`: the target is recovered as a special case;
- `contrasts-with`: an explicit symmetric comparison;
- `derived-from`: the source is directly constructed or proved from the target.

Agents store direct, source-grounded claims rather than transitive closure,
document order, co-occurrence, or generic association. `confidence: high` is a
declaration that the source states the relation explicitly; it is not an
independent audit. `prerequisite-for` is acyclic.

## Writing

All writes go through [transactional ingest](transactional-ingest.md): a
reviewed `kgdistiller-agent-delta-v1` creates, updates or removes entries and
adds or removes edges. `capture prepare` and `harvest apply` build such
requests. Apply holds a writer lock, re-validates the delta against the current
store and the current source text, and installs the changed entries and
`edges.jsonl` atomically. Source documents are never edited.

## Consistency without hashes

`kgdistiller check` validates the whole store and compares every entry with its
source by text:

- errors: invalid entry files, duplicate ids, label or alias collisions, a
  missing, unregistered, ambiguously registered or non-UTF-8 source, a line
  range out of bounds, a kind the document type does not allow, an unknown
  relation, an edge endpoint without an entry, and a `prerequisite-for` cycle;
- an entry is **current** when the whitespace-normalized text of its cited lines
  equals its whitespace-normalized Evidence (normalization joins the
  whitespace-separated tokens with single spaces);
- otherwise the normalized Evidence is searched across the whole source as
  whole lines: a candidate range starts at the first token of a nonblank line
  and ends at the last token of a nonblank line, so text added before or after
  the cited words on the same lines makes the entry stale. Exactly one
  occurrence makes the entry **moved** (reported with the new range), none
  makes it **stale**, and several make it **ambiguous**.

`check` parses each entry file and edge line independently, so one malformed
file is reported as an `invalid-entry` or `invalid-edge` error without hiding
the others. It prints every error and staleness and exits 1 when there are any;
otherwise it prints `OK: <n> entries, <m> edges`. `check --fix-lines`, under
the writer lock, rewrites only `line_start`/`line_end` of moved entries and
checks again. Stale and ambiguous entries need a reviewed re-capture.

Staleness is reported, never enforced: retrieval, graph traversal, MCP and the
Obsidian feed always include every entry and every accepted edge.

## Read-only views

CLI and MCP load the entries and `edges.jsonl` into one in-memory `GraphView`
per call: node records are the entry records plus `entry` (the entry file
path), with incoming and outgoing edges and the label and alias indexes. There
is no secondary database. Readers fail closed: until `check` is clean of
`invalid-entry`, `invalid-edge` and `dangling-edge` errors, `agent` commands,
MCP and `export obsidian` stop with the first such error. While an ingest
journal exists (an install in progress or interrupted), loading fails with a
clear error instead of reading a partial install.

Identity resolution uses ids, canonical labels and aliases under NFKC/casefold
normalization. Lexical score, embedding similarity and graph proximity retrieve
or rank candidates but never create identity or edges. See
[retrieval](retrieval.md).

## Obsidian graph feed

`kgdistiller export obsidian` writes one `kgdistiller-obsidian-graph-v1` file,
`.knowledge/build/obsidian/semantic-graph.json` by default:

```json
{
  "schema": "kgdistiller-obsidian-graph-v1",
  "counts": {"concepts": 1, "sources": 1, "semantic_edges": 0, "definitions": 1},
  "concepts": [{"id": "measure-space", "label": "Measure space", "kind": "definition",
                "aliases": ["测度空间"], "authority": ".knowledge/entries/measure-space.md",
                "understanding": "unknown"}],
  "sources": [{"authority": "notes/measure.tex"}],
  "semantic_edges": [],
  "definitions": [{"source_authority": "notes/measure.tex", "target": "measure-space",
                   "line_start": 42, "line_end": 47}]
}
```

Every entry is a concept whose `authority` is its entry file; every cited source
is a source; every accepted edge is a semantic edge; every entry's citation is
a definition. Paths are any safe relative paths. This is the only supported
input to the Obsidian plugin. The feed is derived: never register it in
`.knowledge/sources.json`, scan it, or ingest it back.

## Required invariants

- one entry file per id, named `<id>.md`, with a valid frontmatter and body;
- unique labels and aliases across the store;
- every entry cites an existing, singly registered UTF-8 source within bounds;
- every kind is allowed by its source's document type;
- no dangling edge endpoints and no cycles in `prerequisite-for`;
- identity comes only from reviewed entries, never from source syntax, order,
  headings or co-occurrence;
- consistency with sources is decided by comparing text; no content hash is
  stored or compared anywhere;
- staleness never hides knowledge from retrieval or the feed.
