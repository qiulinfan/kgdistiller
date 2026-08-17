# Changelog

All notable changes are documented here. Beginning with 0.4, published
`kgdistiller-*` schema names are immutable; incompatible data-contract changes
require incrementing the affected contract version.

## 0.4.0 — unreleased

- Add a cross-platform installed `kgdistiller`/`kgd` command with a strict
  machine-local vault registry at `~/.kgdistiller/vaults.json`. Commands can
  select a registered vault by name or stable UUID from any working directory,
  while portable identity lives in tracked `knowledge/vault.json` and is bound
  into `kgdistiller-store-v1` snapshots.
- Make `knowledge/entries/<node-id>.md` the atomic-entry authority visible to
  Obsidian. Graph JSONL entry shards remain deterministic derived indexes, and
  the graph manifest binds the entry Markdown inventory.
- Add `knowledge/derived/by-source/` for collision-free in-vault Typst, LaTeX,
  and PDF conversions plus `knowledge/derived/imports/` for explicitly targeted
  external sources. Internal conversions record upstream provenance; external
  imports intentionally begin the persisted provenance chain.
- Establish the `kgdistiller-*` schema namespace with independent v1 contracts
  such as `kgdistiller-graph-v1`, `kgdistiller-sources-v1`, and
  `kgdistiller-store-v1`. Pre-0.4 schema aliases and readers are not retained.
- Make Markdown, Typst, and LaTeX the only authorities and retain the
  deterministic `kgdistiller-graph-v1` JSON graph as the derived machine contract.
- Require `kgdistiller-sources-v1`, `kgdistiller-identities-v1`, and
  `kgdistiller-agent-delta-v1` at the persisted core boundary; rebuild derived
  graph artifacts from native authorities when upgrading.
- Replace the disposable SQLite Agent index with a generation-checked in-memory
  `GraphView` used by CLI, MCP, and the native frontend.
- Remove embedding, vector, model-provider, machine-profile, database override,
  and store-materialization runtime paths without a compatibility shim.
- Add deterministic `kgdistiller-retrieval-plan-v1`, `kgdistiller-search-result-v1`, and
  `kgdistiller-search-execution-v1` contracts for identity, lexical, and bounded graph
  retrieval; reject semantic-query fields.
- Replace the incompatible 0.3 query handoffs with
  `kgdistiller-context-bundle-v1`, `kgdistiller-alignment-report-v1`,
  `kgdistiller-graph-comparison-v1`, and `kgdistiller-agent-proposal-v1`. Alignment decisions
  bind the reviewed-registry digest; comparison reports only identity and edge
  presence, while proposals remain non-actionable review packages.
- Add bounded `kgdistiller-candidate-graph-v1`; its node,
  label, namespace, collection, provenance, and relationship limits match the
  snapshot and query output boundaries.
- Add bounded `kgdistiller-agent-snapshot-v1` and
  `kgdistiller-alignments-v1`; their contracts make the accepted
  namespace, node, relation, provenance, diagnostic, and collection limits
  explicit.
- Add file-based `kgdistiller-store-v1`. A verified clone
  includes entry Markdown and its evidence, is immediately queryable, and
  requires no materialization step.
- Add `kgdistiller-ingest-request-v1`, bind its
  reviewed semantic update to `kgdistiller-agent-delta-v1`, and keep transactional
  ingest stale-safe while atomically installing identity authorities, entry
  Markdown, reviewed registries, and the deterministic graph generation.
- Add strict
  `kgdistiller-ingest-receipt-v1`, which records the JSON-memory query backend and no
  index-rebuild stage.
- Package a self-contained native frontend and preserve loopback binding by
  default.
- Add `kgdistiller-static-export-v1` so the persisted bundle binds its private
  source to `kgdistiller-graph-v1`; 0.4 does not read pre-0.4 bundle manifests.
- Add `kgdistiller-obsidian-projection-v1` as a lossy, disposable downstream export
  that is never registered, rescanned, or used for round-trip authoring.
- Add a read-only Obsidian plugin and the digest-bound
  `kgdistiller-obsidian-graph-v1` projection. The custom view keeps semantic
  edge type, direction, and evidence distinct from source definition/reference
  edges while native Obsidian backlinks continue to work independently.
- Remove superseded 0.3 database/vector design specifications; Git history is
  their archive.

## 0.3.0

- Added the first portable store, transactional ingest, machine-local query
  index, bounded hybrid retrieval, and multi-platform release coverage.
- Added explicit embedding policy/provider experiments and versioned retrieval
  execution contracts. These derived runtime paths are removed in 0.4.0.

## 0.2.1

- Separated provider-neutral query and ingestion Skills.
- Added conservative identity resolution, graph comparison, and reviewed
  alignment handling.

## 0.2.0

- Added the self-contained Agent snapshot, bounded graph retrieval, context
  bundles, and read-only MCP tools.

## 0.1.0

- Initial standalone Markdown, Typst, and LaTeX graph compiler and local
  browser.
