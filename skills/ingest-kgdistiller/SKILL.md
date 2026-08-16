---
name: ingest-kgdistiller
description: Apply a reviewed source-backed knowledge update through kgdistiller's native Vault transaction boundary, requiring a canonical plan, stale-safe apply, and durable verified receipt.
---

# Ingest kgdistiller

Use this Skill for the only high-level native knowledge mutation. Accept a
fully reviewed `qlkg-vault-ingest-request-v1`; do not discover concepts or
resolve ambiguous identities during apply.

Read [references/transaction-contract.md](references/transaction-contract.md)
before planning.

## Workflow

1. Confirm that the request is closed, canonical, self-digested, and explicitly
   reviewed. It must bind one registered `vault_id`, registry generation, Vault
   manifest, source-ledger/graph/note bases, canonical federated recall report,
   exact native note patches, derivation updates, and empty alignment mutations.

2. Recheck read-only identity decisions with `$query-kgdistiller` only when the
   handoff is stale or incomplete. Do not alter the request silently. Return it
   to curation/review for a new canonical digest.

3. Plan without changing live Vault bytes:

   ```sh
   kgdistiller knowledge ingest plan REQUEST.json --output PLAN.json
   ```

   Review the closed plan, request digest, Vault ID, base/current generations,
   note changes, derivation coverage, graph result, and validation stages. A
   plan is not a commit receipt.

4. Apply the exact same canonical request only after explicit approval:

   ```sh
   kgdistiller knowledge ingest apply REQUEST.json --receipt RECEIPT.json
   ```

   Do not rewrite the request between plan and apply. Apply rechecks the
   machine registry, Vault root/manifest, source ledger and live source,
   concept notes, recall report, and graph generation under the Vault writer
   guard.

5. Accept success only from a closed `qlkg-vault-ingest-report-v1` whose
   outcome is `committed` or `already-committed`, and whose referenced durable
   `qlkg-vault-ingest-receipt-v1` is canonical, self-digested, content-addressed,
   and bound to the request/Vault. Treat `cleanup_status: pending` as committed
   with a recovery warning, not as a clean transaction.

6. Run the native integrity check after a committed result:

   ```sh
   kgdistiller knowledge check --vault VAULT_ID
   ```

   Refresh a portable store only when the user asks for a snapshot; delegate
   that separate boundary to `$deploy-kgdistiller`.

## Boundaries

- Do not author missing review, infer identity, weaken a stale precondition, or
  repair a request during apply.
- Do not edit concept notes, ledgers, graph files, receipts, journals, or
  `.kgdistiller/store.json` directly.
- Do not delete a pending journal, stage, backup, or receipt to hide an error.
- A plan, source capture, graph compile, Git commit, store snapshot, static
  export, and remote push are distinct outcomes.
- Use legacy `kgdistiller ingest` with `qlkg-ingest-request-v2` only when the
  user explicitly selects a legacy marker project. Never submit that contract
  to `knowledge ingest` or relabel it as native v1.
- Match user-facing explanations, prompts, and handoffs to the user's language
  unless the user requests another language. Keep commands, identifiers,
  schema keys, action codes, and raw errors unchanged.

## Handoff

Report the request and plan digests, Vault and registry generation, plan result,
apply outcome, receipt digest and portable receipt path, before/after graph,
source-ledger and note-inventory generations, changed note paths and source
versions, validation stages, cleanup status, and warnings. State exactly which
operation committed and which requested deployment actions remain undone.
