# Changelog

All notable changes are documented here. Schema names are immutable after
release; incompatible data-contract changes require an explicit migration and
a new schema version.

## 0.3.0 — unreleased

- Add the Git-friendly `qlkg-store-v1` authority store, canonical document
  inventory, content-addressed exact float32 embedding bundle, integrity
  verification, and provider-free SQLite materialization.
- Add `store snapshot`, `store verify`, and `store materialize` commands plus a
  deployment Skill for backup and multi-machine workflows.
- Add deterministic `qlkg-candidate-graph-v1` build and validation.
- Add content-addressed transactional ingest plan/apply requests, canonical
  receipts, single-writer locking, rollback, crash recovery, and idempotency.
- Bind writes to reviewed candidate, query, graph, alignment, and source
  digests.
- Keep query and MCP surfaces read-only while preserving old/new generation
  reader isolation.
- Make SQLite snapshots derive from committed hydrated graph artifacts so a
  fresh read does not rebuild an already-current index.
- Bound hybrid retrieval's personalized PageRank to the already-expanded local
  neighborhood while preserving full-namespace PPR as a standalone operation.
- Add disposable large-graph, multi-format, GraphRAG, incremental,
  concurrency, fault-injection, and transaction stress coverage.
- Make writer locking, bound Agent-index generation publication and cleanup,
  long transaction backup paths, stress reporting, structured console output,
  and closure contract glob validation portable across Windows native and WSL,
  with Windows and macOS CI jobs and retained Linux-version compatibility
  checks.
- Activate the ignored machine-local profile with deterministic CLI override
  precedence, safe status output, a bounded provider registry, and a stdlib
  OpenAI-compatible batched embedding adapter.
- Document local deployment, backup/restore, compatibility, migration, and
  public release order, including a measured 100,000-node baseline.

## 0.2.1

- Separate provider-neutral query and ingestion Skills.
- Add conservative identity resolution, graph comparison, and reviewed
  alignment handling.

## 0.2.0

- Add the self-contained Agent snapshot and provider-neutral SQLite index.
- Add bounded GraphRAG retrieval, context bundles, and read-only MCP tools.

## 0.1.0

- Initial standalone Markdown, Typst, and LaTeX graph compiler and local
  browser.
