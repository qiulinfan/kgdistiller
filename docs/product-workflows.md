# kgdistiller product workflows

kgdistiller owns the deterministic engine, the CLI and read-only MCP server,
the product Skills, per-runtime agent presets, and the runtime workflow
manifests `workflows/manifest.json` (Codex) and `workflows/claude-manifest.json`
(Claude Code). Both manifests declare the same Skills and workflows; only
their installers and agent-preset formats differ.

A base is a directory registered in `$KGDISTILLER_HOME/config.json` (default
`~/.knowledge/config.json`). Its knowledge is records in
`.knowledge/entries/<id>.md`: nodes, the concepts and the results a source
states, whose roles link what they are about, and relations between records,
each bound to the roles its user-registered document type declares. Every Skill that writes
knowledge writes drafts, which wait in `.knowledge/drafts/` until the owner
accepts them. Local sources are any UTF-8 text documents read as numbered
lines; kgdistiller never parses their syntax. A foreign record cites the web
page of an outside work instead. Retrieval reads the derived
database `$KGDISTILLER_HOME/index.sqlite`, which `kgd index` keeps up to date.
Publishing the notes belongs to the repositories that own them.

Reference documents installed with this guide:

- [model.md](model.md): records, foreign knowledge, grammar, drafts, `check`,
  `accept`, `source` and the lock;
- [retrieval.md](retrieval.md): the database, `kgd index`, lag, `search`,
  `resolve`, `get`, `neighbors`, `browse`, `pack` and the MCP tools;
- [obsidian.md](obsidian.md): editing records in Obsidian and the plugin's
  lookup sidebar, agent and Capture view;
- [deployment.md](deployment.md): installation, registration, restore and Git.

## Installing the integration

The manifests are the portable asset and workflow inventory. Install and
validate the integration for the runtime in use:

```sh
kgdistiller codex link
kgdistiller codex doctor
```

```sh
kgdistiller claude link
kgdistiller claude doctor
```

The portable entry is
`$CODEX_HOME/workflow-products/kgdistiller/workflows/manifest.json` for Codex
and `workflow-products/kgdistiller/workflows/claude-manifest.json` below the
Claude Code home (`$CLAUDE_CONFIG_DIR` when set, otherwise `.claude` in the
user profile) for Claude Code; resolve `workflow_guide` and
`workflow_resources` relative to that canonical product root. Each linker
manages only manifest-declared kgdistiller assets and namespaced state. It
never replaces global `AGENTS.md`, `config.toml`, `CLAUDE.md`, `settings.json`,
unrelated Skills or unrelated agent presets. Copy mode is a snapshot that must
be refreshed after product changes; live link modes follow the source. Every
installed copy is product-owned: `doctor` reports a copy whose bytes differ
from the product source, and the next `link` replaces it and removes retired
copies, discarding local edits to installed files.

## Workflows

| Workflow | Skill | Mode | Steps |
|---|---|---|---|
| `capture-knowledge` | `capture-kgdistiller` with `capture-writer` | write | One lookup turn (`kgd source`, the cited passage, one `kgd resolve` and one `kgd drafts --name` of the item and the concepts it links, `kgd search --no-dense --limit 5`); a decision by the conflict rules and the document type that fits the source; one command that writes the draft (citing its lines verbatim, a foreign draft quoting the web page of the work it borrows from, or a replacement of the recorded item), marks a local one in its source with `kgd mark` and lists it with `kgd drafts`; report. The owner accepts it. |
| `compile-knowledge` | `compile-kgdistiller` | write | Set the bounded scope; `kgd source FILE` and the document type that fits it; one lookup turn for the whole candidate batch in every base and in the drafts; new drafts and replacement drafts by the conflict rules in one write turn; in one command, `kgd mark` for each new local draft, `kgd relocate` for each recorded item whose citation moved (following its markers first, with the passage when that is refused) and `kgd drafts --source` with the `source` value `kgd source` printed, every draft free of problems; report. The owner accepts them. |
| `relocate-knowledge` | `relocate-kgdistiller` | write | The records `kgd check` reports moved, followed or stale, or the one the owner names (one lookup turn: `kgd resolve`, `kgd search --no-dense --limit 5`, `kgd drafts --name`); `kgd check --fix-lines` and `kgd relocate UID`, which follow each record's markers through its own file, its base and every other base; a reworded passage judged by its meaning, relocated with `kgd relocate UID -` and its new text, or a replacement draft when the meaning changed; unmarked knowledge found nowhere made foreign with `kgd relocate UID --url URL -` and the page's own text; report. |
| `query-knowledge` | `query-kgdistiller` with `query-reviewer` | read-only | `search`, `resolve`, `get`, `neighbors`, `browse`, `pack`, `drafts`, `find`, `markers` across every base through the CLI or the MCP tools; bounded evidence with `source:lines`; identity classification; refresh on lag. |
| `deploy-kgdistiller` | `deploy-kgdistiller` | write | Install with the `retrieval` extra; `kgd base add` with the base's document types; types and `embedding` in the home; `kgd check`; `kgd index`; `kgd obsidian install` (enables the plugin and hidden indexing; report any newLinkFormat warning); `kgd claude link`/`kgd codex link` and both doctors. |

Step modes mean:

- `read-only`: reads knowledge and changes nothing but the derived database
  (`kgd index --no-embed` when a result reports changed files);
- `write`: writes drafts, moves accepted records' citations with
  `kgd relocate`, or changes the home through `kgd` commands.

`agent: null` means the step runs in the current agent; a named agent refers
to an installed preset. Manifests describe assets, not automatic triggers or a
scheduler.

**Agents draft; the owner accepts.** No Skill runs `kgd accept` or edits what
an accepted record says: a capture, a compile and a re-ingested source all
end in drafts, new records or replacements of accepted ones. Moving where a
record cites, a localization included, is the one direct write,
`kgd relocate` (and `kgd check --fix-lines` for what only moved), which also
rewrites the links to a record it moves into another base. `kgd mark`, which
the writing Skills run after a local draft, writes one marker into the
draft's source and changes no record.
The owner reviews, revises and accepts them in the Obsidian Capture view or
with `kgd accept`, which indexes and embeds what it admits. Re-embedding a
record edited by hand is the owner's call (`kgd index`, the plugin's
Re-embed). The plugin refreshes the keyword index when the owner saves a
record in Obsidian; edits made elsewhere lag until the next index run, and
every read reports that lag.

## Capture while reading

`$capture-kgdistiller` drafts one item the owner selected while reading: a
concept, a result the source states (a theorem, a finding or an example, whose
roles link what it is about), or a relation between records, in a short fixed
pass. One lookup turn reads the profiles of the source's base with
`kgd source FILE`, from which it chooses the type that fits the document, and
compares existing senses with one
`kgd resolve`, one `kgd drafts --name` and one `kgd search` of the item and
the concepts it links, in every base. A label names one concept: a record of
the same name and meaning is the same knowledge point, and a new concept of
another meaning with a taken name adds its source's name, `Label (source)`.
A new item becomes a draft that one shell command writes, marks in its
source with `kgd mark` (or by wrapping a name the source already writes in its
own marker syntax) and lists with `kgd drafts`, whose `problems` must be empty
(the capture request is the consent). A draft in a base of several types
declares its `type:`. A concept the passage only uses or credits to other work is drafted
as foreign knowledge from the web page of the work that originates or
explains it, in the base of the source being read. An item already recorded
follows the conflict rules of [model.md](model.md) "Conflicts, updates and
relocation": it is ignored when its meaning is unchanged, and otherwise drafted
as a replacement whose report says whether the new definition is more
general, corrects the recorded one or conflicts with it; a record of another
base is not replaced from there, and the proposal is reported. Every value is
a link to a record or a draft of any base; a concept nothing records is left
out of the values and reported, to be captured on its own first, and a
statement node needs at least one value.
`understanding` is `yes` unless the owner says otherwise. It reports the
classification and the draft in one short answer.

The `capture-writer` preset runs this Skill in the background on a small model
at low reasoning effort (Claude Code: `model: haiku`, `effort: low`; Codex:
`model_reasoning_effort = "low"`), because the model's reasoning volume, not
`kgd`, dominates a capture's time; the owner keeps reading and gets one line
when it is done. Its lookup loads no embedding model: it searches the name and
lexical lanes with the item's names in both languages.

## Compile a source

`$compile-kgdistiller` extracts a bounded scope of one source, up
to the whole file when the owner asks for full distillation, typically for the
owner's own notes or familiar material; re-admitting a rewritten note is the
same request. It works in batches: one lookup turn looks up the whole
candidate batch in every base and in the drafts, and one write turn writes
every draft, of the document type that fits the source, each new local one
marked with `kgd mark`. It applies the conflict rules of [model.md](model.md)
"Conflicts, updates and relocation": new knowledge becomes new drafts,
recorded knowledge whose citation moved is relocated with `kgd relocate`
(following its markers, or to the passage when that is refused), into
this source's base when the record lies in another, and recorded knowledge
whose meaning changed becomes a replacement draft in its record's base (a
record of another base is reported with the proposed replacement).
Records whose knowledge the source no longer contains, or whose source file no
longer exists, are reported for the owner to decide. The run finishes with
`kgd drafts --source` and the base-relative `source` value `kgd source`
printed, every draft free of problems. It also serves
requests to file or ingest a document into the knowledge base.

## Review and acceptance

The owner reviews the drafts in the Obsidian plugin's Capture view, which
groups them by source, or opens them like any file (with the plugin's hidden
indexing on, `.knowledge/` opens like any folder), edits a draft's fields and
links on its Capture view card or with `kgd edit`, and accepts them there or
with `kgd accept DRAFT...`, which admits exactly the drafts it is given,
indexes and embeds them. A draft that links a draft not given is refused with
`select [[x]] too`: the plugin passes a draft's `with` together, and a CLI
caller names them. A replacement draft is refused when its record changed
after the draft was written. A refusal changes nothing and names the
problem. Deleting a draft rejects it and leaves no trace.

## Relocate a record

Sources change, and records follow them through their knowledge markers
([model.md](model.md) "Finding and relocating passages").
`$relocate-kgdistiller` starts from what `kgd check` reports (`moved`,
`follow` and `stale` records, and missing source files) or from the
knowledge the owner names, exactly or roughly, found in one lookup turn
(asking the owner when several records remain plausible).

- `kgd check --fix-lines` rewrites the records whose quotes only shifted, and
  those whose markers lead to another file of their base where the quotes
  still hold.
- `kgd relocate UID`, without a passage, follows each remaining record's
  markers through its own file, then its base, then every other base, and
  moves it where its quotes still hold, into another base with every role
  and `requires` link to it rewritten to its new uid when it lies there.
- Where the markers name a record but its quotes no longer hold, the Skill
  reads the marked passage and judges it by meaning: the same knowledge
  reworded is relocated with `kgd relocate UID -` and the new passage on
  standard input, which finds it in the sources of every base by its words
  in order whatever the line breaks and hidden markup and replaces the
  Evidence with it; a changed meaning gets a replacement draft. A passage the
  owner pastes is relocated the same way, and one found several times needs
  `--base` or `--source`.
- A record no marker names is looked for once with `kgd find`; when it is
  nowhere in the owner's files, the Skill finds the outside page that states
  it, preferring the originating work, and makes the record foreign with
  `kgd relocate UID --url URL -` and the page's own text.

`kgd relocate` rewrites only the record's `source`, `lines` and, when needed,
Evidence, and refreshes the keyword index when it wrote an accepted record. A
foreign record relocated to a local passage becomes local. `kgd find` (MCP
`kg_find`) is the passage search on its own, read-only: where a passage or a
name occurs in the sources.

## Query

`$query-kgdistiller` and the `query-reviewer` preset read through
`kgd search`, `kgd resolve`, `kgd get`, `kgd neighbors`, `kgd browse`,
`kgd pack`, `kgd drafts`, `kgd find` and `kgd markers` (CLI only), or the MCP tools `kg_search`,
`kg_resolve`, `kg_get`, `kg_neighbors`, `kg_browse`, `kg_pack`, `kg_drafts`
and `kg_find` of `kgd mcp`, across every registered base. `neighbors` gives
prerequisite closures and, with `--dir in`, the statement nodes and relations
that link a record; `pack` gives a bounded evidence packet whose gaps are
reported, never filled by guessing.
`search` fuses a lexical, a dense and a name lane; when the retrieval extra is
missing it fails unless the dense lane is skipped with `--no-dense` (MCP:
`no_dense`). Answers cite `source:lines` with evidence quotes. When a result
reports `lag.changed_files > 0`, the reader runs `kgd index --no-embed` and
repeats the query; that keyword refresh is its only write. `lag.unembedded`
and `lag.embedding_changed` are reported as weaker dense coverage; re-embedding
is the owner's call. Lookup during reading happens at the
actual use sites; a record not found is not proof the owner lacks the concept,
and a record marked `understanding: no` is one the owner has not mastered. Identity classification for authoring returns `matched`,
`ambiguous` or `unmatched` per candidate.

## Set up, check and restore

`$deploy-kgdistiller` installs the package with the `retrieval` extra,
registers bases with their document types (`kgd base add --type`), writes the
types and the `embedding` model id into the home, runs `kgd check` (`--fix-lines` after a
source edit moves cited lines), builds the index (`kgd index`; delete
`index.sqlite*` and rerun it to restore, which re-embeds every record),
installs the Obsidian plugin and links the agent runtimes. Git initialization,
commits, remotes and pushes remain separate explicit actions.

## Reading papers

Ordinary paper reading and explanation need no Skill. For paper source
reading, prefer HTML, then the LaTeX source package, then the PDF. Do not
require TeX manifests for HTML or PDF reading. Local source acquisition and
original knowledge extraction are not redistribution: a no-redistribution
notice alone does not justify blocking ordinary local reading. Keep full source
copies local, separate from original knowledge notes and short necessary
excerpts.

Local evidence needs a source: an `.md`, `.tex` or `.txt` file of a base
outside hidden directories, read as UTF-8, whose lines the record cites and
quotes verbatim. To capture from a paper, keep its text (an extracted
Markdown, a translation or the LaTeX source) in the base, or capture into an
authored reading note. A concept from a
work the owner does not hold is foreign knowledge: its record cites the web
page (preferably the work's arXiv page, otherwise an encyclopedia article)
and quotes it verbatim; it can be localized with `kgd relocate` once the
work is in a base.

## Handoffs

Reading handoffs lead with the explanation and the records actually found,
with their uids and `source:lines`; if a lookup was omitted or unavailable,
say so instead of inventing results. Write handoffs name the drafts written,
the records they replace, and their `problems` as `kgd drafts` lists them. Git state and the
Obsidian plugin each have their own status; do not collapse them into a
generic "deployed" result.
