---
name: deploy-kgdistiller
description: Set up, register and verify a kgdistiller base in the ~/.knowledge home, registering source globs and user-defined document types, then clone, restore and check it as plain Git-friendly files and refresh the Obsidian plugin's graph feed. Use when setting up kgdistiller on a machine, registering a base, its source globs or document types, checking reviewed entries and edges against their source documents after a clone or source edit, or installing and refreshing the Obsidian plugin.
---

# Deploy kgdistiller

kgdistiller has one global home, `$KGDISTILLER_HOME` (default `~/.knowledge`),
and any number of bases. The home registers every base in `config.json`, maps
each base's source globs to user-defined document types and holds those types
in `types/<name>.md`. A base—not a product checkout or generated
projection—is the deployment and backup unit of its knowledge: registered
source documents, `.knowledge/entries/<id>.md` and `.knowledge/edges.jsonl`.
There is no database, model provider, snapshot or materialization step.
Optional retrieval caches under `.knowledge/build/` are rebuildable.

## Align language

Match user-facing explanations, prompts, and handoffs to the user's language
unless the user requests another language. Keep commands, identifiers, schema
keys and action codes, and raw errors unchanged.

## Load the deployment contract

Read [references/deployment-contract.md](references/deployment-contract.md)
completely before changing a base. Every base-bound command takes `--base NAME`
after the command (`kgd check --base NAME`), or runs with the working directory
inside a registered base root; there is no upward search and no default base.
Relative path arguments are resolved against the working directory. Record
installed product version and exact product commit when known. Never place
personal sources, entries, the home's `config.json` or types, credentials, or
feeds in the kgdistiller product repository.

## Register a base

Register the directory once:

```sh
kgd base add BASE_ROOT --name NAME
kgd base list
```

`base add` creates the home on first use (`config.json` as
`{"bases": {}, "embedding": null}`, an empty `types/` and its `.gitignore`),
registers `BASE_ROOT` under `NAME` and creates
`BASE_ROOT/.knowledge/entries/`. It scans nothing and creates no entries. The
default name is the directory's basename; pass `--name` when that is not a
lowercase slug. Base roots never
nest, and the home never lies inside a base root. `kgd base rm NAME` removes
only the registration.

Then register sources and types in the home, following the examples in the
deployment contract:

- Edit `bases.NAME.sources` in `$KGDISTILLER_HOME/config.json`: each key is a
  glob relative to the base root, and each value names one document type.
- Every type is a file `$KGDISTILLER_HOME/types/<type>.md` whose frontmatter
  lists `node_kinds` and optionally `relation_kinds` and `epistemic`, and whose
  body is the user's extraction guidance.

Names, kinds and guidance come from the user. Do not install a fixed catalog of
document classes or infer types from file formats or domains. Every matched
file has exactly one type; review the globs with
`kgd scan --file SOURCE --base NAME` before any extraction. Sources are any
UTF-8 text documents; kgdistiller never parses or converts them. Recommend
keeping the home's `config.json`, `types/` and `.gitignore` in a private local
Git repository; it is owner data, separate from every base.

Entries are created only by reviewed capture or curation through transactional
ingest (`$capture-kgdistiller`, `$curate-kgdistiller-notes`,
`$ingest-kgdistiller`). Never infer nodes from headings, document order,
proximity, or similarity.

## Verify, clone and restore

After setup, an ordinary clone or clean pull, or any source edit:

```sh
kgd check --base NAME
kgd agent status --base NAME
kgd agent resolve "KNOWN NAME" --base NAME
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
machine. A base moved on disk needs its `path` edited in the home's
`config.json`. Never delete an interrupted ingest journal under
`.knowledge/build/kgdistiller-ingest/`; rerun `ingest apply` or report it.

## Initialize Git only with authorization

Recommend private Git when appropriate, but run `git init`, commit, configure a
remote, or push only when explicitly requested. A base tracks its registered
sources, `.knowledge/entries/` and `.knowledge/edges.jsonl`. kgdistiller writes
no `.gitignore` into a base, so the base itself must ignore `.knowledge/build/`
(journals, plans, receipts, reviews, retrieval caches and the Obsidian graph
feed), credentials and query logs. The home is its own private local repository
tracking `config.json`, `types/` and `.gitignore`; it has no remote by default,
so a second machine restores it by hand.

Say `check passed locally` only after `check` prints `OK`, `committed locally`
only after inspecting the commit, and `remote confirmed` only after a successful
push whose remote ref contains that commit.

## Install the plugin and refresh the Obsidian graph feed

Open the base root itself as the Obsidian vault. Install or upgrade the bundled
plugin, then regenerate its feed after every ingest or source edit:

```sh
kgd obsidian install --replace --base NAME
kgd export obsidian --base NAME
```

The plugin reads one derived file, `.knowledge/build/obsidian/semantic-graph.json`
(`kgdistiller-obsidian-graph-v1`), built from every entry and every accepted
edge. Registered sources and `.knowledge/entries/*.md` remain the knowledge.
The feed lies under the hidden `.knowledge/build/`, which no source glob
matches; never scan or ingest it. kgdistiller has no publishing surface;
websites and their registries belong to the repositories that own the notes.

## Relink agent runtimes

`kgdistiller codex link` or `kgdistiller claude link` installs the product's
Skills and presets; run the matching `doctor` first. Installed copies are
product-owned, so relinking replaces a copy that `doctor` reports as differing
and discards local edits to installed files. Report such a copy before
relinking.

## Return a deployment receipt

Summarize the base name and resolved root, the home path, entry and edge counts
from `agent status`, the `check` result, installed version/commit, verified Git
state of the base and the home, and the Obsidian plugin and feed paths when
they were refreshed. Never include full source or entry content, credentials,
or unbounded excerpts.
