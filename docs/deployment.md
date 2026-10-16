# Local-first deployment and recovery

kgdistiller has one global home and any number of bases. The home,
`$KGDISTILLER_HOME` (default `~/.knowledge`), registers every base, maps each
base's source documents to user-defined document types, holds those types, and
keeps the derived database and the write lock. A base is a directory, normally
one Obsidian vault, that owns its source documents and its knowledge under
`.knowledge/`; it is the backup unit of its knowledge. The product checkout
owns engine code, the Obsidian plugin bundle, Skills, presets, workflow
definitions and optional document-type starter templates. kgdistiller has no
publishing surface; a repository that publishes its notes owns its website
and registries.

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
    └── drafts/<id>.md             # proposed records, new or replacements, same format
```

`.knowledge/` is a base's only knowledge root, and the product reads and
writes only these two folders in it. `entries/` is the knowledge. `drafts/`
holds the proposals awaiting the owner's `kgd accept`. The
database is derived from `config.json`, the `entries/` files and the embedding
model; deleting it loses nothing but the time to rebuild and re-embed.

## Install and register a base

Install the package as a user-level tool on Windows, macOS or Linux, with the
`retrieval` extra for the dense search lane, then register each knowledge
directory once. The package bundles the Obsidian plugin, whose built
`integrations/obsidian/main.js` is not tracked in git, so a checkout needs
Node 22 and npm to build it before any `uv` command builds the package; a
direct `git+https` install therefore fails.

```sh
git clone https://github.com/qiulinfan/kgdistiller.git
cd kgdistiller
npm run build                     # builds integrations/obsidian/main.js
uv tool install '.[retrieval]'    # or --editable '.[retrieval]'
uv tool update-shell
kgd base add BASE_ROOT --name research
kgd base list
```

The extra brings NumPy, PyTorch, transformers and sentencepiece; without it,
keep `embedding` `null`.

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
`config.json`. A path argument (`kgd source`, `kgd accept`) belongs to the registered root containing its real path; there is no upward
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
  "embedding": "BAAI/bge-m3"
}
```

- The top level has exactly `bases` and `embedding`; each base has exactly
  `path` and `sources`.
- `embedding` is a Hugging Face id of an XLM-RoBERTa sentence encoder that ships `sentencepiece.bpe.model`, such as `BAAI/bge-m3`, or `null`. The recommended
  value is `"BAAI/bge-m3"`: a model id only, with no revision pin, and the
  device chosen automatically. It runs with int8 weights in about 1 GB
  ([retrieval.md](retrieval.md#the-embedding-process)). `null` means search runs only the lexical and
  name lanes. After a change, the next index run of any kind drops the old
  vectors, and the next plain `kgd index` embeds every record. Change it with `kgd embedding` rather than by hand (below).
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

### Changing the embedding model

`kgd embedding` shows the model, the precision, what the stored vectors were
computed with and how many records await a re-embed; `kgd embedding set
MODEL` downloads the model into the Hugging Face cache, checks that
kgdistiller can run it (its configuration first, so another architecture is
refused before its weights download) and then writes it into `config.json`
under the home lock; `kgd embedding off` writes `null`. Neither re-embeds:
the next index run of any kind drops the vectors of the old model, and the
next plain `kgd index` embeds every record with the new model.

```sh
kgd embedding
kgd embedding set BAAI/bge-m3
kgd embedding off
```

The Obsidian plugin's **Embedding** settings run the same commands.

### Model cache

`kgd embedding set` downloads the model's weights into the Hugging Face cache
(`~/.cache/huggingface`, or `$HF_HOME`), about 2.3 GB for `BAAI/bge-m3`.
Later loads may still contact the Hugging Face Hub unless
`HF_HUB_OFFLINE=1` is set; after the first download, set it in the environment
of `kgd` and the MCP server so every load stays local. No token is sent and no remote code runs. Offline loading by
model id needs a complete cache entry: the snapshot and the
`refs/main` pointer that an online load writes. A cache copied without
`refs/` fails offline with "couldn't find them in the cached files"; one online
load (or restoring `refs/main` with the snapshot's directory name) fixes it.
Hugging Face progress bars are off by default; set
`HF_HUB_DISABLE_PROGRESS_BARS=0` to watch the first download.

### Document types

The owner defines which knowledge to retain from each kind of source. Capture
and compilation read the assigned profile before extraction; file extensions
do not select a policy. The engine validates the declared kinds and roles.

Optional editable profiles are supplied in
`templates/document-types/{math-notes,ai-research,cs-notes}.md`, also bundled
under `kgdistiller/product/templates/document-types/` in the installed package.
Copy a selected profile into the home's `types/`, review and edit it, and map
the relevant source globs to its file stem. Neither installation nor
`kgd base add` activates these templates or overwrites the owner's profiles.

A type file `types/<name>.md` has a slug stem, YAML frontmatter read with
PyYAML's `BaseLoader` and a non-empty body, the extraction guidance:

```markdown
---
node_kinds:
  definition: []
  concept: []
  theorem: [about]
  example: [uses, setting]
relation_kinds:
  implies: [premise, conclusion]
epistemic: [proved, stated]
---
Write the guidance an agent follows when extracting knowledge from these
documents.
```

`node_kinds` is required; `relation_kinds` and `epistemic` are optional. Both
kind sections map each kind to its ordered roles: a node kind may declare none
(`[]`), or roles linking what it is about, and then each of its records links
at least one record there; a relation kind declares at least one, and each of
its records binds at least two values. A kind's section
decides its records' class, so moving a kind between the sections reclassifies
them without re-indexing. Edit
`sources` and the types by hand; `kgd check` validates the result, and
`kgd source SOURCE` shows the profile a source resolves to. Sources are
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
`type`, `source-type`, `id`, `frontmatter`, `source`, `kind`, `link`,
`label` and `body` ([model.md](model.md) lists what each covers).

After a source edit shifts cited lines, `check --fix-lines` rewrites the
`lines:` of each moved record (every quote found exactly once elsewhere in the
source) under the home lock and adds `fixed` and `skipped` lists; a file that
changed during the run is skipped. A stale record (a quote no longer found, or
found several times) is moved to the passage that now states it with the
relocate Skill (`kgd relocate`); when its meaning changed, it gets a
replacement draft. Staleness is reported only; it never hides a record from
retrieval.

## Index and restore

```sh
kgd index
kgd index --rebuild
```

`kgd index` brings `$KGDISTILLER_HOME/index.sqlite` up to date with the
`entries/` files of every registered, available base and prints a report with,
per base, `parsed`, `deleted` and `unparseable` files, plus `unavailable`
bases. It exits 1 when a file is unparseable, a base is
unavailable or embedding failed. With `embedding` set, an embedding phase
follows: the rows whose vector is NULL (new or changed text, or every row
after a model change) are encoded in batches of 64. The model loads only when such a row exists, so an
up-to-date index runs in well under a second. The report adds `reused`
(vectors kept by text, such as an id rename), `embedded`, `unembedded` and
`truncated` (texts longer than the model's input limit, embedded from their
leading part and listed rather than cut silently) and `embedding_error`
(`null`, or why the embedding phase failed). `--no-embed` skips the
embedding phase and leaves changed rows unembedded until the next run.
`kgd accept` runs the index itself and embeds only the records it creates or
replaces; an edited record is re-embedded only by a plain
`kgd index` the owner runs.
`--rebuild` re-derives every row in place in one transaction and re-uses the
existing vectors by text, so it loads no model. If `embedding` is set but the
`retrieval` extra is missing, `kgd index` commits the lexical phase, reports
`embedding_error` ending in ``install kgdistiller[retrieval] or run
`kgd embedding off` `` and exits 1. The Obsidian plugin runs
`kgd index --no-embed` when the owner saves a record; edits made elsewhere lag
until the next index run, and every read reports that lag.

Restore after a lost or damaged database with one command:

```sh
rm -f "$KGDISTILLER_HOME"/index.sqlite*  # optional: kgd index also recreates a damaged file
kgd index
```

A missing, unreadable or other-version database is deleted with its `-wal` and
`-shm` files and rebuilt from the record files and `config.json`, and every
record is re-embedded, at about 14 records a second with `BAAI/bge-m3` on
Apple silicon. `kgd index --rebuild` re-uses the stored vectors by text and
loads no model.

## Git synchronization

Initialize a private Git repository, commit, add a remote or push only when
the owner explicitly authorizes that action. A base tracks:

- every registered source document and required authored asset;
- `.knowledge/entries/` and `.knowledge/drafts/`, so drafts awaiting the owner
  travel between machines.

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
`kg_search`, `kg_resolve`, `kg_get`, `kg_neighbors`, `kg_browse`, `kg_pack`,
`kg_drafts` and `kg_find` ([retrieval.md](retrieval.md#mcp-server-kgd-mcp)).
It takes no arguments, opens a fresh read-only connection for every retrieval
call and reports lag like the CLI; `kg_drafts` reads the draft files and
`kg_find` the registered sources. It loads no
model: `kg_search`'s dense lane asks the user's one embedding process, which
every `kgd` command shares; `kg_search` takes `no_dense` to skip the dense
lane. Register it in an agent runtime as the command `kgd` with the argument `mcp`,
with `HF_HUB_OFFLINE=1` in its environment once the model is cached.

OMP loads MCP stdio servers natively. It reads a project `.mcp.json` or `mcp.json`, a project `.omp/mcp.json`, or the
user-level `~/.omp/agent/mcp.json`, and `/mcp add kgdistiller -- kgd mcp`
writes such an entry:

```json
{"mcpServers": {"kgdistiller": {"command": "kgd", "args": ["mcp"],
                                "env": {"HF_HUB_OFFLINE": "1"}}}}
```

Add `"KGDISTILLER_HOME"` to `env` to serve a scratch home instead of the
default one. The tools then appear as `mcp__kgdistiller_kg_search`,
`mcp__kgdistiller_kg_pack` and so on. OMP waits only 250 ms for MCP servers by
default, so for print-mode runs (`omp -p`) set `OMP_MCP_REQUIRE_READY=1`, or
`mcp.startupTimeoutMs: 0` in a run-local configuration, so that the first turn
waits for the server. OMP saves tool output above `tools.artifactSpillThreshold`
(in KB, default 50) as an artifact and keeps only its tail inline; raise it
above the largest `pack` budget a run uses so packets reach the model whole.

## Obsidian plugin

First open the base root as a vault in Obsidian once, so that its `.obsidian`
directory exists. Then install the plugin and reload Obsidian:

```sh
kgd obsidian install --base research
```

Without `--base`, the command uses the base whose root contains the working
directory. It refuses a root that is not a registered base; run `kgd base add`
first. The same command installs, updates and re-checks the plugin:

- it copies `main.js`, `manifest.json` and `styles.css` into
  `<root>/.obsidian/plugins/kgdistiller/`, replacing an older bundle;
- it adds `kgdistiller` to `.obsidian/community-plugins.json`, which enables
  the plugin;
- it sets `hiddenKnowledgeEnabled` to `true` in the plugin's `data.json` and
  keeps every other key there, so Obsidian indexes `.knowledge/`;
- it warns when `.obsidian/app.json` sets `newLinkFormat` to `relative`. That
  format writes `../` links, which the record link grammar rejects; keep the
  setting unset (shortest) or `absolute`.

The plugin is desktop only. It reads knowledge and drafts only through a
resident `kgd mcp` and never parses records itself
([obsidian.md](obsidian.md)).

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
`check` result, the `embedding` model id, the `index` report summary with its
`reused`, `embedded`, `unembedded` and `truncated` counts and the wall time of
a full embed, the plugin path, version and `hidden_indexing` result when
installed together with any `newLinkFormat` warning, both doctors' status, and Git
commit or remote state only when actually confirmed. Never include full source
or record content, credentials or unbounded excerpts.
