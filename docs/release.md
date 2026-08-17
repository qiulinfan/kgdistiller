# Public release and compatibility policy

This document defines release gates. It does not authorize a push, package
publication, tag, GitHub release, or disclosure of personal knowledge.

## Version 0.4 contract matrix

| Contract | Read | Write | Role |
| --- | --- | --- | --- |
| `kgdistiller-vault-v1` | yes | yes | Portable stable vault identity. |
| `kgdistiller-vault-registry-v1` | yes | yes | Machine-local name/UUID/path locator. |
| `qlkg-v3` | yes | yes | Deterministic authority graph. |
| `qlkg-sources-v3` | yes | yes | Bounded Markdown/Typst/LaTeX registry. |
| `qlkg-identities-v2` | yes | yes | Reviewed authored-name changes and aliases. |
| `qlkg-scoped-aliases-v1` | yes | nested | Collision-aware aliases within one authority scope. |
| `qlkg-alignments-v2` | yes | yes | Bounded fingerprint-bound reviewed mappings. |
| `qlkg-agent-delta-v3` | yes | yes | Reviewed semantic graph delta. |
| `qlkg-agent-snapshot-v2` | yes | yes | Bounded self-contained hydrated graph generation. |
| `qlkg-query-status-v1` | yes | yes | GraphView status and generation binding. |
| `qlkg-retrieval-plan-v2` | yes | yes | Identity, lexical, and bounded graph plan. |
| `qlkg-search-result-v3` | yes | yes | Deterministic lane results. |
| `qlkg-search-execution-v2` | yes | yes | Query execution/generation envelope. |
| `qlkg-context-bundle-v2` | yes | yes | Budgeted source-backed query context. |
| `qlkg-alignment-report-v2` | yes | output | Conservative cross-namespace alignment report. |
| `qlkg-graph-comparison-v2` | yes | output | Matched-node and edge-presence comparison. |
| `qlkg-agent-proposal-v2` | yes | output | Non-mutating review proposal package. |
| `qlkg-candidate-graph-v2` | yes | yes | Bounded isolated source-grounded candidates. |
| `qlkg-ingest-request-v2` | yes | yes | Transactional reviewed write request. |
| `qlkg-ingest-plan-v1` | yes | output | Staged review result, not a receipt. |
| `qlkg-ingest-receipt-v2` | yes | yes | JSON-memory committed-write receipt. |
| `qlkg-ingest-error-v1` | yes | output | Stable transactional failure envelope. |
| `qlkg-document-record-v1` | yes | output | Canonical store authority inventory row. |
| `qlkg-store-v2` | yes | yes | JSON-only portable generation. |
| `qlkg-store-report-v1` | yes | output | Verified store operation result. |
| `qlkg-site-graph-v1` | yes | output | Privacy-filtered hydrated site graph. |
| `qlkg-static-export-v2` | yes | yes | Verifiable privacy-filtered site export. |
| `qlkg-static-export-report-v1` | yes | output | Static export operation and cleanup result. |
| `qlkg-static-export-verification-v1` | yes | output | Standalone verifier result. |
| `qlkg-obsidian-projection-v1` | yes | yes | Lossy downstream Obsidian projection. |
| `qlkg-obsidian-export-report-v1` | yes | output | Obsidian build or verification result. |
| `qlkg-obsidian-concept-v1` | yes | output | Generated concept-note frontmatter tag. |
| `qlkg-obsidian-source-v1` | yes | output | Generated source-proxy frontmatter tag. |
| `qlkg-curation-check-v1` | yes | output | Scoped curation readiness report. |
| `qlkg-audit-v1` | yes | output | Whole-graph deterministic audit report. |

Published schema names are immutable. A changed invariant, required field,
identity meaning, or digest algorithm requires a new schema version. Readers
must fail closed on unknown incompatible schemas rather than silently
downgrading them.

## Breaking boundary from 0.3

Version 0.4 removes the SQLite Agent index and every embedding, provider,
machine-profile, database override, and store-materialization path. It also
removes v1 retrieval/execution/result and portable-store compatibility from the
active product boundary.

There is no automatic core-contract or database migration. Version 0.4 refuses
the 0.3 `qlkg-v2` graph, `qlkg-sources-v2`, `qlkg-identities-v1`, and
`qlkg-agent-delta-v2` discriminators. Before upgrading, commit the native
authorities and reviewed registries so Git history provides an exact rollback
point. While still on 0.3, export any Agent-curated entries or semantic edges
that must survive for later human review. With 0.4 installed, explicitly move
the old generated `knowledge/graph/` outside the project or delete that exact
directory after confirming the rollback commit. Review and update the
source-registry discriminator to `qlkg-sources-v3` and the optional
identity-registry discriminator to `qlkg-identities-v2`, then run an unscoped
`sync` to rebuild a fresh `qlkg-v3` graph from the native Markdown, Typst, and
LaTeX authorities. Reissue any still-needed reviewed metadata as
`qlkg-agent-delta-v3`; do not relabel an old delta without re-reviewing it
against the rebuilt generation. The rebuild restores marker-derived nodes and
references but intentionally discards un-reissued 0.3 Agent-curated entries and
semantic edges.

Databases and vectors were derived data and are not migrated. After rebuilding,
create and verify a new `qlkg-store-v2` snapshot. If only a `qlkg-store-v1`
copy survives, first restore its native authorities with the earlier release;
0.4 intentionally does not interpret the old store or core graph.

Version 0.4 also refuses the 0.3 `qlkg-static-export-v1` manifest. Rebuild a
consumer bundle as `qlkg-static-export-v2` from the verified v3 authority graph
instead of relabeling or reusing old bundle bytes.

Retrieval clients must emit `qlkg-retrieval-plan-v2`, omit
`semantic_queries`, and consume `qlkg-search-execution-v2` with nested
`qlkg-search-result-v3` plus `qlkg-context-bundle-v2`. The 0.3 context-bundle,
alignment-report, graph-comparison, and Agent-proposal shapes are incompatible;
0.4 emits only their v2 discriminators and binds alignment, comparison, and
proposal review artifacts to `alignment_sha256`. A verified v2 store clone is
immediately queryable; do not call or emulate a materialization command.

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
- retrieval plan v2 rejects `semantic_queries` and all results bind to one
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
