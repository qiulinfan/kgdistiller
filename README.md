# kgdistiller

`kgdistiller` compiles registered Markdown, Typst, and LaTeX identity
authorities plus Markdown atomic entries into a deterministic, source-backed
`kgdistiller-graph-v1` JSON graph. Graph files, browser views, search results,
static sites, and managed Obsidian projections are derived products.

Version 0.4 is a breaking file-based release. It has no SQLite, vector,
embedding-provider, machine-profile, or materialization runtime. Read-only
queries load one generation-checked in-memory `GraphView` from the committed
graph artifacts.

Version 0.4 establishes the `kgdistiller-*` contract namespace. Its persisted
core accepts only `kgdistiller-graph-v1`, `kgdistiller-sources-v1`,
`kgdistiller-identities-v1`, and `kgdistiller-agent-delta-v1`; there are no
legacy schema aliases or readers. Before upgrading, commit native authorities
and reviewed registries as a Git rollback point, preserve any curated content
that needs human re-review, remove the old generated graph, and run an unscoped
`sync`. Re-author retained metadata under `kgdistiller-agent-delta-v1`.

## Authority markers

Each global knowledge name has at most one active definition marker.

| Format | Definition | Reference |
| --- | --- | --- |
| Typst | `#kn[Measure space]` | `#ref[Measure space]` |
| Markdown | `--[[Measure space]]--` | `[[Measure space]]` |
| LaTeX | `\kn{Measure space}` | `\knref{Measure space}` |

Markdown display aliases use `[[Measure space|spaces]]`. Headings, document
order, examples, equations, and unmarked prose never create identities.
Reviewed canonical names and aliases live in `knowledge/identities.json`;
reviewed cross-namespace mappings live in `knowledge/alignments.json`.

## Install and initialize

```sh
git clone https://github.com/qiulinfan/kgdistiller.git
cd kgdistiller
uv sync
uv run kgdistiller --help
```

Or install the command:

```sh
uv tool install git+https://github.com/qiulinfan/kgdistiller.git
uv tool update-shell
kgdistiller --help
```

`uv tool install` creates the `kgdistiller` and `kgd` console commands on
Windows, macOS, and Linux. Restart the shell after `uv tool update-shell` if
the command was not already on `PATH`.

Initialize a knowledge project, review its bounded source registry, then build
and validate the first generation:

```sh
cd your-notes-repository
kgdistiller init --source-root notes
kgdistiller sync
kgdistiller check
```

Initialization creates `knowledge/vault.json`, the stable UUID identity that
travels with the repository. Register the repository once to run the global
command from any working directory:

```sh
kgdistiller vault register /absolute/path/to/your-notes-repository --name research
kgdistiller vault list
kgdistiller --vault research agent status
```

The first registered vault becomes the default, so `kgdistiller agent status`
also works outside that repository. Use `kgdistiller vault default NAME` to
change it, `kgdistiller vault default --clear` to require explicit selection,
and `kgdistiller vault doctor` to validate all registered paths and identities.
`KGDISTILLER_VAULT=NAME` is the environment equivalent of `--vault`.

The machine-local locator is `~/.kgdistiller/vaults.json` (under the Windows
user profile on Windows). Override its directory with the absolute
`KGDISTILLER_HOME` path when isolation is needed. This registry contains local
names and absolute paths only; it is not knowledge authority and should not be
committed or copied with a vault. After moving a vault, register its new path
again; pass `--replace` only when the old registered path still exists.

Target resolution is deterministic: explicit `--repo-root`, then
`--vault`/`KGDISTILLER_VAULT`, then the nearest local knowledge project, then a
registered ancestor, then the configured default. `init` deliberately ignores
the default so a new working directory can be initialized safely.

Typst is required only when Typst-authored labels must be rendered. Markdown
and LaTeX scanning use the Python standard library.

## Vault data layout and derivation

The editor/source tree is never used for generated files. Every managed
derivation and atomic entry lives under the owning vault's `knowledge/` tree:

```text
vault/
├── notes/chapter.typ
└── knowledge/
    ├── derived/
    │   ├── by-source/notes/chapter.typ.md
    │   └── imports/paper.md
    ├── entries/measure-space.md
    └── graph/
```

`knowledge/derived/by-source/` mirrors an in-vault `.typ`, `.tex`, or `.pdf`
path and retains its original suffix before `.md`, so same-stem inputs cannot
collide. Its frontmatter records the upstream vault-relative path, format, and
digest. `knowledge/derived/imports/` is for sources outside every vault. Such a
source requires explicit `--repo-root` or `--vault`; the installed Markdown is
the beginning of the persisted provenance chain and therefore does not record
the external machine path.

```sh
kgdistiller derive locate notes/chapter.typ
kgdistiller derive install notes/chapter.typ --input converted.md
kgdistiller --vault research derive install /tmp/paper.pdf --input paper.md
```

`derive install` places already-converted Markdown; conversion itself remains
an extractor/Agent responsibility. It never writes beside the input. The
nearest enclosing `knowledge/vault.json` wins. If none exists, the command
fails until a target vault is explicitly selected. Initialized vaults register
`knowledge/derived/imports/**/*.md` and internal `*.pdf.md` derivations as
Markdown identity sources; Typst and LaTeX identities continue to come from
their native markers.

Curated atomic content is authoritative in
`knowledge/entries/<node-id>.md`, including its Markdown evidence path and
digests. These files are ordinary Obsidian-visible notes. Applying a reviewed
delta creates or updates them; `sync` reads them back and rebuilds the JSONL
entry shards under `knowledge/graph/` only as a bounded query index.

## Deterministic graph and queries

The committed graph under `knowledge/graph/` contains the `kgdistiller-graph-v1` manifest,
nodes, edges, references, diagnostics, and bounded derived entry shards. The
manifest binds the `knowledge/entries/*.md` inventory. A file path is
provenance, never identity. Source hashes use UTF-8 text with CRLF/CR normalized
to LF, so checkout newline conversion alone does not create a new generation.
The manifest also binds the canonical source registry and optional reviewed
identity registry; changing ownership, subject/origin metadata, names, or
aliases requires a new sync before a store or downstream export can be current.

Use the public query surface rather than reading graph shards directly:

```sh
kgdistiller agent status
kgdistiller agent resolve "Measure space" "Sigma algebra"
kgdistiller agent search "measure space" --limit 20
kgdistiller agent get measure-space
kgdistiller agent expand measure-space --depth 2
kgdistiller agent context "measure space" --budget 6000
```

Every independent CLI or MCP request loads and validates one immutable-in-
practice `GraphView`. The loader checks the graph manifest before and after
hydration and retries or fails if the generation changes, so a request never
mixes old and new graph files.

Cross-language retrieval is deterministic. Exact names and collision-free
reviewed global aliases may establish identity. Scoped aliases, Unicode
NFKC/casefolded lexical matching, and typed graph traversal only retrieve or
rank bounded candidates; similar text, acronyms, and graph proximity never
establish identity.

Planned search accepts `kgdistiller-retrieval-plan-v1`:

```sh
kgdistiller agent search --plan knowledge/build/query.plan.json
kgdistiller agent context --plan knowledge/build/query.plan.json --budget 6000
```

The plan has only `identity_queries`, `lexical_queries`, and a bounded graph
lane. A `semantic_queries` field is rejected. Results use
`kgdistiller-search-result-v1` inside `kgdistiller-search-execution-v1` and bind to the exact
snapshot and graph digests used by the request.

## MCP and local browser

Start the JSON-RPC MCP server with:

```sh
kgdistiller mcp
```

Its read-only tools are `kg_status`, `kg_resolve_concepts`, `kg_search`,
`kg_get_node`, `kg_expand`, `kg_ppr`, `kg_build_context`, `kg_align_graph`,
`kg_compare_graph`, and `kg_create_proposal`. MCP accepts bounded inputs and
does not mutate authorities, registries, or graph artifacts.

The native browser is packaged with kgdistiller and requires no external web
application or content delivery network:

```sh
kgdistiller serve
```

It binds to <http://127.0.0.1:8765/> by default. Treat an explicitly selected
non-loopback host as a separate security decision; the server is not an
authenticated multi-user service. Source excerpts are accepted only for the
snapshot currently loaded by the page and for authority text whose hash still
matches that snapshot; after a sync or edit, reload the page rather than mixing
generations.

## Reviewed transactional ingest

Agents first resolve identities through the read-only query surface, then pass
one reviewed `kgdistiller-ingest-request-v1` to the only high-level write boundary:

```sh
kgdistiller ingest plan request.json --output plan.json
kgdistiller ingest apply request.json --receipt receipt.json
```

Plan runs in staging. Apply rechecks graph, alignment, source, candidate, and
query-report digests under the single-writer lock; installs the authority,
registries, and deterministic graph generation atomically; and returns a
canonical `kgdistiller-ingest-receipt-v1`. See
[docs/transactional-ingest.md](docs/transactional-ingest.md).

## Portable store

`kgdistiller-store-v1` is the portable backup boundary. It contains registered
authorities, Markdown entry authorities and their evidence, registries,
deterministic graph artifacts, and the canonical document inventory—no database
or model-derived vectors.

```sh
kgdistiller check
kgdistiller store snapshot
kgdistiller store verify
```

To create a separate self-contained copy:

```sh
kgdistiller --repo-root /absolute/path/to/notes store snapshot \
  --output /absolute/path/to/private-store
kgdistiller --repo-root /absolute/path/to/private-store store verify
kgdistiller --repo-root /absolute/path/to/private-store agent status
```

A verified clone is immediately queryable; there is no materialization step.
Use private Git for backup only with explicit authorization. Track authorities,
`knowledge/vault.json`, `knowledge/sources.json`, optional identity/alignment
registries,
`knowledge/derived/`, `knowledge/entries/`, `knowledge/graph/`,
`knowledge/documents.jsonl`, and `knowledge/store.json`.
Keep `knowledge/build/`, plans, receipts, transaction journals, and credentials
untracked.

## Downstream exports

Create an independently verifiable static site bundle with `export site`; its
standalone verifier is the adoption boundary for another repository:

```sh
kgdistiller export site --output knowledge/export/site \
  --product-commit FULL_PRODUCT_COMMIT \
  --source-repository https://example.invalid/owner/knowledge
python knowledge/export/site/verify_export.py knowledge/export/site
```

For editor-plus-browser use, open the knowledge repository itself as the
Obsidian vault and keep the projection at its ignored default location:

```sh
kgdistiller obsidian install
kgdistiller export obsidian --replace
```

With the global registry, both commands can be run from any directory by adding
`--vault <registered-name-or-id>`. Plugin updates require
`obsidian install --replace`; the installer preserves plugin settings and
configures the community plugin as enabled. Reload Obsidian after installation
or update.

The repository root is the editor vault, and its registered Markdown files plus
`knowledge/entries/*.md` remain non-lossy authorities. The managed
`kgdistiller-obsidian-projection-v1` subtree under `knowledge/build/obsidian/` is a
deliberately lossy, disposable view. Its source proxies link to registered
Markdown authorities elsewhere in the same vault. An output outside the
repository is a browsing-only vault/projection and uses `file:` links back to
authority files. The managed subtree is never authority, must not be registered
in `sources.json`, and must never be scanned or ingested back into kgdistiller.
The same export also writes a digest-bound `kgdistiller-obsidian-graph-v1`
artifact at `knowledge/build/obsidian/semantic-graph.json`. Obsidian's native
graph still treats the generated Wikilinks as ordinary links; the optional
[kgdistiller Obsidian plugin](integrations/obsidian/README.md) reads this JSON in
a separate view and preserves semantic edge type, direction, evidence, and the
distinct source-definition/reference layers. The plugin is read-only and never
turns the projection into authority.
For a registered Markdown definition, a portable, collision-free exact
Wikilink target becomes the projection filename; Typst and LaTeX definitions
use their canonical label. This lets native Markdown `[[Label]]` references and
`--[[Label]]--` definitions navigate without a plugin, including when semantic
label cleanup differs from the literal Markdown target. Unsafe, overlong,
Windows-reserved, or Unicode/case-colliding targets use a deterministic `_kgd-`
hash filename instead; generated relation and source-proxy links still work,
but raw authority markers for those targets require a future plugin. The
exporter fails closed when a planned raw target collides with a registered
Markdown authority basename. Other unregistered same-basename notes also make
Obsidian resolution ambiguous and are outside the exporter inventory. Identity
aliases remain metadata and display labels—raw `[[Alias]]` navigation is not a
supported no-plugin contract.
The exporter fails if registered authority or identity/config state is newer
than the graph; sync first, then rebuild. Do not edit projection notes as a
round-trip source.

## Product Skills and development

Install the shipped Codex Skills, agent presets, and workflow manifest with:

```sh
kgdistiller codex link
kgdistiller codex doctor
```

The supported workflow order is documented in
[docs/product-workflows.md](docs/product-workflows.md). Development changes
must pass:

```sh
uv run python -m unittest discover -s tests -v
uv build --out-dir build/release/0.4.0
uv run python scripts/check_distribution.py --dist-root build/release/0.4.0
cd integrations/obsidian && npm ci && npm run check
```

See [docs/graph-contract.md](docs/graph-contract.md),
[docs/deployment.md](docs/deployment.md), and
[docs/release.md](docs/release.md) for the data, deployment, and compatibility
contracts.
