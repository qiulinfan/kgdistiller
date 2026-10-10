---
name: deploy-kgdistiller
description: Set up, register and verify kgdistiller bases in the ~/.knowledge home — install with the retrieval extra, register source globs, user-defined document types and the embedding model, check records against their sources, build or restore the derived index, install the Obsidian plugin and link the agent runtimes. Use when setting up kgdistiller on a machine, registering a base, its sources or document types, checking a base after a clone or source edit, restoring the index, or installing the Obsidian plugin.
---

# Deploy kgdistiller

kgdistiller has one global home, `$KGDISTILLER_HOME` (default `~/.knowledge`),
and any number of bases. The home registers the bases in `config.json`, maps
each base's source globs to user-defined document types in `types/<name>.md`,
and holds the derived database `index.sqlite` and the writer `lock`. A base is
a directory (normally one Obsidian vault) whose knowledge lives in
`.knowledge/entries/` (accepted records), `.knowledge/drafts/` (proposed
records) and `.knowledge/sheets/` (generated sheets).

Match the owner's language. Keep commands, ids, keys and raw errors unchanged.

Read [references/deployment-contract.md](references/deployment-contract.md)
completely before changing a home or base. Never place personal sources,
records, the home's `config.json` or types, or the database in the kgdistiller
product repository.

## 1. Install

```sh
git clone https://github.com/qiulinfan/kgdistiller.git
cd kgdistiller
npm run build                     # builds the bundled Obsidian plugin
uv tool install '.[retrieval]'    # or --editable '.[retrieval]'
uv tool update-shell
kgd --help
```

The package bundles the Obsidian plugin, whose built
`integrations/obsidian/main.js` is not tracked in git, so building it needs
Node 22 and npm, and `npm run build` must run before any `uv` command builds
the package; a direct `git+https` install fails for that reason. The
`retrieval` extra brings NumPy and sentence-transformers for the dense
lane; without it `embedding` must stay `null`.

## 2. Register a base

```sh
kgd base add BASE_ROOT --name NAME
kgd base list
```

`base add` creates the home on first use (`config.json`, empty `types/`,
`.gitignore`), registers the root and creates `BASE_ROOT/.knowledge/entries/`.
Base roots never nest, and the home never lies inside a base root.
`kgd base rm NAME` removes only the registration and lists the links other
bases still hold into it.

## 3. Register sources, types and the embedding model

Edit `$KGDISTILLER_HOME/config.json` by hand:

- `bases.NAME.sources`: each key is a glob relative to the base root, each
  value a type name. Every matched file has exactly one type.
- `embedding`: recommend `"BAAI/bge-m3"`, a sentence-transformers model id
  only, with no revision pin. The first `kgd index` downloads the weights into
  the Hugging Face cache (`~/.cache/huggingface`, several GB for this model)
  and embeds every record; afterwards set `HF_HUB_OFFLINE=1` so every load
  stays local. If an offline load reports that the files are not in the
  cached files, the cache entry is incomplete (often a missing `refs/main`);
  report it rather than going online unasked. `null` means lexical and name
  search only. Changing the id re-embeds every record on the next `kgd index`.

Write each `$KGDISTILLER_HOME/types/<type>.md` from the owner's vocabulary:
`node_kinds`, optional `relation_kinds` (each kind with its ordered roles, for
example `example: [uses, setting]` for applications) and optional `epistemic`,
with the owner's extraction guidance as the body. Do not install a fixed
catalog or infer types from file formats. Preview a source with
`kgd sheet SOURCE --json`. Recommend keeping the home's `config.json`, `types/`
and `.gitignore` in a private local Git repository; initialize it only when the
owner asks.

## 4. Check

```sh
kgd check
kgd check --base NAME --fix-lines
```

`check` prints `{"errors", "stale", "moved"}` and exits 1 when any is
non-empty. `--fix-lines` rewrites the `lines:` of records whose evidence moved
after a source edit. `stale` evidence needs a reviewed edit of the quote;
never force a line range. Errors after a clone mean the checkout differs from
what was committed: restore a known-good revision or repair the source on its
owning machine.

## 5. Index

```sh
kgd index
kgd index --rebuild
```

`kgd index` updates `index.sqlite` from the record files of every available
base, then embeds the rows that have no vector, and prints a report with
`reused`, `embedded`, `unembedded`, `truncated` and `embedding_error`; it exits
1 when a file is unparseable, a base is unavailable or embedding failed. The
model loads only when some row needs a vector. `--no-embed` skips embedding
and leaves those rows unembedded. `--rebuild` re-derives every row in place
and re-uses vectors by text, so it loads no model. If `embedding` is set but
the retrieval extra is missing, the report's `embedding_error` says
`install kgdistiller[retrieval] or set embedding to null`; the lexical index
is committed, so report the message and install the extra rather than editing
the model id away unasked.

To restore a lost or damaged database, delete `index.sqlite*` in the home and
run `kgd index`; it re-embeds every record (about two minutes per 500 records
with `BAAI/bge-m3` on Apple silicon), so report the wall time. `kgd base list`
then shows each base's `records`, `drafts`, `indexed` count and `lag`.

## 6. Obsidian

The owner opens the base root as a vault in Obsidian once, so that its
`.obsidian` directory exists. Then:

```sh
kgd obsidian install --base NAME
```

The command refuses a root that is not a registered base; run `kgd base add`
first. It copies `main.js`, `manifest.json` and `styles.css` into
`<root>/.obsidian/plugins/kgdistiller/` (updating an older bundle), enables the
plugin in `.obsidian/community-plugins.json`, and sets `hiddenKnowledgeEnabled`
to `true` in the plugin's `data.json` while keeping its other keys, so Obsidian
indexes `.knowledge/`. The plugin is desktop only and reads record frontmatter
live. Ask the owner to reload Obsidian.

The result's `warnings` list reports `newLinkFormat` set to `relative` in
`.obsidian/app.json`, which writes `../` links that the record link grammar
rejects. Report the warning to the owner; never edit `app.json` unasked. Unset
(shortest) and `absolute` are both fine.

## 7. Agent runtimes

```sh
kgd claude doctor
kgd claude link
kgd codex doctor
kgd codex link
```

Run the matching `doctor` first. Installed copies are product-owned: relinking
replaces a copy that `doctor` reports as differing and removes retired Skills
and presets, discarding local edits to installed files. Report such a copy
before relinking, then run `doctor` again.

Other MCP-capable runtimes, such as OMP, register `kgd mcp` (command `kgd`,
argument `mcp`, `HF_HUB_OFFLINE=1` in its environment) in their own MCP
configuration, as the product's deployment guide describes; the Skill-only
linker still links the Skills.

## Git

Run `git init`, commit, configure a remote or push only when the owner asks. A
base tracks its sources and `.knowledge/entries/`, `drafts/` and `sheets/`. The
home tracks `config.json`, `types/` and `.gitignore`; `index.sqlite*` and
`lock` are ignored. Say `committed locally` only after inspecting the commit,
and `remote confirmed` only after a successful push.

## Deployment receipt

Return the home path, each base's name, root, `records`, `drafts`, `indexed`
and `lag` from `base list`, the `check` result, the `embedding` model id, the
`index` report summary with `reused`, `embedded`, `unembedded` and `truncated`
and the wall time of a restore, the plugin path, version and `hidden_indexing`
result when installed with any `newLinkFormat` warning, both doctors' status, the installed version and commit when known, and Git state only as
actually confirmed. Never include full source or record content, credentials or
unbounded excerpts.
