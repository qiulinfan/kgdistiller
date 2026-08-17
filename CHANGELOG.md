# Changelog

All notable changes are documented here. Published schema names are immutable;
incompatible data-contract changes require a new schema version.

## 0.4.0 — unreleased

- Add a cross-platform installed `kgdistiller`/`kgd` command with a strict
  machine-local vault registry at `~/.kgdistiller/vaults.json`. Commands can
  select a registered vault by name or stable UUID from any working directory,
  while portable identity lives in tracked `knowledge/vault.json` and is bound
  into `qlkg-store-v2` snapshots.
- Make Markdown, Typst, and LaTeX the only authorities and retain the
  deterministic `qlkg-v3` JSON graph as the derived machine contract.
- Replace the 0.3 core discriminators with `qlkg-v3`, `qlkg-sources-v3`,
  `qlkg-identities-v2`, and `qlkg-agent-delta-v3`. Version 0.4 refuses the old
  registries and graph; users explicitly update reviewed registry
  discriminators and rebuild derived graph artifacts from native authorities.
- Replace the disposable SQLite Agent index with a generation-checked in-memory
  `GraphView` used by CLI, MCP, and the native frontend.
- Remove embedding, vector, model-provider, machine-profile, database override,
  and store-materialization runtime paths without a compatibility shim.
- Add deterministic `qlkg-retrieval-plan-v2`, `qlkg-search-result-v3`, and
  `qlkg-search-execution-v2` contracts for identity, lexical, and bounded graph
  retrieval; reject semantic-query fields.
- Replace the incompatible 0.3 query handoffs with
  `qlkg-context-bundle-v2`, `qlkg-alignment-report-v2`,
  `qlkg-graph-comparison-v2`, and `qlkg-agent-proposal-v2`. Alignment decisions
  bind the reviewed-registry digest; comparison reports only identity and edge
  presence, while proposals remain non-actionable review packages.
- Replace candidate graph v1 with bounded `qlkg-candidate-graph-v2`; its node,
  label, namespace, collection, provenance, and relationship limits match the
  snapshot and query output boundaries.
- Replace unbounded Agent snapshots and alignment registries with
  `qlkg-agent-snapshot-v2` and `qlkg-alignments-v2`; v2 makes the accepted
  namespace, node, relation, provenance, diagnostic, and collection limits
  explicit.
- Replace `qlkg-store-v1` with JSON-only `qlkg-store-v2`. A verified clone is
  immediately queryable and requires no materialization step.
- Replace `qlkg-ingest-request-v1` with `qlkg-ingest-request-v2`, bind its
  reviewed semantic update to `qlkg-agent-delta-v3`, and keep transactional
  ingest stale-safe while atomically installing only native authorities,
  reviewed registries, and the deterministic graph generation.
- Replace the index-backed ingest receipt v1 with strict
  `qlkg-ingest-receipt-v2`, which records the JSON-memory query backend and no
  index-rebuild stage.
- Package a self-contained native frontend and preserve loopback binding by
  default.
- Replace `qlkg-static-export-v1` with `qlkg-static-export-v2` so the persisted
  bundle honestly binds its private source to `qlkg-v3`; 0.4 does not read the
  superseded v1 bundle manifest.
- Add `qlkg-obsidian-projection-v1` as a lossy, disposable downstream export
  that is never registered, rescanned, or used for round-trip authoring.
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
