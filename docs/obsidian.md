# Obsidian preview and adjustment

A base is normally one Obsidian vault, and its knowledge lives in the vault's
hidden `.knowledge/` folder: accepted records in `entries/`, proposed records in
`drafts/` and generated def/pending sheets in `sheets/`. Obsidian itself is the
editor. The kgdistiller plugin adds two things: it lets Obsidian index the
hidden folder, and it draws the records as a typed graph. `kgd check` stays the
only validator; the plugin never writes knowledge files.

## Native-first editing

With the plugin's **Index hidden knowledge folder** setting on (desktop only),
`.knowledge/**` joins Obsidian's vault and metadata cache like any other
folder. Then:

- the Properties panel edits `label`, `aliases`, `understanding`, `requires`
  and every role list, with link autocomplete;
- Backlinks groups a record's incoming links by role name, because every role
  is a top-level list property;
- renaming a record file rewrites the local links that point to it;
- drafts and sheets open in place, so a sheet's draft checkboxes are ticked in
  the ordinary editor before `kgd harvest`.

After any edit, run `kgd check` (and `kgd index` to refresh retrieval). Mobile
is out of scope: Obsidian Sync does not sync dot folders, and hidden indexing
needs the desktop filesystem adapter.

## The live model

The plugin builds its model only from
`app.metadataCache.getFileCache(file).frontmatter` for
`.knowledge/entries/*.md` and `.knowledge/drafts/*.md`. It builds the model
once the metadata cache is resolved and then updates it on each metadata-cache
`changed` and `deleted` event and each vault `rename` under those two folders,
re-rendering open graph views. There is no exported feed, no polling, no
database access and no reading of the kgdistiller home.

The plugin knows exactly what kgd fixes in the record format (see
[model.md](model.md)):

- the fixed keys `label`, `kind`, `source`, `lines`, `aliases`,
  `understanding`, `epistemic`, `requires`, `tags` and `cssclasses`; every
  other key with a list value is a role;
- the class rule: a record is a relation if and only if a non-fixed key holds a
  non-empty list, otherwise a node;
- the value grammar. A value is a link `[[id]]`, `[[base:id]]` or
  `[[.knowledge/entries/id]]`, where a trailing `.md` and a `|display` part are
  ignored and the target is matched after NFKC normalization and casefolding;
  any other single-line string is a pending term.

Links are resolved textually, never through Obsidian's own link resolution, so
the plugin and kgd always agree: a local id resolves to `entries/<id>.md`, and
for a draft also to `drafts/<id>.md`. An accepted record's link to a draft
therefore shows as missing, as `kgd check` reports it. The plugin cannot read
the home registry, so it does not know the vault's base name: a
`[[<own base>:id]]` link, which `kgd check` rejects, is drawn as a link into
another base. The plugin's tests assert that it parses every case of the
shared link-grammar fixture exactly as kgd does.

## Rendering and controls

The graph view (ribbon icon or **Open typed graph**) uses Cytoscape with its
built-in `cose` layout.

- **Nodes.** Node-class records are circles. The border shows understanding:
  green when understood, amber when not yet understood.
- **Typed binary edges.** A relation with exactly two link values, both in
  roles, that no link targets is drawn as one edge labelled with its kind:
  - both values in one role: an undirected edge;
  - values in two roles: an arrow from the first role's value to the second,
    in frontmatter order;
  - the same record twice: a loop.
- **Diamonds.** Every other relation is a diamond with one role-labelled edge
  per value. That includes every relation that another record links to, so a
  relation about relations always has a node to attach to.
- **Requires.** Each `requires` link is a dashed arrow.
- **Drafts.** Drafts and their edges are dashed. The **Show drafts** setting
  (default on) and the toolbar's **Show drafts** toggle hide them.
- **Other bases and missing records.** A `[[base:id]]` target is a grey stub
  labelled with its uid. A local link to a missing record ends at a red node
  named by the id, and its edge is red.
- **Colour.** Relation kinds take colours from a fixed palette indexed by the
  kind's position in sorted kind order.
- **Toolbar.** A kind filter shows the records of one kind together with the
  records they link to; the drafts toggle; fit to view.
- **Details pane.** Selecting a node or edge shows the record's label, kind,
  class, understanding, epistemic label, source and lines, each role with the
  labels of its values (pending terms and missing records are marked), and
  `requires`. **Open record** opens the record file.
- **Commands.** **Reload typed graph** re-reads every record from the metadata
  cache; **Rescan hidden knowledge folder** re-runs hidden indexing.

Planned for the plugin: neighbourhood mode around the active file,
filters by class, source prefix and understanding, a deterministic node fill
per kind, pending terms as optional ghost nodes, rendering record bodies and
evidence in the details pane, buttons that open the source at its first line
and the record's sheet, `isDesktopOnly`, removing the exclusions setting, the
switch to the `fcose` layout, and `kgd obsidian install` enabling hidden
indexing and warning about the `relative` link format. Editing commands, md/tex
to record jump links, "new record from selection", a shipped `.base` file and a
Bases view are later owner-scheduled work.

## Installation

```bash
kgd obsidian install [--base B] [--replace] [--no-enable]
```

The command copies the bundled `main.js`, `manifest.json` and `styles.css` to
`<root>/.obsidian/plugins/kgdistiller/` of a registered base (by default the
base whose root contains the working directory), keeps an existing `data.json`,
and adds the plugin to the vault's `community-plugins.json` unless
`--no-enable` is given. An existing bundle that differs is replaced only with
`--replace`. Reload Obsidian afterwards.

Then enable **Index hidden knowledge folder** in the plugin settings. Until it
is on, Obsidian does not index `.knowledge/`, and the graph view says so instead
of drawing records.

Keep Obsidian's **New link format** unset (shortest) or `absolute`. The
`relative` format writes `../` links, which are outside the record grammar.

## Hidden-folder indexing

### Settings and operation

In the plugin settings, under **Hidden knowledge folder**:

1. Adjust **Excluded folders** if needed and select **Apply**.
2. Enable **Index hidden knowledge folder**.
3. Check **Indexing status** for the result.

The settings are stored as `hiddenKnowledgeEnabled` (default `false`) and
`hiddenKnowledgeExclusions` (default empty). Only the `.knowledge` subtree is
included; other dot folders, including Obsidian's configuration directory,
remain outside the feature's scope. The folder is the product's single
knowledge root, so it is not configurable.

Each exclusion is a folder path relative to `.knowledge`, entered comma- or
newline-separated, and covers that folder's whole subtree. The settings field
drops leading and trailing slashes, so `archive/` and `/archive` both mean
`archive`. The remaining entries must be relative paths without empty, `.` or
`..` segments or backslashes; an invalid entry sets the status to invalid and
removes the injected cache. Nothing under `.knowledge/` needs excluding, so the
default list is empty; never exclude `entries`, `drafts` or `sheets`, which the
graph and the sheet review in Obsidian read. Changing the list rescans the folder: newly excluded
files leave the native cache and newly included files join it, without any file
being written or deleted on disk. Watcher events below an excluded folder are
ignored.

Stored plugin settings are type-checked at load. A value whose type differs
from the default is replaced by the default and named in a notice; keys the
plugin no longer has are ignored.

If `.knowledge` does not yet exist, the setting is retained and the status
reports it as missing. After creating it, select **Rescan** or run **Rescan
hidden knowledge folder** from the command palette
(`kgdistiller:rescan-hidden-knowledge-folder`). Changes within an indexed folder
are delivered through the adapter's normal file reconciliation and watching
behavior, which is what keeps the graph live.

### Compatibility and boundaries

Hidden-folder indexing requires the desktop filesystem adapter. Mobile and
adapters without the required capabilities report an unsupported status. The
feature uses private Obsidian reconciliation and watcher APIs because the
public API does not provide a hidden-folder allowlist. A later Obsidian release
can require adaptation; capability checks must fail with a bounded status
instead of claiming successful indexing.

Do not run this feature alongside **Hidden Folders Access**. If that plugin is
enabled or loaded, kgdistiller reports the conflict and leaves its own hidden
indexer inactive. Disable the other plugin, then rescan. The setting does not
make hidden files accessible to mobile, other vaults, or other devices by
itself, and it does not promise compatibility with every third-party plugin.

Indexing is configured during plugin load before workspace restoration. Plugin
unload restores its adapter hooks and releases its watches while retaining the
native cache entries and workspace leaves. This avoids removing an open hidden
note during plugin reload. Explicitly switching the setting off removes the
injected cache entries so the folder becomes hidden again; it does not delete
the files on disk.

### Verified behavior

On Obsidian 1.13.7 for macOS, hidden indexing passed native editor, search,
backlinks, internal file renaming with link updates, external nested directory
changes, Unicode names and case-only rename checks. Plugin reload and vault
reload preserved an open hidden note. These checks cover the tested desktop
version; they do not establish behavior on mobile or all future app versions.
The live record model, its link resolution and its rendering rules are verified
by the plugin's vitest suite with a mocked metadata cache, not yet in a live
Obsidian session.

## Source and distribution

The adapter strategy is adapted from
[Hidden Folders Access 2.1.1](https://github.com/dsebastien/obsidian-hidden-folders-access/tree/de3734d36997a98b81a6a6644984748af1e6b3b0),
commit `de3734d36997a98b81a6a6644984748af1e6b3b0`. kgdistiller narrows the feature
to its own `.knowledge` folder and integrates it with its own settings and
lifecycle. The upstream MIT license is preserved in full in
`THIRD_PARTY_NOTICES.md` at the product repository root and in the distributed
JavaScript bundle. The root build verifies that the complete notice, including
the upstream revision, remains in the bundle.
