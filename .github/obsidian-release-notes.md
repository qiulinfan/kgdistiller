kgdistiller Obsidian 3.0.0 makes knowledge markers part of reading and writing
knowledge. It works with the kgdistiller 3.0.0 CLI (`kgd`), whose path you set
under **Settings → kgdistiller → kgd executable**; earlier CLIs lack
`kgd markers` and `kgd mark`.

- **See where knowledge lives.** A marker names a record where its source
  states it: `--[[NAME]]--` or `--[[NAME|TEXT]]--` in Markdown and text,
  `\kn{NAME}` or `\kn[NAME]{TEXT}` in LaTeX. Markers show as purple chips in
  live preview and reading view, and as raw text with a purple mark while the
  cursor is in them.
- **Jump both ways.** Cmd/Ctrl+click a marker to open its record. The command
  **Jump between knowledge and its marker** opens a record's marker in its
  source, or the record named by the marker under the cursor. It has no
  default hotkey; bind one under **Settings → Hotkeys**.
- **LaTeX through LaTeX Live.** In `.tex` files opened with LaTeX Live (with
  its editor extension API), `\kn` markers get the same chips and jumps.
- **Capture marks what it drafts.** After writing a local draft, Capture runs
  `kgd mark`, which writes `--[[LABEL]]--:` (or `\kn[LABEL]{}` in LaTeX) on the
  line of its first quote without changing the line count. Foreign drafts take
  no marker.
- **Several document types per base.** Capture lets the agent choose the type
  that fits the document and writes it as the draft's `type:`.

This release needs the 3.0 home format: each base in `config.json` lists its
`types` instead of source globs, since every md, tex and txt file of a base is
a source. Run `kgd check` after updating; see the changelog for the one-time
change. Reload Obsidian after updating the plugin.

The source adapts Hidden Folders Access 2.1.1 at commit
`de3734d36997a98b81a6a6644984748af1e6b3b0`. Its full original MIT license and
source attribution ship in the JavaScript bundle alongside the MIT notices of
Cytoscape.js, cytoscape-fcose 2.2.0, cose-base 2.2.0 and layout-base 2.0.1,
and kgdistiller's own MIT license.
