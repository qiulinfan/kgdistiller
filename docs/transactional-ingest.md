# Transactional ingest contract

`transactional-ingest-v1` is kgdistiller's only high-level personal-knowledge
write API. It accepts one reviewed delta of entries and edges and commits it as
a single client-visible transaction: the changed `.knowledge/entries/<id>.md`
files and `.knowledge/edges.jsonl` are installed together or not at all.

Ingest does not discover concepts or decide ambiguous identities. Resolve them
through the read-only query surface first. Source documents are only read;
ingest never edits them.

## Commands and Python API

```sh
kgdistiller --repo-root PROJECT ingest plan request.json --output plan.json
kgdistiller --repo-root PROJECT ingest apply request.json --receipt receipt.json
```

```python
from kgdistiller.ingest import IngestPaths, apply_ingest, plan_ingest

paths = IngestPaths(repo_root=project, registry=project / ".knowledge/sources.json")
plan = plan_ingest(paths, request)
receipt = apply_ingest(paths, request)
```

The request `mode` must match the selected operation. `capture prepare` writes
a plan request and an apply request for one reviewed capture, and
`harvest apply` builds and applies one request for the checked sheet items.

## Request

The packaged schema is
`kgdistiller/schemas/kgdistiller-ingest-request-v1.schema.json`. A request is
`{schema, request_id, mode, capabilities, delta, review}`:

- `request_id`: readable, `[A-Za-z0-9._-]`, at most 128 characters, such as
  `capture-measure-space-1`;
- `mode`: `plan` or `apply`;
- `capabilities`: contains `transactional-ingest-v1`;
- `delta`: one `kgdistiller-agent-delta-v1`;
- `review`: `status: reviewed`, the `reviewer`, the identity `evidence` and the
  source `provenance`.

The delta has exactly five lists:

| Key | Item |
|---|---|
| `create_entries` | A complete entry record. |
| `update_entries` | `{expected_label, entry}`; the entry fully replaces the current record. |
| `remove_entries` | `{id, expected_label}`. |
| `add_edges` | `{source, relation, target, origin, confidence, evidence}`. |
| `remove_edges` | `{source, relation, target}`. |

An entry record is `{id, label, kind, aliases, source, line_start, line_end,
understanding, summary, evidence}` plus the optional `context`, `role`,
`prerequisites`, `pending_prerequisites`, `common_confusions` and
`open_questions`; see the [knowledge contract](graph-contract.md). Removing an
entry does not cascade: its edges must be removed in the same delta.

## Plan

Planning applies the delta to the current store in memory, validates the
result and writes nothing to the store. It returns a readable
`kgdistiller-ingest-plan-v1`:

```json
{
  "schema": "kgdistiller-ingest-plan-v1",
  "request_id": "capture-measure-space-1",
  "status": "planned",
  "changes": {
    "entries_created": ["measure-space"],
    "entries_updated": [],
    "entries_removed": [],
    "aliases_changed": ["measure-space"],
    "edges_added": [],
    "edges_removed": []
  },
  "counts": {"before": {"entries": 315, "edges": 219}, "after": {"entries": 316, "edges": 219}}
}
```

## Apply: lock plus semantic re-validation

Apply holds the single-writer lock for the whole operation:

1. finish or roll back an interrupted earlier install from its journal;
2. if a receipt for `request_id` exists, return it when the stored request is
   identical (compared as canonical JSON text) and fail with `request-conflict`
   otherwise;
3. re-validate the delta against the current store and current source text:
   update and remove targets exist with their `expected_label`; created ids,
   labels and aliases collide with nothing; every created or updated entry's
   Evidence equals its cited source lines now; kinds are allowed by the
   sources' document types; post-delta edge endpoints exist;
   `prerequisite-for` stays acyclic; and the whole resulting store validates;
4. write a journal, back up every target, and install the changed entry files
   and `edges.jsonl` atomically; journal targets are restricted to
   `entries/*.md` and `edges.jsonl`, and backups and staging are named by
   `request_id`;
5. write the receipt and mark the journal committed.

There is no content hash of the base store: concurrent writers are excluded by the
lock, and staleness is detected by re-validating meaning and source text at
apply time. Nothing needs rebuilding afterwards; the next reader loads the new
files. While a journal exists, readers and `check` fail with a clear error
instead of reading a partial install, and the next `ingest apply` recovers it.
Preserve a degraded journal and its backups for manual recovery; never delete
them to hide a mixed state.

## Receipt and idempotency

The receipt is written to
`.knowledge/build/kgdistiller-ingest/receipts/<request_id>.json` and validated
against `kgdistiller-ingest-receipt-v1`:

```json
{
  "schema": "kgdistiller-ingest-receipt-v1",
  "request_id": "capture-measure-space-1",
  "status": "committed",
  "request": {"...": "the applied request"},
  "changes": {"entries_created": ["measure-space"], "...": []},
  "counts": {"entries": 316, "edges": 219}
}
```

Accept success only when `status` is `committed`; then run `kgdistiller check`
and compare `agent status` counts with the receipt. Reapplying an identical
request returns its stored receipt. Receipts and journals are local state below
`.knowledge/build/` and are never tracked. Git commit, remote push and the
Obsidian graph feed are separate actions and require explicit scope.

## Stable failure behavior

Errors are printed as a `kgdistiller-ingest-error-v1` envelope
`{schema, error: {code, message, stage, diagnostics}}`. The codes are:

| Code | Meaning |
|---|---|
| `invalid-request` | The request or delta violates its schema or shape. |
| `invalid-store` | The current store, a receipt or the journal cannot be read. |
| `lock-conflict` | Another writer holds the lock, or an interrupted ingest is pending (for `plan`). |
| `missing-entry` | An update or removal target does not exist. |
| `label-mismatch` | A target's current label differs from `expected_label`. |
| `entry-exists` | A created id already exists. |
| `identity-collision` | A label or alias is already used by another entry. |
| `missing-source` | A cited source is missing, unsafe or not UTF-8 text. |
| `source-not-registered` | No single registered source admits a cited path. |
| `line-range` | A cited range exceeds the source's line count. |
| `stale-evidence` | An entry's Evidence differs from its cited lines. |
| `kind-not-allowed` | A kind is not in the source document type's `node_kinds`. |
| `missing-edge` | A removed edge does not exist. |
| `invalid-edge` | An edge has an unknown relation or malformed fields. |
| `dangling-edge` | An edge endpoint has no entry after the delta. |
| `cycle` | `prerequisite-for` would contain a cycle. |
| `request-conflict` | The `request_id` was committed with a different request. |
| `install-failed` | Installation or receipt writing failed; targets were restored. |

Any rejection before installation performs zero store writes. An installation
failure restores every backed-up target before returning.
