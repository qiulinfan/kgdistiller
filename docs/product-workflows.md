# kgdistiller product workflows

kgdistiller owns the deterministic engine, the CLI and read-only MCP server,
the product Skills, per-runtime agent presets, and the runtime workflow
manifests `workflows/manifest.json` (Codex) and `workflows/claude-manifest.json`
(Claude Code). Both manifests declare the same Skills and workflows; only
linkers and agent-preset formats differ.

A base is a directory registered in `$KGDISTILLER_HOME/config.json` (default
`~/.knowledge/config.json`). Its knowledge is records in
`.knowledge/entries/<id>.md`: nodes, and relations that bind records to the
roles their user-registered document type declares. Proposed records wait in
`.knowledge/drafts/`, and `.knowledge/sheets/` holds the generated def/pending
view of each source. Sources are any UTF-8 text documents read as numbered
lines; kgdistiller never parses their syntax. Retrieval reads the derived
database `$KGDISTILLER_HOME/index.sqlite`, which `kgd index` keeps up to date.
Publishing the notes belongs to the repositories that own them.

Reference documents installed with this guide:

- [model.md](model.md): records, grammar, drafts, sheets, `check`, `accept`,
  `harvest`, `sheet` and the lock;
- [retrieval.md](retrieval.md): the database, `kgd index`, lag, `search`,
  `resolve`, `get`, `neighbors`, `browse`, `navigate`, `pack` and the MCP tools;
- [obsidian.md](obsidian.md): editing records in Obsidian and the plugin's
  live graph;
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
| `capture-knowledge` | `capture-kgdistiller` | write | Read profile; look up the item and its terms in every base, pending included; accept a new scientific draft, or ignore, overwrite or relocate a recorded one by the conflict rules and ask the owner otherwise; pending terms and stated understanding; link the pending uses of created concepts in every base; automatically invoke `organize-kgdistiller` for changed accepted UIDs; index and report admission/navigation separately. |
| `compile-knowledge-sheets` | `compile-knowledge-sheets` | write | Set the bounded scope; `kgd sheet FILE --json`; look up every knowledge point and term in every base, pending included; drafts for new records; ignore, overwrite, relocate or turn pending the recorded knowledge by the conflict rules and ask the owner otherwise; `kgd check --base B` with every draft passing; `kgd sheet FILE`; organize changed accepted UIDs; index and report. |
| `harvest-kgdistiller` | `harvest-kgdistiller` | write | Look up the ticked drafts and their pending terms again; `kgd harvest SHEET [--dry-run]`; link the pending uses of created concepts in every base; after admission automatically organize created accepted UIDs; index; report refused rows and navigation separately. |
| `query-knowledge` | `query-kgdistiller` with `query-reviewer` | read-only | CLI `navigate` entrances/summaries and declared-role descent; global lexical/dense/name `search`, `resolve`, scientific `neighbors`, `get`, `browse`, `pack`; bounded evidence or ordered scientific UIDs; refresh on lag. Existing MCP reads remain optional. |
| `organize-knowledge` | `organize-kgdistiller` | write | Register an agent-authored guide; read current topics and changed scientific records; choose useful local/global split, merge, reparent or retirement; helper checks DAG/coverage and stale reads; new navigation drafts plus accept, guarded navigation edits; check and index. |
| `deploy-kgdistiller` | `deploy-kgdistiller` | write | Install with the `retrieval` extra; `kgd base add`; sources, types and `embedding` in the home; `kgd check`; `kgd index`; `kgd obsidian install` (enables the plugin and hidden indexing; report any newLinkFormat warning); `kgd claude link`/`kgd codex link` and both doctors. |

Step modes mean:

- `read-only`: reads knowledge and changes nothing but the derived database
  (`kgd index` when a result reports lag);
- `write`: changes knowledge or the home through `kgd` commands, drafts,
  sheets or in-place record edits.

`agent: null` means the step runs in the current agent; a named agent refers
to an installed preset. Manifests describe assets, not automatic triggers or a
scheduler.

**Every Skill that writes knowledge finishes with `kgd index`**, so database
lag stays rare. Edits the owner makes in Obsidian lag until the next
`kgd index`; every read reports that lag.

## Capture while reading

`$capture-kgdistiller` saves one item the owner selected while reading: a
node, a relation or an example. It reads the type profile with
`kgd sheet FILE --json`, compares existing senses with `kgd resolve` and
`kgd search` in every base, pending uses included (the same name is not the
same concept). A new item becomes a draft that it accepts with `kgd accept`
(the capture request is the consent). An item already recorded follows the
conflict rules of [model.md](model.md) "Conflicts, updates and relocation":
it is ignored when its meaning is unchanged, overwritten and relocated when the
new definition is more general or corrects the recorded one, and otherwise
left to the owner's decision. In-place edits use a stale-read-safe editing
tool followed by `kgd check --base B`. Unexplained terms become plain pending
values, one level deep, unless a record anywhere defines them. `understanding` is set only from the owner's explicit statement. After
acceptance it links the pending uses, in every base, that mean a created
concept. It ends by automatically invoking `$organize-kgdistiller` for changed
accepted UIDs,
then `kgd index`, and reports the uids, receipt and `understanding_set` plus
the separate navigation result.

This is the usual writing path for a new article. Familiarity does not
establish any record's understanding.

## Compile a source

`$compile-knowledge-sheets` extracts a bounded scope of one registered source,
up to the whole file when the owner asks for full distillation, typically for
the owner's own notes or familiar material; re-admitting a rewritten note is
the same request. It looks up every knowledge point in every base, pending
uses included, and applies the conflict rules of [model.md](model.md)
"Conflicts, updates and relocation". New knowledge becomes drafts that pass
`kgd check`, listed in the sheet that `kgd sheet FILE` generates. Knowledge
already recorded is ignored, re-cited, overwritten and relocated in place.
Records whose knowledge the source no longer contains, or whose source file
no longer exists, are deleted to pending. Other changed meanings wait for the
owner's decision. These in-place edits follow "Updates, deletes and identity
changes of accepted records"; the run then organizes the changed accepted UIDs
and finishes with `kgd index`. It also serves requests to file or ingest a
document into the knowledge base.

## Review and harvest

The owner reviews the drafts in Obsidian (with the plugin's hidden indexing on,
`.knowledge/` opens like any folder), edits them if needed and ticks the rows
to accept in the source's sheet. A checkbox means "selected for acceptance",
never "understood". `$harvest-kgdistiller` first looks the ticked drafts and
their pending terms up again, since knowledge admitted after the compile run
may now record them, then runs `kgd harvest SHEET`,
which accepts exactly the ticked drafts in one call, regenerates the sheet and
prints the receipt. The Skill then links the pending uses, in every base,
that mean a created concept, runs `$organize-kgdistiller` for the created
scientific UIDs and finishes with `kgd index`. A refusal changes nothing and
lists the rows to fix, usually `select [[x]] too`. Harvest never re-extracts.
Deleting a draft rejects it and leaves no trace. Model invocation is enabled
for this Skill in both runtimes.

## Query

`$query-kgdistiller` and the `query-reviewer` preset read through
the public CLI `kgd navigate`, `kgd search`, `kgd resolve`, `kgd get`, `kgd neighbors`, `kgd browse` and
`kgd pack`, or the MCP tools `kg_search`, `kg_resolve`, `kg_get`,
`kg_neighbors`, `kg_browse` and `kg_pack` of `kgd mcp`, across every
registered base. The CLI is the default ingestion/retrieval experiment path.
For broad questions `navigate` discovers topic entrances, reads summaries
and descends declared organization roles, with global search to supplement
routes. Multiple paths retain one base-qualified scientific UID. The topic
directory is generated during ingestion; queries never rebuild it. `neighbors`
gives scientific prerequisite and claim closures, and `pack`
a bounded evidence packet whose gaps are reported, never filled by guessing.
`search` fuses a lexical, a dense and a name lane; when the retrieval extra is
missing it fails unless the dense lane is skipped with `--no-dense` (MCP:
`no_dense`). Answers cite `source:lines` with evidence quotes. When a result
reports `lag.changed_files > 0`, `lag.unembedded > 0` or
`lag.embedding_changed`, the reader runs `kgd index` and repeats the query;
that derived refresh is its only write. Lookup during reading happens at the
actual use sites; a record not found is not proof the owner lacks the concept,
and only the owner's stated understanding allows treating a record as
mastered. Identity classification for authoring returns `matched`,
`ambiguous` or `unmatched` per candidate.

## Automatic organization

`$organize-kgdistiller` generates theme names, depth, branching, readable
summaries and shared-UID membership from the current accepted corpus and its
scientific links. The owner writes finite extraction templates, not a growing
directory. This phase normally follows scientific capture/harvest or an
authorized accepted-record edit; it can also reorganize existing knowledge
on request. Draft scientific records remain outside navigation until admitted.

The optional finite navigation profile declares only kind/role conventions.
The Skill initializes a registered agent-authored guide inside a base,
outside `.knowledge/`, and ordinary topic/organization records quote this
guide. Freely generated classification is explicitly a compiler judgment;
research evidence remains with the scientific records. There is no new home
field, catalog, tree store, provider dependency or model call in `kgd index`.

The agent proposes local adjustments first and broader reorganization when
corpus growth warrants it. It chooses semantically useful splits, merges,
reparents and shared entrances without a fixed depth/branch count. The helper
rejects cycles, invalid links, lost leaf coverage, science edits, stale
contexts and changes to owner-corrected navigation. Routine organization needs
no further owner review. Failures do not undo admitted scientific knowledge;
report completed phases, preserve recoverable drafts and finish with index.

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

Evidence needs a registered source: a local UTF-8 text file inside a base,
matched by one of its globs, whose lines the record cites and quotes verbatim.
To capture from a paper, keep its text (an extracted Markdown or the LaTeX
source) in the base and register a glob for it, or capture into an authored
reading note that is itself a registered source. A PDF or a web page cannot be
cited directly.

## Handoffs

Reading handoffs lead with the explanation and the records actually found,
with their uids and `source:lines`; if a lookup was omitted or unavailable,
say so instead of inventing results. Write handoffs name the uids, the accept
receipt or the `check` result, and the `index` report. Git state and the
Obsidian plugin each have their own status; do not collapse them into a
generic "deployed" result.
