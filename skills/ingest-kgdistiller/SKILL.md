---
name: ingest-kgdistiller
description: Apply a reviewed, source-backed knowledge update of entries and direct semantic edges through kgdistiller's transactional ingest API and return its readable receipt. Use after capture-kgdistiller, curate-kgdistiller-notes, query-kgdistiller or another extractor has decided identities, entries, aliases, kinds and direct relations with their cited source lines.
---

# Ingest into kgdistiller

Be the only Skill that mutates the personal knowledge base. Execute reviewed
decisions; do not rediscover concepts or hand-edit `.knowledge/entries/` or
`.knowledge/edges.jsonl` as a substitute for the transaction API.

## Align language

Match user-facing explanations, prompts, and handoffs to the user's language
unless the user requests another language. Keep commands, identifiers, schema
keys and action codes, and raw errors unchanged.

## Load the write contract

Read [references/transaction-contract.md](references/transaction-contract.md)
completely before the first write. Use the public `kgd` CLI with
`--base B` after every command (`kgd check --base B`), or run it inside the
registered base root; relative paths are resolved against the working
directory.

Start with `agent status` (entry and edge counts) and `check`. A store with
errors cannot accept a transaction until they are repaired; entries reported as
moved can be fixed first with `check --fix-lines`.

## Require a reviewed handoff

Require one bounded `kgdistiller-ingest-request-v1` whose
`kgdistiller-agent-delta-v1` lists:

- `create_entries`: complete new entry records;
- `update_entries`: full replacement records, each with the `expected_label`
  the reviewer saw;
- `remove_entries`: ids with their `expected_label`;
- `add_edges` and `remove_edges`: direct semantic edges with concrete evidence;

plus review evidence and source provenance. `capture prepare` and
`harvest apply` build such requests; a curation handoff may supply one written
to the contract. Reject unresolved or ambiguous identities as writes. Never
create new identities from them.

Every entry cites one registered source by path and line range, and its
`evidence` must equal those lines exactly. Source documents are any registered
text format and are never edited. Each source has exactly one document type,
registered in the home: a glob in `bases.B.sources` of
`$KGDISTILLER_HOME/config.json` maps it to `$KGDISTILLER_HOME/types/<type>.md`.
That type's `node_kinds` restrict the allowed `kind` values; it never creates
identities or bypasses review.

## Plan, review, then apply

1. Run:

   ```sh
   kgd ingest plan PLAN_REQUEST.json --output PLAN.json --base B
   ```

   The request's `mode` must be `plan`. Planning applies the delta in memory,
   validates the resulting store and writes nothing to the store.
2. Review the plan's `changes` (entries created, updated, removed, aliases
   changed, edges added and removed) and its before/after `counts`.
3. Apply the same request with `mode: apply`:

   ```sh
   kgd ingest apply APPLY_REQUEST.json --receipt RECEIPT.json --base B
   ```

4. Accept only `kgdistiller-ingest-receipt-v1` with `status: committed`, then run
   `check` and `agent status` and confirm the counts match the receipt.
5. Refresh the Obsidian graph feed only when the user uses it:
   `kgd export obsidian --base B`.

The engine owns the home lock (`$KGDISTILLER_HOME/lock`), semantic re-validation against the current
store, atomic installation, crash recovery and idempotency. A failed transaction
returns a stable error code and leaves the store unchanged.

## Return the receipt

Return the receipt path, the request id, the changed entries and edges, the
post-commit `check` result and any unapplied decisions. Report Git state only as
`local-only`, `committed locally`, or `remote confirmed`, using the latter
states only after the explicitly authorized action succeeds. Do not include
source bodies, paper text, credentials, or unbounded evidence.
