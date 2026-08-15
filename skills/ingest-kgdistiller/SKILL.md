---
name: ingest-kgdistiller
description: Apply a reviewed, source-backed knowledge update through kgdistiller's transactional ingest API and return a canonical receipt. Use after query-kgdistiller or another extractor has decided identities, native Markdown, Typst, or LaTeX authority markers, refs, entries, aliases, direct semantic edges, and optional mappings, or for explicitly authorized paper imports and reviewed cross-namespace alignment persistence.
---

# Ingest into kgdistiller

Be the only Skill that mutates the personal knowledge base. Execute reviewed
decisions; do not rediscover concepts or compose low-level writers as a
substitute for the transaction API.

## Align language

Match user-facing explanations, prompts, and handoffs to the user's language
unless the user requests another language. Keep commands, identifiers, schema
keys and action codes, and raw errors unchanged.

## Load the write contract

Read [references/transaction-contract.md](references/transaction-contract.md)
completely before the first write. Use the public
`kgdistiller --repo-root PROJECT` CLI.

Start with `agent status`. Require `qlkg-query-status-v1`, the
`json-memory`/`read-only-query-v3` capabilities, `qlkg-ingest-request-v2`, and
exact target graph, snapshot, and alignment digests from `$query-kgdistiller`.
The ingest request separately declares `transactional-ingest-v1`; return to
query when any precondition is stale.

## Require a reviewed handoff

Require one bounded request containing:

- one decision per candidate: reuse, add, update, reject, or defer;
- exact registered authority paths, normalized expected source hashes, native
  patch contents, and complete post-patch marker/ref state;
- content-addressed candidate snapshot and query report paths;
- one reviewed `qlkg-agent-delta-v3`;
- optional reviewed alignment decisions with evidence/justification;
- review evidence and source provenance.

Reject unresolved `uncertain` or `conflict` candidates as writes. Never create
new identities from them. Preserve unrelated prose and user-authored markers.
Paper snapshots remain read-only unless the user explicitly authorizes exact
selected entries or mappings for import.

Compute authority hashes over UTF-8 text with universal-newline normalization
(CRLF/CR to LF), not raw checkout bytes. This boundary matches sync, ingest,
store, check, and export.

## Plan, review, then apply

1. Build canonical `qlkg-ingest-request-v2` content in `plan` mode and compute
   `request_sha256` over canonical JSON excluding that field.
2. Run:

   ```sh
   kgdistiller --repo-root PROJECT ingest plan REQUEST.json --output PLAN.json
   ```

3. Review predicted authority, node, edge, ref, alignment, and digest changes.
   Planning must leave all live bytes unchanged.
4. Change only `mode` to `apply`, recompute `request_sha256`, then run:

   ```sh
   kgdistiller --repo-root PROJECT ingest apply REQUEST.json \
     --receipt RECEIPT.json
   ```

5. Accept only `qlkg-ingest-receipt-v2` with `status: committed`, a valid
   canonical digest, and after-digests matching fresh `agent status`.
6. If `knowledge/store.json` exists, refresh and verify it:

   ```sh
   kgdistiller --repo-root PROJECT store snapshot
   kgdistiller --repo-root PROJECT store verify
   ```

   This records the JSON-only `qlkg-store-v2` generation. If no store exists,
   report `local-only`; do not silently initialize Git, commit, or push.
7. Create a static-site or lossy Obsidian projection only when explicitly in
   scope. Neither export is authority, and the managed Obsidian subtree or an
   external browsing-only vault/projection must never be rescanned or ingested.

The engine owns locking, optimistic concurrency, staging, scan, delta apply,
sync, curation, global validation, atomic JSON-generation installation, crash
recovery, and idempotency. There is no database/index/vector rebuild. A failed
transaction must return its stable error and preserve the before-digests.

## Return the receipt

Return receipt path plus request/engine/schema/capability versions; before/after
graph, alignment, and authority hashes; changed nodes, refs, edges, alignments,
and source patches; validations, warnings, and unapplied decisions; and store
generation/document count when refreshed.

Report Git state only as `local-only`, `committed locally`, or `remote
confirmed`, using the latter states only after the explicitly authorized action
succeeds. Report requested export receipts separately. Do not include authority
bodies, paper text, credentials, or unbounded evidence.
