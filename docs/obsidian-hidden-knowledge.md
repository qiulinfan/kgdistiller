# Native Obsidian indexing for a hidden knowledge folder

The Obsidian plugin can make one configured hidden folder, `.knowledge` by
default, participate in the desktop vault's normal file and metadata indexing.
The option is off by default. It preserves the files' actual paths and lets
Obsidian handle editing, links, backlinks, search and its native graph for the
file formats those features support. It does not convert source files or add
new Markdown behavior to Typst or LaTeX files.

## Settings and operation

In the plugin settings, under **Hidden knowledge folder**:

1. Set **Hidden folder path** if needed and select **Apply**.
2. Enable **Index hidden knowledge folder**.
3. Check **Indexing status** for the result.

The settings are stored as `hiddenKnowledgeEnabled` (default `false`) and
`hiddenKnowledgeFolder` (default `.knowledge`). Only that folder's subtree is
included; other dot folders, including Obsidian's configuration directory,
remain outside the feature's scope.

If the configured folder does not yet exist, the setting is retained and the
status reports it as missing. After creating it, select **Rescan** or run
**Rescan hidden knowledge folder** from the command palette
(`kgdistiller:rescan-hidden-knowledge-folder`). Changes within an indexed
folder are delivered through the adapter's normal file reconciliation and
watching behavior.

This setting changes indexing only. It does not move an existing knowledge
base, rename `knowledge/`, rewrite source links, or change the **Semantic graph
path** setting. The core locates the project's single `knowledge/` or
`.knowledge/` tree; new projects still default to `knowledge/`. Both trees
cannot coexist. Moving an existing tree requires an explicit migration that
updates entry evidence paths, graph inventories, source-sheet links and
consumer configuration. The indexing toggle never performs that migration.

## Compatibility and boundaries

The plugin still supports mobile for its existing graph features; hidden
folder indexing requires the desktop filesystem adapter. Mobile and adapters
without the required capabilities report an unsupported status. The feature
uses private Obsidian reconciliation and watcher APIs because the public API
does not provide a hidden-folder allowlist. A later Obsidian release can require
adaptation; capability checks must fail with a bounded status instead of
claiming successful indexing.

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
the files on disk. Private API behavior still needs verification when the
Obsidian version changes.

## Verified behavior

On Obsidian 1.13.7 for macOS, the integrated feature passed native editor,
search, backlinks, internal file renaming with link updates, external nested
directory changes, Unicode names and case-only rename checks. Plugin reload
and vault reload preserved an open hidden note. These checks cover the tested
desktop version; they do not establish behavior on mobile or all future app
versions.

## Source and distribution

The adapter strategy is adapted from
[Hidden Folders Access 2.1.1](https://github.com/dsebastien/obsidian-hidden-folders-access/tree/de3734d36997a98b81a6a6644984748af1e6b3b0),
commit `de3734d36997a98b81a6a6644984748af1e6b3b0`. kgdistiller narrows the feature
to one configured knowledge folder and integrates it with its own settings and
lifecycle. The upstream MIT license is preserved in full in
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) and in the distributed
JavaScript bundle. The root build verifies that the complete notice, including
the upstream revision, remains in the bundle.
