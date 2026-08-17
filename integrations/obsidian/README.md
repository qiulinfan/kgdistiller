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

## Install in a vault

Install the plugin bundled with the global kgdistiller command into a registered
Obsidian vault from any directory:

```sh
kgdistiller --vault <registered-name-or-id> obsidian install
```

The command atomically installs `main.js`, `manifest.json`, and `styles.css`
under `.obsidian/plugins/kgdistiller/`, preserves existing `data.json` settings,
and adds `kgdistiller` to `.obsidian/community-plugins.json`. Use `--replace` to
update an existing bundle, or `--no-enable` to leave the enabled-plugin list
unchanged. Reload Obsidian after installation or update.

Then generate the graph projection:

```sh
kgdistiller --vault <registered-name-or-id> export obsidian --replace
```

## Develop the plugin

From this directory:

```sh
npm ci
npm run check
```

The production bundle consists of:

- `main.js`
- `manifest.json`
- `styles.css`

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

## Roadmap

- [ ] Add an explicit opt-in, desktop-only hot-update pipeline. Registered
  authority changes should trigger a debounced `kgdistiller sync` followed by
  `kgdistiller export obsidian --replace`; generated graph/build paths must be
  excluded to prevent feedback loops, and failures must remain visible to the
  user. The existing artifact watcher already refreshes open views after a
  successful export.
