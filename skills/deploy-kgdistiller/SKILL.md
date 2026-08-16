---
name: deploy-kgdistiller
description: Initialize, register, move, verify, clone, snapshot, and serve native kgdistiller Vaults through the portable Vault store v3 boundary without inferring Git or network authority.
---

# Deploy kgdistiller

Manage native Vault lifecycle and portable `qlkg-vault-store-v3` snapshots.
Keep machine registry state, Vault content, Git synchronization, legacy exports,
and network publication as separate authorities.

Read [references/deployment-contract.md](references/deployment-contract.md)
before changing registration or producing a snapshot.

## Workflow

1. Inspect the machine registry and health:

   ```sh
   kgdistiller vault list
   kgdistiller vault doctor
   kgdistiller recall status
   ```

   Report missing Vaults rather than silently omitting them.

2. Initialize a new ordinary directory, or register an existing local native
   Vault:

   ```sh
   kgdistiller vault init PATH --id VAULT_ID --label "LABEL"
   kgdistiller vault add PATH
   ```

   Never initialize over an existing authority layout. `vault add` validates
   the existing `.kgdistiller/vault.json`; it does not assign a new identity or
   require an existing store pointer. Portable snapshot clones use the stricter
   verify-then-add flow below.

3. Resolve source ownership without asking for a root:

   ```sh
   kgdistiller vault locate SOURCE
   ```

   Require exactly one registered owner. Do not fall back to the current
   directory.

4. Check native content before snapshotting:

   ```sh
   kgdistiller knowledge check --vault VAULT_ID
   kgdistiller vault doctor VAULT_ID
   ```

5. Refresh the in-place portable pointer or create a no-clobber external copy:

   ```sh
   kgdistiller vault snapshot VAULT_ID
   kgdistiller vault snapshot VAULT_ID --output SNAPSHOT_PATH
   kgdistiller vault verify SNAPSHOT_PATH
   ```

   A snapshot contains the Vault manifest, native concept/taxonomy notes,
   current source ledger and generation, referenced blobs, all durable receipts,
   exact derived graph, and fixed scaffolds. It excludes the machine registry,
   `.git`, `.obsidian`, build journals/caches, old source generations,
   unreferenced blobs, and legacy knowledge store.

6. Clone through file or Git copy, then verify before registration:

   ```sh
   kgdistiller vault verify NEW_PATH
   kgdistiller vault add NEW_PATH
   kgdistiller recall status --vault VAULT_ID
   ```

   Do not automatically replace the registration for another root with the
   same Vault ID. Have the user explicitly remove the old mapping, then add the
   new path.

7. Move a Vault with an explicit bounded outage:

   ```sh
   kgdistiller vault snapshot VAULT_ID
   kgdistiller vault verify OLD_PATH
   # move or clone the directory using the user's chosen filesystem/Git tool
   kgdistiller vault verify NEW_PATH
   kgdistiller vault remove VAULT_ID
   kgdistiller vault add NEW_PATH
   kgdistiller vault doctor VAULT_ID
   ```

   The remove/add registry transition is not an atomic filesystem move. Keep a
   verified rollback copy until the new registration passes.

8. Start the installed multi-Vault workspace from any working directory:

   ```sh
   kgdistiller serve
   ```

   It binds loopback by default, serves packaged assets plus `/api/v1`, and
   requires no CDN. Do not broaden host exposure.

## Explicit legacy-only branch

Enter this branch only when the user names a legacy marker project. Keep its
`--repo-root`, `qlkg-store-v2`, query, and export artifacts completely separate
from native Vault state.

Verify the legacy project/store before either export:

```sh
kgdistiller --repo-root PROJECT check
kgdistiller --repo-root PROJECT store snapshot
kgdistiller --repo-root PROJECT store verify
```

Create and independently verify a legacy static bundle only with explicit
publication provenance:

```sh
kgdistiller --repo-root PROJECT export site --output SITE \
  --product-commit FULL_PRODUCT_COMMIT \
  --source-repository https://example.invalid/owner/knowledge
python SITE/verify_export.py SITE
```

Create the disposable legacy Obsidian projection only on explicit request:

```sh
kgdistiller --repo-root PROJECT export obsidian --replace
```

Use `kgdistiller --repo-root PROJECT serve --legacy` for the legacy browser.
Never register a static bundle/projection as a Vault or ingest its output.

## Boundaries

- Never initialize Git, commit, add a remote, push, overwrite a snapshot root,
  or expose a service without explicit user authority.
- Do not edit `.kgdistiller/store.json`, graph artifacts, ledgers, receipts, or
  machine registry files directly.
- Verification is read-only and does not repair a transaction journal.
- A store snapshot is not a Git backup, remote synchronization, static export,
  or deployment receipt.
- `qlkg-store-v2`, `kgdistiller store`, `export site`, and
  `export obsidian` belong only to explicitly selected legacy marker projects.
  Use `kgdistiller serve --legacy` only for that isolated legacy browser. Never
  mix legacy artifacts into a native `qlkg-vault-store-v3` inventory.
- Match user-facing explanations, prompts, and handoffs to the user's language
  unless the user requests another language. Keep commands, identifiers,
  schema keys, action codes, and raw errors unchanged.

## Handoff

Return the confirmed Vault ID/label/root, registry generation, doctor and
knowledge-check outcome, store schema/layout/digest/content generation, exact
snapshot or clone path, verification result, registration state, local server
address when started, and every action not authorized or not performed.
