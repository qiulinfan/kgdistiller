<div align="center">

<h1>kgdistiller</h1>
<p><strong>A local research knowledge base of source-backed records in your Obsidian vaults.</strong></p>

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776ab)](pyproject.toml)
[![Obsidian desktop 1.13.7+](https://img.shields.io/badge/Obsidian%20desktop-1.13.7%2B-7c3aed)](manifest.json)
[![Plugin release](https://img.shields.io/github/v/release/qiulinfan/kgdistiller?label=Obsidian%20plugin)](https://github.com/qiulinfan/kgdistiller/releases)
[![CI](https://github.com/qiulinfan/kgdistiller/actions/workflows/ci.yml/badge.svg)](https://github.com/qiulinfan/kgdistiller/actions/workflows/ci.yml)

**English** · [简体中文](README_zh-CN.md)

</div>

## Highlights

- Keep definitions, results and the relations between them as Markdown records
  next to the papers and notes they cite, each with a verbatim quote and a line
  range.
- Bind a relation to any number of participants by role, using kinds and roles
  you declare per document type.
- Search every registered vault at once through one SQLite index; `kgd search`
  fuses full-text, dense-embedding (for example BGE-M3) and name matches.
- Review and edit records in Obsidian, with a typed graph view, and let coding
  agents capture, compile and query them through Skills and an MCP server.

This repository holds the Obsidian plugin (**0.1.5**) and the Python core
(**0.4.0**). They are versioned and tagged separately: plugin tags are bare
`x.y.z`, core tags are `core-x.y.z`.

## Obsidian plugin

Open the [kgdistiller community listing](https://community.obsidian.md/plugins/kgdistiller),
choose **Add to Obsidian**, then enable **kgdistiller**. For a manual install,
download `main.js`, `manifest.json` and `styles.css` from the
[plugin release](https://github.com/qiulinfan/kgdistiller/releases) into
`<vault>/.obsidian/plugins/kgdistiller/`, reload Obsidian and enable the plugin.
Obsidian **1.13.7 or newer** on desktop is required; the plugin does not load
on mobile.

This README describes plugin 0.1.5 or newer. Earlier releases read an older
export format and show none of the records below. If the community listing or
the release page still offers an earlier version, install with
`kgd obsidian install` instead, which copies the bundled 0.1.5.

Records live in the vault's hidden `.knowledge/` folder, which Obsidian skips
by default. Turn on **Settings → kgdistiller → Index hidden knowledge folder**
so that `.knowledge/` appears in the file explorer, search, Properties and
Backlinks like any other folder. `kgd obsidian install` (below) installs the
plugin from the Python package and turns this setting on for you.

Run **kgdistiller: Open typed graph** to open the graph in the right sidebar.
It draws nodes, role-bound relations, drafts and pending terms, either around
the active record, source or sheet (depth 1 or 2) or as the full graph, with
filters for kind, class, understanding and source path. The details pane shows
a record's roles, body and evidence, with buttons to open the record, its source
at the cited line, and the source's sheet. The view updates as you edit records
in Obsidian. Use the Properties panel to edit them, then run `kgd check` and
`kgd index`; the plugin itself never writes a knowledge file. The full
description is in [docs/obsidian.md](docs/obsidian.md).

## Privacy and data boundaries

The Obsidian plugin reads the frontmatter of `.knowledge/entries/` and
`.knowledge/drafts/` from Obsidian's metadata cache, and reads a record file
when you select it. It makes no network requests, collects no telemetry, needs
no account, reads no file outside the vault and installs or updates nothing.
Its only write is its own `data.json`. Hidden-folder indexing patches internal,
undocumented Obsidian file-adapter methods and uses Node's file system API to
check paths under the vault's `.knowledge/` folder, which is why the plugin is
desktop only. It also exposes `.knowledge/` to Obsidian search and to other
plugins in that vault. If the Hidden Folders Access plugin is enabled,
kgdistiller reports the conflict and leaves its own indexing off.

The CLI and MCP server run locally. They read the home's `config.json` and
`types/`, the records and drafts, and the registered source files (to check
evidence and for `kgd get --source-lines`). They write only:

- the home: `config.json` through `kgd base add`/`rm`, the `types/` folder and
  `.gitignore` that the first `kgd base add` creates, `index.sqlite*` and
  `lock`;
- the `entries/`, `drafts/` and `sheets/` folders under a base's `.knowledge/`;
- the plugin folder, `community-plugins.json` and the plugin's `data.json` in
  the vault's `.obsidian/`, through `kgd obsidian install`;
- the agent runtime homes, through `kgd claude link` and `kgd codex link`.

The MCP server speaks stdio only and has no write tools.

The only network access is sentence-transformers downloading the embedding
model into the Hugging Face cache on first use (several GB for `BAAI/bge-m3`).
No token is sent and no remote code runs. After that download, set
`HF_HUB_OFFLINE=1` so every load stays local. Nothing is committed or pushed
for you: keep each vault and the home in your own Git repositories.

## License

Original kgdistiller code is [MIT](LICENSE), copyright 2026 Qiulin Fan. It
permits commercial use, modification and redistribution with the copyright and
permission notices retained. The plugin bundle includes Cytoscape.js,
cytoscape-fcose, cose-base and layout-base, all MIT, and its hidden-folder
indexing is adapted from Hidden Folders Access 2.1.1 (MIT). Their full notices
are embedded in the bundle and listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Core concepts

kgdistiller is a knowledge base for academic research: papers you read, papers
you write and your own research notes. Each registered directory, usually one
Obsidian vault, is a base. A base keeps its knowledge as Markdown records
in `.knowledge/entries/`, proposals in `.knowledge/drafts/` and generated
review sheets in `.knowledge/sheets/`. A record is either a node (a concept or
a precisely stated result) or a relation whose role keys bind other records.
Every record cites a source file of its base by path, line range and verbatim
quotes. Sources are plain text in any format; kgdistiller reads them as
numbered lines and never parses their syntax.

The global home, `$KGDISTILLER_HOME` (default `~/.knowledge`), registers the
bases, maps source globs to the document types you define, and keeps the
derived database `index.sqlite`:

```text
~/.knowledge/                      # $KGDISTILLER_HOME
├── config.json                    # bases, their source globs, embedding model
├── types/<name>.md                # one document type per file
├── .gitignore                     # "index.sqlite*" and "lock"
├── index.sqlite                   # derived; rebuilt by `kgd index`
└── lock

~/research/                        # a base, usually an Obsidian vault
├── notes/                         # sources: any UTF-8 text
└── .knowledge/
    ├── entries/<id>.md            # accepted records
    ├── drafts/<id>.md             # proposed records, same format
    └── sheets/<source path>.md    # generated def/pending sheets
```

The database holds FTS5 full-text rows and float32 vectors, searched by an
exact NumPy scan. `kgd search` fuses three lanes (lexical, dense and name
matches on labels and aliases) by reciprocal rank. Every read reports `lag`
when record files changed after the last `kgd index`. The record files are the
knowledge; the database can always be rebuilt from them.

## Install

Building the package needs Node 22 and npm, because the bundled plugin's
`main.js` is built from source and is not tracked. A direct
`uv tool install git+https://…` therefore fails; install from a checkout:

```sh
git clone https://github.com/qiulinfan/kgdistiller.git
cd kgdistiller
npm run build
uv tool install '.[retrieval]'
uv tool update-shell
```

This creates the `kgd` and `kgdistiller` commands (the same program) on
Windows, macOS and Linux; restart the shell if `kgd` is not yet on `PATH`. The
`retrieval` extra adds sentence-transformers and NumPy for the dense lane.
Without it (`uv tool install .`), keep `"embedding": null` in the home config
and search uses only the lexical and name lanes.

## Quick start

Register a base. The first `base add` creates the home:

```sh
kgd base add ~/research --name research
```

Define a document type in `~/.knowledge/types/research-notes.md`. Its
frontmatter lists the node kinds and the relation kinds with their ordered
roles; its body is the guidance an agent follows when extracting records from
documents of this type. kgdistiller ships no types.

```markdown
---
node_kinds: [definition, theorem]
relation_kinds:
  requires-for: [prerequisite, dependent]
  example: [uses, setting]
epistemic: [proved, stated]
---
Extract each definition and each precisely stated result as a node. Record a
relation only when the text states it. Unexplained terms become pending values.
```

Map the base's sources to that type and choose an embedding model in
`~/.knowledge/config.json` (use `null` for no dense lane):

```json
{
  "bases": {
    "research": {
      "path": "~/research",
      "sources": {
        "notes/**/*.md": "research-notes"
      }
    }
  },
  "embedding": "BAAI/bge-m3"
}
```

Then add records. Ask an agent with the Skills installed to capture a
definition or compile a whole note, or write a draft by hand in
`~/research/.knowledge/drafts/` (see [Record format](#record-format)) and
accept it:

```sh
cd ~/research
kgd sheet notes/measure.md --json
kgd check
kgd accept .knowledge/drafts/sigma-algebra.md
kgd index
kgd search "sigma algebra"
```

`kgd sheet --json` prints the type profile and the records a source already
has. `kgd check` validates the home, records and drafts, and reports evidence
that moved or no longer matches its source. `kgd accept` moves drafts into
`entries/`, all or nothing. `kgd index` updates the database, and its first
dense run downloads the model; set `HF_HUB_OFFLINE=1` afterwards. Every
command prints JSON and exits 0 on success, 1 on findings or a refusal, and 2
on a usage error.

## Record format

A record is one file whose stem is its id: a readable slug of at most 80
characters, CJK allowed. Its global uid is `<base>:<id>`. A node:

```markdown
---
label: Sigma-algebra
kind: definition
aliases:
  - σ-algebra
source: notes/measure.md
lines: 3-4
requires:
  - "set"
---
A sigma-algebra on a set X is a collection of subsets of X closed under complements and countable unions.

## Evidence

> A sigma-algebra on a set X is a collection of subsets of X that contains X
> and is closed under complements and countable unions.
```

A relation of the kind `requires-for`, whose roles come from the document
type:

```markdown
---
label: Sigma-algebra is required for measure space
kind: requires-for
prerequisite:
  - "[[sigma-algebra]]"
dependent:
  - "[[measure-space]]"
epistemic: stated
source: notes/measure.md
lines: 6-7
---
A measure space is defined over a sigma-algebra.

## Evidence

> F is a sigma-algebra on X
```

- `label`, `kind`, `source` (path relative to the base root) and `lines`
  (`a` or `a-b`) are required. `aliases`, `understanding` (`unknown`,
  `not-yet-understood` or `understood`), `epistemic` and `requires` are
  optional; `tags` and `cssclasses` are allowed and ignored.
- Every other key is a role. A record with at least one non-empty role list is
  a relation; otherwise it is a node. A relation may have any number of roles.
- A list value is a link (`"[[id]]"`, `"[[base:id]]"` or
  `"[[.knowledge/entries/id]]"`) or a plain term. A plain term is a pending
  gap: the source uses it without explaining it. `set` above is one.
- The body is prose, an optional `## Search terms` section, then a final
  `## Evidence` section of verbatim quotes from the cited lines.
- Examples and applications are relations of a kind you register, such as
  `example: [uses, setting]`.

Edit accepted records in place, then run `kgd check` and `kgd index`. When a
source edit shifts the cited lines, `kgd check --fix-lines` rewrites the
`lines:` of each record whose quotes it finds exactly once elsewhere in the
source. [docs/model.md](docs/model.md) has the full grammar and validation
rules.

## Commands

| Command | Purpose |
|---|---|
| `kgd base add PATH [--name N]`, `base rm NAME`, `base list` | Register, unregister and list bases with record, draft and index counts. |
| `kgd check [--base B] [--fix-lines]` | Validate the home, records and drafts; report moved or stale evidence. |
| `kgd sheet SOURCE [--json]` | Write a source's sheet of records, pending terms and drafts, or print its profile. |
| `kgd accept [--dry-run] DRAFT...` | Promote drafts to accepted records, all or nothing. |
| `kgd harvest [--dry-run] SHEET` | Accept the ticked drafts of a sheet and regenerate it. |
| `kgd index [--rebuild] [--no-embed]` | Bring the database up to date with the record files. |
| `kgd search QUERY [--limit N] [--no-dense]` | Rank records by the lexical, dense and name lanes. |
| `kgd resolve TERM...` | List the senses, mentions and pending uses of terms. |
| `kgd get UID... [--source-lines N]` | Read complete records with their links and, optionally, the live cited source lines. |
| `kgd neighbors UID... [--role R] [--dir out\|in\|both] [--depth N]` | Follow links from records: dependency and claim closures. |
| `kgd browse [HANDLE]` | List bases, a base's source directories, a source's records by kind, or every record of a `--kind`. |
| `kgd pack UID... [--budget BYTES] [--requires-depth N]` | Pack whole records within a byte budget, with shared relations and the gaps left open. |
| `kgd obsidian install [--base NAME]` | Install or update the plugin in a base's vault and enable it with hidden-folder indexing. |
| `kgd claude link\|doctor`, `kgd codex link\|doctor` | Install or verify the Skills, agent preset and workflow files for Claude Code or Codex. |
| `kgd mcp` | Serve the read tools over stdio for every base. |

`search`, `resolve`, `neighbors`, `browse` and `pack` take the filters
`--base`, `--kind`, `--class {node,relation}`, `--source PREFIX` and
`--understanding`, each repeatable. A bare id works where it is unique across
bases. Run `kgd <command> --help` for details and
[docs/retrieval.md](docs/retrieval.md) for the output shapes.

## Sheets and harvest

A sheet is the review page for one source. `kgd sheet notes/measure.md` writes
`.knowledge/sheets/notes/measure.md.md`, which lists the source's accepted
records by kind, its open pending terms, and its drafts as checkboxes:

```markdown
## Drafts
- [ ] [[.knowledge/drafts/measure-space|Measure space]] · definition · L6-7 — A measure space is a triple …
```

Open the sheet in Obsidian, review or edit the drafts, and tick the ones to
accept. A tick means "accept this draft"; it says nothing about whether you
understand it. Then:

```sh
kgd harvest .knowledge/sheets/notes/measure.md.md
kgd index
```

Harvest accepts exactly the ticked drafts in one step and regenerates the
sheet. If a ticked relation links a draft you did not tick, it refuses and
changes nothing, with a message such as `select [[measure-space]] too`. To
reject a draft, delete its file.

## Agent integration

Five Skills cover the write and read workflows:

| Skill | What it does |
|---|---|
| `capture-kgdistiller` | Saves or updates one record while you read: a node, a relation or an example, with its quote and pending terms. |
| `compile-knowledge-sheets` | Extracts drafts from part or all of a registered source, following its document type, and writes the sheet. It never changes accepted records. |
| `harvest-kgdistiller` | Runs `kgd harvest` on the drafts you ticked, then `kgd index`. |
| `query-kgdistiller` | Answers from the base with search, resolve, get, neighbors, browse and pack, citing `source:lines`. Read-only, with the `kgdistiller-query-reviewer` agent preset. |
| `deploy-kgdistiller` | Installs, registers bases, sources, types and the model, then checks, indexes, installs the plugin and links the runtimes. |

Every Skill that writes knowledge ends with `kgd index`. Install them for your
runtime:

```sh
kgd claude link
kgd claude doctor
```

```sh
kgd codex link
kgd codex doctor
```

`link` installs the Skills, the `kgdistiller-query-reviewer` preset and the
workflow manifest under `~/.claude` or `~/.codex` (honoring `CLAUDE_CONFIG_DIR`
and `CODEX_HOME`) and never edits `CLAUDE.md`, `settings.json`, `AGENTS.md` or
`config.toml`. OpenCode and OMP get the Skills only, from the checkout:
`./scripts/link-skills.sh opencode` or `./scripts/link-skills.sh omp`
(PowerShell 7: `./scripts/link-skills.ps1 -Runtime opencode`).

The MCP server exposes `kg_search`, `kg_resolve`, `kg_get`, `kg_neighbors`,
`kg_browse` and `kg_pack`, with the same JSON as the CLI. It keeps the model
loaded between calls, so only the first dense search pays the load time.
Register it as the command `kgd mcp`:

```sh
claude mcp add kgdistiller -s user -e HF_HUB_OFFLINE=1 -- kgd mcp
codex mcp add kgdistiller --env HF_HUB_OFFLINE=1 -- kgd mcp
```

OMP loads stdio MCP servers natively from `.mcp.json`, `.omp/mcp.json` or
`~/.omp/agent/mcp.json`; `/mcp add kgdistiller -- kgd mcp` inside OMP writes
the entry. For `omp -p`, set `OMP_MCP_REQUIRE_READY=1` so the first turn waits
for the server. [docs/deployment.md](docs/deployment.md#mcp-server) has the
details.

## Agent install recipe

When asked to install kgdistiller from this repository, an agent should:

1. Clone it, run `npm run build`, then `uv tool install '.[retrieval]'` and
   `uv tool update-shell`, and confirm `kgd --help` runs.
2. Link its own runtime: `kgd claude link` then `kgd claude doctor`, or
   `kgd codex link` then `kgd codex doctor`; for OpenCode or OMP,
   `./scripts/link-skills.sh opencode|omp`. Start a new session afterwards.
3. Follow the `deploy-kgdistiller` Skill: `kgd base add` for each vault, write
   the source globs, document types and `embedding` into
   `~/.knowledge/config.json` and `~/.knowledge/types/`, then run `kgd check`,
   `kgd index` and, for a vault opened in Obsidian, `kgd obsidian install`.
4. Smoke-test with `kgd base list` and `kgd search "KNOWN NAME" --base NAME`.

After that, requests such as "capture this definition into my kgd knowledge
base" or "search my knowledge base for measure spaces" reach the matching
Skill.

## Backup and restore

Commit each base's sources together with `.knowledge/entries/`, `drafts/` and
`sheets/`. Keep the home's `config.json`, `types/` and `.gitignore` in a
separate private repository; `index.sqlite*` and `lock` are ignored. On a new
machine, restore those files, fix each base's `path`, and rebuild:

```sh
kgd check
kgd index
```

`kgd index` recreates a missing or damaged database from the record files and
re-embeds every record. Deleting the database first
(`rm -f ~/.knowledge/index.sqlite*`) is optional. With `BAAI/bge-m3` on Apple
silicon, restoring a 535-record base took 115 s.
`kgd index --rebuild` re-derives every row without re-embedding.

## Development

```sh
npm run build
uv run --locked python -m unittest discover -s tests -v
uv run --locked ruff check src tests scripts
uv build --out-dir build/release/0.4.0
uv run --locked python scripts/check_distribution.py --dist-root build/release/0.4.0
npm test
(cd integrations/obsidian && npm ci && npm run check)
```

Root `npm run build` comes first: every `uv run` and `uv build` in a fresh
checkout needs the built plugin. Tests use a temporary `KGDISTILLER_HOME` and a
fake encoder. [docs/release.md](docs/release.md) lists the release gates,
including the opt-in real-model test.

Further reading: [docs/model.md](docs/model.md) (records and the write path),
[docs/retrieval.md](docs/retrieval.md) (database, search and MCP tools),
[docs/obsidian.md](docs/obsidian.md) (the plugin),
[docs/deployment.md](docs/deployment.md) (install, configuration, restore) and
[docs/product-workflows.md](docs/product-workflows.md) (Skills and workflows).
