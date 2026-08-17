# Public release and compatibility policy

This document defines release gates. It does not authorize a push, package
publication, tag, GitHub release, or disclosure of personal knowledge.

## Version 0.4 contract matrix

| Contract | Read | Write | Role |
| --- | --- | --- | --- |
| `kgdistiller-vault-v1` | yes | yes | Portable stable vault identity. |
| `kgdistiller-vault-registry-v1` | yes | yes | Machine-local name/UUID/path locator. |
| `kgdistiller-derived-markdown-v1` | yes | yes | In-vault conversion provenance frontmatter. |
| `kgdistiller-entry-v1` | yes | yes | Obsidian-compatible atomic-entry authority. |
| `kgdistiller-entry-index-v1` | yes | nested | Manifest inventory of atomic-entry authorities. |
| `kgdistiller-entry-source-index-v1` | yes | nested | Manifest inventory of current Markdown evidence bytes. |
| `kgdistiller-graph-v1` | yes | yes | Deterministic authority graph. |
| `kgdistiller-sources-v1` | yes | yes | Bounded Markdown/Typst/LaTeX registry. |
| `kgdistiller-identities-v1` | yes | yes | Reviewed authored-name changes and aliases. |
| `kgdistiller-scoped-aliases-v1` | yes | nested | Collision-aware aliases within one authority scope. |
| `kgdistiller-alignments-v1` | yes | yes | Bounded fingerprint-bound reviewed mappings. |
| `kgdistiller-agent-delta-v1` | yes | yes | Reviewed semantic graph delta. |
| `kgdistiller-agent-snapshot-v1` | yes | yes | Bounded self-contained hydrated graph generation. |
| `kgdistiller-query-status-v1` | yes | yes | GraphView status and generation binding. |
| `kgdistiller-retrieval-plan-v1` | yes | yes | Identity, lexical, and bounded graph plan. |
| `kgdistiller-search-result-v1` | yes | yes | Deterministic lane results. |
| `kgdistiller-search-execution-v1` | yes | yes | Query execution/generation envelope. |
| `kgdistiller-context-bundle-v1` | yes | yes | Budgeted source-backed query context. |
| `kgdistiller-alignment-report-v1` | yes | output | Conservative cross-namespace alignment report. |
| `kgdistiller-graph-comparison-v1` | yes | output | Matched-node and edge-presence comparison. |
| `kgdistiller-agent-proposal-v1` | yes | output | Non-mutating review proposal package. |
| `kgdistiller-candidate-graph-v1` | yes | yes | Bounded isolated source-grounded candidates. |
| `kgdistiller-ingest-request-v1` | yes | yes | Transactional reviewed write request. |
| `kgdistiller-ingest-plan-v1` | yes | output | Staged review result, not a receipt. |
| `kgdistiller-ingest-receipt-v1` | yes | yes | JSON-memory committed-write receipt. |
| `kgdistiller-ingest-error-v1` | yes | output | Stable transactional failure envelope. |
| `kgdistiller-document-record-v1` | yes | output | Canonical store authority inventory row. |
| `kgdistiller-store-v1` | yes | yes | File-based portable authority and graph generation. |
| `kgdistiller-store-report-v1` | yes | output | Verified store operation result. |
| `kgdistiller-site-graph-v1` | yes | output | Privacy-filtered hydrated site graph. |
| `kgdistiller-static-export-v1` | yes | yes | Verifiable privacy-filtered site export. |
| `kgdistiller-static-export-report-v1` | yes | output | Static export operation and cleanup result. |
| `kgdistiller-static-export-verification-v1` | yes | output | Standalone verifier result. |
| `kgdistiller-obsidian-projection-v1` | yes | yes | Lossy downstream Obsidian projection. |
| `kgdistiller-obsidian-export-report-v1` | yes | output | Obsidian build or verification result. |
| `kgdistiller-obsidian-concept-v1` | yes | output | Generated concept-note frontmatter tag. |
| `kgdistiller-obsidian-source-v1` | yes | output | Generated source-proxy frontmatter tag. |
| `kgdistiller-curation-check-v1` | yes | output | Scoped curation readiness report. |
| `kgdistiller-audit-v1` | yes | output | Whole-graph deterministic audit report. |

The `kgdistiller-*` v1 names are the first public contract generation in this
namespace. Once 0.4 is published, a changed invariant, required field, identity
meaning, or digest algorithm requires incrementing that contract's own version.
Readers fail closed on unknown incompatible schemas.

## Clean boundary from pre-0.4 artifacts

Version 0.4 removes the SQLite Agent index and every embedding, provider,
machine-profile, database override, and store-materialization path. It also
removes v1 retrieval/execution/result and portable-store compatibility from the
active product boundary.

There is no legacy schema reader or automatic core/database migration. Before
upgrading, commit native authorities and reviewed registries so Git history
provides an exact rollback point. Preserve any Agent-curated entries or
semantic edges that must survive for later human review. Then move the old
generated `knowledge/graph/` outside the project, or delete that exact directory
after confirming the rollback commit. Write registries with the current
`kgdistiller-sources-v1` and `kgdistiller-identities-v1` discriminators and run
an unscoped `sync` to derive `kgdistiller-graph-v1`. Reissue retained reviewed
metadata as `kgdistiller-agent-delta-v1`; never relabel an old delta without
re-reviewing it against the rebuilt generation.

Databases and vectors were derived data and are not migrated. After rebuilding,
create and verify a new `kgdistiller-store-v1` snapshot. If only an older store
survives, restore its native authorities with the earlier release first; 0.4
does not interpret pre-0.4 stores or graphs.

Rebuild consumer bundles as `kgdistiller-static-export-v1` from the verified
`kgdistiller-graph-v1` authority graph instead of relabeling or reusing old
bundle bytes.

Retrieval clients must emit `kgdistiller-retrieval-plan-v1`, omit
`semantic_queries`, and consume `kgdistiller-search-execution-v1` with nested
`kgdistiller-search-result-v1` plus `kgdistiller-context-bundle-v1`. Alignment,
comparison, and proposal review artifacts bind to `alignment_sha256`. A
verified `kgdistiller-store-v1` clone is immediately queryable; do not call or
emulate a materialization command.

Obsidian exports are new downstream projections, not a migration target. They
must never be registered or scanned back into the authority graph.

## Release gates

Run from a clean engine worktree:

```sh
uv run python -m unittest discover -s tests -v
uv build --out-dir build/release/0.4.0
uv run python scripts/check_distribution.py --dist-root build/release/0.4.0
```

Then verify that:

- wheel and sdist contain Python modules, native static frontend assets, every
  current JSON Schema, product Skill, workflow manifest, workflow guide, and
  `.codex/agents` preset;
- an isolated environment installs the wheel and runs `kgdistiller --help`;
- installed `kgdistiller`/`kgdistiller.exe` registers and queries a vault from
  an unrelated working directory on Linux, Windows, and macOS;
- help exposes no profile, embedding, database, provider, or materialization
  command/flag;
- a Markdown/Typst/LaTeX fixture passes sync, check, `agent status`, exact and
  lexical/graph query, MCP smoke, and loopback browser smoke tests;
- GraphView load detects a generation change and never returns mixed old/new
  graph records;
- `kgdistiller-retrieval-plan-v1` rejects `semantic_queries` and all results bind to one
  snapshot and graph digest;
- transactional plan/apply, idempotency, stale preconditions, lock conflict,
  fault injection, crash recovery, and old/new reader isolation pass;
- `store snapshot` and `store verify` cover in-place and separate snapshots,
  safe paths, digest failures, and a cold clone immediately queried without
  materialization;
- static export passes its packaged schema and dependency-free verifier,
  rejects dirty/untracked instance inputs, and preserves an existing valid
  destination on failed `--replace`;
- Obsidian export validates its managed boundary, rejects unsafe/unmanaged
  replacement, and can be regenerated solely from the native graph;
- every materially updated Skill passes the active `skill-creator` validator,
  product doctor, and an isolated Agent evaluation;
- POSIX and Windows copy/link doctor tests preserve unrelated Codex files;
- no credential, personal graph, authority note, generated store, build
  artifact, or private fixture is tracked in the product repository.

## Static consumer release order

1. Publish and verify the kgdistiller release commit and distributions.
2. Install that exact product in the authority project, run checks, and commit
   all instance inputs so its checkout is clean.
3. Create `export site` with exact producer and source repository provenance.
4. Run the bundled `verify_export.py` without kgdistiller installed.
5. Adopt and commit exactly the verified bundle bytes in the consumer.
6. Run consumer-specific checks and record manifest/export/graph digests.

Product release, authority generation, export generation, and consumer
adoption remain separate auditable events.

## Supply-chain checklist

Review the complete diff/status, version, changelog, schemas, and license.
Build from a clean tagged commit into an empty distribution directory, inspect
wheel/sdist contents, and smoke-install the wheel in an isolated environment.
Use short-lived or trusted publishing credentials and never commit tokens. Tag
only after all gates pass; do not move a published tag. Keep the previous
release available for authority recovery, while treating 0.4 data/API changes
as intentionally incompatible.
