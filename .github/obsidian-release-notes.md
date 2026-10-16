kgdistiller Obsidian 2.0.0 makes every piece of knowledge pass your review:
whatever the agent, a Skill or you capture becomes a draft you edit and
accept. It needs the kgdistiller 2.0.0 CLI (`kgd`), whose path you set under
**Settings → kgdistiller → kgd executable**. Records and document types of
1.x need a one-time edit; the changelog's upgrade notes list it.

- **Capture view.** Drafts are grouped by source on the left, by file name,
  with foreign drafts under **foreign**. Each draft's card edits its label,
  kind, other names, epistemic label, understanding, statement and every
  role's and `requires`' links in place, through `kgd edit`. **Accept**
  admits a draft with the drafts it links and embeds it.
- **Revise a record.** **Revise as draft** in the lookup sidebar, or dragging
  a record onto the Capture view, copies it into a draft that replaces it
  when accepted.
- **Foreign knowledge.** A concept the text only uses or credits to other
  work is drafted from the web page of the work that originates it, found by
  **Find sources**. **Localize here** moves it to the local passage once you
  hold the work.
- **Statements and relations.** A theorem, finding or example about one
  record is a node linked to it; a relation connects two or more records. The
  record pane shows its **Connections**, how many records it links or is
  linked by.
- **Lookup everywhere.** Text selected in the sidebar or the Capture view is
  looked up and captured like text in a note.
- **Embedding model in the sidebar.** The model is shown beside **Re-embed**
  and switched from the runnable models in the local cache or by id, after a
  confirmation. An index schema change keeps the existing vectors.
- **Desktop only.** Hidden-folder indexing needs the desktop filesystem
  adapter, and Obsidian Sync does not sync dot folders.

The plugin reads knowledge only through `kgd mcp` and changes it only through
`kgd` commands and the draft files it writes.

`kgd obsidian install` installs the plugin into a registered base's vault and
turns on **Index hidden knowledge folder** in its settings.

The source adapts Hidden Folders Access 2.1.1 at commit
`de3734d36997a98b81a6a6644984748af1e6b3b0`. Its full original MIT license and
source attribution ship in the JavaScript bundle alongside the MIT notices of
Cytoscape.js, cytoscape-fcose 2.2.0, cose-base 2.2.0 and layout-base 2.0.1,
and kgdistiller's own MIT license.
