# Native Vault transactional ingest

`qlkg-vault-ingest-request-v1` is the only high-level write boundary for native
Vault knowledge. It commits ordinary Markdown concept/taxonomy notes, reviewed
source derivations, and one deterministic `qlkg-v3` graph generation as a
single client-visible transaction.

Ingest does not discover concepts, capture evidence, resolve ambiguous
identities, register a Vault, or decide what a paper means. Complete those
bounded review stages first.

## Commands

```sh
kgdistiller knowledge ingest plan REQUEST.json --output PLAN.json
kgdistiller knowledge ingest apply REQUEST.json --receipt RECEIPT.json
```

The native commands resolve `vault_id` through the machine registry. They do
not accept a repository-root argument. Plan and apply use the same canonical
request; planning does not reserve its base generation.

## Request boundary

The packaged `qlkg-vault-ingest-request-v1` is closed, bounded, canonical, and
self-digested. It binds:

- request ID/digest and supported capabilities;
- one Vault ID, registry generation, and Vault manifest digest;
- current source-ledger, graph-generation, and note-inventory bases;
- one canonical federated recall report path and digest;
- exact Vault-relative native note write/delete images;
- committed or reviewed-empty derivation updates for captured source versions;
- reviewed candidate dispositions and concept/relation evidence spans;
- empty `alignment_mutations` for this release;
- explicit reviewer, evidence, and provenance.

Every evidence span names its captured `version_id`, bounded line/optional
column range, and exact `excerpt_sha256`. Committed concepts and relations must
be closed by current evidence; a reviewed-empty derivation has no candidates,
concepts, or relations. Ambiguous recall identities cannot be reused or updated.

Authority and native note patch paths are portable Vault-relative paths. The
query-report artifact path is portable and relative to the directory containing
the request artifact; the report's selected source and evidence must still
belong to the target Vault. A change to any logical request field requires a
new canonical request digest. Do not modify a request between plan and apply.

## Plan

Plan constructs and validates the complete transaction in isolated staging. It
does not publish a note, ledger row, graph artifact, receipt, or portable store
pointer. Review the returned `qlkg-vault-ingest-report-v1`, canonical plan
digest, base/current generations, exact note changes, derivation coverage,
compiled graph result, and validation stages.

A successful plan is not a receipt and does not make stale preconditions valid.
If source, notes, registry, or graph changes before apply, apply fails closed.

## Apply and atomic install

Apply obtains one Vault writer guard and revalidates:

1. machine registry generation and registered root;
2. Vault manifest and configured authority roots;
3. current source-ledger generation and every referenced source version/span;
4. live source hashes and current note inventory;
5. federated recall report and graph base;
6. exact note before-images and reviewed derivation closure;
7. deterministic native compilation and global graph invariants.

It prepares a final canonical receipt, stages every file, journals exact
before/after images, installs note/ledger/graph targets, publishes the source
and graph generations, installs the durable receipt, and commits the journal.
A reader observes the complete old or complete new generation.

Managed parent directories are byte-free scaffolding. Apply creates a missing
parent with anchored no-clobber semantics and rolls it back only when the exact
owned filesystem object is unchanged and empty. A hard crash may conservatively
leave harmless empty scaffolding; it must not leave an authority file, ledger
pointer, graph pointer, or published receipt from an uncommitted transaction.

Any rejection before installation changes no live knowledge byte. A recoverable
installation failure restores exact backups. An uncertain third state or
degraded journal fails closed; preserve the journal/backups for diagnosis.

## Receipt and idempotency

Success is a closed `qlkg-vault-ingest-report-v1` with `committed` or
`already-committed`. It refers to a canonical
`qlkg-vault-ingest-receipt-v1` stored at:

```text
.kgdistiller/receipts/sha256/aa/FULL_SHA256.json
```

The receipt is finalized before installation so derivation rows can bind its
self-digest. The post-commit report separately returns the final source-ledger
generation. Accept it only when request/Vault/before/after digests, changed
notes, derivation summaries, and validation stages match.

Reapplying the identical canonical request may return the stored receipt with
`already-committed`. Reusing a request ID with different content fails. A
`cleanup_status: pending` result is committed but requires explicit cleanup
follow-up; it is not a clean no-op.

## After commit

```sh
kgdistiller knowledge check --vault VAULT_ID
```

Only on separate user request, refresh a portable store:

```sh
kgdistiller vault snapshot VAULT_ID
kgdistiller vault verify VAULT_PATH
```

Git commit/push, clone registration, server start, static publication, and
Obsidian projection remain separate authorities.

## Legacy isolation

The old `qlkg-ingest-request-v2`, `qlkg-ingest-receipt-v2`, and
`kgdistiller ingest plan/apply` keep their marker-project meaning. They are not
accepted by `knowledge ingest`, do not write native concept notes/source
ledgers, and must not be relabeled as v1. Use them only when the user explicitly
selects an isolated legacy project.

Never combine legacy marker patches/deltas with native note patches or bind a
legacy query report to a native request.
