kgdistiller Obsidian 3.0.2 finishes the community review fixes of 3.0.1. It
works with the kgdistiller 3.0.0 CLI (`kgd`), whose path you set under
**Settings → kgdistiller → kgd executable**.

- **Searchable settings.** The settings tab uses Obsidian 1.13's settings
  definitions, so its rows appear in Obsidian's settings search. Choosing,
  adding or removing a model provider, or changing its service, redraws the
  tab the way Obsidian 1.13 expects.
- **Since 3.0.1.** Knowledge-marker chips take the command key from Obsidian's
  `Platform`; timers run on the window for popout windows; release assets
  carry GitHub artifact attestations.

Nothing changes in the knowledge format or the CLI. Reload Obsidian after
updating the plugin.

The source adapts Hidden Folders Access 2.1.1 at commit
`de3734d36997a98b81a6a6644984748af1e6b3b0`. Its full original MIT license and
source attribution ship in the JavaScript bundle alongside the MIT notices of
Cytoscape.js, cytoscape-fcose 2.2.0, cose-base 2.2.0 and layout-base 2.0.1,
and kgdistiller's own MIT license.
