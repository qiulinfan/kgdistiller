# kgdistiller for Obsidian

This plugin adds a separate `kgdistiller Graph` view to Obsidian. It complements
the native graph instead of replacing it:

- Obsidian's native graph and backlinks show ordinary Markdown/Wikilink
  connectivity.
- The kgdistiller view shows directed, typed concept relations with their
  evidence.
- Source → concept definition and reference edges remain visually distinct.

The plugin is read-only. It reads the public
`kgdistiller-obsidian-graph-v1` projection and opens vault files, but it never
reads kgdistiller's internal JSONL files and never edits authorities or graph
state.

## Build and install

From this directory:

```sh
npm ci
npm run check
```

Create `<your-vault>/.obsidian/plugins/kgdistiller/` and copy these files into
it:

- `main.js`
- `manifest.json`
- `styles.css`

Then, from any directory, generate the projection for the registered vault:

```sh
kgdistiller --vault <registered-name-or-id> export obsidian --replace
```

Open the vault in Obsidian, enable **kgdistiller** under Community plugins, and
run **kgdistiller: Open typed graph** or use the ribbon icon. The default input
is `knowledge/build/obsidian/semantic-graph.json`; it can be changed in plugin
settings.

The export must live inside the Obsidian vault for note navigation. An external
projection remains useful as a browsing-only vault, but its authority files are
outside Obsidian's vault boundary.

## Graph controls

The view can filter semantic relations and knowledge fields, independently
show or hide source, definition, and reference layers, inspect edge evidence,
and open projected concept notes or their source notes. Edge styles are:

- solid colored arrows: concept → concept semantic relations;
- green dotted arrows: source → concept definitions;
- blue dashed arrows: source → concept references.

Regenerate the projection after `kgdistiller sync` or ingest. The plugin watches
the semantic graph file and reloads open graph views when that artifact changes.
