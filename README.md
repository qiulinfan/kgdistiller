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
- Look knowledge up from a selection in Obsidian, see a record's typed
  neighbourhood, capture drafts with a built-in agent and accept them in a
  Capture view; coding agents capture, compile and query through Skills and
  an MCP server.

This repository holds the Obsidian plugin and the Python core, both at
**1.1.0**. They are versioned and tagged separately: plugin tags are bare
`x.y.z`, core tags are `core-x.y.z`.

## Why the knowledge graph comes first

> 我们这个 research oriented 的知识库，我之所以把知识图谱作为其最根本的核心，本质上是我认为：
>
> 一篇知识密集、研究性的知识源并不是每段文字都是关键，有很多东西是规范性目的或者“向读者证明我这个研究/架构有意义”。但是，它们进入我的眼睛的时候，我早就确认了它们是有意义的，因而我们真正需要的知识仅仅只是这个文章的一些知识节点而已。
>
> 这个 philosophy 是这个图谱的关键，因而检索“到底在哪篇文章的哪个段落”是完全依赖知识图谱的，向量和 bm25 检索也仅仅针对每个知识节点。

An agent can first find a record, then inspect its original context:

```sh
kgd get research:sigma-algebra --source-lines 20
```

This reads the live cited range with 20 lines on either side. For more context,
the agent uses the base root from `kgd base list` and the record's relative
`source` path to read the original file. Context stays in the source; the
record's base, path, lines and evidence already provide the connection.

## Obsidian plugin

Open the [kgdistiller community listing](https://community.obsidian.md/plugins/kgdistiller),
choose **Add to Obsidian**, then enable **kgdistiller**. For a manual install,
download `main.js`, `manifest.json` and `styles.css` from the
[plugin release](https://github.com/qiulinfan/kgdistiller/releases) into
`<vault>/.obsidian/plugins/kgdistiller/`, reload Obsidian and enable the plugin.
Obsidian **1.13.7 or newer** on desktop is required; the plugin does not load
on mobile.

Records live in the vault's hidden `.knowledge/` folder, which Obsidian skips
by default. Turn on **Settings → kgdistiller → Index hidden knowledge folder**
so that `.knowledge/` appears in the file explorer, search, Properties and
Backlinks like any other folder. With the CLI from [Install](#install),
`kgd obsidian install` copies the plugin bundled with the CLI into the vault
and turns this setting on in one step.
[docs/obsidian.md](docs/obsidian.md#installation) lists what it changes.

With the path of `kgd` set in **Settings → kgdistiller → kgd executable**:

- **Lookup sidebar.** Select text and hold ⌘ (Ctrl elsewhere) for 0.3 s, run
  **Look up selection**, or type into the sidebar's search box (the ribbon
  icon **Open kgdistiller** opens it). It looks the text up in every
  registered base and shows the records of the same name, the record in
  focus with a typed graph of its
  neighbours within 3 steps, and the related records. Opening a record or a
  source in the editor focuses it there too. **Re-embed** below the search box
  is solid, with a count, while records await embedding.
- **Agent** (optional). With a model provider configured under **Model
  providers**, the sidebar judges whether the selection is already recorded,
  answers questions from the knowledge base and from papers and the web,
  finds where a term comes from (**Find sources**) and captures a selection
  as a draft (**Capture**).
- **Capture view.** Drafts wait in the **kgdistiller Capture** view below the
  sidebar, each shown in full with whether `kgd accept` would take it.
  **Accept** admits a draft with the drafts it links, indexes and embeds it;
  **Open draft** opens the file for editing; **Discard** deletes it after
  asking. Nothing enters the knowledge base until you accept it.
- **Embedding settings.** **Settings → kgdistiller → Embedding** shows the
  home's embedding model and changes it through `kgd embedding`.

Edit records with the Properties panel; the keyword index follows saved
record files by itself, and an edited record is re-embedded when you press
Re-embed. The full description is in [docs/obsidian.md](docs/obsidian.md).

## Privacy and data boundaries

The Obsidian plugin reads knowledge through the `kgd` engine you configure
and never opens the home or the database itself. It writes its own
`data.json` (API keys stay in Obsidian's SecretStorage; `data.json` holds only
their names), and, when you capture, accept or discard, drafts under
`.knowledge/drafts/` and the commands `kgd accept` and `kgd index` run for
you. It collects no telemetry, needs no account and installs or updates
nothing. Lookup without the agent makes no network request. The agent sends
the selection, its context and the records it reads to the model provider you
choose; web and paper searches go to Exa (`mcp.exa.ai`); **Find sources**
also reads Wikipedia and arXiv. Hidden-folder indexing patches internal,
undocumented Obsidian file-adapter methods and uses Node's file system API to
check paths under the vault's `.knowledge/` folder, which is why the plugin is
desktop only. It also exposes `.knowledge/` to Obsidian search and to other
plugins in that vault. If the Hidden Folders Access plugin is enabled,
kgdistiller reports the conflict and leaves its own indexing off.

The CLI and MCP server run locally. They read the home's `config.json` and
`types/`, the records and drafts, and the registered source files (to check
evidence and for `kgd get --source-lines`). They write only:

- the home: `config.json` through `kgd base add`/`rm` and `kgd embedding`, the `types/` folder and
  `.gitignore` that the first `kgd base add` creates, `index.sqlite*` and
  `lock`;
- the `entries/` and `drafts/` folders under a base's `.knowledge/`;
- the plugin folder, `community-plugins.json` and the plugin's `data.json` in
  the vault's `.obsidian/`, through `kgd obsidian install`;
- the agent runtime homes, through `kgd claude link` and `kgd codex link`.

The MCP server speaks stdio only and has no write tools.

The core's only network access is downloading the embedding model from the
Hugging Face Hub: `kgd embedding set` fetches it into the Hugging Face cache
(about 2.3 GB for `BAAI/bge-m3`), and later loads may still contact the Hub
unless `HF_HUB_OFFLINE=1` is set; the plugin sets it for every other run. No
token is sent and no remote code runs. The model runs on your computer in one
shared process, and record text never leaves the machine for embedding.
Nothing is committed or pushed for you: keep each vault and the home in your
own Git repositories.

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
in `.knowledge/entries/` and proposals awaiting your acceptance in
`.knowledge/drafts/`. A record is either a node (a concept or a precisely
stated result) or a relation whose role keys bind other records. Every record
cites a source file of its base by path, line range and verbatim quotes, or,
as foreign knowledge, the web page of an outside work it quotes. Sources are plain text in any format; kgdistiller reads them as
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
    └── drafts/<id>.md             # proposed records, same format
```

The database holds FTS5 full-text rows and float32 vectors, searched by an
exact NumPy scan. The embedding model runs with int8 weights in one shared
process per user, about 1 GB for `BAAI/bge-m3`, which exits after 15 idle
minutes. `kgd search` fuses three lanes (lexical, dense and name
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
`retrieval` extra adds PyTorch, transformers, sentencepiece and NumPy for the
dense lane. Without it (`uv tool install .`), keep `"embedding": null` in the
home config and search uses only the lexical and name lanes.

## Quick start

Register a base. The folder must already exist; `mkdir -p ~/research/notes`
creates it. The commands below also need `notes/measure.md`, whose lines 3-4
must contain the text quoted in the sigma-algebra example under
[Record format](#record-format). The first `base add` creates the home:

```sh
kgd base add ~/research --name research
```

Define a document type in `~/.knowledge/types/research-notes.md`. Its
frontmatter lists the node kinds and the relation kinds with their ordered
roles; its body is the guidance an agent follows when extracting records from
documents of this type. No types are activated automatically; you can write
one or adapt a [starter template](#document-types-and-starter-templates).

```markdown
---
node_kinds: [definition, theorem]
relation_kinds:
  requires-for: [prerequisite, dependent]
  example: [uses, setting]
epistemic: [proved, stated]
---
Extract each definition and each precisely stated result as a node. Record a
relation only when the text states it.
```

Map the base's sources to that type in `~/.knowledge/config.json`:

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
  "embedding": null
}
```

Then choose the embedding model. `kgd embedding set` downloads it and checks
that kgdistiller can run it; it must be an XLM-RoBERTa sentence encoder that
ships `sentencepiece.bpe.model`, such as `BAAI/bge-m3`. Skip it to search by
keywords and names only:

```sh
kgd embedding set BAAI/bge-m3
```

Then add records. Ask an agent with the Skills installed to capture a
definition or compile a whole note; every result is a draft you review and
accept. To add one by hand, print the source's type profile with
`kgd source`, write a draft in
`~/research/.knowledge/drafts/` (see [Record format](#record-format)), then
check, accept and index it:

```sh
cd ~/research
kgd source notes/measure.md
kgd check
kgd accept .knowledge/drafts/sigma-algebra.md
kgd search "sigma algebra"
```

`kgd source` prints the type profile and the records and drafts a source
already has. `kgd check` validates the home, records and drafts, and reports evidence
that moved or no longer matches its source. `kgd accept` moves drafts into
`entries/`, all or nothing, then indexes them and embeds them; a draft with an
accepted record's id replaces that record.
Every
command prints JSON and exits 0 on success, 1 on findings or a refusal, and 2
on a usage error.

## Document types and starter templates

> 我之前有另一个重要的设计：agent 并不懂你要什么样的知识节点，因而我认为一个知识源文章属于某个类型（比如 math notes、ai research），用户自己为这个类型去制定知识节点类型以供 agent 去提取。
>
> 我作为仓库创始人自己需要的类型（比如 math notes、ai research、cs notes），直接作为默认 templates 就可以了。

A document type declares `node_kinds`, relation kinds and their roles,
optional epistemic labels, and prose guidance for extraction. Source globs in
`config.json` assign exactly one type to each source.

Capture and compilation read that policy through `kgd source SOURCE`
before proposing records. `kgd check` and acceptance validate kinds and roles
against it. You can change the policy or add types without changing the engine.

Templates:

| Template | Knowledge to retain |
|---|---|
| [math-notes](templates/document-types/math-notes.md) | Definitions, axioms and precise theorems, with derivations and examples. |
| [ai-research](templates/document-types/ai-research.md) | Defined concepts, algorithms, architectures, objectives and precise results, with comparisons and evaluations. |
| [cs-notes](templates/document-types/cs-notes.md) | Definitions, algorithms, data structures, architectures and invariants, with implementation and complexity relations. |

Copy a chosen file into `$KGDISTILLER_HOME/types/`, edit its vocabulary and
guidance, then map the relevant source globs to its file stem. These are
starting templates; installation and `kgd base add` leave the active type
catalog for you to define. The templates are also bundled under
`kgdistiller/product/templates/document-types/` in the Python package.

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
  - "[[set]]"
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
  (`a` or `a-b`) are required. A label names one concept across every base:
  a concept of another meaning whose name is taken adds its source's file
  name, as in `Autoencoder (Autoencoder&VAE)`. A foreign record's `source` is the web address
  of the outside work it quotes, and it has no `lines`. `aliases`,
  `understanding` (`yes` or `no`, default `yes`), `epistemic` and `requires`
  are optional; `tags` and `cssclasses` are allowed and ignored.
- Every other key is a role. A record with at least one non-empty role list is
  a relation; otherwise it is a node. A relation may have any number of roles.
- A list value is a link: `"[[id]]"`, `"[[base:id]]"` or
  `"[[.knowledge/entries/id]]"`. A concept nothing records yet is captured on
  its own first, from a local source or as foreign knowledge.
- The body is prose, an optional `## Search terms` section, then a final
  `## Evidence` section of verbatim quotes from the cited lines (or page).
- Examples and applications are relations of a kind you register, such as
  `example: [uses, setting]`.

Agents only write drafts; you accept them. Edit accepted records by hand in
place, then run `kgd check` and `kgd index --no-embed`
(the plugin refreshes the keyword index by itself); an edited record keeps
its old vector until you re-embed with `kgd index`. When a
source edit shifts the cited lines, `kgd check --fix-lines` rewrites the
`lines:` of each record whose quotes it finds exactly once elsewhere in the
source. [docs/model.md](docs/model.md) has the full grammar and validation
rules.

## Commands

| Command | Purpose |
|---|---|
| `kgd base add PATH [--name N]`, `base rm NAME`, `base list` | Register, unregister and list bases with record, draft and index counts. |
| `kgd embedding`, `embedding set MODEL`, `embedding off` | Show the home's embedding model and its vectors, download and check a model and use it, or turn the dense lane off. |
| `kgd check [--base B] [--fix-lines]` | Validate the home, records and drafts; report moved or stale evidence. |
| `kgd source SOURCE` | Print a source's document-type profile and the records and drafts citing it. |
| `kgd find TEXT [--base B] [--source PREFIX]` | Find a passage or a name in the registered sources, whatever its line breaks and hidden markup. |
| `kgd relocate TARGET TEXT` | Move a record's source, lines and evidence to the only passage of its base that holds the text. |
| `kgd drafts [--base B] [--source PREFIX] [--name NAME]` | List the drafts with their links and what `kgd accept` would refuse; writes nothing. |
| `kgd edit DRAFT FIELD [VALUE...]` | Set one field of a draft: label, kind, epistemic, understanding, aliases, requires, a role or its statement. |
| `kgd accept [--dry-run] DRAFT...` | Admit drafts as new records or replacements of the records with their ids, all or nothing, then index and embed them. |
| `kgd index [--rebuild] [--no-embed]` | Bring the database up to date with the record files; `--no-embed` refreshes only the keyword index. |
| `kgd search QUERY [--limit N] [--no-dense]` | Rank records by the lexical, dense and name lanes. |
| `kgd resolve TERM...` | List the senses and mentions of terms. |
| `kgd get UID... [--source-lines N]` | Read complete records with their links and, optionally, the live cited source lines. |
| `kgd neighbors UID... [--role R] [--dir out\|in\|both] [--depth N]` | Follow links from records: dependency and claim closures. |
| `kgd browse [HANDLE]` | List bases, a base's source directories, a source's records by kind, or every record of a `--kind`. |
| `kgd pack UID... [--budget BYTES] [--requires-depth N]` | Pack whole records within a byte budget, with shared relations and the gaps left open. |
| `kgd obsidian install [--base NAME]` | Install or update the plugin in a base's vault and enable it with hidden-folder indexing. |
| `kgd claude link\|doctor`, `kgd codex link\|doctor` | Install or verify the Skills, agent presets and workflow files for Claude Code or Codex. |
| `kgd mcp` | Serve the read tools over stdio for every base. |

`search`, `resolve`, `neighbors`, `browse` and `pack` take the filters
`--base`, `--kind`, `--class {node,relation}`, `--source PREFIX` and
`--understanding`, each repeatable. A bare id works where it is unique across
bases. Run `kgd <command> --help` for details and
[docs/retrieval.md](docs/retrieval.md) for the output shapes.

## Reviewing drafts

Every capture and compile ends in drafts. Review them in the plugin's Capture
view, which lists them by source and shows each one's links and what
`kgd accept` would refuse, or open them like any file and run:

```sh
kgd drafts --source notes/measure.md
kgd accept .knowledge/drafts/measure-space.md
```

`kgd accept` admits the drafts given and the drafts they link, all or
nothing, and indexes and embeds them. If a draft links a draft you did not
give, it refuses and changes nothing, with a message such as
`select [[measure-space]] too`. To reject a draft, delete its file.

## Agent integration

Five Skills cover the write and read workflows:

| Skill | What it does |
|---|---|
| `capture-kgdistiller` | Drafts one record while you read: a node, a relation or an example, with its quote, or a borrowed concept as foreign knowledge from the page of its work. The `kgdistiller-capture-writer` agent preset runs it in the background. |
| `compile-kgdistiller` | Drafts records from part or all of a registered source, following its document type. Knowledge already recorded is ignored, or drafted as a replacement, by the conflict rules in [docs/model.md](docs/model.md#conflicts-updates-and-relocation). |
| `relocate-kgdistiller` | Moves one record to the passage you paste, after a source was edited or renamed: it finds the record from the knowledge's name and runs `kgd relocate`. |
| `query-kgdistiller` | Answers from the base with search, resolve, get, neighbors, browse and pack, citing `source:lines`. Read-only, with the `kgdistiller-query-reviewer` agent preset. |
| `deploy-kgdistiller` | Installs, registers bases, sources, types and the model, then checks, indexes, installs the plugin and links the runtimes. |

The Skills write drafts only; accepting them indexes and embeds them, and
re-embedding a record you edited by hand is left to you. Install them for your
runtime:

```sh
kgd claude link
kgd claude doctor
```

```sh
kgd codex link
kgd codex doctor
```

`link` installs the Skills, the `kgdistiller-query-reviewer` and
`kgdistiller-capture-writer` presets and the
workflow manifest under `~/.claude` or `~/.codex` (honoring `CLAUDE_CONFIG_DIR`
and `CODEX_HOME`) and never edits `CLAUDE.md`, `settings.json`, `AGENTS.md` or
`config.toml`. OpenCode and OMP get the Skills only, from the checkout:
`./scripts/link-skills.sh opencode` or `./scripts/link-skills.sh omp`
(PowerShell 7: `./scripts/link-skills.ps1 -Runtime opencode`).

The MCP server exposes `kg_search`, `kg_resolve`, `kg_get`, `kg_neighbors`,
`kg_browse`, `kg_pack` and `kg_drafts`, with the same JSON as the CLI. It
loads no model: the dense lane asks the shared embedding process, so only the
first dense search after 15 idle minutes waits for the model.
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
   the source globs and document types into `~/.knowledge/config.json` and
   `~/.knowledge/types/`, set the model with `kgd embedding set BAAI/bge-m3`,
   then run `kgd check`,
   `kgd index` and, for a vault opened in Obsidian, `kgd obsidian install`.
4. Smoke-test with `kgd base list` and `kgd search "KNOWN NAME" --base NAME`.

After that, requests such as "capture this definition into my kgd knowledge
base" or "search my knowledge base for measure spaces" reach the matching
Skill.

## Backup and restore

Commit each base's sources together with `.knowledge/entries/` and
`drafts/`. Keep the home's `config.json`, `types/` and `.gitignore` in a
separate private repository; `index.sqlite*` and `lock` are ignored. On a new
machine, restore those files, fix each base's `path`, and rebuild:

```sh
kgd check
kgd index
```

`kgd index` recreates a missing or damaged database from the record files and
re-embeds every record. Deleting the database first
(`rm -f ~/.knowledge/index.sqlite*`) is optional. The embedding process
encodes about 14 records a second with `BAAI/bge-m3` on Apple silicon.
`kgd index --rebuild` re-derives every row without re-embedding.

## Development

```sh
npm run build
uv run --locked python -m unittest discover -s tests -v
uv run --locked ruff check src tests scripts
uv build --out-dir build/release/1.1.0
uv run --locked python scripts/check_distribution.py --dist-root build/release/1.1.0
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
