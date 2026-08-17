# Local-first deployment and recovery

The knowledge project is the deployment and backup unit. It owns registered
Markdown, Typst, and LaTeX authorities, reviewed registries, the deterministic
`qlkg-v3` graph, and the `qlkg-store-v2` manifest. The kgdistiller product
checkout owns only engine code, schemas, native frontend assets, Skills, and
workflow definitions.

## Portable layout

```text
personal-knowledge-store/
├── notes/                         # native authorities
└── knowledge/
    ├── vault.json                 # portable kgdistiller-vault-v1 identity
    ├── sources.json               # qlkg-sources-v3
    ├── identities.json            # optional reviewed renames/aliases
    ├── alignments.json             # reviewed cross-namespace mappings
    ├── graph/                      # deterministic qlkg-v3 generation
    ├── documents.jsonl            # canonical authority inventory
    ├── store.json                 # qlkg-store-v2
    └── build/                      # ignored plans, receipts, journals, previews
```

There is no database, vector bundle, model provider, local profile, or
materialized query index. A verified checkout is directly queryable through a
generation-checked in-memory `GraphView`.

## Global command and machine-local registration

Install the package as a user-level tool on Windows, macOS, or Linux, then
register each knowledge repository once:

```sh
uv tool install git+https://github.com/qiulinfan/kgdistiller.git
uv tool update-shell
kgdistiller vault register PROJECT --name research
kgdistiller --vault research agent status
```

The portable `knowledge/vault.json` stores the stable vault UUID and must travel
with the repository. The user-level `~/.kgdistiller/vaults.json` stores only
machine-local name/UUID/absolute-path mappings and the optional default. On
Windows, `~` is the current user's profile directory. Do not commit the
user-level registry, since its absolute paths are host-specific and may expose
local directory names. `KGDISTILLER_HOME` may select a different absolute
registry directory, while `KGDISTILLER_VAULT` selects a registered name or UUID.

Use `vault list`, `vault show NAME`, `vault default NAME`, `vault doctor`, and
`vault unregister NAME` to inspect and maintain the locator. Unregistering
never deletes the repository or its portable identity. A moved repository can
be registered at its new path by identity; `--replace` is required only when
the old path still exists, which prevents accidentally treating a copied vault
as a relocation.

Version 0.4 does not read or migrate a `qlkg-v2` graph,
`qlkg-sources-v2`, or `qlkg-identities-v1`. Before upgrading an authority
repository, commit the native authorities and reviewed registries as a Git
rollback point. While still running 0.3, export any Agent-curated entries and
semantic edges that must survive for later human review. With 0.4 installed,
explicitly move the old generated `knowledge/graph/` outside the project or
delete that exact directory after confirming the rollback commit. Review and
change the source registry discriminator to `qlkg-sources-v3` and the optional
identity registry discriminator to `qlkg-identities-v2`, then run an unscoped
`sync` to rebuild `qlkg-v3` artifacts from the native authorities. Re-review
any old `qlkg-agent-delta-v2` content before issuing it as
`qlkg-agent-delta-v3`. The rebuild restores marker-derived nodes and references
only; it intentionally drops 0.3 Agent-curated entries and semantic edges that
are not re-authored after the rebuild.

## Create or refresh a store

Refresh in place when the notes repository is the desired private backup unit:

```sh
kgdistiller --repo-root PROJECT check
kgdistiller --repo-root PROJECT agent status
kgdistiller --repo-root PROJECT store snapshot
kgdistiller --repo-root PROJECT store verify
```

Create a separate self-contained snapshot when authorities live elsewhere:

```sh
kgdistiller --repo-root PROJECT store snapshot --output STORE
kgdistiller --repo-root STORE store verify
```

`STORE` must not be nested in `PROJECT`. Snapshot copies only registered,
already-ingested authorities and the exact portable vault identity, registries,
graph generation, and document inventory that describe them. Snapshot and
verify never contact a network service.

`store verify` validates the manifest schema and digest, safe managed paths,
canonical inventory, all authority hashes, registries, entry shards, graph and
snapshot digests, and the combined store generation. It recomputes the document
inventory from the copied authorities, source registry, and graph rather than
trusting inventory rows in isolation. Source roots must resolve inside the
project, including when a registered glob currently matches no files.
Neither `qlkg-store-v1` nor a store carrying a `qlkg-v2` core graph is a 0.4
compatibility input. If an old store is the only surviving copy, use the earlier
release to restore its native authorities and reviewed registries, commit that
recovery point, then follow the explicit discriminator-update and rebuild
procedure above.

Graph artifact size/digest records use LF-normalized UTF-8 text, matching the
graph loader and authority hash boundary. A Git checkout that materializes CRLF
therefore remains verifiable, while every non-newline content change still
fails closed.

## Git synchronization

Initialize a private Git repository, commit, add a remote, or push only when
the user explicitly authorizes that action. Track:

- every registered authority and required authored asset;
- `knowledge/vault.json`;
- `knowledge/sources.json`;
- optional `knowledge/identities.json` and `knowledge/alignments.json`;
- `knowledge/graph/`, `knowledge/documents.jsonl`, and `knowledge/store.json`.

Ignore `knowledge/build/`, transaction staging and journals, plans, receipts,
credentials, query logs, and generated exports unless an export is deliberately
adopted by a consumer. Verification proves local integrity, not that a commit
or remote synchronization happened.

After clone or pull:

```sh
kgdistiller --repo-root STORE store verify
kgdistiller --repo-root STORE agent status
kgdistiller --repo-root STORE agent resolve "KNOWN NAME"
```

Do not run `sync` to hide a verification failure. Restore a known-good revision
or repair the native authority on its owning machine, then create and transfer
one complete new store generation.

Git metadata is ignored by `store verify`, but an external snapshot operation
will never replace a store root that contains `.git`; that would discard
repository history. Refresh a cloned store in place, or write a new snapshot to
a separate empty path and adopt it through Git review.

## Local services and exports

`kgdistiller serve` uses the frontend assets packaged with the installed
product and binds to `127.0.0.1` by default. It is not an authenticated public
service. `kgdistiller mcp` exposes only bounded read-only graph operations.

`export site` produces a privacy-filtered `qlkg-static-export-v2` bundle with a
dependency-free verifier. Producer release, authority generation, export, and
consumer adoption are separate provenance events. Verify the bundle before a
consumer commits its exact files.

`export obsidian` produces a `qlkg-obsidian-projection-v1` downstream view.
Open the knowledge repository root as the editor vault; its registered Markdown
files remain non-lossy native authorities. Only the managed default subtree is
a lossy projection. An external output is a browsing-only vault/projection and
links back with `file:` URLs. Never register projected output as a source,
rescan it, or use edits in it to update the graph. Replace the projection from
the native authorities instead.

## Deployment receipt

Record the absolute project/store root, installed kgdistiller version and exact
product commit when known, store schema and generation digests, document count,
Git commit/remote state only when actually confirmed, and any static-export
receipt. Never include full authority content, credentials, or unbounded source
excerpts.
