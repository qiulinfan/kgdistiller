# Deployment contract

## Home and base layout

```text
$KGDISTILLER_HOME/            # default ~/.knowledge; owner data
├── config.json               # bases, their source globs, embedding model id
├── types/<USER_TYPE>.md      # one user-defined document type per file
├── .gitignore                # "index.sqlite*" and "lock"
├── index.sqlite (-wal, -shm) # derived database; deleting it loses nothing
└── lock                      # writer lock; its contents are meaningless

BASE_ROOT/
├── notes/                    # registered source documents, any text format
└── .knowledge/
    ├── entries/<id>.md       # accepted records: nodes and relations
    ├── drafts/<id>.md        # proposed new records, same format
    └── sheets/<source>.md    # generated def/pending sheets
```

`KGDISTILLER_HOME` is the only environment variable for kgdistiller's own home
and data (the runtime linkers also honor `CODEX_HOME` and `CLAUDE_CONFIG_DIR`);
it must be absolute after `~` expansion. `config.json` and `types/` cannot be
derived; `index.sqlite*` and `lock` can, and the home's `.gitignore` excludes
them.

A base owns its sources and its `.knowledge/` tree, the base's only knowledge
root. Under `.knowledge/` the product reads and writes only `entries/`,
`drafts/` and `sheets/`; anything else there is ignored. Commit all three with
the base: drafts and sheet ticks are unharvested review state that should
travel between machines. kgdistiller writes no `.gitignore` into a base.

## Record format

A record is YAML frontmatter plus a Markdown body:

```markdown
---
label: Sum of two subspaces is a subspace
kind: implies
premise:
  - "[[subspace]]"
conclusion:
  - "[[sum-of-subspaces]]"
epistemic: stated
source: notes/linear-algebra/chapters/01-review.tex
lines: 24-32
---
若 $U_1,U_2$ 是 $V$ 的 subspaces，则 $U_1+U_2$ 也是 subspace。

## Evidence

> 两个 subspace \(U_{1},U_{2}\) 的 sum \(U_{1} + U_{2}\) 也是一个 subspace
```

- Fixed keys: `label`, `kind`, `source`, `lines` (required), `aliases`,
  `understanding`, `epistemic`, `requires`; `tags` and `cssclasses` are
  accepted and ignored. The id is the file stem.
- Every other key is a role declared for `kind` by the source's type. A record
  with a non-empty role list is a relation; otherwise it is a node.
- Values are quoted links (`"[[id]]"`, `"[[base:id]]"`,
  `"[[.knowledge/entries/id]]"`) or plain pending terms.
- The body ends with `## Evidence`: verbatim quotes of the cited lines, one
  blockquote each. An optional `## Search terms` section comes just before it.

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

## Source globs and document types

Names, kinds and guidance come from the owner; the product ships no types.
With placeholder values, `config.json`:

```json
{
  "bases": {
    "NAME": {
      "path": "~/BASE_ROOT",
      "sources": {
        "notes/**/*.md": "USER_TYPE",
        "notes/*/main.tex": "USER_TYPE"
      }
    }
  },
  "embedding": "BAAI/bge-m3"
}
```

and `types/USER_TYPE.md`:

```markdown
---
node_kinds: [USER_NODE_KIND]
relation_kinds:
  USER_RELATION_KIND: [USER_ROLE, USER_OTHER_ROLE]
  example: [uses, setting]
epistemic: [USER_STATUS]
---
The owner's rules for nodes, relations, examples and pending terms.
```

- The top level holds exactly `bases` and `embedding`; each base holds exactly
  `path` and `sources`. `embedding` is a sentence-transformers model id with no
  revision pin (`BAAI/bge-m3` is the recommended one; the device is chosen
  automatically) or `null` for lexical and name search only. It needs the
  package installed with the `retrieval` extra. The first `kgd index` downloads
  the weights into the Hugging Face cache (`~/.cache/huggingface`, or
  `$HF_HOME`); afterwards `HF_HUB_OFFLINE=1` keeps every load local. Changing
  the id re-embeds every record on the next `kgd index`.
- Each `sources` key is a glob relative to the base root with Python glob
  semantics: `*` stays within one path segment, `**` spans directories, and
  hidden files and directories (`.knowledge/`, `.obsidian/`, `.git/`) never
  match.
- Each value names an existing type file. Globs naming two different types for
  one file are an error, so every source has exactly one type.
- A type file has a slug stem and frontmatter read with PyYAML's `BaseLoader`.
  `node_kinds` is a required non-empty list of unique slugs; `relation_kinds`
  maps each kind to a non-empty list of unique role slugs (no dots, never a
  fixed key); `epistemic` is a list of slugs, and without it records may not
  carry `epistemic`. No kind is both a node kind and a relation kind. The
  non-empty body is the extraction guidance.

Sources are format-agnostic: any UTF-8 text document is read as lines and
never parsed or converted. `kgd sheet SOURCE --json` shows a source's base,
type, kinds, roles, guidance and line count, so extraction reads the owner's
policy before any record exists.

## Check

`kgd check [--base B]... [--fix-lines]` reads files only and prints JSON:

```json
{"errors": [{"path": "/abs/.knowledge/entries/x.md", "rule": "link", "message": "…"}],
 "stale": ["/abs/.knowledge/entries/y.md"],
 "moved": [{"path": "/abs/.knowledge/entries/z.md", "lines": "40-46"}]}
```

`rule` is one of `config`, `type`, `source-type`, `id`, `frontmatter`,
`source`, `kind`, `link`, `body`. The exit code is 1 when any list is
non-empty. `moved` evidence (each quote found exactly once elsewhere in the
source) is repaired by `--fix-lines`, which rewrites only the `lines:` line
under the home lock and adds `fixed` and `skipped` lists. `stale` evidence
needs the quote re-copied from the source by a reviewed edit. Staleness never
hides a record from retrieval.

## Index and restore

`kgd index [--rebuild] [--no-embed]` brings `index.sqlite` up to date with the
`entries/` files of every registered, available base, re-parsing files whose
stat changed, then embeds every row whose vector is NULL with the `embedding`
model. The model loads only when such a row exists. Its JSON report lists per
base `parsed`, `deleted` and `unparseable`, plus `unavailable` bases,
`understanding_changed`, `reused`, `embedded`, `unembedded` and `truncated`
(texts longer than the model's input limit); it exits 1 when a file is
unparseable or a base is unavailable. `--rebuild` re-derives every row in
place and re-uses vectors by text. `--no-embed` skips the embedding phase and
leaves changed rows unembedded. With `embedding` set and the `retrieval` extra
missing, it exits 1 with `install kgdistiller[retrieval] or set embedding to
null` after committing the lexical index.

Restore after a lost or damaged database in one step:

```sh
rm -f "$KGDISTILLER_HOME/index.sqlite" "$KGDISTILLER_HOME/index.sqlite-wal" "$KGDISTILLER_HOME/index.sqlite-shm"
kgd index
```

This re-embeds every record, which is the only slow part of a restore (about
two minutes per 500 records with `BAAI/bge-m3` on Apple silicon); report the
wall time. A missing, unreadable or other-version database is also rebuilt
automatically by `kgd index`. Readers (`search`, `resolve`, `get`,
`neighbors`, `browse`, `pack`, MCP) open it read-only and report `lag`; a
missing database tells them to run `kgd index`.

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
root that is not a registered base. The plugin is desktop only and reads record
frontmatter live from Obsidian's metadata cache.

`kgdistiller codex link` and `kgdistiller claude link` treat installed copies
as product-owned: `doctor` reports a copy that differs from the product source,
and relinking replaces it or removes a retired one, discarding local edits to
installed files. Report a differing copy before relinking.

Installing, linking, committing and pushing are separate authorities. Never
place private sources, records, the home's `config.json` or types, the
database or secrets in a product repository, receipt, command output or agent
configuration.
