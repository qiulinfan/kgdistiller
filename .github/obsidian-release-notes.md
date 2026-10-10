kgdistiller Obsidian 1.0.0, the first stable release, draws the records in the vault's hidden `.knowledge/`
tree live from Obsidian's metadata cache and lets you inspect each one beside
its source. There is no exported file to keep in sync: editing a record in
Obsidian, or writing one with `kgd`, updates the view.

- **Desktop only.** The manifest sets `isDesktopOnly`. Hidden-folder indexing
  needs the desktop filesystem adapter, and Obsidian Sync does not sync dot
  folders, so mobile is out of scope. Mobile installs of 0.1.4 receive no
  update.
- **Live model.** The plugin reads the frontmatter of
  `.knowledge/entries/*.md` (accepted records) and `.knowledge/drafts/*.md`
  (proposed records) from the metadata cache. It builds the model once the
  cache is resolved and then updates it on each metadata change, deletion and
  rename under those two folders. Values are resolved textually with the same
  link grammar as `kgd`: `[[id]]`, `[[base:id]]` and
  `[[.knowledge/entries/id]]`, ignoring a `.md` suffix and display text,
  matched after NFKC normalization and casefolding. Pending terms are grouped
  by the same name key as `kgd`.
- **Neighbourhood and full graph.** By default the view shows the
  neighbourhood of the active file at depth 1 or 2: the record itself, the
  records of an open source, or the records of an open sheet's source. A
  toggle switches to the full graph.
- **Filters.** Kind, class, understanding, source prefix and drafts.
- **Details pane.** The selected record's label, kind, class, epistemic label,
  understanding, roles and `requires`, its body and Evidence rendered as
  Markdown, and buttons to open the record, open the source at its first line
  and open the source's sheet when it exists.
- **Rendering.** Records are filled by kind from a fixed palette. Borders show
  understanding: dashed grey when unknown, amber when not yet understood,
  green when understood. Relations with two link values are typed edges or
  loops; every other relation, including any relation that another record
  links to, is a diamond with one role-labelled edge per value. `requires`
  links are dashed arrows. Drafts have a dashed outline, a translucent fill and
  a `draft` badge, behind the **Show drafts** toggle.
- **Pending terms.** Optional ghost nodes, off by default, one per name key.
- **Other bases and missing records.** `[[base:id]]` targets are grey stubs
  labelled with the uid; local links to a missing record are drawn in red.
- **Layout.** The fcose force-directed layout.

Removed: `contract.ts` and its feed parser, the **Semantic graph path**,
**Show source nodes**, **Show definition edges** and **Show reference edges**
settings, the **Hidden folder path** setting, and the re-checks on window focus
and active-leaf change. Hidden indexing now always covers the whole
`.knowledge` folder, which is no longer configurable. Stored values of the
removed settings are ignored.

`kgd obsidian install` installs the plugin into a registered base's vault and
turns on **Index hidden knowledge folder** in its settings. Hidden indexing
uses private desktop adapter APIs and reports unsupported environments or a
conflict with Hidden Folders Access.

The source adapts Hidden Folders Access 2.1.1 at commit
`de3734d36997a98b81a6a6644984748af1e6b3b0`. Its full original MIT license and
source attribution ship in the JavaScript bundle alongside the MIT notices of
Cytoscape.js, cytoscape-fcose 2.2.0, cose-base 2.2.0 and layout-base 2.0.1,
and kgdistiller's own MIT license.
