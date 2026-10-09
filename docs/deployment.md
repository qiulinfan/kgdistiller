# Local-first deployment and recovery

The knowledge project is the deployment and backup unit. It owns registered
Markdown, Typst and LaTeX source authorities, one Markdown file per accepted
entry, reviewed registries and graph state. The product checkout owns only
engine code, schemas, frontend assets, Skills and workflow definitions.

## Core and optional artifacts

```text
personal-knowledge-project/
├── notes/                         # original native sources
└── knowledge/
    ├── vault.json                 # stable vault identity
    ├── sources.json               # source registration; optional document types
    ├── entries/                   # sole persisted entry bodies, in Markdown
    └── graph/                     # manifest, nodes, edges, reference occurrences
```

`kgdistiller-graph-v2` reads entry content directly from its bound Markdown
authorities. It has no entry JSONL shards or persisted diagnostic report.
Graph records preserve stable IDs, aliases, orphan state and accepted semantic
edges; source prose cannot reconstruct all of that state. Keep the graph with
the sources and entries, rather than deleting it as a cache.

Add artifacts only for actual uses:

- `identities.json` for reviewed renames/aliases and `alignments.json` for
  accepted cross-namespace mappings; absent registries are valid.
- `documents.jsonl` and `store.json` for an explicitly requested portable
  snapshot, not daily capture or ordinary Git backup.
- `build/` for ignored plans, receipts, journals and previews.
- Static or Obsidian exports for chosen consumers. Preserve or rebuild outputs
  still adopted by a site or local plugin.

A normal checkout is queryable through a generation-checked in-memory
`GraphView`; it does not need a store snapshot, database or materialization
step. Source registration requires `id`, `root` and `files`, plus its registry
schema. Optional fields/topics, document types and display configuration are
user decisions, not prefilled placeholder structures.

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

## Existing graph generations

The loader accepts current public `kgdistiller-graph-v1` and
`kgdistiller-graph-v2` without changing files. Explicit synchronization or a
reviewed writer produces v2, removes its own obsolete body shards/diagnostic
artifact and preserves accepted identities, aliases, relations and source
content. No manual graph replacement is needed for this transition.

Pre-0.4 graphs and SQLite data remain unsupported. Recover their original
sources and reviewed metadata with the earlier release, preserve a recoverable
copy, then rebuild under current registries and review the retained semantics.
Do not apply that pre-0.4 recovery procedure to a current public v1 graph.

## Create or refresh a store

A portable snapshot is an optional self-contained backup package. Do not create
one as a prerequisite for ordinary query, capture, export or Git clone. For an
explicitly requested in-place snapshot:

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
already-ingested identity authorities, manifest-bound entry Markdown, their
native or explicitly selected evidence, and the exact portable vault identity, registries, graph
generation, and document inventory that describe them. Snapshot and verify
never contact a network service.

`store verify` validates the manifest schema and digest, safe managed paths,
canonical inventory, all authority and entry hashes, registries, graph and
snapshot digests, and the combined store generation. Existing public graph-v1
snapshots additionally validate their bound legacy shards. It recomputes the document
inventory from the copied authorities, source registry, and graph rather than
trusting inventory rows in isolation. Source roots must resolve inside the
project, including when a registered glob currently matches no files.
Pre-0.4 stores and graphs are not compatibility inputs. If an old store is the
only surviving copy, use the earlier release to restore its native authorities
and reviewed registries, commit that recovery point, then rebuild under the
current contracts.

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
- `knowledge/entries/` and all evidence files they reference, including any
  explicitly retained older `knowledge/derived/` files;
- `knowledge/graph/`;
- `knowledge/documents.jsonl` and `knowledge/store.json` only when maintaining
  an actual portable snapshot.

Ignore `knowledge/build/`, transaction staging and journals, plans, receipts,
credentials, query logs, and generated exports unless an export is deliberately
adopted by a consumer. Verification proves local integrity, not that a commit
or remote synchronization happened.

After an ordinary knowledge-project clone or pull:

```sh
kgdistiller --repo-root PROJECT check
kgdistiller --repo-root PROJECT agent status
kgdistiller --repo-root PROJECT agent resolve "KNOWN NAME"
```

If `knowledge/store.json` exists and the checkout is used as a portable
snapshot, run `store verify` before accepting or restoring that snapshot.
Do not generate a new snapshot merely to make an absent manifest pass a check.
Do not run `sync` to hide a verification failure. Restore a known-good revision
or repair the native authority, then explicitly refresh the intended snapshot.
An existing snapshot left behind after source changes is stale until refreshed;
that does not turn its optional inventory into live knowledge authority.

Git metadata is ignored by `store verify`, but an external snapshot operation
will never replace a store root that contains `.git`; that would discard
repository history. Refresh a cloned store in place, or write a new snapshot to
a separate empty path and adopt it through Git review.

## Local services and exports

`kgdistiller serve` uses the frontend assets packaged with the installed
product and binds to `127.0.0.1` by default. It is not an authenticated public
service. `kgdistiller mcp` exposes only bounded read-only graph operations.

`export site` produces a privacy-filtered `kgdistiller-static-export-v1` bundle with a
dependency-free verifier. Producer release, authority generation, export, and
consumer adoption are separate provenance events. Verify the bundle before a
consumer commits its exact files.

`export obsidian` produces a `kgdistiller-obsidian-projection-v1` downstream view.
Install the packaged read-only plugin into a selected vault with
`kgdistiller --vault <name-or-id> obsidian install`; use `--replace` for an
upgrade. The installer manages only `main.js`, `manifest.json`, and `styles.css`,
preserves `data.json`, and can leave the enabled-plugin list untouched with
`--no-enable`.
Open the knowledge repository root as the editor vault; its registered Markdown
files and `knowledge/entries/*.md` remain non-lossy authorities. Only the managed default subtree is
a lossy projection. An external output is a browsing-only vault/projection and
links back with `file:` URLs. Never register projected output as a source,
rescan it, or use edits in it to update the graph. Replace the projection from
the identity and entry authorities instead.

The projection contains `semantic-graph.json`, a validated
`kgdistiller-obsidian-graph-v1` plugin boundary. Native Obsidian keeps using
ordinary Wikilinks and backlinks. The optional kgdistiller plugin adds a
separate typed graph view over semantic, definition, and reference edges; it
does not read private JSONL graph internals or write any authority.

## Deployment receipt

Record the absolute project/store root, installed kgdistiller version and exact
product commit when known, graph generation, optional snapshot schema/digests and
document count only when a snapshot was created or verified,
Git commit/remote state only when actually confirmed, and any static-export
receipt. Never include full authority content, credentials, or unbounded source
excerpts.
