# Native Vault transaction contract

## Request

Accept only `qlkg-vault-ingest-request-v1` for the native workflow. Its
canonical self-digest binds:

- one Vault ID, machine registry generation, and Vault manifest digest;
- source-ledger, graph, and note-inventory preconditions;
- a canonical federated recall report path and digest;
- bounded exact native note writes/deletes with expected raw digests;
- committed or reviewed-empty derivations tied to captured source versions;
- canonical concept/relation evidence spans and candidate dispositions;
- empty `alignment_mutations` in this release;
- explicit reviewer identity, evidence, and provenance.

Authority and native note patch paths are portable Vault-relative paths. The
query-report artifact path is portable and relative to the request artifact
root; its selected source and evidence must still belong to the target Vault.
All identifiers and arrays use the closed schema order/uniqueness rules.
Changing any byte of the logical request requires a new `request_sha256`.

## Plan

`knowledge ingest plan` stages and validates the complete transaction without
publishing live bytes. Review the returned `qlkg-vault-ingest-report-v1` and
the canonical plan artifact. Planning does not reserve the base generation and
does not authorize apply; a later change must make apply fail stale.

## Apply

Apply obtains the Vault writer guard, re-resolves the registered root, and
revalidates every bound generation and content image. It installs native notes,
source derivation rows, one deterministic `qlkg-v3` graph generation, and a
canonical receipt as one client-visible transaction. Readers observe the
complete old or complete new generation.

Any pre-install rejection changes no live authority, ledger, or graph byte. A
recoverable install failure restores owned files and retains only conservative
empty scaffolding. A degraded journal or uncertain third state fails closed and
requires explicit diagnosis; never remove evidence manually.

## Receipt and idempotency

The canonical `qlkg-vault-ingest-receipt-v1` is stored under
`.kgdistiller/receipts/sha256/aa/FULL_SHA256.json`. Its path key is its receipt
self-digest; its file bytes also have an independently verified raw digest in a
portable Vault store.

Reapplying the same canonical request may return `already-committed` with the
same receipt. Reusing a request ID for different content is a conflict. Accept
only a receipt whose request, Vault, before/after generations, changed notes,
derivation summaries, and validation stages match the closed apply report.

## Separate outcomes

The transaction does not commit Git, push a remote, refresh
`.kgdistiller/store.json`, create an external snapshot, publish a legacy static
bundle, or generate a legacy Obsidian projection. Require separate explicit
authority for each.
