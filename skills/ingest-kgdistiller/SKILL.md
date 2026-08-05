---
name: ingest-kgdistiller
description: Apply a reviewed, source-backed knowledge update through kgdistiller's transactional ingest API and return a canonical receipt. Use after query-kgdistiller or another extractor has decided identities, native authority markers, refs, entries, aliases, direct semantic edges, and optional mappings; for changed Markdown, Typst, or LaTeX knowledge; for explicitly authorized paper imports; or for reviewed cross-namespace alignment persistence.
---

# Ingest into kgdistiller

Be the only Skill that mutates the personal knowledge base. Execute reviewed
decisions; do not rediscover concepts, repeat GraphRAG, or call the legacy write
commands as a substitute for the transaction API.

## Load the write contract

Read `docs/graph-contract.md` and `docs/transactional-ingest.md` completely
before the first write. In qlblog read the copies under
`vendor/kgdistiller/docs/` and use `python3 knowledge/kgd.py`; elsewhere use
`kgdistiller --repo-root PROJECT`.

Start with `agent status`. Require `transactional-ingest-v1`,
`qlkg-ingest-request-v1`, and the exact target graph, snapshot, and alignment
digests from `$query-kgdistiller`. Return to query if any precondition is stale.

## Require a reviewed handoff

Require one bounded request containing:

- a decision for every candidate: reuse, add, update, reject, or defer;
- exact registered authority paths, expected source hashes, native patch
  contents, and complete post-patch marker/ref state;
- the candidate snapshot and query report paths with canonical digests;
- one reviewed `qlkg-agent-delta-v2`;
- optional reviewed alignment decisions with evidence and justification;
- review evidence and source provenance.

Reject unresolved `uncertain` or `conflict` candidates. Never convert them into
new identities. Preserve unrelated prose and user-authored markers. Paper
snapshots remain read-only unless the user explicitly authorizes selected
knowledge or mappings for import.

## Plan, review, then apply

1. Build a canonical `qlkg-ingest-request-v1` in `plan` mode. Compute
   `request_sha256` over canonical JSON excluding that field.
2. Run:

   ```sh
   kgdistiller ingest plan REQUEST.json --output PLAN.json
   ```

3. Review the predicted source, node, edge, ref, alignment, and digest changes.
   Planning must leave authority, graph, alignment, and index bytes unchanged.
4. Change only `mode` to `apply`, recompute `request_sha256`, then run:

   ```sh
   kgdistiller ingest apply REQUEST.json --receipt RECEIPT.json
   ```

5. Accept success only when the returned `qlkg-ingest-receipt-v1` has
   `status: committed`, every validation passed, its canonical digest verifies,
   and its `after` digests match `agent status`.
6. If `knowledge/store.json` exists, refresh and verify the portable generation:

   ```sh
   kgdistiller store snapshot
   kgdistiller store verify
   ```

   This captures exact embeddings already present in SQLite without calling a
   provider. If no portable store exists, report that the ingest is local-only
   and recommend `$deploy-kgdistiller`; do not silently initialize Git, commit,
   or push.

The engine owns locking, optimistic concurrency, staging, scan, delta apply,
sync, curation, global validation, atomic installation, crash recovery,
idempotency, and disposable-index rebuild. Do not reproduce those steps in this
Skill. A failed transaction must return its stable error code and leave
authority, graph, and alignment hashes unchanged.

## Return the receipt

Return the receipt path and a compact summary of:

- request, engine, schema, and capability versions;
- before/after graph, alignment, and authority hashes;
- nodes added, reused, updated, orphaned, or removed;
- refs, edges, alignments, and source patches changed;
- validation results, warnings, and unapplied review decisions.
- portable store generation, document count, and embedding count when refreshed;
- synchronization state as `local-only`, `committed locally`, or `remote
  confirmed`, with the latter two used only when the corresponding explicitly
  authorized Git operation has actually succeeded.

Do not include full authority content, paper text, credentials, or unbounded
evidence in the response.
