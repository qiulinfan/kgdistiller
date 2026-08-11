# Public release and compatibility policy

This document prepares a release; it does not authorize pushing a branch,
publishing a package, creating a GitHub release, or uploading personal data.

## Version 0.3 compatibility matrix

| Contract | Read | Write | Notes |
| --- | --- | --- | --- |
| `qlkg-v2` | yes | yes | Existing committed graphs remain authoritative. |
| `qlkg-sources-v2` | yes | yes | Markdown, Typst, and LaTeX sources are supported. |
| `qlkg-identities-v1` | yes | yes | Reviewed authored-name changes only. |
| `qlkg-alignments-v1` | yes | yes | Fingerprint-bound reviewed mappings. |
| `qlkg-agent-delta-v2` | yes | yes | Low-level compatibility primitive. |
| `qlkg-agent-snapshot-v1` | yes | yes | Self-contained hydrated query input. |
| `qlkg-agent-index-v2` | yes | disposable | Rebuilt locally; never a migration authority. |
| `qlkg-store-v1` | yes | yes | Portable authority generation; Git-friendly manifest. |
| `qlkg-document-record-v1` | yes | yes | Canonical JSONL inventory of ingested sources. |
| `qlkg-embedding-bundle-v1` | yes | legacy only | Published four-key provider digest contract; new snapshots do not write it. |
| `qlkg-embedding-record-v1` | yes | legacy only | Legacy digest is recomputed and retained exactly on materialization. |
| `qlkg-embedding-bundle-v2` | yes | yes | Current exact portable retrieval artifacts, never identity. |
| `qlkg-embedding-record-v2` | yes | yes | Four-field logical key with an exact opaque machine-local provider-config digest. |
| `qlkg-candidate-graph-v1` | yes | builder input | Isolated namespace and source locations required. |
| `qlkg-ingest-request-v1` | yes | accepted | Content-addressed plan/apply request. |
| `qlkg-ingest-plan-v1` | output | output | Review artifact, not a commit receipt. |
| `qlkg-ingest-receipt-v1` | output | output | Canonical committed/rejected result. |
| `qlkg-local-profile-v1` | yes | user-authored | Machine-local paths and credential environment-variable names; never portable. |
| `qlkg-embedding-policy-v1` | yes | user-authored | Portable vector-space and required-coverage policy without credentials; drives local status/sync. |
| `qlkg-retrieval-plan-v1` | yes | accepted input | Bounded lane-specific query input for Python, CLI, and MCP. |
| `qlkg-search-result-v2` | output | output | Bounded per-lane status, deterministic fusion, and evidence. |
| `qlkg-search-execution-v1` | output | output | Immutable plan-mode, generation, and identity-resolution envelope; its nested `qlkg-search-result-v2` is validated separately. |
| `qlkg-document-record-v2` | proposed | proposed | Stable inventory identity only; it does not define graph node identity. |
| `qlkg-document-upsert-request-v1` | proposed | proposed | Reviewed annotated-document input; plan/apply behavior is deferred. |
| `qlkg-document-ingest-receipt-v1` | proposed | proposed | Resumable stage output; enrichment orchestration is deferred. |
| MCP `2024-11-05` through `2025-11-25` | yes | read-only | No MCP mutation tools. |

The package requires Python 3.9 or newer. The required Windows-host acceptance
matrix is:

| Environment | Python coverage | Typst | Role |
| --- | --- | --- | --- |
| Windows native | 3.13 CI job; 3.14.6 local pass | 0.15.1 | supported and required |
| WSL Ubuntu 26.04 | 3.14.4 local pass | 0.15.1 | supported and required |
| macOS GitHub runner | 3.13 CI job | 0.15.1 | supported release gate |
| Ubuntu GitHub runner | 3.9, 3.11, 3.13 compatibility jobs | 0.15.1 | supported release gate |

Every full-suite CI job uses Typst 0.15.1. The deterministic core has no runtime
model provider dependency. The optional `openai-compatible` embedding adapter
uses only the Python standard library and reads credentials from the configured
environment variable. Typst is an external requirement only when Typst-authored
labels must be rendered.

The adapter requires HTTPS except for numeric-loopback HTTP fixtures, validates
bounded header-safe bearer tokens, and applies one monotonic deadline to HTTP
status, header, and body streaming after operating-system resolver/socket setup.
Resolver and multi-address connection latency is OS-governed and is classified
as a timeout when it returns after the deadline. Provider configuration,
transport, framing, JSON, and vector failures must have stable structured codes
with no retained credential, response body, or raw exception chain.

`embedding status` reads every profile in the portable policy without creating
a provider. `embedding sync` is the only CLI document-vector maintenance path:
it batches only eligible missing/stale inputs, bounds retries and total work,
validates the graph generation before one atomic publication, and is a provider
no-op on a second unchanged run. Query paths do not invoke document sync. This
release does not yet use embedding coverage as a `store snapshot` or
`store verify` readiness gate, so a valid portable store must not be advertised
as RAG-ready on that basis alone.

`agent search`, `agent context`, `kg_search`, and `kg_build_context` execute a
bounded retrieval plan or adapt one legacy query. They never materialize an
index or call document embedding. The semantic lane requires matching current
materialized vectors and makes at most one query-only batch for all semantic
expressions in a plan. Results bind to one snapshot/graph generation, preserve
machine-readable ambiguity, and report lane-local degradation without exposing
provider configuration or credentials through MCP arguments.

## Schema evolution

- A published schema name is immutable.
- Additive fields must be preserved by readers that round-trip compatible data.
- A changed invariant, required field, identity meaning, or digest algorithm
  requires a new schema version.
- A writer must never silently downgrade or destructively rewrite an unknown
  schema.
- A migration must be explicit, deterministic, reversible from Git, and tested
  on copies of old fixtures.
- Candidate/personal namespaces and bridges remain separate across migrations.

When a future release needs migration, ship a dry-run report first. It must
state input/output schemas, affected paths, before/after digests, losses or
defaults, rollback instructions, and validation commands. Do not couple a data
migration to an Agent's semantic inference.

## Release gates

Run from a clean engine worktree:

```sh
uv run python -m unittest discover -s tests -v
uv build
uv run python scripts/check_distribution.py
```

Then verify:

- wheel and sdist contain Python modules, static browser assets, and all JSON
  Schemas;
- an isolated environment can install the wheel and run `kgdistiller --help`;
- that installed wheel can load the default local profile in two fresh
  processes, apply database/store/embedding-profile overrides, expose the same
  non-secret configuration digest, and keep credential sentinels out of output;
- that installed wheel exposes `embedding status` and `embedding sync`, resolves
  a repository-relative policy, synchronizes the selected/overridden profile,
  and performs zero document calls on an unchanged second invocation;
- that the installed wheel loads a retrieval plan, executes planned and legacy
  search/context in fresh processes, validates the nested v2 result, makes zero
  document calls, and leaves missing/stale indexes unpublished;
- plain `PYTHONPATH=src python3` imports candidate and ingest without undeclared
  dependencies;
- a Markdown/Typst/LaTeX fixture passes sync and check;
- transactional plan/apply, idempotency, stale preconditions, lock conflict,
  fault injection, crash recovery, and old/new reader isolation pass;
- embedding category/coverage, batch/retry/work bounds, provider failure,
  single-node invalidation, stale-generation rejection, and unchanged-vector
  byte preservation pass without default network access;
- the 100,000-node disposable stress harness records a Windows-native or WSL
  baseline before any performance envelope is used as a release gate; the
  historical baseline in [the performance notes](performance.md) is
  informational only;
- the semantic benchmark supports 1k, 10k, and 100k ready-vector cases and
  records p50/p95/max, provider call counts, exact-scan limits, and byte-stable
  read-only evidence;
- Skills pass structural validation and a real isolated Agent evaluation;
- no credential, personal graph, authority note, generated SQLite, build
  artifact, or stress fixture is tracked.

Host integration must separately run its knowledge workflow, graph check,
website check, and production build against the exact engine commit being
released.

## Supply-chain and publishing checklist

1. Review `git diff`, `git status`, the version, changelog, and license.
2. Build from a clean tagged commit; do not reuse old `dist/` contents.
3. Inspect wheel/sdist file lists and install the wheel in an empty environment.
4. Prefer a short-lived or trusted publishing mechanism; never commit a PyPI or
   GitHub token.
5. Publish kgdistiller before updating a public host's submodule pointer.
6. Create checksums for the exact distributions and attach them to the release.
7. Tag only after all release gates pass; do not move a published tag.
8. Update the host repository and repeat its gates before publishing the host.
9. Keep the previous compatible release available for rollback.

## Release order for the current host

The qlblog integration references engine behavior introduced by kgdistiller.
The safe order is:

1. merge and publish the kgdistiller release commit;
2. verify that the remote commit and package contain the transactional ingest
   schemas, candidate builder, query/index consistency fix, bounded GraphRAG,
   and stress harness;
3. update qlblog's submodule pointer to that published commit;
4. run qlblog knowledge and site gates;
5. publish qlblog.

Reversing this order leaves the public host pointing at an unavailable engine
commit.
