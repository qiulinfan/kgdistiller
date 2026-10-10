# Local-first deployment and recovery

kgdistiller has one global home and any number of bases. The home,
`$KGDISTILLER_HOME` (default `~/.knowledge`), registers every base, maps each
base's source documents to user-defined document types, holds those types, and
keeps the derived database and the write lock. A base is a directory, normally
one Obsidian vault, that owns its source documents and its knowledge under
`.knowledge/`; it is the backup unit of its knowledge. The product checkout
owns only engine code, the Obsidian plugin bundle, Skills, presets and workflow
definitions. kgdistiller has no publishing surface; a repository that publishes
its notes owns its website and registries.

The record format and the write path are specified in [model.md](model.md),
the database and reads in [retrieval.md](retrieval.md), the plugin in
[obsidian.md](obsidian.md).

## Layout

```text
$KGDISTILLER_HOME/                 # default ~/.knowledge
├── config.json                    # bases, their source globs, embedding
├── types/<name>.md                # one user-defined document type per file
├── .gitignore                     # "index.sqlite*" and "lock"
├── index.sqlite (-wal, -shm)      # derived database; rebuilt by `kgd index`
└── lock                           # writer lock; its contents are meaningless

BASE_ROOT/
├── notes/                         # source documents, any text format
└── .knowledge/
    ├── entries/<id>.md            # accepted records: nodes and relations
    ├── drafts/<id>.md             # proposed new records, same format
    └── sheets/<source path>.md    # generated def/pending sheets
```

`.knowledge/` is a base's only knowledge root, and the product reads and
writes only these three folders in it. `entries/` is the knowledge. `drafts/`
holds proposals awaiting `kgd accept` or `kgd harvest`, and `sheets/` the
generated views whose draft checkboxes carry the owner's selection. The
database is derived from `config.json` and the `entries/` files; deleting it
loses nothing but the time to rebuild.

## Install and register a base

Install the package as a user-level tool on Windows, macOS or Linux, then
register each knowledge directory once:

```sh
uv tool install git+https://github.com/qiulinfan/kgdistiller.git
uv tool update-shell
kgd base add BASE_ROOT --name research
kgd base list
```

`kgd base add PATH [--name N]` creates the home on first use (`config.json` as
`{"bases": {}, "embedding": null}`, an empty `types/` and the `.gitignore`),
registers the directory and creates `PATH/.knowledge/entries/`. The default
name is the directory's basename; a name must match `^[a-z0-9][a-z0-9-]*$`, so
pass `--name` otherwise. `kgd base rm NAME` removes only the registration,
never touches the directory, and lists under `dangling` the `[[NAME:…]]` links
other bases still hold. `base add` and `base rm` hold the home lock and write
`config.json` atomically.

`kgd base list` prints, for each base:

| Field | Meaning |
|---|---|
| `name`, `path`, `root` | Registered name, stored path and resolved root. |
| `available` | Whether the root exists. |
| `records`, `drafts` | Files in `entries/` and `drafts/` (`null` when unavailable). |
| `indexed` | Rows of this base in the database (0 before the first `kgd index`). |
| `lag` | `changed_files`, `unavailable_bases`, `unembedded`, `embedding_changed` for this base. |

`KGDISTILLER_HOME` is the only environment variable for kgdistiller's own home
and data (the runtime linkers `codex|claude link|doctor` also honor
`CODEX_HOME` and `CLAUDE_CONFIG_DIR`); it must be absolute after `~` expansion.
On Windows, `~` is the current user's profile directory. There is no default
base and no base identity file: to move a base, edit its `path` in
`config.json`. A path argument (`kgd sheet`, `kgd accept`, `kgd harvest`)
belongs to the registered root containing its real path; there is no upward
search for a `.knowledge` directory.

## Home configuration

```json
{
  "bases": {
    "research": {
      "path": "~/research",
      "sources": {
        "notes/**/*.md": "research-notes",
        "papers/*/main.tex": "research-notes"
      }
    }
  },
  "embedding": null
}
```

- The top level has exactly `bases` and `embedding`; each base has exactly
  `path` and `sources`. Keep `embedding` `null`: search runs the lexical and
  name lanes, and the dense lane is a later release.
- `path` is stored as `~/…` (forward slashes) when the root lies under the
  user's home, otherwise as an absolute path.
- No base root may be equal to, an ancestor of or a descendant of another, and
  the home may not lie inside a base root.
- Each `sources` key is a glob relative to the base root, expanded with
  Python's `glob.glob(pattern, root_dir=root, recursive=True)`: `*` stays within
  one path segment, `**` spans directories, and hidden files and directories
  (`.knowledge/`, `.obsidian/`, `.git/`) never match. Globs are non-empty,
  relative, use `/`, and contain no `..` or hidden segment.
- Each value names a type in `types/`. Globs naming two different types for one
  file are an error, so every source has exactly one type.

A type file `types/<name>.md` has a slug stem, YAML frontmatter read with
PyYAML's `BaseLoader` and a non-empty body, the extraction guidance:

```markdown
---
node_kinds: [definition, theorem, concept]
relation_kinds:
  implies: [premise, conclusion]
  example: [uses, setting]
epistemic: [proved, stated]
---
Write the guidance an agent follows when extracting knowledge from these
documents.
```

`node_kinds` is required; `relation_kinds` and `epistemic` are optional. Edit
`sources` and the types by hand; `kgd check` validates the result, and
`kgd sheet SOURCE --json` shows the profile a source resolves to. Sources are
UTF-8 text documents of any format; kgdistiller reads them as numbered lines
and never parses or converts them.

## Check

```sh
kgd check
kgd check --base research --fix-lines
```

`check` validates the home and every record and draft of the selected bases
(all by default; `--base` repeats) and compares each record's Evidence quotes
with its cited lines. It reads files only and prints JSON:

```json
{"errors": [{"path": "/abs/…/entries/x.md", "rule": "link", "message": "…"}],
 "stale": ["/abs/…/entries/y.md"],
 "moved": [{"path": "/abs/…/entries/z.md", "lines": "41-47"}]}
```

The exit code is 1 when any list is non-empty. `rule` is one of `config`,
`type`, `source-type`, `id`, `frontmatter`, `source`, `kind`, `link` and
`body` ([model.md](model.md) lists what each covers).

After a source edit shifts cited lines, `check --fix-lines` rewrites the
`lines:` of each moved record (every quote found exactly once elsewhere in the
source) under the home lock and adds `fixed` and `skipped` lists; a file that
changed during the run is skipped. A stale record (a quote no longer found, or
found several times) needs a reviewed edit of its quotes. Staleness is
reported only; it never hides a record from retrieval.

## Index and restore

```sh
kgd index
kgd index --rebuild
```

`kgd index` brings `$KGDISTILLER_HOME/index.sqlite` up to date with the
`entries/` files of every registered, available base and prints a report with,
per base, `parsed`, `deleted` and `unparseable` files, plus `unavailable` bases
and `understanding_changed`. It exits 1 when a file is unparseable or a base is
unavailable. `--rebuild` re-derives every row in place in one transaction.
`--no-embed` is accepted and changes nothing until the dense lane exists.
Every Skill that writes knowledge ends with `kgd index`; edits made in
Obsidian lag until the next run, and every read reports that lag.

Restore after a lost or damaged database with one command:

```sh
rm -f "$KGDISTILLER_HOME"/index.sqlite*  # optional: kgd index also recreates a damaged file
kgd index
```

A missing, unreadable or other-version database is deleted with its `-wal` and
`-shm` files and rebuilt from the record files and `config.json`.

## Git synchronization

Initialize a private Git repository, commit, add a remote or push only when
the owner explicitly authorizes that action. A base tracks:

- every registered source document and required authored asset;
- `.knowledge/entries/`, `.knowledge/drafts/` and `.knowledge/sheets/`, so
  unharvested drafts and their tick state travel between machines.

The home is separate owner data: keep `config.json`, `types/` and its
`.gitignore` in their own private local Git repository, with no remote by
default; `index.sqlite*` and `lock` are ignored. `config.json` and `types/`
cannot be derived from a base, so on a second machine restore them by hand (or
from that private repository), fix each base `path`, then run `kgd index`.

After an ordinary clone or pull:

```sh
kgd check --base research
kgd index
kgd search "KNOWN NAME" --base research
```

If `check` fails on a clean checkout, restore a known-good revision or repair
the source on its owning machine. `check` proves local consistency, not that a
commit or remote synchronization happened.

## MCP server

`kgd mcp` is a read-only stdio server over the whole home with the tools
`kg_search`, `kg_resolve` and `kg_get`. It takes no arguments, opens a fresh
read-only connection for every call and reports lag like the CLI. Register it
in an agent runtime as the command `kgd` with the argument `mcp`.

## Obsidian plugin

```sh
kgd obsidian install --base research
kgd obsidian install --base research --replace
```

The installer copies `main.js`, `manifest.json` and `styles.css` into
`<root>/.obsidian/plugins/kgdistiller/`, preserves `data.json`, and enables the
plugin unless `--no-enable` is given; use `--replace` for an upgrade. Open the
base root as the vault and reload Obsidian. Then turn on **Index hidden
knowledge folder** in the plugin settings (desktop only): the plugin builds its
graph live from the metadata cache of `.knowledge/entries/` and
`.knowledge/drafts/`, so until hidden indexing is on the view is empty. There
is no export step. Keep Obsidian's new-link format unset (shortest) or
`absolute`.

## Agent runtime integration

`kgdistiller codex link` and `kgdistiller claude link` install the Skills,
agent presets and product root declared by the workflow manifests; the matching
`doctor` command verifies them. Installed copies are product-owned: `doctor`
reports a copy that differs from the product source, and relinking replaces it
and deletes retired copies, discarding any local edits to the installed files.
Change the product checkout, not the installed copy. The `deploy-kgdistiller`
Skill walks an agent through this whole document.

## Deployment receipt

Record the home path, each base's name, root and `base list` counts and lag,
the installed kgdistiller version and exact product commit when known, the
`check` result, the `index` report summary, the plugin path when installed,
both doctors' status, and Git commit or remote state only when actually
confirmed. Never include full source or record content, credentials or
unbounded excerpts.
