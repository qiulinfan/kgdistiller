kgdistiller Obsidian 0.1.5 draws the records in the vault's hidden `.knowledge/`
tree live from Obsidian's metadata cache. There is no exported file to keep in
sync: editing a record in Obsidian, or writing one with `kgd`, updates the view.

- **Live model.** The plugin reads the frontmatter of
  `.knowledge/entries/*.md` (accepted records) and `.knowledge/drafts/*.md`
  (proposed records) from the metadata cache. It builds the model once the
  cache is resolved and then updates it on each metadata change, deletion and
  rename under those two folders. Values are resolved textually with the same
  link grammar as `kgd`: `[[id]]`, `[[base:id]]` and
  `[[.knowledge/entries/id]]`, ignoring a `.md` suffix and display text,
  matched after NFKC normalization and casefolding. A local link resolves to
  `entries/<id>.md`; a draft's link also resolves to `drafts/<id>.md`.
- **Nodes.** A record with no role values is a node, ringed green when
  understood and amber when not yet understood.
- **Requires arrows.** Each `requires` link is a dashed arrow. Pending terms
  are not drawn.
- **Typed binary edges and loops.** A relation with exactly two link values,
  both in roles, that no other record links to is one edge labelled with its
  kind. Values in two roles give an arrow from the first role's value to the
  second in frontmatter order; two values in one role give an undirected edge;
  the same record twice gives a loop.
- **N-ary diamonds.** Every other relation, including any relation that another
  record links to, is a diamond with one role-labelled edge per value.
- **Dashed drafts.** Drafts have a dashed outline and dashed edges. The
  **Show drafts** setting (default on) and the toolbar toggle hide them.
- **Other bases and missing records.** `[[base:id]]` targets are grey stubs
  labelled with the uid; local links to a missing record are drawn in red.
- **Controls.** The toolbar filters by kind and toggles drafts. The legend
  explains nodes, relations and drafts. The details pane shows the label,
  kind, class, roles and `requires` of the selected record, with an
  **Open record** button.
- Relation kinds take colours from a fixed palette in sorted kind order.

Removed: `contract.ts` and its feed parser, the **Semantic graph path**,
**Show source nodes** and **Show definition edges** settings, and the re-checks
on window focus and active-leaf change. Stored values of the removed settings
are ignored. The **Excluded folders** setting stays, and its default is now
empty, so the whole `.knowledge` folder is indexed.

The model needs Obsidian to index `.knowledge`: enable **Index hidden
knowledge folder** in the plugin settings (desktop only). Hidden indexing uses
private desktop adapter APIs and reports unsupported environments or a
conflict with Hidden Folders Access.

The source adapts Hidden Folders Access 2.1.1 at commit
`de3734d36997a98b81a6a6644984748af1e6b3b0`. Its full original MIT license and
source attribution ship in the JavaScript bundle alongside the Cytoscape.js
notices and kgdistiller's own MIT license.
