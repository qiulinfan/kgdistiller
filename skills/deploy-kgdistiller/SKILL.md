---
name: deploy-kgdistiller
description: Create, refresh, verify, clone, and restore a small personal kgdistiller knowledge base as a portable JSON-only Git-friendly store. Use when setting up kgdistiller on a machine, protecting native Markdown, Typst, or LaTeX authorities against machine loss, synchronizing them across computers, verifying qlkg-store-v2, serving the self-contained local frontend, or producing static-site and lossy Obsidian downstream exports.
---

# Deploy kgdistiller

Treat the knowledge project—not a product checkout or generated projection—as
the portable authority store. Version 0.4 has no database, embedding, provider,
profile, or materialization step.

## Align language

Match user-facing explanations, prompts, and handoffs to the user's language
unless the user requests another language. Keep commands, identifiers, schema
keys and action codes, and raw errors unchanged.

## Load the deployment contract

Read [references/deployment-contract.md](references/deployment-contract.md)
completely before changing a project. Use
`kgdistiller --repo-root PROJECT`. Record installed product version and exact
product commit when known. Never place personal sources, generated graphs,
credentials, or exports in the kgdistiller product repository.

## Choose one store layout

- Refresh in place when the private notes repository is the backup unit.
- Use `store snapshot --output STORE` when notes live elsewhere or the user
  wants a separate private backup. `STORE` must be separate from and not nested
  in `PROJECT`.

Both layouts contain only registered, already-ingested Markdown, Typst, and
LaTeX authorities, source/identity/alignment registries, deterministic graph
artifacts, canonical document inventory, and `qlkg-store-v2` manifest.

## Create, refresh, and restore

For a new project, initialize and review bounded source roots/globs, add native
definition/reference markers, then run `sync`. Never infer nodes from headings,
document order, proximity, or similarity.

For an in-place snapshot, run this complete command set:

```sh
kgdistiller --repo-root PROJECT check
kgdistiller --repo-root PROJECT agent status
kgdistiller --repo-root PROJECT store snapshot
kgdistiller --repo-root PROJECT store verify
```

For a separate snapshot, run this complete command set:

```sh
kgdistiller --repo-root PROJECT check
kgdistiller --repo-root PROJECT agent status
kgdistiller --repo-root PROJECT store snapshot --output STORE
kgdistiller --repo-root STORE store verify
```

On clone or clean pull:

```sh
kgdistiller --repo-root STORE store verify
kgdistiller --repo-root STORE agent status
kgdistiller --repo-root STORE agent resolve "KNOWN NAME"
```

A verified store is immediately queryable through generation-checked
`GraphView`; do not call or emulate materialization. If verification fails,
stop. Restore a known-good Git revision or repair the native authority on the
owning machine and publish one complete new snapshot generation.

Version 0.4 refuses `qlkg-v2`, `qlkg-sources-v2`,
`qlkg-identities-v1`, and `qlkg-agent-delta-v2`. Do not claim an old core
generation can be retained. Before an upgrade, require a committed Git rollback
point for native authorities and reviewed registries. Export entries and edges
that must survive for review while still on 0.3. With 0.4 installed, explicitly
move the old generated `knowledge/graph/` outside the project or delete that
exact directory after confirming the rollback commit. Review and update the
registry discriminators, then run an unscoped `sync` to rebuild `qlkg-v3` from
native authorities. Re-author reviewed metadata as `qlkg-agent-delta-v3` after
the rebuild. State explicitly that marker-derived nodes and refs return, while
un-reissued 0.3 Agent-curated entries and semantic edges are intentionally lost.

## Initialize Git only with authorization

Recommend private Git when appropriate, but run `git init`, commit, configure a
remote, or push only when explicitly requested. Track registered authorities,
`knowledge/sources.json`, optional `identities.json`/`alignments.json`,
`knowledge/graph/`, `knowledge/documents.jsonl`, and `knowledge/store.json`.
Ignore `knowledge/build/`, journals, plans, receipts, credentials, query logs,
and disposable projections.

Say `store verified locally` only after verify succeeds, `committed locally`
only after inspecting the commit, and `remote confirmed` only after a
successful push whose remote ref contains that commit.

## Serve and export

`kgdistiller serve` uses packaged native assets and binds to `127.0.0.1` by
default. Do not expose it on a network without an explicit separate decision.

For a static consumer, create `export site`, run the bundled standalone
verifier, and report the `qlkg-static-export-report-v1` operation result plus
the bundle's `qlkg-static-export-v2` manifest digest. The consumer adopts the
verified bundle, not the engine checkout.

For editor-plus-browser use, open `PROJECT` itself as the Obsidian vault and
keep the projection at its ignored default:

```sh
kgdistiller --repo-root PROJECT export obsidian --replace
```

`PROJECT` is the editor vault, and registered Markdown files there remain
non-lossy native authorities. The managed `qlkg-obsidian-projection-v1`
subtree is lossy and disposable. Never register that subtree in `sources.json`,
scan/ingest it, or treat projected-note edits as round-trip authority. Source
proxies for Typst and LaTeX navigate to their native files. An explicit external
`--output VAULT` is a browsing-only vault/projection and links back to authority
files. Regenerate either projection from the native authority graph.

## Return a deployment receipt

Summarize absolute roots, layout, `qlkg-store-v2` schema, graph/store generation
digests, document count, installed version/commit, verified Git state, and any
static/Obsidian export path and digest. Never include full authority content,
credentials, or unbounded excerpts. Keep snapshot, Git, export, and network
publication as separate authorities.
