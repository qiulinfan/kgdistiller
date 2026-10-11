kgdistiller Obsidian 1.0.1 reads the record format of the kgdistiller 1.0.1
core, where a record's `lines` may list several ascending, disjoint ranges such
as `lines: 12-18, 40-44`. The details pane opens the source at the first line
of the first range. The view, filters and rendering are unchanged from 1.0.0.

- **Desktop only.** The manifest sets `isDesktopOnly`. Hidden-folder indexing
  needs the desktop filesystem adapter, and Obsidian Sync does not sync dot
  folders, so mobile is out of scope.
- **Live model.** The plugin reads the frontmatter of
  `.knowledge/entries/*.md` (accepted records) and `.knowledge/drafts/*.md`
  (proposed records) from Obsidian's metadata cache, so editing a record in
  Obsidian or writing one with `kgd` updates the view.

`kgd obsidian install` installs the plugin into a registered base's vault and
turns on **Index hidden knowledge folder** in its settings.

The source adapts Hidden Folders Access 2.1.1 at commit
`de3734d36997a98b81a6a6644984748af1e6b3b0`. Its full original MIT license and
source attribution ship in the JavaScript bundle alongside the MIT notices of
Cytoscape.js, cytoscape-fcose 2.2.0, cose-base 2.2.0 and layout-base 2.0.1,
and kgdistiller's own MIT license.
