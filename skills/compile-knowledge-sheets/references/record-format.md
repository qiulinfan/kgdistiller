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
| `lines` | yes | One or more ranges `a` or `a-b`, 1-based and inclusive, comma-separated, ascending and disjoint, within the source, such as `126-140, 878-884`. |
| `aliases` | no | List of other names and symbols, any language. Not unique. |
| `understanding` | no | `unknown`, `not-yet-understood` or `understood`; absent means `unknown`. |
| `epistemic` | no | One value from the type's `epistemic` list: how the cited passage itself supports the record. A result the source only reports from other work is stated. |
| `requires` | no | List of values: direct understanding prerequisites, one level. |
| `tags`, `cssclasses` | no | Obsidian's own properties; accepted and ignored. |

There is no `id` or `schema` key: the id is the file stem.

## Role keys and the class rule

Every other key must be a role that the type declares for `kind`
(`relation_kinds: {kind: [role, …]}`). A role key is a top-level list of
values, in order. A record is a **relation** if and only if at least one role
key holds a non-empty list; otherwise it is a **node**. `kgd check` requires a
node kind to have no role keys and a relation kind to have at least one value.
A kind whose meaning does not change when its participants swap has a single
role listing them unordered, like `equivalent: [side]` below; a kind with
several roles is directional, so write each fact in one form only.

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

# a relation about a relation: the witness is itself a relation record;
# the two contrasted records swap freely, so they share one role
kind: contrasts
side: ["[[direct-sum]]", "[[sum-of-subspaces]]"]
witness: ["[[subspace-sum-with-itself]]"]

# application / example: setting binds a concept; the concrete
# construction ("F^n with the standard basis") is written in the prose
kind: example
uses: ["[[direct-sum]]", span]
setting: ["[[coordinate-space]]"]
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
- A **term** is a pending gap: a concept the source uses without explaining
  it here. It never binds by name, even when a record has that label. It is
  resolved only by editing the value into a link.
- Every value is a knowledge reference, never a literal. Settings,
  hyperparameters, resolutions, splits, units and measured values belong in
  the prose and the Evidence quotes.
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
  inside one cited range. Cite distant passages, such as a definition and its
  appendix details, as separate ranges rather than one range spanning both.
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
- Choose the id from the meaning. Leave out section, figure, table, equation
  and theorem numbers, source ordinals, paths and kinds; they change when a
  source is revised. A label may keep the source's numbering when that is the
  item's name, but then write the id yourself.
- An accepted id is permanent: never rename it for a better name (use `label`
  or `aliases`) and never reuse it for another concept. A changed meaning of
  the same knowledge point overwrites the record in place under the knowledge
  model's conflict rules and keeps the id. Merging duplicates or splitting a
  conflated record rewrites the inbound links once (see the knowledge model's
  identity changes).

## Drafts

- A draft proposes a **new** record: its id must not exist in `entries/`. There
  are no update drafts; change an accepted record by editing it in place.
- A draft's local links may target accepted records or other drafts of the
  same base. Foreign links must target accepted records. Accepted records never
  link to drafts.
- `kgd check` validates drafts like records. Drafts are never indexed.
- Deleting a draft rejects it. Nothing remembers the rejection, so a later
  compile may propose the same record again.

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

- **Nodes** are noun-like: the concepts, methods, architectures, objectives and
  quantities the source defines, plus theorems it states precisely. Each
  concept has one record, owned by the source that defines or originates it.
  A concept this source only uses, even one it explains at length, is not a
  node here: it is a pending term or a link to its defining source's record.
- **Relations** are every other statement that can be true or false:
  propositions, remarks, measured results, comparisons, findings, limitations,
  derivations and explanatory hypotheses. Bind each participant to the role the
  type declares; arity is unbounded, a binary relation is the 2-role case, and
  a finding about one concept has that single participant. A participant may
  repeat (self-relation) and may itself be a relation.
- **Only stated connections.** A relation citing this source quotes text that
  states the relation. A connection you infer by combining passages, or
  between this source and another one it does not mention, is never written
  as a relation of this source; report it instead.
- **Applications and examples** are relations of the kind the type registers
  for them, for example `example: [uses, setting]`.
- **Pending gaps** are terms the source uses without explaining. Record them
  one level deep, in `requires` or the role where they occur; never chase the
  next level now. Gaps are global: `kgd resolve` checks every base, and a term
  stays pending only while no registered base holds the record that defines
  it. When one does, link it there, as `[[base:id]]` across bases. Never write
  a stand-in definition for a concept whose defining source is missing.
- **Understanding** is set only from the owner's explicit statement. Reading,
  capturing or finding a definition never establishes it.
- Same name is not same concept. Compare definitions with `kgd resolve` and
  `kgd search`: same-named records are different concepts with different
  definitions, and a name collision needs nothing more.
