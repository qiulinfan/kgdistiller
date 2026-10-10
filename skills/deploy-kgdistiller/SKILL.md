---
name: deploy-kgdistiller
description: Set up, register, verify, clone and restore a small personal kgdistiller knowledge project as plain Git-friendly files, and refresh the Obsidian plugin's graph feed. Use when setting up kgdistiller on a machine, registering sources and user-defined document types, checking reviewed entries and edges against their source documents after a clone or source edit, or installing and refreshing the Obsidian plugin.
---

# Deploy kgdistiller

Treat the knowledge project—not a product checkout or generated projection—as
the deployment and backup unit. Its knowledge is plain files: registered source
documents, `.knowledge/entries/<id>.md` and `.knowledge/edges.jsonl`. There is
no database, model provider, snapshot or materialization step. Optional
retrieval caches under `.knowledge/build/` are rebuildable. User-registered
extraction profiles belong in the source registry.

## Align language

Match user-facing explanations, prompts, and handoffs to the user's language
unless the user requests another language. Keep commands, identifiers, schema
keys and action codes, and raw errors unchanged.

## Load the deployment contract

Read [references/deployment-contract.md](references/deployment-contract.md)
completely before changing a project. Use `kgdistiller --repo-root PROJECT` or a
registered `--vault NAME`. Record installed product version and exact product
commit when known. Never place personal sources, entries, credentials, or feeds
in the kgdistiller product repository.

## Set up a project

For a new project, initialize the knowledge tree and register bounded sources:

```sh
kgdistiller --repo-root PROJECT init --source-root notes --files "**/*.md"
kgdistiller vault register PROJECT --name NAME
```

`init` writes `.knowledge/sources.json`, `.knowledge/vault.json`, an empty
`.knowledge/entries/`, an empty `.knowledge/edges.jsonl` and
`.knowledge/.gitignore` (ignoring `build/`). It scans nothing and creates no
entries. Review the source roots and globs, and add any user-defined extraction
profiles under `document_types`, following the registry example in the
deployment contract. Each source may select one registered `document_type`
whose profile lists `node_kinds` and `extraction_guidance`. Do not install a
fixed catalog of document classes or infer types from file formats or domains.
Sources are any UTF-8 text documents; kgdistiller never parses or converts them.

Entries are created only by reviewed capture or curation through transactional
ingest (`$capture-kgdistiller`, `$curate-kgdistiller-notes`,
`$ingest-kgdistiller`). Never infer nodes from headings, document order,
proximity, or similarity.

## Verify, clone and restore

After setup, an ordinary clone or clean pull, or any source edit:

```sh
kgdistiller --repo-root PROJECT check
kgdistiller --repo-root PROJECT agent status
kgdistiller --repo-root PROJECT agent resolve "KNOWN NAME"
```

`check` validates every entry and edge and prints `OK: <n> entries, <m> edges`
when the store is consistent. It reports an entry as `moved` when its Evidence
quote now occurs, exactly once, at another line range of its source;
`check --fix-lines` rewrites those ranges and nothing else. A `stale` entry's
quote no longer occurs in its source, and an `ambiguous` one occurs several
times; both need a reviewed re-capture, not a forced line change. Staleness is
reported only; it never hides an entry from retrieval or the graph feed.

A failing `check` after a clone means the checkout differs from what was
committed: restore a known-good revision or repair the source on its owning
machine. Never delete an interrupted ingest journal under
`.knowledge/build/kgdistiller-ingest/`; rerun `ingest apply` or report it.

## Initialize Git only with authorization

Recommend private Git when appropriate, but run `git init`, commit, configure a
remote, or push only when explicitly requested. Track the registered sources,
`.knowledge/sources.json`, `.knowledge/vault.json`, `.knowledge/entries/`,
`.knowledge/edges.jsonl` and `.knowledge/.gitignore`. Ignore `.knowledge/build/`
(journals, plans, receipts, reviews, retrieval caches and the Obsidian graph
feed), credentials and query logs.

Say `check passed locally` only after `check` prints `OK`, `committed locally`
only after inspecting the commit, and `remote confirmed` only after a successful
push whose remote ref contains that commit.

## Install the plugin and refresh the Obsidian graph feed

Open `PROJECT` itself as the Obsidian vault. Install or upgrade the bundled
plugin, then regenerate its feed after every ingest or source edit:

```sh
kgdistiller --repo-root PROJECT obsidian install --replace
kgdistiller --repo-root PROJECT export obsidian
```

The plugin reads one derived file, `.knowledge/build/obsidian/semantic-graph.json`
(`kgdistiller-obsidian-graph-v1`), built from every entry and every accepted
edge. Registered sources and `.knowledge/entries/*.md` remain the knowledge;
never register the feed in `sources.json` or scan it. kgdistiller has no
publishing surface; websites and their registries belong to the repositories
that own the notes.

## Relink agent runtimes

`kgdistiller codex link` or `kgdistiller claude link` installs the product's
Skills and presets; run the matching `doctor` first. Installed copies are
product-owned, so relinking replaces a copy that `doctor` reports as differing
and discards local edits to installed files. Report such a copy before
relinking.

## Return a deployment receipt

Summarize absolute roots, the registered vault name, entry and edge counts from
`agent status`, the `check` result, installed version/commit, verified Git
state, and the Obsidian plugin and feed paths when they were refreshed. Never
include full source or entry content, credentials, or unbounded excerpts.
