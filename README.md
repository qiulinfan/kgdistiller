# kgdistiller

`kgdistiller` compiles registered Markdown, Typst, and LaTeX authorities into a
deterministic, source-backed `qlkg-v3` JSON graph. The authority remains in its
native source format; graph files, browser views, search results, static sites,
and Obsidian notes are derived products.

Version 0.4 is a breaking JSON-only release. It has no SQLite, vector,
embedding-provider, machine-profile, or materialization runtime. Read-only
queries load one generation-checked in-memory `GraphView` from the committed
graph artifacts.

Version 0.4 accepts only `qlkg-v3`, `qlkg-sources-v3`,
`qlkg-identities-v2`, and `qlkg-agent-delta-v3` at the persisted core
boundary. It does not migrate or retain 0.3 core artifacts. Before upgrading,
commit the native authorities and reviewed registries as a Git rollback point.
Export any 0.3 Agent-curated entries or semantic edges that must survive for
later human review. Then explicitly move/delete the old generated
`knowledge/graph/`, review and update the source/optional identity registry
discriminators, and run an unscoped `sync` to rebuild the graph from the native
Markdown, Typst, and LaTeX authorities. Re-review and re-author any metadata
that must be recreated under the v3 delta contract; otherwise the rebuild
intentionally discards it.

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

## Deterministic graph and queries

The committed graph under `knowledge/graph/` contains the `qlkg-v3` manifest,
nodes, edges, references, diagnostics, and bounded entry shards. A file path is
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

Planned search accepts `qlkg-retrieval-plan-v2`:

```sh
kgdistiller agent search --plan knowledge/build/query.plan.json
kgdistiller agent context --plan knowledge/build/query.plan.json --budget 6000
```

The plan has only `identity_queries`, `lexical_queries`, and a bounded graph
lane. A `semantic_queries` field is rejected. Results use
`qlkg-search-result-v3` inside `qlkg-search-execution-v2` and bind to the exact
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
one reviewed `qlkg-ingest-request-v2` to the only high-level write boundary:

```sh
kgdistiller ingest plan request.json --output plan.json
kgdistiller ingest apply request.json --receipt receipt.json
```

Plan runs in staging. Apply rechecks graph, alignment, source, candidate, and
query-report digests under the single-writer lock; installs the authority,
registries, and deterministic graph generation atomically; and returns a
canonical `qlkg-ingest-receipt-v2`. See
[docs/transactional-ingest.md](docs/transactional-ingest.md).

## Portable JSON store

`qlkg-store-v2` is the portable backup boundary. It contains registered
authorities, registries, deterministic graph artifacts, and the canonical
document inventory—no database or model-derived vectors.

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
`knowledge/graph/`, `knowledge/documents.jsonl`, and `knowledge/store.json`.
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
kgdistiller export obsidian --replace
```

The repository root is the editor vault, and its registered Markdown files
remain non-lossy native authorities. The managed
`qlkg-obsidian-projection-v1` subtree under `knowledge/build/obsidian/` is a
deliberately lossy, disposable view. Its source proxies link to registered
Markdown authorities elsewhere in the same vault. An output outside the
repository is a browsing-only vault/projection and uses `file:` links back to
authority files. The managed subtree is never authority, must not be registered
in `sources.json`, and must never be scanned or ingested back into kgdistiller.
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
```

See [docs/graph-contract.md](docs/graph-contract.md),
[docs/deployment.md](docs/deployment.md), and
[docs/release.md](docs/release.md) for the data, deployment, and compatibility
contracts.
