kgdistiller Obsidian 2.0.2 makes editing global and shows kgd's own errors.
It needs the kgdistiller 2.0.2 CLI (`kgd`), whose path you set under
**Settings → kgdistiller → kgd executable**. After upgrading from 2.0.0, run a plain
`kgd index` (or **Re-embed** in the sidebar) once: every record is embedded
again.

- **Localize across bases.** **Localize here** and **Localize to selection**
  move a foreign record or draft into the base of the local source you
  selected, with every link to it rewritten, and the sidebar follows it to
  its new address. **Localize here** appears only on a registered source.
- **Drafts of every base.** A draft may link drafts of any base; the Capture
  card's **+** finds them, and a linked draft of another base shows its base.
- **Foreign web addresses.** A foreign draft's card edits the address of the
  page it cites.
- **Results are nodes.** A theorem, finding or example links every record it
  concerns and must link at least one; a relation connects records.
- **Clear errors.** When `kgd` refuses something, the plugin shows `kgd`'s
  own message: Localize stops at once instead of retrying, and an
  interrupted accept says what it admitted.
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
