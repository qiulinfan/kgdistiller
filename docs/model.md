# Knowledge model

kgdistiller stores research knowledge as plain Markdown records inside the
Obsidian vaults that hold their sources, and serves retrieval from one derived
SQLite file. This document is the normative description of the model, the
files and the write path. Retrieval is in [retrieval.md](retrieval.md), the
Obsidian plugin in [obsidian.md](obsidian.md), installation and restore in
[deployment.md](deployment.md).

## Principles

1. **Two authorities, everything else derived.** The authorities are the base
   files (`<root>/.knowledge/entries/*.md` for accepted knowledge and
   `<root>/.knowledge/drafts/*.md` for proposals) and the home files
   (`$KGDISTILLER_HOME/config.json` and `$KGDISTILLER_HOME/types/*.md`). The
   database, the sheets and the Obsidian graph are derived; a sheet also
   carries the checkbox state of its draft rows, and nothing else.
2. **One file format for every knowledge object.** Nodes and n-ary relations
   differ only by whether role properties are present. Applications and
   examples are relations of a user-registered kind. Pending gaps are plain
   text values where a link would go.
3. **No hashes, no content-derived ids, no compatibility code.** Change
   detection uses file stat; evidence freshness is checked by finding the quote
   text in the source; re-embedding is decided by comparing stored text;
   concurrency uses one lock file, create-only moves and edit tools that refuse
   stale reads.
4. **Format-agnostic sources.** A source is any registered UTF-8 text file,
   addressed by path, a 1-based inclusive line range and verbatim quotes. The
   product never parses source syntax.
5. **No owner vocabulary in product code.** The product fixes only the
   record's fixed keys, the understanding enum, the section names
   `Search terms` and `Evidence`, the folder names `entries`, `drafts` and
   `sheets`, and the link grammar. Node kinds, relation kinds, roles and
   epistemic labels are user data; the product ships no types and no default
   embedding model.
6. **Files decide validity; the database serves retrieval.** Validation and
   writes read files only. Retrieval reads the database only, except
   `get --source-lines`, which reads live source text on request.
7. **The database may lag, never differ.** It is a pure function of
   `config.json`, the `entries/` files of the registered, available bases and
   the embedding model. An incremental index produces the same rows as a
   rebuild, and losing the database costs one command plus the time to
   re-embed.

## Data model

| Concept | Representation |
|---|---|
| Home | `$KGDISTILLER_HOME` (default `~/.knowledge`): base registry, source→type globs, document types, embedding model id, the database and the lock. |
| Base | A registered directory, normally one Obsidian vault, holding its own knowledge under `.knowledge/`. Its name is a slug that exists only in `config.json`. |
| Source | A file of a base that one of the base's globs matches, read as UTF-8 text with 1-based lines. It has exactly one document type. |
| Document type | `types/<name>.md`: `node_kinds`, `relation_kinds` (each kind with ordered roles) and an optional `epistemic` list; the body is the extraction guidance. |
| Record | One file `.knowledge/entries/<id>.md`. Its global address is the uid `<base>:<id>`. |
| Node | A record with no non-empty role property: a noun-like concept or an important, precisely stated result. Its kind is a node kind. |
| Relation | A record with at least one non-empty role property. Its kind is a relation kind and its role keys are that kind's roles. Arity is unbounded; a binary relation is the 2-role case. A participant may repeat (self-relation) and may be a relation (a relation about relations). |
| Application / example | A relation of a kind the owner registers for it, such as `example: [uses, setting]`. |
| Link | A value `"[[id]]"`, `"[[base:id]]"` or `"[[.knowledge/entries/id]]"` in a role list or in `requires`. |
| Pending gap | A plain-text value in a role list or in `requires`: the source uses the term without explaining it. One direct level only. It never binds by name; it is resolved by editing it into a link. |
| `requires` | A list on every record holding direct understanding prerequisites. Proof or derivation dependencies are relations. |
| Evidence | `source` + `lines` + one or more verbatim quotes in the body's final `## Evidence` section. |
| Understanding | `unknown`, `not-yet-understood` or `understood`; absent means `unknown`. Independent of coverage and evidence. |
| Epistemic | Optional; a value from the type's `epistemic` list. |
| Draft | `.knowledge/drafts/<id>.md`: a proposed new record in exactly the record format. Not knowledge and not indexed. |
| Sheet | `.knowledge/sheets/<source path>.md`: the generated def/pending view of one source. |

There are no source ids, courses or groups, passages, stored sense groups,
pending tables, edge files, identity or alignment registries, review or
curation status, receipts or plan files, per-record `id` or `schema` fields,
or update drafts.

## Home and base layout

```text
$KGDISTILLER_HOME/                 default ~/.knowledge; absolute after ~ expansion
├── config.json                    bases, their source→type globs, embedding model id
├── types/<name>.md                one document type per file
├── .gitignore                     "index.sqlite*" and "lock", written when the home is created
├── index.sqlite (-wal, -shm)      derived database
└── lock                           write lock; its contents are meaningless

<base root>/.knowledge/
├── entries/<id>.md                accepted records: nodes and relations, one folder
├── drafts/<id>.md                 proposed new records, same format
└── sheets/<source path>.md        generated def/pending sheets
```

`KGDISTILLER_HOME` is the only environment variable the product reads for its
own data. Under `.knowledge/` the product reads and writes only `entries/`,
`drafts/` and `sheets/`; other files there are ignored. The product writes no
vault `.gitignore`.

`config.json` has exactly the keys `bases` and `embedding`. Each base has
exactly `path` and `sources`; base names match `^[a-z0-9][a-z0-9-]*$`. A path
under the user's home is stored as `~/…`. No base root may contain another,
and the home may not lie inside a base root. Each `sources` key is a glob
relative to the base root, expanded with Python's
`glob.glob(pattern, root_dir=root, recursive=True)`: `*` stays in one path
segment, `**` spans directories, and hidden files and directories never match.
A file matched by globs naming two different types is an error. `embedding` is
`null` or a sentence-transformers model id, with no revision pin; with `null`
the index is lexical and name only. Changing it re-embeds every record on the
next `kgd index`.

A type file's stem matches `^[a-z0-9][a-z0-9-]*$`. Its frontmatter, read with
PyYAML `BaseLoader`, has only `node_kinds` (required, non-empty, unique slugs),
`relation_kinds` (kind slug → non-empty list of unique role slugs matching
`^[a-z][a-z0-9-]*$`, never a fixed key) and `epistemic` (unique slugs; without
it records of this type may not carry `epistemic`). No kind is both a node kind
and a relation kind. The body, the guidance, is non-empty.

## Record format

A record is YAML frontmatter followed by a Markdown body. The file starts with
a line `---`; the frontmatter ends at the next line that is exactly `---`.
Files are read as UTF-8 with a leading byte-order mark dropped and universal
newlines.

```markdown
---
label: Sum of two subspaces is a subspace
kind: implies
premise:
  - "[[subspace]]"
conclusion:
  - "[[sum-of-subspaces]]"
epistemic: stated
source: notes/math/linear-algebra/chapters/01-review.tex
lines: 24-32
---
若 $U_1,U_2$ 是 $V$ 的 subspaces，则 $U_1+U_2$ 也是 subspace。

## Conditions

$U_1,U_2\subset V$ 均为 subspace。

## Evidence

> 两个 subspace \(U_{1},U_{2}\) 的 sum \(U_{1} + U_{2}\) 也是一个 subspace, 并且

> 且 \(U_{1} + U_{2}\) 是同时包含 \(U_{1}\) 和 \(U_{2}\) 的 \(V\) 的最小 subspace.
```

**Fixed keys**

| Key | Required | Value |
|---|---|---|
| `label` | yes | Non-empty single-line string in any language. Not unique. |
| `kind` | yes | A node kind or relation kind of the source's type. |
| `source` | yes | POSIX path relative to the base root, no leading `/`, no `..`; a registered source of this base. |
| `lines` | yes | `a` or `a-b`, 1 ≤ a ≤ b ≤ the source's line count. |
| `aliases` | no | List of single-line names in any language, symbols. Not unique. |
| `understanding` | no | `unknown`, `not-yet-understood` or `understood`; empty or absent means `unknown`. |
| `epistemic` | no | A slug from the type's `epistemic` list. |
| `requires` | no | List of values. |
| `tags`, `cssclasses` | no | Obsidian's own properties; accepted and ignored. |

**Role keys.** Every other key is a role declared for `kind`, holding a list of
values in order. Roles are top-level list properties so that Obsidian's
Properties panel edits them with link autocomplete and Backlinks groups
incoming links by role.

**Class rule.** A record is a relation if and only if at least one non-fixed
key holds a non-empty list; otherwise it is a node. The Obsidian plugin applies
the same rule.

**YAML reading.** Frontmatter is read with `yaml.load(text,
Loader=yaml.BaseLoader)`, so every scalar is a string (`on`, `yes` and `40-46`
stay strings). It must be a mapping whose values are strings or lists of
strings; an empty scalar on a list or role key reads as an empty list. An
unquoted wikilink such as `premise: [[x]]` parses as a nested list and is
reported as "quote wikilinks". The product never re-serializes frontmatter; the
only product-side frontmatter edit is the textual `lines:` rewrite of
`check --fix-lines`.

**Value grammar** (`requires` and every role value):

```text
value   := link | term
link    := "[[" target ( "|" display )? "]]"         the whole trimmed string; display is ignored
target  := id | base ":" id | ".knowledge/entries/" id      a trailing ".md" is ignored
term    := non-empty single-line string containing neither "[[" nor "]]"
```

Targets match after NFKC normalization and casefolding. A string with `[[` or
`]]` that is not one whole link, a target containing `#` or `^`, any other path
form, and a local link prefixed with the record's own base name are errors.
Wikilinks inside body prose are free navigation and are neither validated nor
indexed.

**Body grammar**

```text
<prose: any Markdown>
[## Search terms
<free text, one phrase per line>]
## Evidence
<one or more blockquotes separated by blank lines>
```

Level-2 headings are lines starting with `## ` outside fenced code blocks.
`## Evidence` is the last level-2 heading and occurs once; `## Search terms`,
when present, is the heading immediately before it. Other headings belong to
the prose. The Evidence section contains only `>` lines and blank lines; each
maximal run of `>` lines is one quote, with the `>` and one optional space
removed. At least one non-empty quote is required. Search terms hold
retrieval phrasings that are not names; names go in `aliases`.

**Gloss.** The first sentence of the first prose paragraph that is not a
heading: lines joined with spaces, ending at `。！？` or at `.!?` followed by
whitespace or the end, capped at 240 characters, computed at read time.

**Ids.** The id is the file stem. A valid id matches
`^[^\W_]+(?:-[^\W_]+)*$` (Unicode letters and digits, CJK included, joined by
single hyphens), equals its own NFKC casefold and has at most 80 characters.
`slug(label)` creates one: NFKC, casefold, replace each run of `[\W_]` with
`-`, strip `-`, and cut at the last `-` at or before position 80 (or at 80).
An empty slug means the writer chooses an id; collisions append `-2`, `-3`, … .
Ids are unique case-insensitively across `entries/` and `drafts/` of a base and
never derive from a hash.

## Drafts

- A draft proposes a new record: its id must not exist in `entries/`. There are
  no update drafts; accepted records are edited in place.
- A draft's local links may target accepted records or other drafts of the
  same base; its foreign links must target accepted records. Accepted records
  never link to drafts.
- The owner may edit a draft before accepting it; the bytes present at
  acceptance are what is accepted. Deleting a draft rejects it.
- Drafts are validated by `kgd check` and never indexed.

## Sheets

`kgd sheet SOURCE` writes `<root>/.knowledge/sheets/<source path>.md`:

```markdown
<!-- generated by kgd sheet; only draft checkboxes are read back -->
# notes/math/linear-algebra/chapters/01-review.tex

[[notes/math/linear-algebra/chapters/01-review.tex]] · type math-notes · 7 accepted · 2 drafts

## definition
- [[.knowledge/entries/subspace|Subspace]] · L7-19 · understood — vector space $V$ 的 subset …

## implies
- [[.knowledge/entries/sum-of-two-subspaces-is-a-subspace|Sum of two subspaces is a subspace]] · L24-32 · premise: Subspace · conclusion: Sum of subspaces — 若 …

## Pending
- vector space — Subspace (requires)

## Drafts
- [x] [[.knowledge/drafts/direct-sum-criterion|Direct sum criterion]] · implies · L37-41 · premise: Direct sum · conclusion: Subspace — …
```

- Accepted records are grouped under one `## <kind>` section per kind: node
  kinds, then relation kinds, each in the type's order, then `## other` for
  undeclared kinds. Rows sort by first line, then id.
- Rows follow this grammar, where `<v>` is the target's label (its id, or uid
  for another base, when missing) or the term itself, and `|`, `[` and `]` in
  display text become spaces:

  ```text
  accepted: - [[.knowledge/entries/<id>|<label>]] · L<a>-<b>[ · <understanding≠unknown>][ · <role>: <v>, …]… — <gloss>
  draft:    - [ ] [[.knowledge/drafts/<id>|<label>]] · <kind> · L<a>-<b>[ · <role>: <v>, …]… — <gloss>
  ```

- `## Pending` lists the term values of this source's accepted records, one
  line per name key, with the owners' labels and roles. `## Drafts` lists the
  drafts whose `source` is this file, by line.
- Regeneration keeps the checkbox of each draft row whose draft still exists
  and overwrites everything else. A checkbox means "selected for acceptance",
  never "understood". `kgd index` never writes sheets.

## Identity and addressing

- The uid `<base>:<id>` is unique by construction.
- In a file, `[[id]]` is local and `[[base:id]]` is foreign; the foreign base
  must be registered and must not be the record's own base. Local links never
  change when a base moves or is renamed in the config.
- Obsidian resolves a link to the same-folder file first, and all records share
  `entries/`, so `[[y]]` in a record resolves to `entries/y.md` in Obsidian
  whenever kgd resolves it so. When a basename is not unique in the vault,
  Obsidian writes the path form `[[.knowledge/entries/y]]`, which the grammar
  accepts. kgd never falls back to aliases; a dangling link is reported with
  the records carrying that alias.
- Labels and aliases are not unique. Two records with the same name are two
  records; equivalence, if wanted, is an explicit relation.
- A record lives in the base whose source supplies its evidence.
- The base for a path is the registered root containing the path's realpath;
  there is no upward walk.
- A CLI uid argument is `base:id`, or a bare `id` when exactly one base has it;
  otherwise the command lists the candidates and exits 1.

## Validation: `kgd check [--base B]... [--fix-lines]`

`check` reads files only and never opens the database. It covers every base by
default; `--base` (repeatable) narrows it. Its output is JSON:

```json
{"errors": [{"path": "/abs/root/.knowledge/entries/x.md", "rule": "link", "message": "premise[0]: dangling link [[y]]"}],
 "stale": ["/abs/root/.knowledge/entries/z.md"],
 "moved": [{"path": "/abs/root/.knowledge/entries/w.md", "lines": "41-47"}]}
```

Paths are absolute. The exit code is 1 when any list is non-empty. Each type
file and `config.json` report their first problem; a broken type file does
not hide a `config.json` error, and record rules run only once both are valid.

| Rule | Checks |
|---|---|
| `config` | `config.json` shape, base names, root nesting, the home outside every root, every root available, no symlinked `.knowledge`, `entries/` or `drafts/`, an unknown `--base`. |
| `type` | Each `types/*.md` obeys the type rules; every type named in `sources` exists. |
| `source-type` | No registered file maps to two types. |
| `id` | The stem is a valid id; ids are unique across `entries/` and `drafts/`; a draft's id is not in `entries/`. |
| `frontmatter` | The record file is a regular file, not a symlink; the frontmatter parses; required keys present with the right shapes; every non-fixed key is a role of `kind`; `understanding` in the enum. |
| `source` | `source` is a registered, readable UTF-8 source of this base whose realpath stays inside the base root; `lines` lies within it. |
| `kind` | `kind` belongs to the source's type; a node kind has no role keys; a relation kind has a non-empty role value; `epistemic` is in the type's list. |
| `link` | Every value matches the grammar; every link resolves to an accepted record (local or foreign, read from files); no self-link; a foreign link to an unregistered base names it. |
| `body` | The body obeys the body grammar. |

Drafts follow the same rules with three changes: the id must not exist in
`entries/` (an identical file there is an interrupted accept, and the message
says that rerunning `kgd accept` finishes it); local links may also resolve to
drafts; foreign links resolve only to accepted records. An accepted record that
links a draft is told to accept it first.

**Evidence freshness.** Quotes and source text are compared after
`" ".join(s.split())`.

- **fresh**: every quote occurs in the cited lines;
- **moved**: not fresh, but every quote occurs exactly once in the whole file;
  the report proposes the range from the first line of the earliest occurrence
  to the last line of the latest;
- **stale**: anything else.

Staleness never hides a record from the index or retrieval.

**`--fix-lines`** runs under the home lock. For each moved file it re-reads the
file; if the text changed since the check read it, the file is skipped,
otherwise only the frontmatter's `lines:` line is rewritten (every other byte,
the byte-order mark and the newline style kept) through a temp file and an
atomic replace. The output adds two lists and keeps in `moved` only the files
not fixed:

```json
{"errors": [], "stale": [], "moved": [], "fixed": ["/abs/…/w.md"], "skipped": []}
```

## Writing knowledge

### Lock

`$KGDISTILLER_HOME/lock` is a dedicated lock file taken without waiting
(`fcntl.flock`, or `msvcrt.locking` on Windows). It is held by `accept`,
`harvest`, `check --fix-lines`, `base add` and `base rm`; a second writer fails
at once. No data file is ever locked or truncated. `kgd index` relies on
SQLite's own locking.

### `kgd accept DRAFT... [--dry-run]`

Each DRAFT is a `.knowledge/drafts/<id>.md` path of a registered base, absolute
or relative to the working directory.

1. Take the lock and parse every selected draft.
2. Validate each one in the post-acceptance world: local links may resolve to
   accepted records or to other selected drafts; a link to an unselected draft
   is refused with `select [[x]] too`; evidence must be fresh; the id must not
   exist in `entries/`, except when the existing file has exactly the draft's
   text (an earlier accept interrupted between link and unlink), in which case
   only the draft is removed.
3. On any problem print `{"refused": [{"path", "message"}]}`, exit 1 and write
   nothing. Problems in other records do not block acceptance.
4. With `--dry-run` print `{"would_create": [uid]}`.
5. Otherwise, in argument order, `os.link(draft, entries/<id>.md)` then
   `os.unlink(draft)`. Nothing is ever overwritten: a target that appeared since
   validation with different text stops the run, and the receipt lists what
   was already created under `created` plus `aborted: {path, message}`.
   Rerunning the same command completes an interrupted run.
6. Print `{"created": [uid], "understanding_set": [{"uid", "value"}]}`.
   `understanding_set` lists the created records whose understanding is not
   `unknown`.

`accept` does not index; the calling Skill runs `kgd index`.

### `kgd harvest SHEET [--dry-run]`

SHEET is `<root>/.knowledge/sheets/<source path>.md`. Harvest collects the
draft ids of the `## Drafts` rows matching
`^- \[[xX]\] \[\[\.knowledge/drafts/<id>(\|…)?\]\]`, skips ticked rows whose
draft no longer exists, runs one `accept` on the rest, regenerates the sheet on
success and prints the accept result. A refusal changes nothing.

### `kgd sheet SOURCE [--json]`

SOURCE is a registered source file, absolute or relative to the working
directory; an unregistered file is an error that asks for a glob in
`config.json`. Without `--json` it writes the sheet through a temp file and an
atomic replace and prints `{"sheet": path}`. With `--json` it writes nothing
and prints the extraction profile and inventory agents use:

```json
{"base": "notes", "source": "notes/math/…/01-review.tex", "type": "math-notes",
 "node_kinds": ["definition", "theorem"], "relation_kinds": {"implies": ["premise", "conclusion"]},
 "epistemic": ["proved", "stated"], "guidance": "…", "line_count": 77,
 "records": [{"uid": "notes:subspace", "label": "Subspace", "kind": "definition", "class": "node",
              "lines": "7-19", "understanding": "understood", "gloss": "…"}],
 "drafts": [{"id": "direct-sum-criterion", "label": "…", "kind": "implies", "class": "relation", "lines": "37-41"}],
 "pending": [{"term": "vector space", "owners": [{"uid": "notes:subspace", "role": "requires"}]}]}
```

Agents read the source text with their own tools; the inventory carries no
lines.

### Updates, deletes and renames of accepted records

- **Update.** Edit `entries/<id>.md` in place with an editing tool that refuses
  when the file changed since it was read (Claude Code `Edit`, Codex
  `apply_patch`); never rewrite a whole record from an earlier read. Then run
  `kgd check --base B` and `kgd index`. The owner reviews agent edits with
  `git diff`. Accept only creates, and the editor guards the window between
  read and write.
- **Delete.** Remove the file. `kgd check` then lists every inbound link left
  dangling, local and foreign. Run `kgd index`.
- **Rename.** Rename the file in Obsidian, which rewrites local links;
  `kgd check` reports the foreign `[[base:old]]` links that remain. A record's
  indexed text does not contain its own id, so indexing re-uses its vector.

### Base registration

`kgd base add PATH [--name N]` creates the home on first use (`config.json` as
`{"bases": {}, "embedding": null}`, an empty `types/` and the `.gitignore`),
registers the directory and creates `<root>/.knowledge/entries/`. The default
name is the directory's basename, never transformed. `kgd base rm NAME` removes
the registration only and adds `dangling`, the `[[NAME:…]]` values left in the
other bases' entries and drafts as `{path, role, value}`. `kgd base list`
prints each base's `name`, `path`, `root`, `available`, `records` and `drafts`
(file counts, `null` when unavailable), `indexed` and `lag`. There is no
`base rename`.
