# Native Obsidian indexing for a hidden knowledge folder

The Obsidian plugin can make the vault-root `.knowledge` folder, the
kgdistiller knowledge tree, participate in the desktop vault's normal file and
metadata indexing. The folder is the product's single knowledge root, so it is
not configurable.
The option is off by default. It preserves the files' actual paths and lets
Obsidian handle editing, links, backlinks, search and its native graph for the
file formats those features support. It does not convert source files or add
Markdown behavior to other file formats.

## Settings and operation

In the plugin settings, under **Hidden knowledge folder**:

1. Adjust **Excluded folders** if needed and select **Apply**.
2. Enable **Index hidden knowledge folder**.
3. Check **Indexing status** for the result.

The settings are stored as `hiddenKnowledgeEnabled` (default `false`) and
`hiddenKnowledgeExclusions` (default `["build"]`). Only the `.knowledge`
subtree is included; other dot
folders, including Obsidian's configuration directory, remain outside the
feature's scope.

Each exclusion is a folder path relative to `.knowledge`, entered comma- or
newline-separated, and covers that folder's whole subtree. The settings field
drops leading and trailing slashes, so `build/` and `/build` both mean `build`.
The remaining entries must be relative paths without empty, `.` or `..`
segments or backslashes; an invalid entry sets the status to invalid and
removes the injected cache. The default keeps the transient `.knowledge/build/`
work tree, including review drafts and the graph feed, out of the native index;
remove `build` from the list to open those drafts in Obsidian. Changing the
list rescans the folder: newly excluded files leave the native cache and newly
included files join it, without any file being written or deleted on disk.
Watcher events below an excluded folder are ignored.

The typed graph view reads its **Semantic graph path** (default
`.knowledge/build/obsidian/semantic-graph.json`) through the vault adapter, so
it loads even when that path is excluded, hidden indexing is off, or the vault
runs on mobile. Obsidian's file events do not fire for unindexed paths, so the
plugin also re-checks the graph file's modification time and size when
Obsidian regains focus or the active leaf changes, and reloads the view only
when the file changed. Loads run one at a time, so the two events firing
together reload once, and a failing file check is shown in the view. **Reload
typed graph** (`kgdistiller:reload-typed-graph`) forces a reload.

The view's Open buttons work for a graph under the vault-root `.knowledge/`
folder, whose entry and source paths are vault-relative. A concept opens its
own entry file (`.knowledge/entries/<id>.md`, the feed's `authority`). A source
opens its document; a definition opens it with the cursor at the first cited
line whenever the document opens in a Markdown editor. A graph path anywhere
else, including a nested folder's `.knowledge/`, has no Open targets. A target
that is not in the vault index produces a notice that names the path; enable
hidden-folder indexing to open entries. Entry ids are short readable slugs
(at most 200 characters, never Windows-reserved names), so every entry has the
file name `<id>.md`.

Stored plugin settings are type-checked at load. A value whose type differs
from the default, such as a string instead of an exclusion list, is replaced by
the default and named in a notice.

If `.knowledge` does not yet exist, the setting is retained and the status
reports it as missing. After creating it, select **Rescan** or run
**Rescan hidden knowledge folder** from the command palette
(`kgdistiller:rescan-hidden-knowledge-folder`). Changes within an indexed
folder are delivered through the adapter's normal file reconciliation and
watching behavior.

These settings change indexing only. They do not move knowledge data, rewrite
source links or change the **Semantic graph path** setting. The kgdistiller core
always keeps a base's knowledge in `<root>/.knowledge/`, the folder the
plugin indexes.

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
versions. The exclusion list, adapter-based graph loading and entry-targeted
Open buttons are verified by the plugin's vitest suite only, not yet in a live
Obsidian session.

## Source and distribution

The adapter strategy is adapted from
[Hidden Folders Access 2.1.1](https://github.com/dsebastien/obsidian-hidden-folders-access/tree/de3734d36997a98b81a6a6644984748af1e6b3b0),
commit `de3734d36997a98b81a6a6644984748af1e6b3b0`. kgdistiller narrows the feature
to its own `.knowledge` folder and integrates it with its own settings and
lifecycle. The upstream MIT license is preserved in full in
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) and in the distributed
JavaScript bundle. The root build verifies that the complete notice, including
the upstream revision, remains in the bundle.
