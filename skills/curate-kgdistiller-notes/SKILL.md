---
name: curate-kgdistiller-notes
description: Extract and review source-grounded knowledge from registered Markdown, Typst, or LaTeX files, resolve identities through federated recall, and prepare one bounded native Vault ingest handoff without applying it.
---

# Curate kgdistiller notes

Prepare a reviewed, source-backed native Vault update. Use `source capture` as
the one authorized append to the source archive before reasoning about the
evidence, preserve explicit identity, and leave native knowledge-note,
derivation, and graph mutation to `$ingest-kgdistiller`.

Read [references/curation-contract.md](references/curation-contract.md) before
building a handoff.

## Workflow

1. Resolve every supplied source path with:

   ```sh
   kgdistiller vault locate SOURCE
   ```

   Require exactly one registered Vault owner. Do not ask for a repository
   root when the registry resolves it. Stop for an unregistered, missing,
   overlapping, excluded, or escaped path.

2. Inspect the current archive state, capture the source, and inspect the
   predecessor diff:

   ```sh
   kgdistiller source status SOURCE
   kgdistiller source capture SOURCE
   kgdistiller source diff SOURCE
   ```

   Capture archives immutable source evidence; it does not approve concepts or
   relations. Bind every candidate and evidence span to the captured
   `version_id`, lines or columns, and `excerpt_sha256`.

3. Start identity work from a healthy federated snapshot:

   ```sh
   kgdistiller recall status --vault VAULT_ID
   kgdistiller recall roots --vault VAULT_ID
   kgdistiller recall resolve "NAME ONE" "NAME TWO" --vault VAULT_ID
   ```

   Keep `vault_id:node_id` handles intact. Batch all plausible names and
   aliases. Exact or reviewed-alias resolution may establish reuse; lexical,
   taxonomy, graph, translation, acronym, heading, and document-order signals
   only retrieve candidates. Preserve every ambiguous or missing result.

4. Use scoped retrieval only after selecting the relevant field/topic
   frontier. Fetch full evidence only for the final bounded set:

   ```sh
   kgdistiller recall search "QUESTION" --vault VAULT_ID --scope VAULT_ID:NODE_ID
   kgdistiller recall context "QUESTION" --vault VAULT_ID --scope VAULT_ID:NODE_ID
   kgdistiller recall context --handle VAULT_ID:NODE_ID
   ```

   The two context forms are alternatives: use either a query (optionally
   scoped) or selected handles, never both in one request.

5. Draft ordinary native Markdown concept, field, or topic note changes under
   the Vault's configured authority roots. Preserve multi-parent taxonomy and
   typed relations. Never insert legacy `--[[...]]--`, `#kn[...]`, or
   `\kn{...}` markers into native Vault evidence files.

6. Produce one bounded handoff for `qlkg-vault-ingest-request-v1`. Include the
   Vault and registry generation, Vault manifest digest, source-ledger and
   graph bases, note inventory digest, canonical recall report, exact note
   patches, candidate dispositions, source-version derivations, evidence
   spans, and explicit reviewer evidence. Do not apply it.

7. Hand the reviewed request to `$ingest-kgdistiller`. Report deferred,
   rejected, ambiguous, stale, and missing identities separately from proposed
   writes.

## Boundaries

- Work only on paths selected by the user or parent workflow.
- Do not directly edit live source, source blobs, ledger JSON, concept notes,
  graph files, registry state, or a portable store. The public `source capture`
  command is the only source-archive append authorized by this workflow.
- Do not read `.kgdistiller/graph` shards; use `recall` results.
- Do not turn a source capture, diff, similarity score, or graph path into a
  semantic approval.
- Treat removed source text as review pressure, not deletion authority.
- Use the legacy marker/project workflow only when the user explicitly selects
  it. Never mix legacy marker authority with native concept-note authority in
  one generation.
- Match user-facing explanations, prompts, and handoffs to the user's language
  unless the user requests another language. Keep commands, identifiers,
  schema keys, action codes, and raw errors unchanged.

## Handoff

Return the Vault ID, captured document/version, source-ledger and graph
generations, recall-report digest, qualified reuse handles, proposed note paths,
derivation/evidence coverage, unresolved decisions, and the canonical request
path and digest. State explicitly that no knowledge transaction was applied.
