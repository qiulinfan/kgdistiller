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
| `qlkg-embedding-bundle-v1` | yes | yes | Exact portable retrieval artifacts, never identity. |
| `qlkg-embedding-record-v1` | yes | yes | Content-addressed float32 vector metadata. |
| `qlkg-candidate-graph-v1` | yes | builder input | Isolated namespace and source locations required. |
| `qlkg-ingest-request-v1` | yes | accepted | Content-addressed plan/apply request. |
| `qlkg-ingest-plan-v1` | output | output | Review artifact, not a commit receipt. |
| `qlkg-ingest-receipt-v1` | output | output | Canonical committed/rejected result. |
| `qlkg-local-profile-v1` | proposed | proposed | Machine-local paths and credential environment-variable names; never portable. |
| `qlkg-embedding-policy-v1` | proposed | proposed | Portable vector-space and required-coverage policy without credentials. |
| `qlkg-retrieval-plan-v1` | proposed | proposed | Bounded lane-specific query input; execution is deferred. |
| `qlkg-search-result-v2` | proposed | proposed | Bounded per-lane status and fusion evidence; public wiring is deferred. |
| `qlkg-document-record-v2` | proposed | proposed | Stable inventory identity only; it does not define graph node identity. |
| `qlkg-document-upsert-request-v1` | proposed | proposed | Reviewed annotated-document input; plan/apply behavior is deferred. |
| `qlkg-document-ingest-receipt-v1` | proposed | proposed | Resumable stage output; enrichment orchestration is deferred. |
| MCP `2024-11-05` through `2025-11-25` | yes | read-only | No MCP mutation tools. |

Python 3.9 and newer are supported. The deterministic core has no runtime model
provider dependency. Typst is an external requirement only when Typst-authored
labels must be rendered.

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
```

Then verify:

- wheel and sdist contain Python modules, static browser assets, and all JSON
  Schemas;
- an isolated environment can install the wheel and run `kgdistiller --help`;
- plain `PYTHONPATH=src python3` imports candidate and ingest without undeclared
  dependencies;
- a Markdown/Typst/LaTeX fixture passes sync and check;
- transactional plan/apply, idempotency, stale preconditions, lock conflict,
  fault injection, crash recovery, and old/new reader isolation pass;
- the 100,000-node disposable stress harness passes the reference-machine
  envelope in [the performance baseline](performance.md), or the deviation is
  documented as a release blocker;
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
