---
name: deploy-kgdistiller
description: Set up, register and verify kgdistiller bases in the ~/.knowledge home — register source globs and user-defined document types, check records against their sources, build or restore the derived index, install the Obsidian plugin and link the agent runtimes. Use when setting up kgdistiller on a machine, registering a base, its sources or document types, checking a base after a clone or source edit, restoring the index, or installing the Obsidian plugin.
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

## 1. Register a base

```sh
kgd base add BASE_ROOT --name NAME
kgd base list
```

`base add` creates the home on first use (`config.json`, empty `types/`,
`.gitignore`), registers the root and creates `BASE_ROOT/.knowledge/entries/`.
Base roots never nest, and the home never lies inside a base root.
`kgd base rm NAME` removes only the registration and lists the links other
bases still hold into it.

## 2. Register sources and types

Edit `$KGDISTILLER_HOME/config.json` by hand:

- `bases.NAME.sources`: each key is a glob relative to the base root, each
  value a type name. Every matched file has exactly one type.
- `embedding`: keep `null`. Search runs the lexical and name lanes; the dense
  lane is a later release.

Write each `$KGDISTILLER_HOME/types/<type>.md` from the owner's vocabulary:
`node_kinds`, optional `relation_kinds` (each kind with its ordered roles, for
example `example: [uses, setting]` for applications) and optional `epistemic`,
with the owner's extraction guidance as the body. Do not install a fixed
catalog or infer types from file formats. Preview a source with
`kgd sheet SOURCE --json`. Recommend keeping the home's `config.json`, `types/`
and `.gitignore` in a private local Git repository; initialize it only when the
owner asks.

## 3. Check

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

## 4. Index

```sh
kgd index
kgd index --rebuild
```

`kgd index` updates `index.sqlite` from the record files of every available
base and prints a report; it exits 1 when a file is unparseable or a base is
unavailable. `--rebuild` re-derives every row in place; `--no-embed` changes
nothing until the dense lane exists. To restore a lost or damaged database,
delete `index.sqlite*` in the home and run `kgd index`. `kgd base list` then
shows each base's `records`, `drafts`, `indexed` count and `lag`.

## 5. Obsidian

```sh
kgd obsidian install --base NAME --replace
```

Open the base root as the vault, reload Obsidian, then enable **Index hidden
knowledge folder** in the kgdistiller plugin settings; until then the graph view
is empty. The plugin reads record frontmatter live; there is nothing to export.
Keep Obsidian's new-link format unset (shortest) or `absolute`.

## 6. Agent runtimes

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

## Git

Run `git init`, commit, configure a remote or push only when the owner asks. A
base tracks its sources and `.knowledge/entries/`, `drafts/` and `sheets/`. The
home tracks `config.json`, `types/` and `.gitignore`; `index.sqlite*` and
`lock` are ignored. Say `committed locally` only after inspecting the commit,
and `remote confirmed` only after a successful push.

## Deployment receipt

Return the home path, each base's name, root, `records`, `drafts`, `indexed`
and `lag` from `base list`, the `check` result, the `index` report summary,
the plugin path when installed, both doctors' status, the installed version and
commit when known, and Git state only as actually confirmed. Never include full
source or record content, credentials or unbounded excerpts.
