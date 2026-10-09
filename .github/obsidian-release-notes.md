kgdistiller Obsidian 0.1.4 adds optional native indexing for a hidden knowledge
folder. Enable **Index hidden knowledge folder** in settings to include the
configured folder (default `.knowledge`) in desktop Obsidian's editing, links,
backlinks, search and native graph. The option is off by default and applies only
to that folder's subtree. **Rescan hidden knowledge folder** includes a folder
created after startup.

This feature does not move existing `knowledge/` data or change the semantic
graph path. Hidden indexing uses private desktop adapter APIs and reports
unsupported environments or a conflict with Hidden Folders Access. Existing
mobile graph features remain available; hidden indexing is desktop-only.

The source adapts Hidden Folders Access 2.1.1 at commit
`de3734d36997a98b81a6a6644984748af1e6b3b0`. Its full original MIT license and
source attribution ship in the JavaScript bundle alongside the Cytoscape.js
notices and kgdistiller's own MIT license.

This repository release also includes commit `189194a`: user-registered source
document types guide knowledge extraction independently of file format or
knowledge domain. Entries can link directly to native Markdown, Typst and LaTeX
evidence without requiring a converted Markdown copy, and reviewed node kinds
survive synchronization. These are Python core source changes in this
repository; the core version remains 0.4.0. This Obsidian release does not
publish a new Python package version.
