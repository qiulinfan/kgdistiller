# Record format

Every piece of knowledge is one record file. Nodes and relations share one
format and one folder; they differ only in whether role keys are present.

- Accepted records: `<base root>/.knowledge/entries/<id>.md`.
- Drafts (proposed new records): `<base root>/.knowledge/drafts/<id>.md`, in
  exactly the same format.
- The global address of a record is its uid `<base>:<id>`.

kgd never re-serializes frontmatter. Write whole files yourself; the only
product-side frontmatter edit is the one-line `lines:` rewrite of
`kgd check --fix-lines`.

## File layout

The file starts with a line `---`; the frontmatter ends at the next line that
is exactly `---`. The frontmatter is read with PyYAML `BaseLoader`, so every
scalar is a string (`on`, `yes` and `40-46` stay strings). Values must be
strings or lists of strings.

```markdown
---
label: Measure space
kind: definition
aliases:
  - 测度空间
source: notes/math/measure-theory/chapters/01-sigma-algebra.tex
lines: 206-224
understanding: understood
requires:
  - "[[sigma-algebra]]"
  - "[[measure]]"
  - measurable space
---
三元组 $(X,\mathcal M,\mu)$，其中 $\mathcal M$ 是 σ-algebra，$\mu$ 是 measure。

## Search terms

what makes a measurable space a measure space

## Evidence

> 我们称 \((X,\mathcal{M},\mu)\) 为一个 measure space.
```

## Fixed keys

| Key | Required | Value |
|---|---|---|
| `label` | yes | Non-empty single-line name in any language. Not unique. |
| `kind` | yes | A node kind or relation kind of the source's document type. |
| `source` | yes | POSIX path relative to the base root (no leading `/`, no `..`); a registered source of this base. |
| `lines` | yes | `a` or `a-b`, 1-based and inclusive, within the source. |
| `aliases` | no | List of other names and symbols, any language. Not unique. |
| `understanding` | no | `unknown`, `not-yet-understood` or `understood`; absent means `unknown`. |
| `epistemic` | no | One value from the type's `epistemic` list. |
| `requires` | no | List of values: direct understanding prerequisites, one level. |
| `tags`, `cssclasses` | no | Obsidian's own properties; accepted and ignored. |

There is no `id` or `schema` key: the id is the file stem.

## Role keys and the class rule

Every other key must be a role that the type declares for `kind`
(`relation_kinds: {kind: [role, …]}`). A role key is a top-level list of
values, in order. A record is a **relation** if and only if at least one role
key holds a non-empty list; otherwise it is a **node**. `kgd check` requires a
node kind to have no role keys and a relation kind to have at least one value.

```yaml
# binary relation (2 roles)
kind: implies
premise: ["[[subspace]]"]
conclusion: ["[[sum-of-subspaces]]"]

# symmetric relation: one role, several values
kind: equivalent
side: ["[[a]]", "[[b]]"]

# self-relation with a pending participant
kind: property
subject: ["[[sum-of-subspaces]]"]
uses: ["[[subspace]]", "[[subspace]]", dimension]

# a relation about a relation: the witness is itself a relation record
kind: contrasts
subject: ["[[direct-sum]]"]
contrast: ["[[sum-of-subspaces]]"]
witness: ["[[subspace-sum-with-itself]]"]

# application / example
kind: example
uses: ["[[direct-sum]]", span]
setting: ["F^n with the standard basis"]
```

## Value grammar

A value (in `requires` or any role) is a link or a term:

```
value   := link | term
link    := "[[" target ( "|" display )? "]]"      the whole trimmed string
target  := id | base ":" id | ".knowledge/entries/" id     a trailing ".md" is ignored
term    := non-empty single-line string without "[[" or "]]"
```

- Quote every link in YAML: `- "[[x]]"`. An unquoted `[[x]]` parses as a nested
  list and `check` reports "quote wikilinks".
- `[[id]]` is local to the record's base; `[[base:id]]` targets another
  registered base. Never prefix a local link with the record's own base name.
- Targets match after NFKC normalization and casefolding. `#`, `^` and other
  path forms are errors.
- A **term** is a pending gap: the source uses it without explaining it here.
  It never binds by name, even when a record has that label. It is resolved
  only by editing the value into a link.
- Wikilinks in body prose are free navigation; they are neither checked nor
  indexed as links.

## Body grammar

```
<prose: any Markdown>
[## Search terms
<retrieval phrasings, one per line>]
## Evidence
<one or more blockquotes separated by blank lines>
```

- Level-2 headings are lines starting with `## ` outside fenced code blocks.
- `## Evidence` is the last level-2 heading. `## Search terms`, when present,
  is the heading immediately before it. Other headings belong to the prose.
- The Evidence section contains only `>` lines and blank lines. Each run of
  consecutive `>` lines is one quote. At least one quote is required.
- Each quote is copied verbatim from the cited lines of `source`. Freshness
  compares quote and source after collapsing whitespace: every quote must occur
  in the cited lines.
- Search terms hold question forms and other non-name phrasings; names in any
  language go in `aliases`.
- The gloss shown in sheets and results is the first sentence of the first
  prose paragraph, so open with the statement.

## Ids

- A valid id matches `^[^\W_]+(?:-[^\W_]+)*$` (Unicode letters and digits,
  CJK included, joined by single hyphens), equals its own NFKC casefold, and is
  at most 80 characters.
- Create it with `slug(label)`: NFKC, casefold, replace each run of
  non-alphanumeric characters with `-`, strip leading and trailing `-`, cut at
  the last `-` at or before 80 characters. On a collision append `-2`, `-3`, … .
  If the slug is empty, choose a readable id.
- Ids are unique case-insensitively across `entries/` and `drafts/` of one base.
  An id changes only by renaming the file.

## Drafts

- A draft proposes a **new** record: its id must not exist in `entries/`. There
  are no update drafts; change an accepted record by editing it in place.
- A draft's local links may target accepted records or other drafts of the
  same base. Foreign links must target accepted records. Accepted records never
  link to drafts.
- `kgd check` validates drafts like records. Drafts are never indexed.
- Deleting a draft rejects it.

`kgd accept DRAFT... [--dry-run]` validates the selected drafts together, under
the home lock, as if they were already accepted: links may resolve to accepted
records or other selected drafts, and evidence must be fresh. It moves each
draft into `entries/` without ever overwriting a file. Results:

```json
{"refused": [{"path": "/abs/.knowledge/drafts/x.md", "message": "premise[0]: select [[y]] too"}]}
{"would_create": ["notes:x"]}
{"created": ["notes:x", "notes:y"], "understanding_set": [{"uid": "notes:x", "value": "understood"}]}
```

A refusal writes nothing and exits 1. A receipt with `aborted` names a target
that appeared meanwhile; rerunning the same command finishes an interrupted
accept. `understanding_set` lists every created record whose understanding is
not `unknown`, so a set understanding is always visible.

## What to extract

Follow the source's document type: its `node_kinds`, `relation_kinds`,
`epistemic` list and guidance (`kgd sheet FILE --json`). In general:

- **Nodes** are noun-like concepts and important, precisely stated, named
  results (definitions, axioms, named theorems, methods, datasets).
- **Relations** are statements that connect existing records: propositions,
  remarks, results, comparisons, derivations. Bind each participant to the role
  the type declares; arity is unbounded and a binary relation is the 2-role
  case. A participant may repeat (self-relation) and may itself be a relation.
- **Applications and examples** are relations of the kind the type registers
  for them, for example `example: [uses, setting]`.
- **Pending gaps** are terms the source uses without explaining. Record them
  one level deep, in `requires` or the role where they occur; never chase the
  next level now.
- **Understanding** is set only from the owner's explicit statement. Reading,
  capturing or finding a definition never establishes it.
- Same name is not same concept. Compare definitions with `kgd resolve` and
  `kgd search`; two senses are two records, and equivalence, if wanted, is an
  explicit relation.
