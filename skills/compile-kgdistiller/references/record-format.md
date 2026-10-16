# Record format

Every piece of knowledge is one record file. Nodes and relations share one
format and one folder; they differ only in whether role keys are present.

- Accepted records: `<base root>/.knowledge/entries/<id>.md`.
- Drafts: `<base root>/.knowledge/drafts/<id>.md`, in exactly the same format.
  A draft is a new record, or, under the id of an accepted record of its base,
  that record's replacement.
- The global address of a record is its uid `<base>:<id>`.

Agents write drafts only; the owner accepts them. The one exception is
`kgd relocate`, which moves a record's citation to a passage without changing
what it says. kgd never re-serializes frontmatter. Write whole files yourself;
the only product-side frontmatter edits are the `lines:` rewrite of
`kgd check --fix-lines` and the `source:` and `lines:` rewrite of
`kgd relocate`.

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
requires:
  - "[[sigma-algebra]]"
  - "[[measure]]"
---
三元组 $(X,\mathcal M,\mu)$，其中 $\mathcal M$ 是 σ-algebra，$\mu$ 是 measure。

## Search terms

what makes a measurable space a measure space

## Evidence

> 我们称 \((X,\mathcal{M},\mu)\) 为一个 measure space.
```

A **foreign** record cites the web page of the outside work that originates or
explains the concept, has no `lines`, and quotes that page verbatim:

```markdown
---
label: Autoencoder
kind: definition
source: https://en.wikipedia.org/wiki/Autoencoder
---
An autoencoder is a neural network trained to reproduce its input through a
learned code of lower dimension.

## Evidence

> An autoencoder is a type of artificial neural network used to learn efficient codings of unlabeled data
```

## Fixed keys

| Key | Required | Value |
|---|---|---|
| `label` | yes | Non-empty single-line name in any language, unique by name key across every base: a concept of another meaning whose name is taken adds its source's name, `Label (source)`. |
| `kind` | yes | A node kind or relation kind of the source's document type; for a foreign record, of a type registered for its base. |
| `source` | yes | A POSIX path relative to the base root (no leading `/`, no `..`) naming a registered source of this base; or a web address `http://…` or `https://…`, which makes the record foreign. |
| `lines` | local only | One or more ranges `a` or `a-b`, 1-based and inclusive, comma-separated, ascending and disjoint, within the source, such as `126-140, 878-884`. A foreign record has none. |
| `aliases` | no | List of other names and symbols, any language. Not unique. |
| `understanding` | no | `yes` or `no`; absent means `yes`. |
| `epistemic` | no | One value from the type's `epistemic` list: how the cited passage itself supports the record. A result the source only reports from other work is stated. |
| `requires` | no | List of links: direct understanding prerequisites, one level. |
| `tags`, `cssclasses` | no | Obsidian's own properties; accepted and ignored. |

There is no `id`, `schema` or `foreign` key: the id is the file stem, and a
record is foreign when its `source` is a web address.

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

# self-relation
kind: property
subject: ["[[sum-of-subspaces]]"]
uses: ["[[subspace]]", "[[subspace]]", "[[dimension]]"]

# a relation about a relation: the witness is itself a relation record;
# the two contrasted records swap freely, so they share one role
kind: contrasts
side: ["[[direct-sum]]", "[[sum-of-subspaces]]"]
witness: ["[[subspace-sum-with-itself]]"]

# application / example: setting binds a concept; the concrete
# construction ("F^n with the standard basis") is written in the prose
kind: example
uses: ["[[direct-sum]]", "[[span]]"]
setting: ["[[coordinate-space]]"]
```

## Value grammar

Every value (in `requires` or any role) is a link:

```
value   := "[[" target ( "|" display )? "]]"      the whole trimmed string
target  := id | base ":" id | ".knowledge/entries/" id     a trailing ".md" is ignored
```

- Quote every link in YAML: `- "[[x]]"`. An unquoted `[[x]]` parses as a nested
  list and `check` reports "quote wikilinks".
- `[[id]]` is local to the record's base; `[[base:id]]` targets another
  registered base. Never prefix a local link with the record's own base name.
- Targets match after NFKC normalization and casefolding. `#`, `^` and other
  path forms are errors. Plain text is an error.
- A link targets an accepted record, or a draft of the same base. A concept
  that nothing records is captured on its own first (locally, or as foreign
  knowledge), and until then it is left out of the values; the prose may
  still name it.
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
- Each quote is copied verbatim from the cited lines of `source`, or from the
  web page of a foreign record. Freshness compares quote and local source
  after collapsing whitespace: every quote must occur inside one cited range.
  Cite distant passages, such as a definition and its appendix details, as
  separate ranges rather than one range spanning both. A foreign record's
  quotes are never checked against the page.
- Search terms hold question forms and other non-name phrasings; names in any
  language go in `aliases`.
- The gloss shown in results is the first sentence of the first prose
  paragraph, so open with the statement.

## Ids

- A valid id matches `^[^\W_]+(?:-[^\W_]+)*$` (Unicode letters and digits,
  CJK included, joined by single hyphens), equals its own NFKC casefold, and is
  at most 80 characters.
- Create it with `slug(label)`: NFKC, casefold, replace each run of
  non-alphanumeric characters with `-`, strip leading and trailing `-`, cut at
  the last `-` at or before 80 characters. On a collision append `-2`, `-3`, … .
  If the slug is empty, choose a readable id.
- Ids are unique case-insensitively within `entries/` and within `drafts/` of
  one base; a draft with an accepted record's id is that record's replacement.
- Choose the id from the meaning. Leave out section, figure, table, equation
  and theorem numbers, source ordinals, paths and kinds; they change when a
  source is revised. A label may keep the source's numbering when that is the
  item's name, but then write the id yourself.
- An accepted id is permanent: never rename it for a better name (use `label`
  or `aliases`) and never reuse it for another concept. A changed meaning of
  the same knowledge point is a replacement draft with the same id, under the
  knowledge model's conflict rules. Merging duplicates or splitting a
  conflated record rewrites the inbound links once, after the owner accepts
  it (see the knowledge model's identity changes).

## Drafts

- A draft whose id is not in `entries/` is a new record; one with an accepted
  record's id replaces that record when accepted. Write a replacement from a
  fresh read of the record: `kgd accept` refuses it when the record changed
  after the draft was written.
- A draft's local links may target accepted records or other drafts of the
  same base. Cross-base links must target accepted records. A link to an id in
  `entries/` always means the accepted record. Accepted records never link to
  drafts.
- `kgd drafts [--source PREFIX] [--name NAME]` lists drafts with each value's
  target, the drafts to accept with them (`with`), the record they replace
  (`replaces`) and `problems`: what `kgd accept` would refuse. Fix a draft
  until its problems are empty. `kgd check` validates drafts like records.
  Drafts are never indexed.
- Deleting a draft rejects it. Nothing remembers the rejection, so a later
  compile may propose the same record again.

The owner accepts drafts with `kgd accept DRAFT...` (the Obsidian Capture view
runs it), which validates them together under the home lock as if they were
already accepted, moves each into `entries/`, then indexes and embeds them.
Agents never run it.

## What to extract

Follow the source's document type: its `node_kinds`, `relation_kinds`,
`epistemic` list and guidance (`kgd source FILE`). In general:

- **Nodes** are noun-like: the concepts, methods, architectures, objectives and
  quantities the source defines, plus theorems it states precisely. Each
  concept has one record, owned by the source that defines or originates it.
  A concept this source only uses, even one it explains at length, is not a
  node here: link its record, or, when the owner asks to capture it, draft it
  as foreign knowledge from the page that originates or explains it.
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
- **Prerequisites** are linked one level deep, in `requires` or the role where
  they occur, when a record or draft holds them; never chase the next level.
  Lookups are global: `kgd resolve` and `kgd drafts --name` check every base,
  and a concept recorded in another base is linked as `[[base:id]]`.
- **Understanding** is `yes` unless the owner says they do not understand the
  item; reading, capturing or finding a definition never sets it to `no`.
- A label names one concept across every base. Compare definitions with
  `kgd resolve` and `kgd search`: a record of the same name and meaning is the
  knowledge point already recorded; a new concept of another meaning whose
  name a record or draft already has takes its source's name in its label,
  `Label (source)`: the source file's name without its extension, or a web
  page's last path segment. `kgd check` and `kgd accept` refuse a repeated
  label.
