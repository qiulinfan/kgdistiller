# Native Vault deployment and recovery

A native Vault is the portable knowledge unit. The machine registry locates it
but is never part of its portable store. The product checkout owns engine code,
schemas, packaged frontend assets, Skills, agents, and workflows—not personal
knowledge.

## Layout

```text
vault-root/
├── Knowledge/
│   ├── Concepts/                 # ordinary Markdown authority
│   ├── Fields/                   # taxonomy authority
│   └── Topics/                   # taxonomy authority
└── .kgdistiller/
    ├── vault.json                # qlkg-vault-v1 portable identity
    ├── sources/
    │   ├── manifest.json         # current qlkg-source-ledger-v1 pointer
    │   ├── generations/<sha>/    # documents/versions/derivations JSONL
    │   └── blobs/sha256/...      # immutable captured raw evidence
    ├── receipts/sha256/...       # durable native ingest receipts
    ├── graph/                    # exact derived qlkg-v3 generation
    ├── store.json                # qlkg-vault-store-v3 pointer
    └── build/                    # local locks/journals/stages/caches
```

Authority roots are configurable in `vault.json`; the default names above are
illustrative. They remain portable, pairwise non-overlapping under Unicode
NFC/casefold comparison, and outside `.git`, `.obsidian`, and `.kgdistiller`.

## Initialize or register

```sh
kgdistiller vault init VAULT_PATH --id VAULT_ID --label "LABEL"
kgdistiller vault list
kgdistiller vault doctor VAULT_ID
```

Register an existing local native Vault without changing its portable ID:

```sh
kgdistiller vault add VAULT_PATH
kgdistiller vault doctor VAULT_ID
kgdistiller knowledge check --vault VAULT_ID
```

`vault init` creates a new layout. `vault add` preserves the existing portable
ID and validates `vault.json`; a local/new Vault need not have `store.json`
yet. A copied portable snapshot uses the stricter verify-then-add flow under
Clone and restore. Neither command configures Git, remotes, credentials,
Obsidian, or network access.

Resolve selected files through the registry:

```sh
kgdistiller vault locate /absolute/path/to/source.tex
```

The native surface has no current-directory or repository-root fallback.

## Integrity and portable snapshots

Check native notes, ledger evidence, and derived graph before snapshotting:

```sh
kgdistiller knowledge check --vault VAULT_ID
kgdistiller vault doctor VAULT_ID
kgdistiller vault snapshot VAULT_ID
```

The in-place snapshot publishes `.kgdistiller/store.json` last and may create
only missing fixed scaffolds without overwriting different bytes. It does not
rewrite authority, ledger, receipts, or graph.

Create a separate no-clobber snapshot and verify without registry dependency:

```sh
kgdistiller vault snapshot VAULT_ID --output SNAPSHOT_PATH
kgdistiller vault verify SNAPSHOT_PATH
```

`qlkg-vault-store-v3` binds:

- the Vault manifest and normalized native-note inventory;
- the current source manifest and exactly its document/version/derivation rows;
- every raw blob referenced by any version;
- every canonical durable ingest receipt, including historical receipts;
- the complete manifest-declared `qlkg-v3` graph and source hashes;
- required portable scaffolds and one self/content-generation digest.

It excludes machine registry state, `.git`, `.obsidian`, internal build state,
old source generations, unreferenced blobs, live locators, and legacy
`knowledge/store.json`. Verification is pure read-only, performs two stable
captures, validates official ledger/receipt/graph semantics, and recompiles
native notes byte-exactly.

## Clone and restore

Use the filesystem or Git transport selected by the user, then:

```sh
kgdistiller vault verify NEW_PATH
kgdistiller vault add NEW_PATH
kgdistiller recall status --vault VAULT_ID
kgdistiller recall get VAULT_ID:KNOWN_NODE
```

Verification precedes registration. A clone is immediately queryable; there is
no profile, provider, database, or materialization step. If the same Vault ID
is already registered at another root, do not replace it automatically. The
user must choose which copy is authoritative.

Do not run `knowledge sync` to conceal a verification error. Restore known-good
bytes or repair the owning Vault through a reviewed transaction, then produce a
complete new store generation.

## Move and registry repair

Moving content and changing the machine registry are separate operations:

```sh
kgdistiller vault snapshot VAULT_ID
kgdistiller vault verify OLD_PATH
# user performs the copy/move and confirms NEW_PATH
kgdistiller vault verify NEW_PATH
kgdistiller vault remove VAULT_ID
kgdistiller vault add NEW_PATH
kgdistiller vault doctor VAULT_ID
```

The remove/add interval is an explicit non-atomic registry outage. Preserve the
verified old copy until the new root is registered and queried. If add fails,
restore the old registration rather than inventing a new ID or editing the
registry JSON.

## Git synchronization

Initialize, commit, add a remote, or push only with explicit user authority.
Git should track native authority, portable metadata/current generations,
referenced blobs, durable receipts, graph artifacts, and `store.json`. Exclude
machine registry state, `.obsidian`, build locks/journals/stages/caches, and
credentials. Store verification proves local content integrity, not Git or
remote state.

## Local workspace

```sh
kgdistiller serve
```

Bare `serve` starts the packaged frontend and versioned federated API from any
working directory, binds loopback by default, and uses no remote asset. It is
not an authenticated public service. Do not bind a public/LAN address.

`kgdistiller serve --legacy` is a different, explicit single-project server.
Its graph/source endpoints and static bytes are isolated from `/api/v1` and the
packaged native workspace.

## Legacy adoption

Never copy or relabel `knowledge/graph`, `knowledge/store.json`, a static
export, Obsidian projection, or Agent delta into `.kgdistiller`.

1. Commit native legacy authorities/registries as a rollback point.
2. With the old release, export any irreplaceable curated entries and semantic
   edges for human review.
3. Prefer a new sibling Vault. This avoids `knowledge`/`Knowledge` collisions
   on Windows and other case-insensitive filesystems.
4. Copy only user-approved source evidence into the native Vault, then run
   `vault locate`, `source capture`, and `source diff`.
5. Generate reviewable native concept/taxonomy note candidates from the legacy
   active nodes; re-resolve every identity with qualified federated recall.
6. Require a reviewed `qlkg-vault-ingest-request-v1` plan/apply.
7. Run `knowledge check`, `vault snapshot`, and `vault verify`.

If no irreplaceable legacy curation exists, re-distillation is safer than a
compatibility layer. Legacy marker commands and `qlkg-store-v2` remain usable
only in their explicit isolated workflow.

## Recovery record

Record confirmed Vault ID/label/root, registry generation, Vault/store/graph and
source-ledger digests, verification result, exact copy/move steps, temporary
registry outage, Git state only when observed, and unresolved cleanup warnings.
Never include credentials, unbounded source text, or absolute paths inside a
portable manifest.
