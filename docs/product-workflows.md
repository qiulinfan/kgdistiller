# kgdistiller product workflows

kgdistiller owns the deterministic engine, the CLI and read-only MCP server,
the product Skills, per-runtime agent presets, and the runtime workflow
manifests `workflows/manifest.json` (Codex) and `workflows/claude-manifest.json`
(Claude Code). Both manifests declare the same Skills and workflows; only
their installers and agent-preset formats differ.

A base is a directory registered in `$KGDISTILLER_HOME/config.json` (default
`~/.knowledge/config.json`). Its knowledge is records in
`.knowledge/entries/<id>.md`: nodes, and relations that bind records to the
roles their user-registered document type declares. Every Skill that writes
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
| `capture-knowledge` | `capture-kgdistiller` with `capture-writer` | write | One lookup turn (`kgd source`, the cited passage, one `kgd resolve` and one `kgd drafts --name` of the item and the concepts it links, `kgd search --no-dense --limit 5`); a decision by the conflict rules; one command that writes the draft (citing its lines verbatim, a foreign draft quoting the web page of the work it borrows from, or a replacement of the recorded item) and lists it with `kgd drafts`; report. The owner accepts it. |
| `compile-knowledge` | `compile-kgdistiller` | write | Set the bounded scope; `kgd source FILE`; one lookup turn for the whole candidate batch in every base and in the drafts; new drafts and replacement drafts by the conflict rules in one write turn; `kgd drafts --source FILE` with every draft free of problems; report. The owner accepts them. |
| `relocate-knowledge` | `relocate-kgdistiller` | write | One lookup turn for the record the owner names (`kgd resolve`, `kgd search --no-dense --limit 5`, `kgd drafts --name`); `kgd relocate UID -` with the passage the owner gives, which finds it in the record's base and rewrites only its `source`, `lines` and, when needed, Evidence; report. |
| `query-knowledge` | `query-kgdistiller` with `query-reviewer` | read-only | `search`, `resolve`, `get`, `neighbors`, `browse`, `pack` across every base through the CLI or the MCP tools; bounded evidence with `source:lines`; identity classification; refresh on lag. |
| `deploy-kgdistiller` | `deploy-kgdistiller` | write | Install with the `retrieval` extra; `kgd base add`; sources, types and `embedding` in the home; `kgd check`; `kgd index`; `kgd obsidian install` (enables the plugin and hidden indexing; report any newLinkFormat warning); `kgd claude link`/`kgd codex link` and both doctors. |

Step modes mean:

- `read-only`: reads knowledge and changes nothing but the derived database
  (`kgd index --no-embed` when a result reports changed files);
- `write`: writes drafts, or changes the home through `kgd` commands.

`agent: null` means the step runs in the current agent; a named agent refers
to an installed preset. Manifests describe assets, not automatic triggers or a
scheduler.

**Agents draft; the owner accepts.** No Skill runs `kgd accept` or edits what
an accepted record says: a capture, a compile and a re-ingested source all
end in drafts, new records or replacements of accepted ones. Moving where a
record cites is the one direct write, `kgd relocate`.
The owner reviews, revises and accepts them in the Obsidian Capture view or
with `kgd accept`, which indexes and embeds what it admits. Re-embedding a
record edited by hand is the owner's call (`kgd index`, the plugin's
Re-embed). The plugin refreshes the keyword index when the owner saves a
record in Obsidian; edits made elsewhere lag until the next index run, and
every read reports that lag.

## Capture while reading

`$capture-kgdistiller` drafts one item the owner selected while reading: a
node, a relation or an example, in a short fixed pass. One lookup turn reads
the type profile with `kgd source FILE` and compares existing senses with one
`kgd resolve`, one `kgd drafts --name` and one `kgd search` of the item and
the concepts it links, in every base. A label names one concept: a record of
the same name and meaning is the same knowledge point, and a new concept of
another meaning with a taken name adds its source's name, `Label (source)`.
A new item becomes a draft that one shell command writes and lists with
`kgd drafts`, whose `problems` must be empty (the capture request is the
consent). A concept the passage only uses or credits to other work is drafted
as foreign knowledge from the web page of the work that originates or
explains it, in the base of the source being read. An item already recorded
follows the conflict rules of [model.md](model.md) "Conflicts, updates and
relocation": it is ignored when its meaning is unchanged, and otherwise drafted
as a replacement whose report says whether the new definition is more
general, corrects the recorded one or conflicts with it. Every value is a link
to a record or a draft; a concept nothing records is left out and reported.
`understanding` is `yes` unless the owner says otherwise. It reports the
classification and the draft in one short answer.

The `capture-writer` preset runs this Skill in the background on a small model
at low reasoning effort (Claude Code: `model: haiku`, `effort: low`; Codex:
`model_reasoning_effort = "low"`), because the model's reasoning volume, not
`kgd`, dominates a capture's time; the owner keeps reading and gets one line
when it is done. Its lookup loads no embedding model: it searches the name and
lexical lanes with the item's names in both languages.

Familiarity does not establish any record's understanding.

## Compile a source

`$compile-kgdistiller` extracts a bounded scope of one registered source, up
to the whole file when the owner asks for full distillation, typically for the
owner's own notes or familiar material; re-admitting a rewritten note is the
same request. It works in batches: one lookup turn looks up the whole
candidate batch in every base and in the drafts, and one write turn writes
every draft. It applies the conflict rules of [model.md](model.md)
"Conflicts, updates and relocation": new knowledge becomes new drafts, and
recorded knowledge whose citation moved or whose meaning changed becomes
replacement drafts, relocated into another base when the passage lies there.
Records whose knowledge the source no longer contains, or whose source file no
longer exists, are reported for the owner to decide. The run finishes with
`kgd drafts --source FILE`, every draft free of problems. It also serves
requests to file or ingest a document into the knowledge base.

## Review and acceptance

The owner reviews the drafts in the Obsidian plugin's Capture view, which
groups them by source, or opens them like any file (with the plugin's hidden
indexing on, `.knowledge/` opens like any folder), edits them if needed and
accepts them there or with `kgd accept DRAFT...`, which admits them with the
drafts they link, indexes and embeds them. A replacement draft is refused when
its record changed after the draft was written. A refusal changes nothing and
names the problem, usually `select [[x]] too`. Deleting a draft rejects it and
leaves no trace.

## Relocate a record

Sources change, and nothing follows them. `$relocate-kgdistiller` takes the
knowledge's name, exact or approximate, and the text of the passage that now
states it. One lookup turn finds the record (asking the owner when several
remain plausible), and `kgd relocate UID -` with the passage on standard input
finds it in the record's base, by its words in order whatever the line breaks
and hidden markup, and rewrites only the record's `source`, `lines` and, when
its quotes are not in the passage, Evidence, then refreshes the keyword index.
A passage found several times needs `--source`; one found only in another
base is a relocation across bases, reported to the owner. A foreign record
relocated this way becomes local. `kgd find` (MCP `kg_find`) is the same
search on its own, read-only: where a passage or a name occurs in the
registered sources.

## Query

`$query-kgdistiller` and the `query-reviewer` preset read through
`kgd search`, `kgd resolve`, `kgd get`, `kgd neighbors`, `kgd browse` and
`kgd pack`, or the MCP tools `kg_search`, `kg_resolve`, `kg_get`,
`kg_neighbors`, `kg_browse` and `kg_pack` of `kgd mcp`, across every
registered base. `neighbors` gives prerequisite and claim closures, and `pack`
a bounded evidence packet whose gaps are reported, never filled by guessing.
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
registers bases (`kgd base add`), writes source globs, document types and the
`embedding` model id into the home, runs `kgd check` (`--fix-lines` after a
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

Local evidence needs a registered source: a UTF-8 text file inside a base,
matched by one of its globs, whose lines the record cites and quotes verbatim.
To capture from a paper, keep its text (an extracted Markdown, a translation
or the LaTeX source) in the base and register a glob for it, or capture into
an authored reading note that is itself a registered source. A concept from a
work the owner does not hold is foreign knowledge: its record cites the web
page (preferably the work's arXiv page, otherwise an encyclopedia article)
and quotes it verbatim; it is localized once the work is in a base.

## Handoffs

Reading handoffs lead with the explanation and the records actually found,
with their uids and `source:lines`; if a lookup was omitted or unavailable,
say so instead of inventing results. Write handoffs name the drafts written,
the records they replace, and their `problems` as `kgd drafts` lists them. Git state and the
Obsidian plugin each have their own status; do not collapse them into a
generic "deployed" result.
