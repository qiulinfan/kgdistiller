# Public release and compatibility policy

This document defines release gates. It does not authorize a push, package
publication, tag, GitHub release, or disclosure of personal knowledge.

## Version 0.4 contract matrix

| Contract | Read | Write | Role |
| --- | --- | --- | --- |
| `kgdistiller-vault-v1` | yes | yes | Portable stable vault identity. |
| `kgdistiller-vault-registry-v1` | yes | yes | Machine-local name/UUID/path locator. |
| `kgdistiller-sources-v1` | yes | yes | Registered source documents of any text format and optional user-owned document type profiles. |
| `kgdistiller-entry-v1` | yes | yes | One reviewed entry per node: Obsidian properties (id, label, kind, aliases, source, line range, understanding), human sections and a verbatim Evidence quote. |
| `kgdistiller-agent-delta-v1` | yes | yes | Reviewed delta of entries (create, update, remove) and edges (add, remove). |
| `kgdistiller-ingest-request-v1` | yes | yes | Transactional reviewed write request. |
| `kgdistiller-ingest-plan-v1` | yes | output | Readable planned changes and counts, not a receipt. |
| `kgdistiller-ingest-receipt-v1` | yes | yes | Readable committed-write receipt keyed by request id. |
| `kgdistiller-ingest-error-v1` | yes | output | Stable transactional failure envelope. |
| `kgdistiller-ingest-journal-v1` | yes | yes | Local crash-recovery journal for an install in progress. |
| `kgdistiller-checkbox-review-v1` | yes | yes | Harvest review manifest binding sheet tasks to frozen captures. |
| `kgdistiller-query-status-v1` | yes | output | Entry and edge counts and relation counts. |
| `kgdistiller-retrieval-plan-v1` | yes | yes | Identity, lexical, and bounded graph plan. |
| `kgdistiller-search-result-v1` | yes | output | Deterministic lane results. |
| `kgdistiller-search-execution-v1` | yes | output | Query execution envelope. |
| `kgdistiller-search-result-v2` | yes | output | Results with embedding and reranker lanes. |
| `kgdistiller-search-execution-v2` | yes | output | Model-assisted execution envelope. |
| `kgdistiller-search-result-v3` | yes | output | Results with graph navigation evidence. |
| `kgdistiller-search-execution-v3` | yes | output | Graph-retrieval execution envelope. |
| `kgdistiller-context-bundle-v1` | yes | output | Budgeted source-backed query context. |
| `kgdistiller-context-bundle-v2` | yes | output | Budgeted context with graph support packets. |
| `kgdistiller-search-document-v1` | yes | output | Entry projection embedded by the model lane. |
| `kgdistiller-vector-cache-v1` | yes | yes | Rebuildable per-model vector cache keyed by entry id. |
| `kgdistiller-obsidian-graph-v1` | yes | yes | Obsidian plugin feed of every entry and accepted edge. |
| `kgdistiller-obsidian-plugin-install-v1` | yes | output | Plugin installation result. |

Accepted edges live in `.knowledge/edges.jsonl` with exactly six fields; that
line format is part of the knowledge contract rather than a named schema. The
`kgdistiller-*` v1 names are the first public contract generation in this
namespace; query, delta and consumer API versions are independent. A read
operation never rewrites knowledge. Once 0.4 is published, a changed invariant,
required field or identity meaning requires incrementing that contract's own
version. Readers fail closed on unknown incompatible schemas.

## Clean boundary from pre-0.4 artifacts

Version 0.4 stores knowledge only as reviewed entries and one edge file. It
removes the SQLite Agent index, the marker-scanned graph and its manifest,
identity and alignment registries, the portable store, source conversion, and
every content-hash mechanism from contracts and storage.
Consistency with sources is checked by comparing text.

There is no older schema reader and no automatic core migration. Graphs,
stores and records of other schemas, including pre-0.4 artifacts, fail closed.
A personal knowledge base is migrated once by its owner, outside the product.

Retrieval clients emit `kgdistiller-retrieval-plan-v1` and consume the search
execution and context bundle versions named above. The Obsidian graph feed is
regenerated from the current entries and edges, not migrated, and must never be
registered or scanned as a source.

## Release gates

Run from a clean engine worktree:

```sh
uv run --locked python -m unittest discover -s tests -v
uv run --locked ruff check src tests scripts
uv build --out-dir build/release/0.4.0
uv run --locked python scripts/check_distribution.py --dist-root build/release/0.4.0
cd integrations/obsidian && npm ci && npm run check
```

Then verify that:

- wheel and sdist contain Python modules, every current JSON Schema, product
  Skill, workflow manifest, workflow guide and its linked resources,
  `.codex/agents` and `.claude/agents` presets, and the three-file Obsidian
  plugin bundle;
- an isolated environment installs the wheel and runs
  `scripts/smoke_installed_runtime.py`: `init` with a plain-text source,
  `scan --file` with numbered lines, capture plus ingest plan/apply (including a
  CJK label with an explicit id), `check` printing `OK`, a source edit that
  shifts lines reported as moved, `check --fix-lines` restoring `OK`,
  `agent status`/`resolve`/`search` (including a CJK query), vault registration,
  plugin installation and `export obsidian` validated against
  `kgdistiller-obsidian-graph-v1`;
- installed `kgdistiller`/`kgdistiller.exe` registers and queries a vault from
  an unrelated working directory on Linux, Windows, and macOS;
- the installed wheel atomically installs the Obsidian plugin into a vault,
  configures it as enabled, and preserves existing plugin settings on update;
- sources of any extension are read as text, and no command parses source
  syntax or converts a source;
- `check` reports every store error and every moved, stale or ambiguous entry,
  exits 1 for any of them, and `--fix-lines` rewrites only moved line ranges
  under the writer lock;
- transactional plan/apply, replay versus `request-conflict`, every semantic
  re-validation error, lock conflict, fault injection and crash recovery pass,
  with no content hash anywhere in requests, receipts or stored knowledge;
- retrieval, graph traversal, MCP and the feed include every entry and edge
  regardless of staleness;
- `export obsidian` rejects unsafe outputs and writes one closed feed of every
  entry and accepted edge atomically;
- the Obsidian plugin passes contract/parser tests, type checking, and a
  production bundle build;
- every materially updated Skill passes the active `skill-creator` validator,
  product doctor, and an isolated Agent evaluation;
- POSIX and Windows copy/link doctor tests preserve unrelated Codex and Claude
  Code files;
- no credential, personal knowledge, source note, build artifact, or private
  fixture is tracked in the product repository.

## Supply-chain checklist

Review the complete diff/status, version, changelog, schemas, and license.
Build from a clean tagged commit into an empty distribution directory, inspect
wheel/sdist contents, and smoke-install the wheel in an isolated environment.
Use short-lived or trusted publishing credentials and never commit tokens. Tag
only after all gates pass; do not move a published tag. Keep the previous
release available for authority recovery, while treating 0.4 data/API changes
as intentionally incompatible.


## Obsidian Community release entry

The existing monorepo publishes the plugin through root `manifest.json` and
`versions.json`, exactly mirrored from `integrations/obsidian`. Plugin version
0.1.0 and Python core version 0.4.0 are independent. A plugin release tag must
be the manifest's exact `x.y.z` version, without `v`.

```sh
npm ci
npm run build
npm test
node scripts/build-obsidian-plugin.mjs --verify-only --tag 0.1.0
```

The root build delegates installation and build to the existing integration,
checks the embedded Cytoscape notice, and copies ignored root `main.js` and
`styles.css` for directory tooling. Python wheels/sdists and the explicit CLI
installer continue consuming `integrations/obsidian/{main.js,manifest.json,styles.css}`.
The version-checked release workflow builds from the tagged checkout, tests
metadata and contracts, and publishes the three assets as a GitHub release.
Do not replace a published version's tag or assets; increment the plugin version
in both manifests/packages and compatibility maps before a new release.

Original project code uses MIT. The embedded Cytoscape.js MIT license and other
upstream notices must remain present in the actual bundle. Obsidian review and
listing are a separate step after GitHub publication.
