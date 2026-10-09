kgdistiller Obsidian 0.1.5 reads the hidden `.knowledge/` tree, which is now the
only kgdistiller knowledge root.

- The default **Semantic graph path** is
  `.knowledge/build/obsidian/semantic-graph.json`. The view reads it through the
  vault adapter, so it loads even when the path is excluded from native
  indexing, hidden indexing is off, or the vault runs on mobile. The view
  re-checks the file when Obsidian regains focus or the active leaf changes and
  reloads only when it changed. Loads are serialized, so simultaneous focus and
  leaf-change events reload once; **Reload typed graph** forces a reload.
- Hidden-folder indexing always targets `.knowledge`; the **Hidden folder
  path** setting is removed and a stored `hiddenKnowledgeFolder` value is
  ignored.
- A new **Excluded folders** setting lists folders under `.knowledge` that stay
  out of native indexing. The default is `build`, which keeps the transient
  `.knowledge/build/` tree out of search, links and the native graph. Remove
  `build` to open review drafts there. Leading and trailing slashes are dropped
  (`build/` means `build`). Changing the list rescans the folder without writing
  or deleting any file.
- The plugin reads only `.knowledge/build/obsidian/semantic-graph.json`,
  regenerated with `kgdistiller export obsidian`. The feed no longer carries
  note paths or fields, the **Field** filter is removed, and older feeds are
  rejected until they are regenerated. Only `kgdistiller-graph-v2` sources are
  accepted. Whenever a product update changes the feed contract, reinstall the
  plugin with `kgdistiller obsidian install --replace` before or together with
  `kgdistiller export obsidian`; an older installed plugin rejects the new feed.
- Open buttons target accepted entries (`.knowledge/entries/<node-id>.md`) for
  concepts and the source authority file for sources, definitions and
  references. Open targets exist only for a graph under the vault-root
  `.knowledge/` folder. A target outside the vault index produces a notice;
  enable hidden-folder indexing to open entries.
- Stored settings are type-checked at load; a wrongly typed value falls back to
  its default and is named in a notice.

Hidden indexing remains optional, desktop-only and off by default. It uses
private desktop adapter APIs and reports unsupported environments or a conflict
with Hidden Folders Access.

The source adapts Hidden Folders Access 2.1.1 at commit
`de3734d36997a98b81a6a6644984748af1e6b3b0`. Its full original MIT license and
source attribution ship in the JavaScript bundle alongside the Cytoscape.js
notices and kgdistiller's own MIT license.
