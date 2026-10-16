# kgdistiller for Obsidian

This plugin brings a kgdistiller knowledge base into Obsidian. It lets
Obsidian index the vault's hidden `.knowledge/` folder, so records open and
edit like any other note, and, once the path of the `kgd` CLI is set, it adds:

- the **kgdistiller Lookup** sidebar, which looks a selection, a typed query
  or the open file up in every registered base and shows the record in focus
  with a typed graph of its neighbours;
- an optional agent that judges whether a selection is already recorded,
  answers questions, finds where a term comes from and captures a selection
  as a draft, with the model provider you configure;
- the **kgdistiller Capture** view, where every draft waits, grouped by
  source, until you edit its fields and links on its card, accept it or
  discard it; a record drawn from the sidebar into it becomes a draft that
  replaces it when accepted.

Every way knowledge enters the base ends in a draft you review.

The plugin never opens the kgdistiller home or its database: it reads
knowledge through `kgd mcp` and changes it only through `kgd` commands
(`kgd source`, `kgd edit`, `kgd accept`, `kgd relocate`, `kgd index`,
`kgd index --no-embed`, `kgd embedding`) and the draft files a capture writes
or a discard deletes. Edit records with Obsidian's editor and
Properties panel; the keyword index follows saved record files by itself.

## Install

Requires Obsidian **1.13.7 or newer** on desktop. The manifest sets
`isDesktopOnly`, so Obsidian does not load the plugin on a phone or tablet.

With the kgdistiller CLI,
`kgd obsidian install` ([CLI installation](#cli-installation)) installs the
bundled plugin and turns on hidden-folder indexing in one step.

Without the CLI, install **kgdistiller** from Community plugins, or download
`main.js`, `manifest.json` and `styles.css` from
https://github.com/qiulinfan/kgdistiller/releases into
`<vault>/.obsidian/plugins/kgdistiller/`. Reload Obsidian, enable the plugin,
and switch on **Index hidden knowledge folder** in its settings.

Hidden-folder indexing alone needs no Python and no server. Lookup, capture
and the Capture view run the separate kgdistiller CLI, which needs Python 3.11
or newer. The plugin has no telemetry, needs no account and installs or updates
nothing. Lookup without the agent makes no network request. The agent sends
the selection, its context and the records it reads to the model provider you
choose; web and paper searches go to Exa (`mcp.exa.ai`), and **Find sources**
also reads Wikipedia and arXiv. API keys stay in Obsidian's SecretStorage, and
`data.json` holds only their names. `kgd embedding set`, run from the
Embedding settings, is the one `kgd` run allowed to download (the embedding
model, from the Hugging Face Hub); the model runs on this computer. The
plugin uses Node's `fs` to check paths under the vault's `.knowledge` folder
for hidden-folder indexing and to write and delete draft files.

Original code is MIT. The bundle embeds Cytoscape.js, cytoscape-fcose,
cose-base and layout-base, all MIT, and the hidden-folder indexer is adapted
from Hidden Folders Access 2.1.1 (MIT). Their licenses are kept in full in the
[third-party notices](../../THIRD_PARTY_NOTICES.md) and in `main.js`.

## CLI installation

Open the base root as a vault in Obsidian once, so that its `.obsidian`
directory exists. Then, from anywhere inside that base:

```sh
kgd obsidian install
```

or, from any directory:

```sh
kgd obsidian install --base <name>
```

Without `--base`, the command uses the registered base whose root contains the
working directory, and refuses a directory outside every registered base
(register one with `kgd base add`). It:

- copies the bundled `main.js`, `manifest.json` and `styles.css` to
  `<root>/.obsidian/plugins/kgdistiller/`, replacing an older bundle through
  a staged copy and restoring the old bundle if anything fails;
- adds `kgdistiller` to `.obsidian/community-plugins.json`;
- sets `hiddenKnowledgeEnabled` to `true` in the plugin's `data.json` and keeps
  every other key;
- warns when `.obsidian/app.json` sets `newLinkFormat` to `relative`, because
  that format writes `../` links that records cannot use. Keep it unset
  (shortest) or `absolute`.

It refuses a symlinked `.knowledge`, `.obsidian` or plugin folder, and a
plugin folder that holds files other than the bundle and `data.json`. It prints
one JSON object whose `status` is `installed`, `updated` or `current`; when
`reload_required` is true, reload Obsidian.

## Settings

- **Hidden knowledge folder.** **Index hidden knowledge folder** (off by
  default, turned on by `kgd obsidian install`) adds the whole `.knowledge`
  subtree to Obsidian's vault, metadata cache, search, links and backlinks.
  Nothing else is indexed, and the folder name is fixed. **Indexing status**
  reports the result and has a **Rescan** button; use it after creating
  `.knowledge`, or run **Rescan hidden knowledge folder** from the command
  palette.
- **Lookup.** **kgd executable** is the absolute path of `kgd` (Obsidian
  started from the Dock does not see the shell's `PATH`); empty turns lookup,
  capture and the Capture view off. **Knowledge home** is the
  `KGDISTILLER_HOME` to use; empty keeps `~/.knowledge`.
- **Embedding.** Shows the knowledge home's embedding model and whether
  records await Re-embed, and changes it with **Use** (`kgd embedding set`,
  which downloads and checks the model) or **Turn off**. The model belongs to
  the home, not to the vault, and nothing is re-embedded until you press
  Re-embed in the sidebar.
- **Model providers.** The services the agent's models come from: a service,
  a name and an API key per provider, one provider shown at a time, with
  model detection and a connection **Test**. The model and reasoning level
  are chosen at the top of the sidebar.
- **Web search.** An optional Exa API key; without one, search uses Exa's
  free rate limits.

Hidden indexing relies on private Obsidian file-system APIs, because the public
API has no way to include a hidden folder. If a later Obsidian release
changes them, the status reports indexing as unsupported. A symlinked
`.knowledge` is reported as invalid. If the **Hidden Folders Access** plugin is
enabled, kgdistiller reports the conflict and leaves its own indexer off.
Indexing makes `.knowledge/` visible to Obsidian search and to the vault's
other plugins. Switching the setting off hides the folder again and never
deletes files on disk. A stored setting of the wrong type falls back to its
default with a notice, and unknown keys are ignored.

## Using it

- **Look up.** Select text and hold ⌘ (Ctrl elsewhere) for 0.3 s, run **Look
  up selection**, or type into the sidebar's search box (**Open kgdistiller**
  in the ribbon or **Search knowledge** puts the cursor there). The sidebar
  shows the records of the same name, the record in focus with its
  neighbours within 3 steps (relations as
  edges, the wheel zooming the graph, a collapsible legend), and the related
  records. Opening a record or a source in the editor focuses it too.
- **Re-embed**, below the search box, is solid with a count while records'
  texts changed since their vectors were computed, or the embedding model
  changed, and runs `kgd index` when pressed.
- **Agent.** With a model chosen in the sidebar, a lookup also says whether
  the selection is already recorded; **Find sources** finds the paper or
  article a term comes from; a typed question gets an answer citing records,
  papers and the web; **Capture** or **Capture selection** drafts a record.
- **Capture view.** Captures appear there while they draft, several at a
  time, then as full drafts saying whether `kgd accept` would take them.
  **Accept** admits a draft with the drafts it links and indexes and embeds
  it; **Open draft** opens the file; **Discard** deletes it after asking.
  **Show drafts** opens the view.

The full description is in [docs/obsidian.md](../../docs/obsidian.md).

## Develop the plugin

From this directory:

```sh
npm ci
npm run check    # vitest suite, then type check and production build
npm run dev      # rebuild main.js on every change, with inline source maps
```

The bundle consists of `main.js`, `manifest.json` and `styles.css`. `main.js`
is not tracked, so a fresh checkout runs `npm run build` at the repository
root before any `uv` command: it checks that the root and plugin metadata
agree, builds the bundle with its license notices, and copies `main.js` and
`styles.css` to the root. With an editable install of the CLI
(`uv tool install --editable '.[retrieval]'`), `kgd obsidian install` copies
the bundle from this directory, so rebuild and rerun it to try a change in a
vault.

## Roadmap

- [x] Publish through the monorepo's guarded root metadata and
  version-checked GitHub release assets. The canonical plugin sources and the
  bundle that `kgd obsidian install` copies remain in this directory.
- [x] Commands that run `kgd`: lookup, capture, accept, index and the
  embedding model.
- [x] A new record from a selection: **Capture** drafts it for the Capture
  view.
- [ ] Editing a draft's fields on its card and asking the agent to revise it.
- [ ] Jump links from source documents to records.
- [ ] A shipped `.base` file and a Bases view.
