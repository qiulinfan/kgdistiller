# kgdistiller for Obsidian

This plugin shows the kgdistiller records of a vault as a typed graph in a
separate `kgdistiller Graph` view, and lets Obsidian index the vault's hidden
`.knowledge/` folder so records open and edit like any other note. It
complements Obsidian's own tools:

- The native editor, Properties panel, backlinks and graph treat records as
  ordinary notes with wikilinks. You edit records there.
- The kgdistiller view draws nodes, role-bound relations, `requires` links,
  drafts and pending terms, and its details pane shows the selected record's
  body and evidence.

The view is live. It builds its graph from the frontmatter that Obsidian's
metadata cache holds for `.knowledge/entries/*.md` and
`.knowledge/drafts/*.md`, and redraws when a record changes, is deleted or is
renamed. The plugin never reads the kgdistiller home or its database. It
never writes knowledge files either: after an edit, run `kgd check` and
`kgd index`.

## Install

Requires Obsidian **1.13.7 or newer** on desktop. The manifest sets
`isDesktopOnly`, so Obsidian does not load the plugin on a phone or tablet.

This README describes plugin 0.1.5 or newer; earlier releases read an older
export format and show none of these records. With the kgdistiller CLI,
`kgd obsidian install` (below) installs the bundled plugin and turns on
hidden-folder indexing in one step. Without it, install **kgdistiller** from
Community plugins, or download `main.js`, `manifest.json` and `styles.css` from
https://github.com/qiulinfan/kgdistiller/releases into
`<vault>/.obsidian/plugins/kgdistiller/`; if either still offers an earlier
version, use `kgd obsidian install` instead. Reload Obsidian, enable the
plugin, and switch on **Index hidden knowledge folder** in its settings.

Viewing records needs no Python and no server. The plugin makes no network
requests, has no telemetry, needs no account and reads nothing outside the
vault. It uses Node's `fs` only to check paths under the vault's `.knowledge`
folder for hidden-folder indexing (symlink checks and, on case-insensitive file
systems, the exact-case name in the parent directory), and the only file it
writes is its own `data.json`. It
neither runs the CLI nor installs or updates software. Checking, accepting and
indexing records, and installing the plugin from the command line, use the
separate kgdistiller CLI, which needs Python >=3.11.

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

Under **Graph view**, **Show drafts** (on by default) draws proposed records
from `.knowledge/drafts`. The toolbar can change it for an open view.

Under **Hidden knowledge folder**, **Index hidden knowledge folder** (off by
default, turned on by `kgd obsidian install`) adds the whole `.knowledge`
subtree to Obsidian's vault, metadata cache, search, links and backlinks.
Nothing else is indexed, and the folder name is fixed. **Indexing status**
reports the result and has a **Rescan** button; use it after creating
`.knowledge`, or run **Rescan hidden knowledge folder** from the command
palette.

Hidden indexing relies on private Obsidian file-system APIs, because the public
API has no way to include a hidden folder. If a later Obsidian release
changes them, the status reports indexing as unsupported. A symlinked
`.knowledge` is reported as invalid. If the **Hidden Folders Access** plugin is
enabled, kgdistiller reports the conflict and leaves its own indexer off.
Indexing makes `.knowledge/` visible to Obsidian search and to the vault's
other plugins. Switching the setting off hides the folder again and never
deletes files on disk.

Only these two settings are stored. A stored value of the wrong type falls
back to its default with a notice, and unknown keys are ignored.

## Graph controls

Open the view with the ribbon icon or **kgdistiller: Open typed graph**. It
opens in the right sidebar.

- **Neighbourhood** (default) draws the records within **Depth** 1 or 2 hops of
  the active file: a record, the records citing an open source, or the records
  of an open sheet. **Full graph** draws every record that passes the filters.
- Filters: **Kind**, **Class** (nodes or relations), **Understanding**
  (`unknown`, `not-yet-understood` or `understood`) and **Source** (a path
  prefix). **Show drafts** and **Show pending terms** add those elements, and
  the **Fit** button fits the view. None of these are stored.
- Nodes are circles colored by kind, with a border for understanding. A
  relation whose roles hold exactly two link values, with no `requires` link
  and nothing linking to it, is one edge labelled with its kind (a diamond
  while pending terms are shown and it has one); any other relation is a
  diamond with one edge per role value. `requires` links are dashed arrows,
  drafts are dashed and translucent, links into other bases end at grey stubs
  and links to missing records are red. The legend in the details pane lists
  every style.
- Selecting an element shows its record: label, kind, class, epistemic label,
  understanding, source and lines, roles and `requires`, then the body and the
  `## Evidence` section rendered as Markdown. **Open record**, **Open source
  at line N** (when the source is in the vault) and **Open sheet** (when
  `.knowledge/sheets/<source>.md` exists) jump to the files.
- **Reload typed graph** re-reads every record from the metadata cache.

The full rendering and model rules are in
[docs/obsidian.md](../../docs/obsidian.md).

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
- [ ] Editing commands, and commands that run `kgd`.
- [ ] Jump links from md/tex sources to records, and "new record from
  selection".
- [ ] A shipped `.base` file and a Bases view.
