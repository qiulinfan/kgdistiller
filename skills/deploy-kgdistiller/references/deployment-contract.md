# Deployment contract

## Home and base layout

```text
$KGDISTILLER_HOME/            # default ~/.knowledge; owner data
├── config.json               # bases, their document types, embedding model id
├── types/<USER_TYPE>.md      # one user-defined document type per file
├── .gitignore                # "index.sqlite*" and "lock"
├── index.sqlite (-wal, -shm) # derived database; deleting it loses nothing
└── lock                      # writer lock; its contents are meaningless

BASE_ROOT/
├── notes/                    # sources: every md, tex and txt file outside hidden directories
└── .knowledge/
    ├── entries/<id>.md       # accepted records: nodes and relations
    └── drafts/<id>.md        # proposed records, new or replacements, same format
```

`KGDISTILLER_HOME` is the only environment variable for kgdistiller's own home
and data (the runtime linkers also honor `CODEX_HOME` and `CLAUDE_CONFIG_DIR`);
it must be absolute after `~` expansion. `config.json` and `types/` cannot be
derived; `index.sqlite*` and `lock` can, and the home's `.gitignore` excludes
them.

A base owns its sources and its `.knowledge/` tree, the base's only knowledge
root. Under `.knowledge/` the product reads and writes only `entries/` and
`drafts/`; anything else there is ignored. Commit both with the base: drafts
are review state awaiting the owner that should travel between machines. kgdistiller writes no `.gitignore` into a base.

## Record format

A record is YAML frontmatter plus a Markdown body:

```markdown
---
label: Sum of two subspaces is a subspace
kind: theorem
about:
  - "[[subspace]]"
  - "[[sum-of-subspaces]]"
epistemic: stated
source: notes/linear-algebra/chapters/01-review.tex
lines: 24-32
---
若 $U_1,U_2$ 是 $V$ 的 subspaces，则 $U_1+U_2$ 也是 subspace。

## Evidence

> 两个 subspace \(U_{1},U_{2}\) 的 sum \(U_{1} + U_{2}\) 也是一个 subspace
```

- Fixed keys: `label`, `kind`, `source` (required), `lines` (required for a
  local source; absent when `source` is a web address, a foreign record),
  `aliases`, `understanding` (`yes` or `no`, default `yes`), `epistemic`,
  `requires`; `tags` and `cssclasses` are
  accepted and ignored. The id is the file stem.
- Every other key is a role declared for `kind` by the source's type. A
  record is a relation when its type declares its kind under
  `relation_kinds` (it then binds at least two values), and a node otherwise;
  a node kind that declares roles, such as a theorem's `about`, links at
  least one record in them.
- Values are quoted links (`"[[id]]"`, `"[[base:id]]"`,
  `"[[.knowledge/entries/id]]"`); plain text is an error.
- The body ends with `## Evidence`: verbatim quotes of the cited lines (of the
  web page, for a foreign record), one blockquote each. An optional `## Search terms` section comes just before it.

## Base registration

`kgd base add BASE_ROOT [--name NAME]` creates the home on first use
(`config.json` as `{"bases": {}, "embedding": null}`, an empty `types/` and the
`.gitignore`), records the base and creates `BASE_ROOT/.knowledge/entries/`.
`kgd base rm NAME` removes only the registration and prints `dangling`: the
`[[NAME:…]]` links that other bases still hold. Both hold the home lock and
write `config.json` atomically. Base names match `^[a-z0-9][a-z0-9-]*$`.

- No base root may equal, contain or lie inside another base root.
- The home may not equal or lie inside a base root.
- `path` is stored as `~/…` when the root lies under the user's home, otherwise
  as an absolute path. A moved base needs its `path` edited by hand.
- A path argument belongs to the registered root that contains its real path;
  there is no upward search and no base identity file.

`kgd base list` prints, per base, `name`, `path`, `root`, `available`,
`records` and `drafts` (file counts), `indexed` (rows in the database) and
`lag`.

## Sources and document types

Names, kinds and guidance come from the owner; the product ships no types.
With placeholder values, `config.json`:

```json
{
  "bases": {
    "NAME": {
      "path": "~/BASE_ROOT",
      "types": ["USER_TYPE", "OTHER_USER_TYPE"]
    }
  },
  "embedding": "BAAI/bge-m3"
}
```

and `types/USER_TYPE.md`:

```markdown
---
node_kinds:
  USER_NODE_KIND: []
  USER_STATEMENT_KIND: [about]
  example: [uses, setting]
relation_kinds:
  USER_RELATION_KIND: [USER_ROLE, USER_OTHER_ROLE]
epistemic: [USER_STATUS]
---
The owner's rules for nodes, relations and examples.
```

- The top level holds exactly `bases` and `embedding`; each base holds exactly
  `path` and `types`. `embedding` is a Hugging Face id of an XLM-RoBERTa sentence encoder that ships `sentencepiece.bpe.model`, such as `BAAI/bge-m3`, with no
  revision pin (`BAAI/bge-m3` is the recommended one; the device is chosen
  automatically) or `null` for lexical and name search only. It needs the
  package installed with the `retrieval` extra. `kgd embedding set` downloads
  the weights into the Hugging Face cache (`~/.cache/huggingface`, or
  `$HF_HOME`; about 2.3 GB for `BAAI/bge-m3`); afterwards `HF_HUB_OFFLINE=1`
  keeps every load local. After a change, the next index run of any kind drops
  the old vectors, and the next plain `kgd index` embeds every record with the
  new model. `kgd embedding`
  shows it, `kgd embedding set MODEL` downloads and checks a model and then
  writes it, and `kgd embedding off` writes `null`.
- A base's sources are not registered: every file with the suffix `.md`,
  `.tex` or `.txt` (any case) under its root is one, except hidden files and
  anything under a hidden directory (`.knowledge/`, `.obsidian/`, `.git/`,
  `.trash/`) and files that resolve outside the root through a symlink.
- `types` lists the existing type files the base's records may use, each once.
  A record names its type in its `type:`; in a base of one type it may leave
  it out. The types of one base must declare every kind they share the same
  way (node or relation, the same roles).
- A type file has a slug stem and frontmatter read with PyYAML's `BaseLoader`.
  `node_kinds` is a required non-empty mapping of kind slugs to lists of
  unique role slugs, possibly empty; `relation_kinds` maps each kind to a
  non-empty list of unique role slugs (no dots, never a fixed key); `epistemic` is a list of slugs, and without it records may not
  carry `epistemic`. No kind is both a node kind and a relation kind. The
  non-empty body is the extraction guidance.

Sources are read as UTF-8 lines and never parsed or converted, except for
their knowledge markers (`--[[NAME]]--` in md and txt, `\kn{NAME}` or
`\kn[NAME]{TEXT}` in tex), which name the record a source states there.
`kgd source SOURCE` shows a source's base, the profile of every type of its
base (kinds, roles, guidance), its line count and markers, so extraction reads
the owner's policy before any record exists; `kgd markers` lists markers with
the records they resolve to.

## Check

`kgd check [--base B]... [--fix-lines]` reads files only and prints JSON:

```json
{"errors": [{"path": "/abs/.knowledge/entries/x.md", "rule": "link", "message": "…"}],
 "stale": ["/abs/.knowledge/entries/y.md"],
 "moved": [{"path": "/abs/.knowledge/entries/z.md", "lines": "40-46"}],
 "follow": [{"path": "/abs/.knowledge/entries/w.md", "round": "base", "base": "B",
             "source": "notes/renamed.md", "lines": "12-14"}]}
```

`rule` is one of `config`, `type`, `id`, `frontmatter`,
`source`, `kind`, `link`, `label`, `body`. The exit code is 1 when any list is
non-empty. `moved` evidence (each quote found exactly once elsewhere in the
source) is repaired by `--fix-lines`, which rewrites only the `lines:` line
under the home lock and adds `fixed` and `skipped` lists. A record whose
quotes are gone from its file but whose markers lead elsewhere is listed in
`follow`: with `lines` when its quotes hold in the one file the markers lead
to (`--fix-lines` rewrites its `source:` and `lines:` when that file is in its
base; `kgd relocate UID` moves it in any case), or with `"evidence": "absent"`
and the markers found when the passage was reworded. A `stale` record
is moved to the passage that now states it with the relocate Skill
(`kgd relocate`), or, when its meaning changed, gets a replacement draft.
Staleness never
hides a record from retrieval.

## Index and restore

`kgd index [--rebuild] [--no-embed]` brings `index.sqlite` up to date with the
`entries/` files of every registered, available base, re-parsing files whose
stat changed, then embeds every row whose vector is NULL with the `embedding`
model. The model loads only when such a row exists. Its JSON report lists per
base `parsed`, `deleted` and `unparseable`, plus `unavailable` bases,
`reused`, `embedded`, `unembedded`, `truncated`
(texts longer than the model's input limit) and `embedding_error` (`null` or
why embedding failed); it exits 1 when a file is unparseable, a base is
unavailable or embedding failed. `--rebuild` re-derives every row in
place and re-uses vectors by text. `--no-embed` skips the embedding phase and
leaves changed rows unembedded. With `embedding` set and the `retrieval` extra
missing, it commits the lexical index, reports `embedding_error` ending in
``install kgdistiller[retrieval] or run `kgd embedding off` `` and exits 1.

Restore after a lost or damaged database in one step:

```sh
rm -f "$KGDISTILLER_HOME/index.sqlite" "$KGDISTILLER_HOME/index.sqlite-wal" "$KGDISTILLER_HOME/index.sqlite-shm"
kgd index
```

This re-embeds every record, which is the only slow part of a restore (about
14 records a second with `BAAI/bge-m3` on Apple silicon); report the
wall time. A missing, unreadable or other-version database is also rebuilt
automatically by `kgd index`, which carries nothing over from it. Readers
(`search`, `resolve`, `get`, `neighbors`, `browse`, `pack`, MCP) open it
read-only and report `lag`; a missing database tells them to run `kgd index`,
and a document type a base's sources name that does not load fails them with
an error naming the file.

## Product provenance and boundaries

Record the installed kgdistiller version and full product commit when
discoverable. kgdistiller has no publishing surface; websites, course
registries and HTML rendering belong to the repositories that own the notes.

`kgd obsidian install --base NAME` installs or updates the bundled plugin in a
registered base's vault, after the owner has opened the root as a vault once.
It copies `main.js`, `manifest.json` and `styles.css` into
`<root>/.obsidian/plugins/kgdistiller/`, enables the plugin in
`.obsidian/community-plugins.json` and sets `hiddenKnowledgeEnabled` to `true`
in the plugin's `data.json`, keeping its other keys, so Obsidian indexes
`.knowledge/`. It warns when `.obsidian/app.json` sets `newLinkFormat` to
`relative`; report the warning and never edit `app.json` unasked. It refuses a
root that is not a registered base. The plugin is desktop only and reads
knowledge only through a resident `kgd mcp`.

`kgdistiller codex link` and `kgdistiller claude link` treat installed copies
as product-owned: `doctor` reports a copy that differs from the product source,
and relinking replaces it or removes a retired one, discarding local edits to
installed files. Report a differing copy before relinking.

Installing, linking, committing and pushing are separate authorities. Never
place private sources, records, the home's `config.json` or types, the
database or secrets in a product repository, receipt, command output or agent
configuration.
