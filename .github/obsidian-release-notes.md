kgdistiller Obsidian 3.0.1 fixes the findings of the community directory's
automated review of 3.0.0. It works with the kgdistiller 3.0.0 CLI (`kgd`),
whose path you set under **Settings → kgdistiller → kgd executable**.

- **Command key from Obsidian.** Knowledge-marker chips decide between ⌘ and
  Ctrl with Obsidian's `Platform` instead of reading the operating system from
  the browser's `navigator`, and are created with Obsidian's element helpers.
- **Popout windows.** Timers run on the window, so lookups, the hold trigger
  and the kgd client behave the same in popout windows.
- **Cleaner code paths.** The hidden-folder indexer imports the file system
  module directly, binary detection uses no control character in a regular
  expression, and the lookup pane's spacing no longer uses a `:has` selector.
- **Verifiable assets.** `main.js`, `manifest.json` and `styles.css` carry
  GitHub artifact attestations of the workflow that built them.

Nothing changes in the knowledge format or the CLI. Reload Obsidian after
updating the plugin.

The source adapts Hidden Folders Access 2.1.1 at commit
`de3734d36997a98b81a6a6644984748af1e6b3b0`. Its full original MIT license and
source attribution ship in the JavaScript bundle alongside the MIT notices of
Cytoscape.js, cytoscape-fcose 2.2.0, cose-base 2.2.0 and layout-base 2.0.1,
and kgdistiller's own MIT license.
