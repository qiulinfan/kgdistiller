# Transactional ingest contract

`transactional-ingest-v1` is kgdistiller's only high-level personal-knowledge
write API. It accepts already reviewed semantic decisions and commits authority
documents, the `qlkg-v2` graph, reviewed alignments, the generated Typst
registry, and the disposable Agent index as one client-visible transaction.

The engine does not discover concepts or decide ambiguous identity during
ingest. Extractors and `query-kgdistiller` must complete those decisions first.

## Commands and Python API

```sh
kgdistiller --repo-root PROJECT ingest plan request.json --output plan.json
kgdistiller --repo-root PROJECT ingest apply request.json --receipt receipt.json
```

The equivalent Python entry points are:

```python
from kgdistiller.ingest import IngestPaths, apply_ingest, plan_ingest

plan = plan_ingest(paths, request)
receipt = apply_ingest(paths, request)
```

Planning runs the complete transaction in isolated staging and never modifies
the live project. Applying repeats validation after acquiring the repository's
single-writer lock. The request `mode` must match the selected operation.

## Request schema

The packaged JSON Schema is
`kgdistiller/schemas/qlkg-ingest-request-v1.schema.json`. Unknown top-level and
transaction-control fields are rejected. Requests are limited to 8 MiB, 128
authority patches, 2 MiB per authority, 4096 candidate decisions, and 1024
alignment decisions.

```json
{
  "schema": "qlkg-ingest-request-v1",
  "request_id": "paper-example-import-1",
  "request_sha256": "<canonical digest>",
  "mode": "plan",
  "capabilities": ["transactional-ingest-v1"],
  "base_graph_sha256": "<query target graph>",
  "base_alignment_sha256": "<query target alignments>",
  "candidate_snapshot": {
    "path": "knowledge/build/paper.snapshot.json",
    "sha256": "<snapshot_sha256>"
  },
  "query_report": {
    "path": "knowledge/build/paper.comparison.json",
    "sha256": "<canonical report digest>"
  },
  "authority_patches": [
    {
      "path": "notes/research/paper.md",
      "operation": "write",
      "expected_sha256": null,
      "content": "> **Definition: --[[New concept]]--**\n> ...\n",
      "content_sha256": "<UTF-8 content digest>",
      "expected_markers": {
        "definitions": ["new-concept"],
        "references": []
      }
    }
  ],
  "decisions": [
    {
      "candidate_id": "new-concept",
      "action": "add",
      "target_id": "new-concept",
      "evidence": "The reviewed paper definition is absent from the personal graph."
    }
  ],
  "delta": {
    "schema": "qlkg-agent-delta-v2",
    "remove_nodes": [],
    "nodes": [],
    "edges": [],
    "remove_edges": []
  },
  "alignment_decisions": [],
  "review": {
    "status": "reviewed",
    "reviewer": "local-user",
    "evidence": ["Reviewed comparison and source patch."],
    "provenance": [{"path": "paper.pdf", "page": 7}]
  }
}
```

`request_sha256` is SHA-256 over compact UTF-8 JSON with sorted object keys and
no insignificant whitespace after removing `request_sha256`. Changing `mode`
from `plan` to `apply` therefore requires recomputing the digest.

Every candidate snapshot node must have exactly one decision. `conflict` and
`uncertain` query results may only be rejected or deferred. Known concepts may
not be added as new identities. The query report must refer to the exact
candidate snapshot and personal graph/snapshot in the request.

Authority paths must be relative, remain under the repository after symlink
resolution, use Markdown, Typst, or LaTeX, and match exactly one registered
bounded source glob. `expected_sha256` is the reviewed pre-edit hash, or `null`
only when the authority does not exist. `expected_markers` is the complete
post-edit set of definition node IDs and reference target IDs for that file.

## Execution and transaction boundary

The engine executes these stages:

1. validate the JSON Schema, canonical request digest, capability, artifact
   digests, source ownership, source hashes, graph digest, alignment digest,
   review coverage, and query binding;
2. copy registered authorities and committed graph state into isolated staging;
3. apply exact reviewed authority replacements or deletions;
4. scan and verify the complete expected marker/ref state;
5. synchronize once to materialize new marker-derived stable IDs;
6. apply the reviewed `qlkg-agent-delta-v2`;
7. synchronize the selected scope and apply reviewed alignment decisions;
8. run scoped curation and a full deterministic graph validation;
9. back up every live target and write a recovery journal;
10. install sources, alignments, graph, and generated registry while holding the
    single-writer lock;
11. rebuild the disposable SQLite index from the committed graph, carrying
    forward only embeddings whose canonical input/provider/model/dimensions
    remain current;
12. persist the canonical receipt, mark the journal committed, and remove the
    backup.

If the process stops before the journal is marked committed, the next apply
restores every backed-up target before validating another request. SQLite is
also restored or rebuilt, but remains disposable.

## Receipt and idempotency

The packaged receipt schema is
`kgdistiller/schemas/qlkg-ingest-receipt-v1.schema.json`. A committed receipt
contains engine capabilities, before/after graph and alignment digests,
before/after source hashes, node/ref/edge/alignment changes, validation stages,
durations, warnings, and `receipt_sha256`.

Applying the same canonical request again returns its stored receipt without
reapplying changes. Reusing a `request_id` with different canonical content is
rejected. Receipts and journals live below the configured database directory in
`kgdistiller-ingest/`; this directory is derived local state and should be
ignored with the rest of `knowledge/build/`.

Receipts never include authority bodies, paper contents, credentials, or model
configuration.

## Stable errors

CLI failures use `qlkg-ingest-error-v1`. Important codes include:

- `unsupported-schema`, `unsupported-capability`, `invalid-request`,
  `invalid-request-digest`, `request-too-large`;
- `unsafe-project-path`, `unsafe-source-path`, `source-ownership`;
- `stale-base-graph`, `stale-base-alignment`, `stale-source`,
  `stale-query-report`;
- `incomplete-review`, `unresolved-identity`, `duplicate-identity`,
  `marker-state-mismatch`;
- `scan-failed`, `delta-failed`, `sync-failed`, `alignment-failed`,
  `curation-failed`, `global-validation-failed`;
- `lock-conflict`, `request-id-conflict`, `install-failed`, and
  `rollback-failed`.

Any rejection before installation performs zero writes. Any installation
failure restores the authority, graph, alignment, generated registry, and index
backups before returning. A `rollback-failed` result requires manual recovery
from the journal backup before further writes.
