kgdistiller Obsidian 2.0.3 fixes Capture retries and follows source provenance
beyond the first search. It works with the kgdistiller 2.0.2 CLI (`kgd`), whose
path you set under **Settings → kgdistiller → kgd executable**.

- **Retry Capture.** The button becomes available after every finished job,
  including a failed capture or a draft you later discard. A separate
  judgment checks that the proposed item matches the exact selection.
- **Follow the sources.** Find sources investigates Wikipedia history and all
  cited works, including non-arXiv papers and references without links. It
  can search titles and authors, read primary pages and follow their links.
  Earlier work and unresolved leads remain visible when an origin cannot be
  verified; a copied passage is not presented as proof of historical priority.
- **Read the original work.** Publisher and author HTML, text-layer PDFs and
  plain text are supported. Capture reuses the source and primary page
  already read by Find sources. If a source is inaccessible, the report
  explains why another page supplies the evidence. Scanned PDFs need OCR.
- **Respect writing-language guidance.** Descriptions and reports follow the
  registered document type's guidance even when a foreign source is in a
  different language. Evidence quotations stay verbatim.

Every capture remains a draft for you to review and accept. No knowledge
format change or re-embedding is needed for this plugin update. Reload
Obsidian after updating the plugin.

The source adapts Hidden Folders Access 2.1.1 at commit
`de3734d36997a98b81a6a6644984748af1e6b3b0`. Its full original MIT license and
source attribution ship in the JavaScript bundle alongside the MIT notices of
Cytoscape.js, cytoscape-fcose 2.2.0, cose-base 2.2.0 and layout-base 2.0.1,
and kgdistiller's own MIT license.
