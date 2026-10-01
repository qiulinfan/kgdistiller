kgdistiller Obsidian 0.1.2 uses a concise "Graph data" settings heading without repeating the plugin name. The graph panel keeps its chosen workspace location when the plugin unloads, and settings use Obsidian's native Setting API.

The clean root builder explicitly installs the integration's development tools even when NODE_ENV=production, so the published bundle can be reproduced from source.

Original project code is MIT-0. The bundled Cytoscape.js retains its MIT notices. The separate Python core remains version 0.4.0.
