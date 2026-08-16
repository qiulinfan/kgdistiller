---
name: query-kgdistiller
description: Query registered kgdistiller Vaults through bounded federated recall, preserve Vault-qualified identity and ambiguity, and return source-backed results without reading graph files or mutating knowledge.
---

# Query kgdistiller

Use only the public federated recall surface. Treat each report as a bounded,
generation-bound view and never infer identity from similarity.

## Workflow

1. Capture federation health before selecting a query path:

   ```sh
   kgdistiller recall status
   ```

   Report missing or incomplete Vaults. Do not silently treat a partial
   federation as complete. Narrow with repeated `--vault VAULT_ID` only when
   the user or task supplies a valid scope.

2. Explore the taxonomy DAG when the query needs a frontier:

   ```sh
   kgdistiller recall roots --vault VAULT_ID
   kgdistiller recall children VAULT_ID:NODE_ID
   ```

   Preserve multiple parents. A display tree is not an identity hierarchy.

3. Batch exact names and aliases before lexical retrieval:

   ```sh
   kgdistiller recall resolve "NAME ONE" "NAME TWO"
   ```

   Keep `missing`, `exact`, `alias`, and `ambiguous` outcomes distinct. Only an
   exact canonical name or collision-free reviewed alias may establish
   identity. Never collapse the same node ID from different Vaults.

4. Search within selected Vaults/frontiers and retain every visible lane reason:

   ```sh
   kgdistiller recall search "QUESTION" \
     --vault VAULT_ID --scope VAULT_ID:TOPIC_ID --limit 20
   ```

   Identity, taxonomy, lexical, and graph lanes retrieve or rank candidates.
   They do not create a mapping or semantic edge. Preserve omissions and
   truncation.

5. Fetch detail or expand only bounded qualified handles:

   ```sh
   kgdistiller recall get VAULT_ID:NODE_ID
   kgdistiller recall expand VAULT_ID:NODE_ID --depth 2 --limit 50
   kgdistiller recall context "QUESTION" --vault VAULT_ID --budget 6000
   kgdistiller recall context --handle VAULT_ID:NODE_ID --budget 6000
   ```

   The context forms are alternatives: pass either a query or selected handles,
   never both. Use context only for the final bounded selection. Preserve
   evidence spans, source-version identity, relation endpoints, and generation
   tokens.

6. Before handing a result to a mutating workflow, ensure all reports refer to
   the intended current Vault/federation generations. Re-run the bounded query
   when a stale token or changed Vault invalidates the decision.

## Boundaries

- Remain read-only. Do not capture sources, patch notes, modify registries,
  compile graphs, ingest requests, create stores, or publish exports.
- Never open `.kgdistiller/graph` or legacy `knowledge/graph` shards directly.
- Do not infer identity from headings, directory layout, document order,
  co-occurrence, translation, acronyms, lexical score, or topology.
- Do not hide missing Vaults, stale curation, ambiguous aliases, omissions, or
  truncated results.
- Use legacy `agent`/single-project query commands only when the user explicitly
  requests the isolated legacy workflow. Do not mix their handles or digests
  with native federated reports.
- Match user-facing explanations, prompts, and handoffs to the user's language
  unless the user requests another language. Keep commands, identifiers,
  schema keys, action codes, and raw errors unchanged.

## Handoff

Return the operation, federation generation, per-Vault graph/source generations,
selected Vaults/scopes, Vault-qualified handles, resolution outcomes, lane
reasons, evidence locations, omissions, and truncation. Explain whether the
result is complete enough for its intended use; never describe a candidate as
an established identity without exact or reviewed-alias evidence.
