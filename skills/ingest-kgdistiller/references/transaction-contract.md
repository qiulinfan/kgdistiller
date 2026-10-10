# Transaction contract

## Knowledge and identity

A knowledge node is one reviewed entry `.knowledge/entries/<id>.md`; accepted
semantic edges are lines of `.knowledge/edges.jsonl`. Identity is created only
by a reviewed `create_entries` record. Headings, ordering, syntax wrappers,
abbreviations, lexical similarity, translation, and graph proximity are not
identity evidence. Labels and aliases are unique across the store under NFKC
normalization, casefolding and whitespace collapsing. A rename is an update
whose new label differs; keep the old label as an alias.

A source is registered when a glob under `bases.B.sources` in
`$KGDISTILLER_HOME/config.json` matches it; the glob maps it to exactly one
user-authored type `$KGDISTILLER_HOME/types/<type>.md`, whose `node_kinds` and
`guidance` are independent of format and knowledge domain. Sources are any
UTF-8 text documents, cited by base-relative path and line range, and are never
edited or converted by ingest.

Every semantic edge is direct, typed, and supported by concrete evidence.
Conflicting or uncertain identities block their own operation.

## Request shape

```json
{
  "schema": "kgdistiller-ingest-request-v1",
  "request_id": "curate-chapter-2",
  "mode": "plan",
  "capabilities": ["transactional-ingest-v1"],
  "delta": {
    "schema": "kgdistiller-agent-delta-v1",
    "create_entries": [
      {
        "id": "measure-space",
        "label": "Measure space",
        "kind": "definition",
        "aliases": [],
        "source": "notes/measure.tex",
        "line_start": 42,
        "line_end": 47,
        "understanding": "unknown",
        "summary": "A measurable space equipped with a measure.",
        "evidence": "THE EXACT TEXT OF LINES 42-47"
      }
    ],
    "update_entries": [],
    "remove_entries": [],
    "add_edges": [
      {
        "source": "sigma-algebra",
        "relation": "prerequisite-for",
        "target": "measure-space",
        "origin": "agent",
        "confidence": "high",
        "evidence": "The definition of a measure space requires a sigma-algebra."
      }
    ],
    "remove_edges": []
  },
  "review": {
    "status": "reviewed",
    "reviewer": "curation-agent",
    "evidence": ["agent resolve found no entry named Measure space."],
    "provenance": [{"source": "notes/measure.tex", "line_start": 42, "line_end": 47}]
  }
}
```

All five delta keys are required. An entry record holds `id`, `label`, `kind`,
`aliases`, `source`, `line_start`, `line_end`, `understanding`, `summary` and
`evidence`, plus the optional `context`, `role`, `prerequisites`,
`pending_prerequisites`, `common_confusions` and `open_questions`. An update is
a full replacement of the record and carries `expected_label`; a removal names
`id` and `expected_label`. Removing an entry does not cascade: remove its edges
in the same delta. The relations are `prerequisite-for`, `implies`,
`generalizes`, `contrasts-with` and `derived-from`; `prerequisite-for` stays
acyclic. `request_id` is readable (`[A-Za-z0-9._-]`, at most 128 characters).

## Plan and apply

`ingest plan` applies the delta to the current store in memory, validates the
result, and returns a readable `kgdistiller-ingest-plan-v1`:
`{schema, request_id, status: planned, changes, counts{before, after}}`. It
writes nothing to the store.

`ingest apply` takes the home lock (`$KGDISTILLER_HOME/lock`), finishes or rolls back any interrupted
earlier install, and re-validates the same delta against the current store:

- update and remove targets exist with their `expected_label`;
- created ids, labels and aliases collide with nothing;
- every created or updated entry's `evidence` equals its cited source lines
  now;
- every kind is allowed by its source's document type;
- every source is a UTF-8 file matched by the base's globs with exactly one
  type (otherwise `source-not-registered`), and every line range is in bounds;
- no registered file of the base, cited or not, matches globs of two different
  types (otherwise `source-type-conflict`, which refuses every write to the base
  until its globs in `$KGDISTILLER_HOME/config.json` are fixed);
- every edge endpoint exists after the delta, and `prerequisite-for` stays
  acyclic;
- the whole resulting store validates.

It then installs the changed entry files and `edges.jsonl` atomically through a
recovery journal and writes the receipt
`.knowledge/build/kgdistiller-ingest/receipts/<request_id>.json`:
`{schema: kgdistiller-ingest-receipt-v1, request_id, status: committed, request,
changes, counts{entries, edges}}`.

Reapplying an identical request returns its stored receipt. Reusing a
`request_id` with a different request fails with `request-conflict`.

## Stable error codes

`invalid-request`, `invalid-store`, `lock-conflict`, `missing-entry`,
`label-mismatch`, `entry-exists`, `identity-collision`, `missing-source`,
`source-not-registered`, `source-type-conflict`, `line-range`, `stale-evidence`, `kind-not-allowed`,
`missing-edge`, `invalid-edge`, `dangling-edge`, `cycle`, `request-conflict`,
`install-failed`. Errors are printed as a `kgdistiller-ingest-error-v1`
envelope. Any rejection before installation leaves the store unchanged; an
installation failure restores every target before returning.

Do not hand-edit `.knowledge/edges.jsonl` or substitute hand edits of entries
for a transaction. If rollback fails, stop writers, preserve the journal and its
backups under `.knowledge/build/kgdistiller-ingest/`, and recover from them or a
known-good Git revision.

## Downstream state and handoff

Run `kgd check --base B` after every commit. Refreshing the Obsidian graph feed
(`export obsidian`) is a separate action; the feed under
`.knowledge/build/obsidian/` is derived and must not be scanned or ingested
back.

Return request and plan paths, reviewed findings, the committed receipt,
post-commit checks and blocked operations. A committed ingest receipt does not
authorize Git actions or remote pushes.
