# Obsidian preview and adjustment

A base is normally one Obsidian vault, and its knowledge lives in the vault's
hidden `.knowledge/` folder: accepted records in `entries/`, proposed records in
`drafts/` and generated def/pending sheets in `sheets/`. Obsidian itself is the
editor. The kgdistiller plugin adds three things: it lets Obsidian index the
hidden folder, it draws the records as a typed graph with a details pane, and,
once a kgd executable is configured, it looks selected text up in every
registered base. `kgd check` stays the only validator; the plugin never writes
knowledge files.

## Native-first editing

`kgd obsidian install` (see [Installation](#installation)) turns on the
plugin's hidden-folder indexing, so `.knowledge/**` joins Obsidian's vault and
metadata cache like any other folder. Then:

- the Properties panel edits `label`, `aliases`, `understanding`, `requires`
  and every role list, with link autocomplete;
- Backlinks groups a record's incoming links by role name, because every role
  is a top-level list property;
- renaming a record file rewrites the local links that point to it;
- drafts and sheets open in place, so a sheet's draft checkboxes are ticked in
  the ordinary editor before `kgd harvest`.

After any edit, run `kgd check` (and `kgd index` to refresh retrieval).

The plugin is desktop only: its manifest sets `isDesktopOnly`, so Obsidian
never loads it on a phone or tablet. Mobile is out of scope, because Obsidian
Sync does not sync dot folders and hidden indexing needs the desktop
filesystem adapter.

## The live model

The plugin builds its model only from
`app.metadataCache.getFileCache(file).frontmatter` for
`.knowledge/entries/*.md` and `.knowledge/drafts/*.md`. It builds the model
once the metadata cache is resolved and then updates it on each metadata-cache
`changed` and `deleted` event and each vault `rename` under those two folders,
re-rendering open graph views. The details pane reads the selected record's
file through the vault API, only when a record is selected, to render its
body. There is no exported feed, no polling, no database access and no reading
of the kgdistiller home.

The plugin knows exactly what kgd fixes in the record format (see
[model.md](model.md)):

- the fixed keys `label`, `kind`, `source`, `lines`, `aliases`,
  `understanding`, `epistemic`, `requires`, `tags` and `cssclasses`; every
  other key with a list value is a role;
- the class rule: a record is a relation if and only if a non-fixed key holds a
  non-empty list, otherwise a node;
- the value grammar. A value is a link `[[id]]`, `[[base:id]]` or
  `[[.knowledge/entries/id]]`, where a trailing `.md` and a `|display` part are
  ignored and the target is matched after NFKC normalization and casefolding;
  any other single-line string is a pending term;
- the name key of a pending term: the term after NFKC normalization and
  casefolding, reduced to its runs of letters and digits joined by single
  spaces, so `Measurable-Space` and `measurable space` share one key.

Links are resolved textually, never through Obsidian's own link resolution, so
the plugin and kgd always agree: a local id resolves to `entries/<id>.md`, and
for a draft also to `drafts/<id>.md`. An accepted record's link to a draft
therefore shows as missing, as `kgd check` reports it. The plugin cannot read
the home registry, so it does not know the vault's base name: a
`[[<own base>:id]]` link, which `kgd check` rejects, is drawn as a link into
another base.

The TypeScript link grammar and name key follow kgd's Python. Casefolding goes
character by character: `integrations/obsidian/src/case-folding.json` lists
every character whose Python `str.casefold` differs from its lowercase mapping
(such as `ß`, `ς`, `ᾳ`, `ǰ` and the Cherokee letters), and the Python suite
checks that table against `unicodedata`; every other character is lowercased on
its own by JavaScript. Two fixtures shared by both test suites pin the results:
`tests/fixtures/link-grammar.json` (every link and term case) and
`tests/fixtures/name-key.json` (name keys, including CJK, final sigma, `ß`,
ligatures, combining marks, roman numerals, Greek iota subscripts, Cherokee
and a symbols-only term with an empty key). The Python tests assert kgd's
results and the plugin's vitest suite asserts the same results for the
TypeScript port. These checks cover the characters assigned in the Unicode
version of the Python that runs them; a character assigned only in a newer
Unicode version can fold differently in the two runtimes.

## Rendering

The graph view (ribbon icon or **Open typed graph**) uses Cytoscape with the
cytoscape-fcose force-directed layout. Positions are not stored; every render
lays the drawn elements out again.

- **Nodes.** Node-class records are circles labelled with their `label`.
- **Kind colour.** Each kind takes a colour from a fixed twelve-colour palette
  by its position in the sorted list of every kind in the model, drafts
  included. Filters and the drafts toggle therefore never change a colour. No
  hashing is involved. Nodes, diamonds, typed edges and role edges take the
  colour of their record's kind.
- **Understanding borders.** The border of every node and diamond shows
  understanding: dashed grey when unknown (the key is absent or empty), amber
  when not yet understood, green when understood. A value outside the three
  states gets no understanding border; `kgd check` reports it.
- **Typed binary edges.** A relation with exactly two link values, both in
  roles, that no link targets is drawn as one edge labelled with its kind:
  - both values in one role: an undirected edge;
  - values in two roles: an arrow from the first role's value to the second,
    in frontmatter order;
  - the same record twice: a loop.
- **Diamonds.** Every other relation is a diamond with one role-labelled edge
  per value. That includes every relation that another record links to, so a
  relation about relations always has a node to attach to.
- **Requires.** Each `requires` link is a dashed arrow.
- **Drafts.** A draft has a dashed outline, a translucent fill and a second
  label line `draft`; its edges are dashed and faded. The draft line and fill
  tell a draft apart from a record whose understanding is unknown.
- **Other bases and missing records.** A `[[base:id]]` target is a grey stub
  labelled with its uid. A local link to a missing record ends at a red node
  named by the id, and its edge is red.
- **Pending terms.** Off by default. With **Show pending terms** on, each
  pending term with a non-empty name key becomes one hollow dotted ghost node
  per name key, labelled with its first spelling in model order (records by
  path, then roles in frontmatter order, then `requires`). Role and `requires`
  edges reach it from every record that uses the term. While ghosts are shown,
  a relation with any pending term is drawn as a diamond, so each term keeps
  its incidence edge; with ghosts off, the binary-edge rule above is unchanged.

The legend in the details pane lists every one of these styles.

## Controls

- **Neighbourhood** (the default mode) draws the part of the graph around the
  active file, following Obsidian's `file-open` event. The active file focuses:
  - a record under `entries/` or `drafts/`: that record;
  - a source: the records whose `source` is that file;
  - a sheet `.knowledge/sheets/<source>.md`: the records of that source.

  A relation drawn as a typed edge focuses both of its endpoints. The view
  keeps every element within **Depth** 1 (default) or 2 hops of the focus,
  following all edges in both directions, so depth 2 reaches the other
  participants of a diamond. Depth counts drawn hops, so turning on **Show
  pending terms** redraws a binary relation that has a pending term as a
  diamond and moves its other participant from depth 1 to depth 2. The status
  line shows how many of the filtered records are drawn. With no focus, the
  view asks for a record, a source or a sheet, or for the full graph.
- **Full graph** draws every record that passes the filters.
- **Filters**, applied before the neighbourhood is taken: **Kind** (one kind of
  the model), **Class** (nodes or relations), **Understanding** (one of the
  three states), **Source** (a path prefix of `source`, applied on change) and
  **Show drafts** (default from the **Show drafts** setting, on). The records
  a selected record links to are still drawn as its endpoints.
- **Show pending terms** toggles the ghost nodes; **Fit** fits the view.
- **Commands.** **Reload typed graph** re-reads every record from the metadata
  cache; **Rescan hidden knowledge folder** re-runs hidden indexing.

Mode, depth, filters and the pending toggle belong to the open view and are
not stored.

## Details pane

Selecting a node, diamond or typed edge shows its record:

- the label, kind, class, epistemic label and understanding (unknown
  included), the source with its lines, each role with the labels of its
  values (pending terms and missing records are marked) and `requires`;
- the record's body and its `## Evidence` section, each rendered through
  Obsidian's `MarkdownRenderer` from the record file, so links, math and
  quotes look as they do in the editor;
- **Open record**, which opens the record file;
- **Open source at line N**, shown when the source file is in the vault. It
  opens the source with `openFile(file, {eState: {line}})` at the first line of
  the first range in `lines`. Markdown views honour `eState.line`; the latex-live and tinymist
  source views are expected to honour it too, which is still to be confirmed
  in Obsidian (see the manual checks below);
- **Open sheet**, shown only when `.knowledge/sheets/<source>.md` exists.

Selecting a role or `requires` edge shows its owning record. A ghost shows its
term, its name key and every record that uses it with the role; a stub shows
its uid; a missing record shows the path that does not exist.

## Lookup

Select text in any view and hold the command key (⌘ on macOS, Ctrl elsewhere)
for 0.3 s, run **Look up selection**, or type into the search box at the top
of the view and press Enter (**Search knowledge** puts the cursor there). The selection, with whitespace
collapsed and cut at 500 characters, is looked up in every registered base,
and the **kgdistiller Lookup** view opens in the right sidebar without taking
focus. A press starts at most one lookup, whatever keydown repeats the system
sends. Pressing another key or a mouse button, or releasing the key, cancels
the hold first, so shortcuts such as ⌘C and ⌘-click never start a lookup, and
leaving the window forgets the press. Without a selection, or without a configured kgd executable, a hold
does nothing and the command says what is missing.

The view shows, from the top:

- **Same name**: the records whose label or alias has the selection's name key
  (the senses of `kg_resolve`). A shared name is not the same concept, so they
  are candidates to compare.
- **Pending**: the records that use the name as a pending term.
- The record in focus (the first name match, otherwise the first search
  result, or any record clicked):
  - its kind, base, source and lines, understanding and epistemic label;
  - **Neighbours within 3 steps**: a small typed graph of the records within
    three concept steps, from `kg_neighbors --dir both --depth 6`. Relations
    are edges: a relation with exactly two link values, both in roles, is one
    edge labelled with its kind, and one with more values or with `requires`
    links is a diamond joining them, its participants one step apart. A
    relation that another record links to, and a relation in focus, remain
    diamonds with their role edges. A relation about a single record is no
    edge and is listed under About it instead; pending terms are left out.
    Node records are circles coloured by kind with the graph view's palette,
    `requires` links are dashed and count as one step. At most the 40 nearest
    records are drawn; clicking a record or an edge puts it in focus. Over the
    graph the wheel zooms, elsewhere it scrolls the panel;
  - its prose and Evidence rendered as in the editor and its links by role;
  - **About it**: the relations it takes part in, grouped by kind, with its
    role in each;
  - **Built on it**: the records whose `requires` lists it;
  - its `requires` chain three levels deep
    (`kg_neighbors --role requires --dir out --depth 3`), each record expanded
    once and pending terms as leaves.
- **Related**: the other records among the first ten of `kg_search`, with each
  lane's rank and the first line of the gloss.

Record ids and uids are never shown: they are addresses for programs, not
knowledge.
- **Open record** and **Open source** open the file in this vault when the
  record's base is this vault, and otherwise through an `obsidian://open?path=`
  link in the vault that holds it.
- When a result reports lag, **Run kgd index** runs `kgd index` and repeats the
  lookup.

Settings, under **Lookup**:

- **kgd executable**: the absolute path of `kgd`, since Obsidian started from
  the Dock does not see the shell's `PATH`. Empty, the default, turns lookups
  off.
- **Knowledge home**: the `KGDISTILLER_HOME` that lookups use; empty keeps
  kgd's default `~/.knowledge`. Plugin settings belong to the vault, so a test
  vault can point at a scratch home.

The plugin starts `kgd mcp` on the first lookup and keeps it running, so the
embedding model loads once (about 4 s for `BAAI/bge-m3` on Apple silicon) and
later searches take tens of milliseconds. It stops the server after 15 minutes
without a lookup, when either setting changes and when the plugin unloads. It
also runs `kgd base list` once per server to locate the bases, and `kgd index`
only from the button. Every run gets `HF_HUB_OFFLINE=1`: vectors exist only
after a local `kgd index` loaded the model, so the model is in the local cache.
These processes read the home, the database and the registered bases; the
plugin itself opens neither the home nor the database, and lookups write no
knowledge.

## Agent

A small agent built into the plugin adds four things to the lookup sidebar:
a judgment of the selection, **Capture**, **Find sources**, and an answer to
whatever is typed into the search box. Nothing is written until the owner
presses Capture or runs **Capture selection**.

The agent's model calls go through one resident `omp auth-gateway stdio`,
OMP's OpenAI-compatible gateway on stdin and stdout. OMP holds the provider
credentials and maps roles such as `@smol` to its configured models, so the
plugin stores no API key and talks to no provider itself. The plugin owns the
prompts and the control flow: the model answers only by calling the agent's
tools, a forced tool where one answer is needed. Each
answer is checked before anything uses it; a refused answer goes back to the
model as the tool's result, within a fixed number of calls. The gateway starts
on the first call, stops after 15 minutes without one, when a setting below
changes and when the plugin unloads.

**Judgment.** After the deterministic results, one call compares the
selection, read in the lines around it, with the records sharing its name and
the search's first results, and shows **Recorded**, **Possibly** or **Not
recorded** with one or two sentences on how the selection connects to them.
The model first says whether the selection is a concept or a statement, and a
match must have that class: a concept is a node, a statement a relation, and a
record that only mentions the selection is related, not the same. A matched
record is put in focus unless the owner has focused another one. With the
model's reasoning off, a judgment takes about 1.5 s on DeepSeek V4 Flash.

**Capture** files the selection as one record by the rules of
[the knowledge model](model.md) that the capture-kgdistiller Skill follows:

- The selection comes with its source lines: from the editor directly, and in
  reading view by finding its text in the source as reading view shows it,
  inside the selection's block when the text occurs more than once.
- The plugin prepares everything deterministic: `kgd sheet SOURCE --json` for
  the document type, the records already citing the source and the base, the
  numbered paragraph around the selection with the nearest heading, the first
  `kg_search` results for the selection, and `kg_resolve` of the
  Latin-script names in it.
- The model may call `lookup` once for other names and phrases, then calls
  `submit` with what it takes the item to be, the decision (new, unchanged,
  superseding, conflicting, or borrowed for a concept the source only uses or
  credits to other work) and, for a record, its label, kind, roles, requires,
  statement and evidence quotes with their lines.
- The plugin refuses kinds and roles the type lacks, links to records the
  model was not shown, and values that are neither links nor terms. It looks
  up every name the record would write (label, aliases and plain terms) and
  returns any recorded concept the model had not seen, so a term is looked up
  before it is written as pending. It drops a relation's aliases.
- Evidence holds only the source's text. A quote is kept when it is verbatim
  in its lines after collapsing whitespace, as `kgd check` compares; moved to
  its only occurrence in the source when the lines were wrong; and replaced
  by the source's own text when an alignment inside the cited lines is at
  least 90% similar. Otherwise the model is shown the cited lines to copy.
- A new record is written as a draft with every frontmatter value a quoted
  string, admitted with `kgd accept` and indexed with `kgd index --no-embed`;
  `kgd accept`'s refusal returns to the model. Unchanged and borrowed write
  nothing. Superseding and conflicting show the proposed record beside the
  recorded one and write nothing yet: the in-place overwrite of a more general
  or corrected definition, relocation, and the linking of pending uses remain
  the capture Skill's.
- The sidebar reports the decision, the record and one line from the agent,
  with a notice when the capture ends. Capture asks for low reasoning: on
  eleven test selections from the test papers and notes it decided every one
  as expected in two runs, with a median of about 6 s and the slowest near
  20 s on DeepSeek V4 Flash, usually in one model call. Without reasoning the
  median was 4 s, but a concept the paper credits to earlier work was filed as
  its own.

**Find sources**, for an unrecorded or borrowed term, shows the works the
selected lines cite, read from the source's own reference list (block ids
`^reference-N`), and on the web the likely originating papers from OpenAlex
and the Wikipedia article for the term or its singular. The papers are ranked
by the share of papers using the term that cite them, a title that starts
with the term, an abstract that introduces it and a citation in the Wikipedia
article. It sends the term to OpenAlex and Wikipedia only when pressed, naming
the plugin in its User-Agent as Wikimedia asks; Wikipedia's text is shown
with its CC BY-SA 4.0 notice. A borrowed capture runs it by itself.

**Search** answers what is typed into the search box, from the knowledge
base first and from the web for what it does not hold. The deterministic
results appear as for a selection; the agent starts from the first search
results and the records named in the question, then reads more through
read-only tools, called together where they are independent:
`kg_search`, `kg_get` and `kg_neighbors` through `kgd mcp`, and `web_search`
through OMP's own search chain (one short `omp -p --mode json` run that may
only call OMP's web_search tool, stopped at its result, since the gateway has
no search route). Each step shows in the sidebar as it runs. The answer cites
records as `[[uid]]` and web sources as `[Wn]`, and every citation must be
one the tools returned; the sidebar shows them as K1, K2, … (a click puts the
record in focus) and W1, W2, … (links to the pages), with the cited records and
pages listed under **From the knowledge base** and **From the web**. The
answer keeps the two apart and says so when the knowledge base holds nothing.
A model that writes its answer as text instead of calling the answer tool has
still answered, and the sixth model call must answer. On seven questions
about the test papers and notes, answers took 4 to 6 s from the knowledge
base alone and 8 to 11 s with a web search, on DeepSeek V4 Flash without
reasoning.

Settings, under **Agent**:

- **omp executable**: the absolute path of `omp`. Empty, the default, turns the
  agent off; lookups then show only the deterministic results.
- **Model**: a model OMP knows, such as `deepseek/deepseek-v4-flash`, or an OMP
  role, `@smol` by default.

The model sees the selection or the question, the paragraph around a
selection and the records the agent reads; web searches send their queries
to the search providers configured in OMP. Capture's drafts and `kgd` runs
stay on this machine.

## Not yet built

Later owner-scheduled frontend work: editing commands, jump links from md/tex
sources to records, a shipped `.base` file and a Bases view. For the agent:
the in-place overwrite of superseded records and relocation, linking pending
uses after a capture, and compiling a whole source.

## Installation

Open the base root as a vault in Obsidian once, so that its `.obsidian`
directory exists, then run:

```bash
kgd obsidian install [--base B]
```

The command works on a registered base: the one named by `--base`, or by
default the base whose root contains the working directory. It refuses any
other root, and a base whose `.knowledge` is a symlink; register a root first
with `kgd base add`. It:

- copies the bundled `main.js`, `manifest.json` and `styles.css` to
  `<root>/.obsidian/plugins/kgdistiller/`, replacing an older bundle;
- adds the plugin to the vault's `community-plugins.json`;
- sets `hiddenKnowledgeEnabled` to `true` in the plugin's `data.json`, keeping
  every other key, so Obsidian indexes `.knowledge/` without a manual step;
- warns when `.obsidian/app.json` sets **New link format** (`newLinkFormat`) to
  `relative`. That format writes `../` links, which are outside the record
  grammar; keep it unset (shortest) or `absolute`.

It prints one JSON object: `status` (`installed`, `updated` or `current`),
`plugin_id`, `plugin_version`, `base`, `vault`, `plugin_root`, `files`
(`{path, bytes}` per copied file), `hidden_indexing` (`enabled` or `current`),
`community_plugins` (`updated` or `current`), `warnings` (strings) and
`reload_required` (true when anything was written).

Reload Obsidian afterwards. The plugin is desktop only.

## Hidden-folder indexing

### Settings and operation

In the plugin settings, under **Hidden knowledge folder**:

- **Index hidden knowledge folder** switches indexing on or off. It is stored
  as `hiddenKnowledgeEnabled`, default `false`; `kgd obsidian install` sets it
  to `true`.
- **Indexing status** reports the result and offers **Rescan**.

Indexing covers the whole `.knowledge` subtree and nothing else; other dot
folders, including Obsidian's configuration directory, remain outside the
feature's scope. The folder is the product's single knowledge root, so it is
not configurable, and nothing under it is excluded.

Stored plugin settings are type-checked at load. A value whose type differs
from the default is replaced by the default and named in a notice; keys the
plugin does not have, including those of removed settings, are ignored.

If `.knowledge` does not yet exist, the setting is retained and the status
reports it as missing. After creating it, select **Rescan** or run **Rescan
hidden knowledge folder** from the command palette
(`kgdistiller:rescan-hidden-knowledge-folder`). A symbolic link in place of
the folder is reported as invalid and not indexed. Changes within the indexed
folder are delivered through the adapter's normal file reconciliation and
watching behavior, which is what keeps the graph live.

### Compatibility and boundaries

Hidden-folder indexing requires the desktop filesystem adapter. A desktop
adapter without the required capabilities reports an unsupported status. The
feature uses private Obsidian reconciliation and watcher APIs because the
public API does not provide a hidden-folder allowlist. A later Obsidian release
can require adaptation; capability checks must fail with a bounded status
instead of claiming successful indexing.

Do not run this feature alongside **Hidden Folders Access**. If that plugin is
enabled or loaded, kgdistiller reports the conflict and leaves its own hidden
indexer inactive. Disable the other plugin, then rescan. Indexing exposes
`.knowledge/` to Obsidian search and to other plugins of the vault; it does not
make hidden files available to other vaults or devices, and it does not
promise compatibility with every third-party plugin.

Indexing is configured during plugin load before workspace restoration. Plugin
unload restores its adapter hooks and releases its watches while retaining the
native cache entries and workspace leaves. This avoids removing an open hidden
note during plugin reload. Explicitly switching the setting off removes the
injected cache entries so the folder becomes hidden again; it does not delete
the files on disk.

### Verified behavior

On Obsidian 1.13.7 for macOS, hidden indexing passed native editor, search,
backlinks, internal file renaming with link updates, external nested directory
changes, Unicode names and case-only rename checks. Plugin reload and vault
reload preserved an open hidden note. These checks cover the tested desktop
version; they do not establish behavior on all future app versions.

The plugin's vitest suite verifies the live model, link resolution, name keys
and rendering rules with a mocked metadata cache: binary, n-ary, self,
relation-as-participant, draft, foreign and pending cases, incremental updates
on change, deletion and rename, the filters, kind colours, understanding
classes, the neighbourhood from a record, a typed edge, a source and a sheet,
the details model with its source line and sheet, and the body and Evidence
split.

Still to be checked by hand in Obsidian 1.14.4 on a real vault:

- a Properties edit of a role list round-trips and `kgd check` stays clean;
- Backlinks groups a record's incoming links by role name;
- the graph renders a vault of several hundred records at a usable speed,
  with the neighbourhood and full-graph modes, the filters, and the body and
  Evidence in the details pane;
- **Open source** lands on the record's first line in the latex-live and
  tinymist views, and **Open sheet** appears once a sheet exists;
- hidden indexing is on after `kgd obsidian install` without a manual toggle.

## Source and distribution

The adapter strategy is adapted from
[Hidden Folders Access 2.1.1](https://github.com/dsebastien/obsidian-hidden-folders-access/tree/de3734d36997a98b81a6a6644984748af1e6b3b0),
commit `de3734d36997a98b81a6a6644984748af1e6b3b0`. kgdistiller narrows the feature
to its own `.knowledge` folder and integrates it with its own settings and
lifecycle. The bundle also embeds Cytoscape.js and the fcose layout with its
dependencies cose-base and layout-base, all MIT. Every upstream license is
preserved in full in `THIRD_PARTY_NOTICES.md` at the product repository root
and in the distributed JavaScript bundle. The root build verifies that the
complete notices, including the Hidden Folders Access revision and the
Cytoscape, cytoscape-fcose, cose-base and layout-base copyright lines, remain
in the bundle.
