---
name: import-paper-knowledge
description: Import an explicitly selected subset of a validated paper candidate graph into one registered native Vault through source capture, qualified recall, reviewed note changes, and stale-safe ingest.
---

# Import paper knowledge

Prepare a native Vault import only after the user selects exact paper
candidates and one target Vault. Keep acquisition, distillation, alignment,
review, and transaction apply as separate decisions.

Read [references/import-contract.md](references/import-contract.md) before
preparing the request.

## Workflow

1. Require the validated paper package, isolated candidate graph, source
   coverage, explicit selected candidate IDs, selected target Vault, and an
   approved source-evidence file inside that Vault. Do not guess the target from
   the current directory or import an unselected candidate.

2. Resolve and capture the evidence path:

   ```sh
   kgdistiller vault locate PAPER_SOURCE
   kgdistiller source status PAPER_SOURCE
   kgdistiller source capture PAPER_SOURCE
   kgdistiller source diff PAPER_SOURCE
   ```

   Require the resolved owner to equal the chosen target Vault. Capture does
   not approve the paper's concepts.

3. Revalidate every selected identity through `$query-kgdistiller` against the
   current federation. Preserve qualified `vault_id:node_id` reuse/update
   targets. Any new ambiguity, missing Vault, generation change, or stale
   curation returns the selection for review.

4. Map exact paper evidence to the captured source-version lines/columns and
   `excerpt_sha256`. Do not cite the isolated candidate artifact as a substitute
   for captured source evidence.

5. Draft bounded native concept/taxonomy note patches in the selected Vault.
   Reuse stable native IDs where identity is established. Express only reviewed
   direct typed relations, preserve multi-parent taxonomy, and keep source
   evidence in the ledger rather than copying unbounded paper text into notes.

6. Build one reviewed `qlkg-vault-ingest-request-v1` with the current registry,
   Vault, source-ledger, graph, note-inventory, and federated recall bindings;
   exact note patches; selected candidate dispositions; committed or
   reviewed-empty derivations; evidence spans; empty alignment mutations; and
   explicit review provenance.

7. Show the final selected candidates, qualified reuse targets, new/updated
   notes, derivations, relations, and exclusions. Only after explicit apply
   authorization, invoke `$ingest-kgdistiller` to plan, review, and apply the
   request.

8. On success, report the durable receipt and re-run bounded recall for the
   imported qualified handles. Delegate any portable snapshot to
   `$deploy-kgdistiller` as a separate action.

## Boundaries

- Never mutate the paper package or candidate graph to make it resemble a
  native Vault generation.
- Never infer a reuse target from lexical/graph similarity, paper order,
  citation, or matching prose.
- Never create evidence spans against an uncaptured or different source
  version.
- Never import unresolved, deferred, or unselected candidates.
- Do not directly edit ledger JSON or write derivation rows, native notes,
  graph files, or receipts outside the native transaction. The public
  append-only `source capture` boundary is explicitly permitted for the exact
  selected evidence path.
- Legacy marker imports are an explicit isolated workflow and use
  `qlkg-ingest-request-v2`; never mix or relabel them as native v1.
- Match user-facing explanations, prompts, and handoffs to the user's language
  unless the user requests another language. Keep commands, identifiers,
  schema keys, action codes, and raw errors unchanged.

## Handoff

Return paper/candidate digests, selected and excluded candidate IDs, target
Vault and generations, captured document/version, current federated token,
qualified reuse/update handles, native note paths, evidence/derivation coverage,
request/plan/receipt digests, apply outcome, and remaining deferred decisions.
