---
name: deploy-kgdistiller
description: Create, refresh, verify, clone, and restore a small personal kgdistiller knowledge base as a portable Git-friendly store. Use when setting up kgdistiller on a machine, protecting it against machine loss, synchronizing it across computers, preserving paid or slow embeddings, materializing local SQLite after git clone or pull, or explaining which knowledge files should be tracked versus ignored.
---

# Deploy kgdistiller

Create a portable authority store and treat local SQLite as its materialized
query view. Preserve exact embeddings without making vectors semantic identity
or graph authority.

## Load the deployment contract

Read `docs/deployment.md` completely from the installed kgdistiller package or
pinned submodule before changing a project. If creating the first graph, also
read `docs/graph-contract.md`. Never place personal sources, vectors,
credentials, or generated graphs in the kgdistiller engine repository.

Use `kgdistiller --repo-root PROJECT` below. In a pinned source checkout, use
`PYTHONPATH=vendor/kgdistiller/src python3 -m kgdistiller --repo-root PROJECT`.

## Choose one store layout

- If the notes repository itself is private and is the desired synchronization
  unit, refresh it in place with `store snapshot`.
- If notes live elsewhere or the user wants a dedicated backup repository, use
  `store snapshot --output STORE`. The output must be separate from, not nested
  in, the source project. It contains only registered, already-ingested
  Markdown, Typst, and LaTeX authorities plus the deterministic graph,
  registries, document inventory, and embedding bundle.

Do not choose the kgdistiller engine checkout as `PROJECT` or `STORE`.

## Create or refresh

For a new project, initialize the bounded source registry, review its roots and
globs, add explicit native markers, then run `sync`. Do not infer nodes from
headings, order, or similarity.

Before every portable snapshot run:

```sh
kgdistiller --repo-root PROJECT check
kgdistiller --repo-root PROJECT agent status
kgdistiller --repo-root PROJECT store snapshot [--output STORE]
kgdistiller --repo-root STORE store verify
```

`store snapshot` rejects stale authority/graph generations. It exports only
embeddings already present in the current SQLite index; it never calls a model.
Report the returned document count, embedding count, and store generation. If
the embedding count is zero, say that explicitly rather than claiming the RAG
cache was preserved.

The embedding bundle records namespace, node ID, provider, model, dimensions,
canonical input digest, provider-config digest, vector digest, and exact
little-endian float32 bytes. These records rank retrieval only. They never
merge identities or create trusted edges.

## Initialize Git only with authorization

Recommend a private Git repository, but run `git init`, add a remote, commit,
or push only when the user requests that Git action. Track:

- registered authority files and authored assets required by them;
- `knowledge/sources.json`, optional `identities.json`, and `alignments.json`;
- all files under `knowledge/graph/`;
- `knowledge/documents.jsonl`, `knowledge/store.json`, and
  `knowledge/embeddings/`.

Ignore `knowledge/build/`, SQLite and WAL files, transaction staging, plans,
receipts, credentials, query logs, and provider caches. Ordinary Git is the
default for a small personal store; do not introduce Git LFS unless the user
has chosen it and every clone environment can materialize LFS objects.

After an authorized commit or push, distinguish these states in the response:

- `store verified locally` only after `store verify` succeeds;
- `committed locally` only after inspecting the new commit;
- `remote confirmed` only after the push succeeds and the remote ref contains
  that commit.

## Restore or update another machine

After `git clone` or a clean `git pull`, do not call an embedding provider:

```sh
kgdistiller --repo-root STORE store verify
kgdistiller --repo-root STORE store materialize
kgdistiller --repo-root STORE agent status
```

`materialize` verifies every source, graph, registry, record, and vector digest
before atomically rebuilding `knowledge/build/knowledge.sqlite`. It skips work
when that SQLite already records the same store generation. Resolve several
known IDs or run one bounded context query as a smoke test.

If verification fails, stop. Do not run `sync` merely to hide the mismatch and
do not hand-edit generated manifests or vector records. Restore a known-good
Git revision, or return to the authority machine, reconcile the source and
graph there, produce a new snapshot, and synchronize that complete generation.

## Return a deployment receipt

Summarize the absolute store root, layout, schema, generation hashes, document
and embedding counts, SQLite materialization state, Git commit when any, and
whether a remote was actually confirmed. Never include full authority content,
vectors, credentials, or unbounded excerpts.
