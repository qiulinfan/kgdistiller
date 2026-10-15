kgdistiller Obsidian 1.1.0 turns the plugin into the place where you use your
knowledge base while reading. It needs the kgdistiller 1.1.0 CLI (`kgd`),
whose path you set under **Settings → kgdistiller → kgd executable**.

- **Lookup sidebar.** Select text and hold ⌘ (Ctrl elsewhere) for 0.3 s, run
  **Look up selection**, or type into the sidebar's search box. It looks the
  text up in every registered base and shows the records of the same name,
  those that use it as a pending term, the record in focus with a typed graph
  of its neighbours within three steps, and the related records. It follows
  the record or source open in the editor.
- **Agent** (optional). With a model provider configured under **Model
  providers** (fifteen hosted services, Ollama, LM Studio or any
  OpenAI-compatible service), the sidebar judges whether a selection is
  already recorded, answers questions from the knowledge base, arXiv papers
  and the web, finds where a term comes from (**Find sources**) and drafts a
  selection (**Capture**). API keys stay in Obsidian SecretStorage.
- **Capture view.** Drafts wait in the **kgdistiller Capture** view, each
  shown in full with whether `kgd accept` would take it. **Accept** admits a
  draft and embeds it, **Open draft** opens it for editing, **Discard**
  deletes it after asking. Nothing enters the knowledge base unseen.
- **Indexing and embedding.** The keyword index follows saved record files by
  itself; **Re-embed** in the sidebar re-embeds edited records when you choose.
  **Settings → kgdistiller → Embedding** shows and changes the knowledge
  home's embedding model, which runs only on your computer.
- **Desktop only.** Hidden-folder indexing needs the desktop filesystem
  adapter, and Obsidian Sync does not sync dot folders.

The plugin reads knowledge only through `kgd mcp` and changes it only through
`kgd` commands and the draft files it writes. The typed graph view of 1.0 is
replaced by the lookup sidebar's neighbour graph.

`kgd obsidian install` installs the plugin into a registered base's vault and
turns on **Index hidden knowledge folder** in its settings.

The source adapts Hidden Folders Access 2.1.1 at commit
`de3734d36997a98b81a6a6644984748af1e6b3b0`. Its full original MIT license and
source attribution ship in the JavaScript bundle alongside the MIT notices of
Cytoscape.js, cytoscape-fcose 2.2.0, cose-base 2.2.0 and layout-base 2.0.1,
and kgdistiller's own MIT license.
