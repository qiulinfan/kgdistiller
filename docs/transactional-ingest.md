# Transactional ingest contract

`transactional-ingest-v1` is kgdistiller's only high-level personal-knowledge
write API. It accepts reviewed semantic decisions and commits identity
authorities, Markdown atomic entries, reviewed registries, and one deterministic
`kgdistiller-graph-v1` JSON generation as a single client-visible transaction.

Ingest does not discover concepts or decide ambiguous identities. Resolve and
compare through the generation-checked read-only query surface first.

## Commands and Python API

```sh
kgdistiller --repo-root PROJECT ingest plan request.json --output plan.json
kgdistiller --repo-root PROJECT ingest apply request.json --receipt receipt.json
```

```python
from kgdistiller.ingest import IngestPaths, apply_ingest, plan_ingest

plan = plan_ingest(paths, request)
receipt = apply_ingest(paths, request)
```

Planning executes the transaction in isolated staging and leaves the live
project unchanged. Apply obtains the single-writer lock and revalidates every
precondition. The request `mode` must match the selected operation.

## Request boundary

The packaged request schema is
`kgdistiller/schemas/kgdistiller-ingest-request-v1.schema.json`. A request binds:

- `request_id`, `request_sha256`, `mode`, and
  `capabilities: ["transactional-ingest-v1"]`;
- base graph and alignment digests from `agent status`;
- a content-addressed candidate snapshot and query report;
- exact registered authority patches, normalized expected source hashes, and
  complete post-patch marker/reference expectations;
- one reviewed `kgdistiller-agent-delta-v1`;
- optional reviewed alignment decisions;
- explicit review evidence and provenance.

`request_sha256` is the SHA-256 of canonical compact UTF-8 JSON with sorted
object keys after removing that field. Changing `mode` from `plan` to `apply`
requires recomputing it.

Every candidate must have one reviewed disposition. `conflict` and `uncertain`
results may only be rejected or deferred; they are never converted into new
identities. Authority paths must remain within the repository, match exactly
one bounded registered source, and use `.md`, `.typ`, or `.tex`.

Authority digests use UTF-8 text after CRLF/CR is normalized to LF. Raw checkout
bytes are not the transaction boundary.

## Atomic generation install

The engine:

1. validates schemas, canonical digests, capabilities, artifact bindings,
   source ownership, review coverage, and base generation;
2. copies registered authorities and committed graph state into staging;
3. applies the exact reviewed native patches and verifies marker/reference
   state;
4. synchronizes stable marker-derived identities, applies the reviewed delta
   and mappings, and runs scoped plus global deterministic validation;
5. backs up every live target and records a recovery journal;
6. installs identity authorities, `knowledge/entries/`, registries, graph
   artifacts, and the generated Typst registry while holding the writer lock;
7. persists the canonical receipt, marks the journal committed, and removes
   the backup.

There is no secondary database or embedding generation to rebuild. A fresh
reader loads the new JSON generation into `GraphView`; generation checks prevent
it from observing mixed files during the commit.

If the process stops before the journal is committed, the next apply restores
the recorded targets before accepting a new request. Preserve a degraded
journal and its backups for manual recovery; never delete them to hide a mixed
state.

## Receipt, idempotency, and store refresh

The packaged receipt schema is
`kgdistiller/schemas/kgdistiller-ingest-receipt-v1.schema.json`. Accept success only
when `status` is `committed`, the canonical receipt digest verifies, and its
after-digests match a fresh `agent status`.

Reapplying an identical canonical request returns its stored receipt. Reusing a
`request_id` with different content is rejected. Receipts and journals are
derived local state below `knowledge/build/` and must not contain authority
bodies, credentials, or model configuration.

When `knowledge/store.json` exists, refresh and verify the portable generation:

```sh
kgdistiller --repo-root PROJECT store snapshot
kgdistiller --repo-root PROJECT store verify
```

This records identity authorities, Markdown entry/evidence authorities, and the
deterministic JSON generation.
Git commit, remote push, static export, and Obsidian projection remain separate
authorities and require explicit scope.

## Stable failure behavior

Important stable codes include `unsupported-schema`, `unsupported-capability`,
`invalid-request`, `invalid-request-digest`, `unsafe-project-path`,
`unsafe-source-path`, `source-ownership`, `stale-base-graph`,
`stale-base-alignment`, `stale-source`, `stale-query-report`,
`incomplete-review`, `unresolved-identity`, `duplicate-identity`,
`marker-state-mismatch`, `scan-failed`, `delta-failed`, `sync-failed`,
`alignment-failed`, `curation-failed`, `global-validation-failed`,
`lock-conflict`, `request-id-conflict`, `install-failed`, and
`rollback-failed`.

Any rejection before installation performs zero live writes. An installation
failure restores all backed-up targets before returning; `rollback-failed`
requires manual recovery before another write.
