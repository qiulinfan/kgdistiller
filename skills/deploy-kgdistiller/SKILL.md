---
name: deploy-kgdistiller
description: Create, refresh, verify, clone, and restore a small personal kgdistiller knowledge base as a portable file-based Git-friendly store. Use when setting up kgdistiller on a machine, protecting Markdown entries and their Markdown, Typst, LaTeX, or derived PDF evidence against machine loss, synchronizing them across computers, verifying kgdistiller-store-v1, or refreshing the Obsidian plugin's graph feed.
---

# Deploy kgdistiller

Treat the knowledge project—not a product checkout or generated projection—as
the portable authority store. The live graph needs no database, model provider
or materialization step. Optional retrieval caches are not canonical knowledge.
User-registered extraction profiles belong in the source registry.

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

## Choose the requested operation

An ordinary knowledge project needs its native sources, `.knowledge/sources.json`,
`vault.json`, entry Markdown and graph records. Optional identity/alignment
registries exist only for actual reviewed content. It does not need
`documents.jsonl` or `store.json` for capture, query, the Obsidian graph feed or
Git backup.
Never create an empty registry or snapshot merely to fill the directory tree.

When the user requests a portable snapshot, choose:

- an in-place snapshot if the notes repository is the intended backup package;
- `store snapshot --output STORE` for a separate self-contained backup.
  `STORE` must be separate from and not nested in `PROJECT`.

Snapshots include registered, already-ingested native sources, bound entry
Markdown and evidence, reviewed registries and graph state, plus document
inventory and a `kgdistiller-store-v1` manifest. Preserve existing derived
evidence only where an accepted entry still uses it. Do not create converted
source copies or duplicate entry bodies.

## Create, refresh, and restore

For a new project, initialize and review bounded source roots/globs and any
user-defined extraction profiles in `.knowledge/sources.json`. Each source can
select one registered `document_type`; the profile specifies `node_kinds` and
`extraction_guidance`. Follow the registry example in the deployment contract;
do not install a fixed catalog of document classes or infer types from file
formats or domains. Add reviewed native definition/reference markers, then run
`sync`. Never infer nodes from headings, document order, proximity, or similarity.
Keep `.md`, `.typ` and `.tex` sources in their original form. Entry Markdown can
link directly to native evidence; no derived Markdown source is required.

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

On an ordinary knowledge-project clone or clean pull:

```sh
kgdistiller --repo-root PROJECT check
kgdistiller --repo-root PROJECT agent status
kgdistiller --repo-root PROJECT agent resolve "KNOWN NAME"
```

When restoring an actual snapshot with `.knowledge/store.json`, run
`store verify` before accepting it. A missing optional snapshot is not a
verification failure; do not create one simply to satisfy a checker. An existing
snapshot that was not refreshed after source changes must be reported as stale
and verified before use as backup.

A valid graph is directly queryable through generation-checked `GraphView`;
there is no materialization step. Verification failures require a known-good
revision or repair of the native authority, not a sync that masks the mismatch.

Require a `kgdistiller-graph-v2` generation; writers preserve IDs, aliases and
accepted relationships. Entry bodies persist once in Markdown. Never discard the
graph as disposable cache. If status reports any other graph schema, stop and
report it to the user; do not relabel or migrate it.

## Initialize Git only with authorization

Recommend private Git when appropriate, but run `git init`, commit, configure a
remote, or push only when explicitly requested. Track registered authorities,
`.knowledge/sources.json`, optional `identities.json`/`alignments.json`,
`.knowledge/vault.json`, `.knowledge/entries/`, `.knowledge/graph/`, and all evidence
actually referenced by accepted entries. Track `documents.jsonl` and `store.json`
only for a deliberately maintained snapshot.
Ignore `.knowledge/build/`, journals, plans, receipts, credentials, query logs,
and the Obsidian graph feed.

Say `store verified locally` only after verify succeeds, `committed locally`
only after inspecting the commit, and `remote confirmed` only after a
successful push whose remote ref contains that commit.

## Refresh the Obsidian graph feed

Open `PROJECT` itself as the Obsidian vault. The kgdistiller plugin reads one
derived file, `.knowledge/build/obsidian/semantic-graph.json`
(`kgdistiller-obsidian-graph-v1`). Regenerate it after a sync or ingest:

```sh
kgdistiller --repo-root PROJECT export obsidian
```

The command writes the file atomically and refuses a graph that is out of sync
with its authorities or registries. Registered sources and
`.knowledge/entries/*.md` remain the authorities; never register the feed in
`sources.json` or scan it. kgdistiller has no publishing surface; websites and
their registries belong to the repositories that own the notes.

## Return a deployment receipt

Summarize absolute roots, chosen layout, graph generation, installed
version/commit, verified Git state, optional snapshot schema/generation/document
count only when created or verified, and the Obsidian graph feed path when it
was refreshed. Never include full authority content, credentials, or unbounded
excerpts. Keep snapshot and Git as separate authorities.
