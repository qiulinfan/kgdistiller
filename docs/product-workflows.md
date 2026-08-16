# kgdistiller product workflows

kgdistiller ships eight product Skills, four Codex agent presets, and
`workflows/manifest.json` version 3. The native workflow treats ordinary
Markdown concept/taxonomy notes as authority, captured Markdown/Typst/LaTeX as
immutable evidence, `qlkg-v3` as derived graph, and qualified federated recall
as the identity boundary.

Install or validate the product assets from any current directory:

```sh
kgdistiller codex link
kgdistiller codex doctor
```

The portable manifest is
`$CODEX_HOME/workflow-products/kgdistiller/workflows/manifest.json`. Resolve its
`workflow_guide` relative to that canonical product root. The linker owns only
manifest-declared kgdistiller assets and namespaced state; it never replaces
global `AGENTS.md`, `config.toml`, unrelated Skills, or unrelated presets.

## Native handoff chain

The default knowledge path is:

1. resolve selected source paths with `vault locate`;
2. inspect, capture, and diff immutable source versions;
3. run federated recall and keep `vault_id:node_id` handles;
4. prepare a reviewed `qlkg-vault-ingest-request-v1`;
5. plan and apply through the native transaction boundary;
6. run `knowledge check`;
7. snapshot/verify `qlkg-vault-store-v3` when requested;
8. use bare `kgdistiller serve` for the packaged multi-Vault workspace.

Each handoff carries the generations and digests needed by the next stage. Do
not turn “captured”, “resolved”, “planned”, “committed”, “snapshotted”,
“verified”, “registered”, “Git-committed”, or “served” into one generic success.

## Eight workflows

### `curate-notes`

`$curate-kgdistiller-notes` locates/captures selected evidence and prepares
native note/derivation changes. `$query-kgdistiller` resolves the full bounded
candidate batch through one coherent federated view. `$ingest-kgdistiller`
plans and applies only the reviewed canonical request. Ambiguity blocks its own
write path; removed evidence never deletes knowledge automatically.

### `federate-paper`

`$extract-paper-markdown` acquires a complete source-traceable package.
`$distill-paper-knowledge` authors an isolated `qlkg-candidate-graph-v2` and
validated snapshot. `$query-kgdistiller` aligns candidates to qualified Vault
handles without importing. Reading, distilling, tracing, or aligning never
authorizes personal mutation.

### `import-paper`

`$import-paper-knowledge` accepts only an explicitly selected paper subset, one
target Vault, and captured evidence. It revalidates qualified identities and
prepares native note/derivation changes. Public `source capture` is the separate
append-only evidence action; `$ingest-kgdistiller` remains the only phase that
mutates native knowledge notes, derivations, and graph state.

### `trace-lineage`

`$trace-concept-lineage` produces source-backed dossiers and a prerequisite
reading route. It does not imply import into a Vault.

### `manage-vaults`

`$deploy-kgdistiller` initializes/registers Vaults, locates paths, runs doctor,
verifies clones, performs explicit remove/add move repair, and starts the
loopback workspace. Registry state, portable identity, filesystem move, Git,
and network exposure remain distinct authorities.

### `portable-store`

`$deploy-kgdistiller` snapshots or verifies `qlkg-vault-store-v3`. An external
clone becomes queryable through `vault verify NEW` followed by `vault add NEW`;
there is no materialization step. Snapshotting never implies Git or remote
synchronization.

### `publish-static` (legacy only)

This workflow retains the old `qlkg-store-v2` marker-project and
`qlkg-static-export-v2` meanings. It is not a native Vault publish command.
Verify the legacy source/store and standalone export receipt without mixing its
bytes or handles into native recall.

```sh
kgdistiller --repo-root PROJECT check
kgdistiller --repo-root PROJECT store snapshot
kgdistiller --repo-root PROJECT store verify
kgdistiller --repo-root PROJECT export site --output SITE \
  --product-commit FULL_PRODUCT_COMMIT \
  --source-repository https://example.invalid/owner/knowledge
python SITE/verify_export.py SITE
```

### `export-obsidian` (legacy only)

This workflow produces the lossy `qlkg-obsidian-projection-v1` for an explicitly
selected legacy marker project or external browsing copy. Never register,
rescan, ingest, or edit the projection as authority. Native Vaults instead open
their ordinary Markdown notes directly in Obsidian.

```sh
kgdistiller --repo-root PROJECT check
kgdistiller --repo-root PROJECT store verify
kgdistiller --repo-root PROJECT export obsidian --replace
```

## Agent ownership

- `note-curator` captures selected source evidence and prepares a native v1
  request handoff, but never applies it.
- `paper-distiller` keeps paper acquisition/candidates separate and prepares
  imports only after exact selection.
- `query-reviewer` is read-only, preserves federation health/ambiguity, and
  returns qualified handles plus generation evidence.
- `transaction-reviewer` reviews native plan/apply, receipts, Vault lifecycle,
  and store/serve boundaries; legacy v2 operations require explicit isolation.

All Skills and agents match user-facing explanations, prompts, and handoffs to
the user's language unless another language is requested. Commands,
identifiers, schema/action keys, and raw errors remain unchanged.

## Recovery and migration handoffs

Before legacy adoption, record a Git rollback point and export irreplaceable
legacy curation with the old release. Prefer a sibling native Vault, copy only
approved source evidence, capture it, generate reviewable concept-note
candidates, re-resolve identities, and apply a reviewed native transaction.
Never relabel legacy graph, store, projection, delta, or receipt bytes.

For a moved native Vault, snapshot and verify the old/root rollback copy,
perform the user-controlled copy or move, run `vault verify NEW_PATH`, then
`vault remove ID`, `vault add NEW_PATH`, `vault doctor ID`, and `recall status
--vault ID`. Report the temporary registry outage and keep rollback evidence
until the new root verifies.
