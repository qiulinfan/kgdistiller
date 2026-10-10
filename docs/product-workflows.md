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
  `resolve`, `get` and the MCP tools;
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
| `capture-knowledge` | `capture-kgdistiller` | write | `kgd sheet FILE --json`; `kgd resolve`/`kgd search` to compare senses; a new item as a draft plus `kgd accept`, or an existing record edited in place plus `kgd check --base B`; pending terms one level deep; understanding only from the owner's statement; `kgd index`; report uids and the receipt. |
| `compile-knowledge-sheets` | `compile-knowledge-sheets` | author | Set the bounded scope; `kgd sheet FILE --json`; `resolve`/`search` for identity; drafts for new records; `kgd check --base B` with every draft passing; `kgd sheet FILE`; stop and report proposed changes to accepted records. |
| `harvest-kgdistiller` | `harvest-kgdistiller` | write | `kgd harvest SHEET [--dry-run]`; `kgd index`; report refused rows. |
| `query-knowledge` | `query-kgdistiller` with `query-reviewer` | read-only | `search`, `resolve`, `get [--source-lines N]` or the MCP tools; deliver `source:lines` and quotes; on lag run `kgd index` and repeat. |
| `deploy-kgdistiller` | `deploy-kgdistiller` | write | `kgd base add`; sources and types in the home; `kgd check`; `kgd index`; `kgd obsidian install` and hidden indexing; `kgd claude link`/`kgd codex link` and both doctors. |

Step modes mean:

- `read-only`: reads knowledge and changes nothing but the derived database
  (`kgd index` when a result reports lag);
- `author`: writes drafts and generated sheets, never accepted records;
- `write`: changes accepted knowledge or the home through `kgd` commands or
  in-place record edits.

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
`kgd search` (the same name is not the same concept), and then either writes a
draft and runs `kgd accept` on it (the capture request is the consent) or edits
the existing record in place with a stale-read-safe editing tool followed by
`kgd check --base B`. Unexplained terms become plain pending values, one level
deep. `understanding` is set only from the owner's explicit statement. It ends
with `kgd index` and reports the uids, the receipt and `understanding_set`.

This is the usual writing path for a new article. Familiarity does not
establish any record's understanding.

## Compile a source

`$compile-knowledge-sheets` extracts a bounded scope of one registered source,
up to the whole file when the owner asks for full distillation, typically for
the owner's own notes or familiar material. It writes drafts only, checks that
they pass `kgd check`, generates the sheet with `kgd sheet FILE` and stops.
Changes it would make to accepted records (edits, deletions, node-to-relation
conversions) are listed in its report and applied in place only when the owner
asks, followed by `kgd check` and `kgd index`. It also serves requests to file
or ingest a document into the knowledge base.

## Review and harvest

The owner reviews the drafts in Obsidian (with the plugin's hidden indexing on,
`.knowledge/` opens like any folder), edits them if needed and ticks the rows
to accept in the source's sheet. A checkbox means "selected for acceptance",
never "understood". `$harvest-kgdistiller` then runs `kgd harvest SHEET`,
which accepts exactly the ticked drafts in one call, regenerates the sheet and
prints the receipt, followed by `kgd index`. A refusal changes nothing and
lists the rows to fix, usually `select [[x]] too`. Harvest never re-extracts.
Deleting a draft rejects it. Model invocation is enabled for this Skill in
both runtimes.

## Query

`$query-kgdistiller` and the `query-reviewer` preset read through
`kgd search`, `kgd resolve` and `kgd get`, or the MCP tools `kg_search`,
`kg_resolve` and `kg_get` of `kgd mcp`, across every registered base. Answers
cite `source:lines` with evidence quotes. When a result reports
`lag.changed_files > 0`, the reader runs `kgd index` and repeats the query;
that derived refresh is its only write. Lookup during reading happens at the
actual use sites; a record not found is not proof the owner lacks the concept,
and only the owner's stated understanding allows treating a record as
mastered. Identity classification for authoring returns `matched`,
`ambiguous` or `unmatched` per candidate.

## Set up, check and restore

`$deploy-kgdistiller` registers bases (`kgd base add`), writes source globs and
document types into the home, runs `kgd check` (`--fix-lines` after a source
edit moves cited lines), builds the index (`kgd index`; delete `index.sqlite*`
and rerun it to restore), installs the Obsidian plugin and links the agent
runtimes. Git initialization, commits, remotes and pushes remain separate
explicit actions.

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
