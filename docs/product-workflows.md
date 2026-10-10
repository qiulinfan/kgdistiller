# kgdistiller product workflows

kgdistiller owns the deterministic engine, CLI/read-only MCP server, JSON
Schemas, product Skills, per-runtime agent presets, and the
runtime workflow manifests `workflows/manifest.json` (Codex) and
`workflows/claude-manifest.json` (Claude Code). Both manifests declare the
same Skills and workflows; only linkers and agent-preset formats differ.

A base is a directory registered in `$KGDISTILLER_HOME/config.json` (default
`~/.knowledge/config.json`). It owns the source documents its globs map to
user-registered document types, its reviewed entries
`.knowledge/entries/<id>.md` and its accepted edges `.knowledge/edges.jsonl`.
Sources are any UTF-8 text documents: kgdistiller reads them as numbered lines
and never parses their syntax, so every text format is treated identically.
Knowledge nodes come only from reviewed capture or curation guided by the
source's user-registered document type. Publishing the notes (websites, course
registries, HTML rendering) belongs to the repositories that own them;
kgdistiller has no publishing surface.

The manifests are the portable asset/workflow inventory. Install and validate
the integration for the runtime you are using from a source checkout or
package with:

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
user profile) for Claude Code; resolve `workflow_guide` relative to that
canonical product root. Each linker manages only manifest-declared kgdistiller
assets and namespaced state. It must not replace global `AGENTS.md`,
`config.toml`, `CLAUDE.md`, `settings.json`, unrelated Skills, or unrelated
agent presets. Explicit copy mode is a snapshot and must be refreshed after
product changes; live link modes reflect source changes. Every installed copy
(copy mode, or a hardlink that became detached) is product-owned: `doctor`
reports a copy whose bytes differ from the product source, and the next `link`
replaces it and removes retired copies, discarding any local edits made to
those installed files. Edit the product checkout instead.

## Workflow boundaries

### Fast single-item capture

`$capture-kgdistiller` saves or updates one selected concept while reading. It
uses the selected passage and necessary nearby context, an identity check
against every existing label and alias, and supported transactional ingest. The
deterministic preparation command copies the cited source lines verbatim into
the entry's Evidence and builds the plan and apply requests; the caller supplies
the line range, the source-backed summary and the reviewed identity intent. See [single-item capture](../skills/capture-kgdistiller/references/capture-contract.md).

This is the usual writing path for a new article. Full-source distillation is
separately requested, generally for the user's own notes or familiar material.
Familiarity does not automatically establish any entry's understanding state.

Entries preserve explicit `understanding` and one layer of
`pending_prerequisites`. Full reading, successful retrieval and current curation
do not establish personal mastery. A partial source remains partial and its
unrelated uncurated concepts do not block the selected update. Full-source
distillation remains a separate explicit request using the same knowledge model.

### Source-scoped knowledge sheets

`$compile-knowledge-sheets` / `/compile-knowledge-sheets` creates or refreshes
partial or complete definition and pending link views for papers, mathematical notes, CS notes,
blogs and project documents. Full knowledge content belongs in accepted
`.knowledge/` metadata; source sheets display names, types, locations and links.
New or changed content first forms a reviewed metadata proposal and uses supported
transactional ingest. Unsupported relations, applications or gap state remain
unapplied proposals. Ordinary source reading does not activate this Skill.
Prepared proposals may appear as clearly labeled draft links with Markdown task
checkboxes. The user selects them directly in Obsidian, then explicitly requests
harvest. Accepted rows link to real metadata; a draft link is visibly distinct.

The [shared model](concepts-and-relations.md) defines nodes, relations and
applications. [Paper sheet projections](paper-sheets-upstream.md) describe the
paper use case and current adapter limitations. Both runtime manifests install
the same generic Skill and bundled contracts. Obsidian opens `.knowledge/`
through the plugin's hidden-folder indexing; entries remain ordinary Markdown
with their properties in frontmatter.

### Independent paper workflows

Ordinary paper reading and explanation need no Skill. Harvesting runs in the
current agent:

| Command (Codex / Claude Code) | Result |
|---|---|
| `$harvest-paper` / `/harvest-paper` | Scripted ingestion of reviewed def-sheet candidates checked in Obsidian |

Harvesting requires an explicit request after human selection in the source def
sheet; it does not repeat that selection in chat or a long explanation through
agent handoffs. Knowledge lookup during reading uses
`$query-kgdistiller` at the actual use sites; an item not found is not proof the
user does not know it, and a lookup error is not a negative match. Only the
user's stated understanding allows treating an entry as mastered.

The `harvest-paper` Skill is explicit-command-only in both runtimes. A direct
request to harvest the checked sheet is explicit harvest intent; ordinary
reading or merely checking a box is not. Codex sets
`allow_implicit_invocation: false`; Claude Code sets `disable-model-invocation: true`.
General note curation, query, ingest and deployment keep their existing triggers.


Knowledge candidates found while reading are prepared with `harvest prepare` as
reviewed capture payloads that cite their source lines. They are not accepted
entries; candidate status does not establish personal understanding. The
source's partial or complete def sheet links to clearly labeled review drafts
under `.knowledge/build/reviews/` (excluded from Obsidian hidden-folder indexing
by default; remove `build` from the plugin's exclusion list to open them there).
Each selectable draft has an ordinary Markdown task checkbox and shows the
proposed entry fields before and after, the cited lines and the Evidence quote,
the target and the reviewed identity decision. Full definitions remain in the
linked entry or draft, not copied into every sheet row.

The user reviews or edits those drafts in Obsidian, checks the desired rows and
explicitly asks to harvest. That request authorizes the checked content and its
stated target. Do not ask the user to repeat selection or confirmation in a
conversation or native question panel. Preparing a sheet or checking a box alone
does not initiate a transaction. A checked task means selected for import and
never means `understood`; preserve the separate personal understanding field.

The harvest script parses the selection and prepared payloads, confirms by text
comparison that each draft, target entry and cited source passage is unchanged
since review, applies one transactional ingest and refreshes successful rows to
real entry links. It preserves unchecked rows, unrelated
annotations and partial coverage. The usual path reuses reviewed content and
identity decisions without a full source reread or another model extraction.
The agent prepares reviewed `add`/`update` capture payloads with `harvest prepare`
during candidate preparation; an explicit harvest runs `harvest apply` on the
sheet. See the [checkbox contract](../skills/harvest-paper/references/checkbox-contract.md)
for exact commands and generated task bindings. Ordinary todos are ignored.
Draft text edits require a targeted re-review before the prepared payload is
applied; the script rejects mismatches instead of importing stale content.
The agent handles only actual ambiguity, invalid input, stale content or an
unsupported operation. A metadata commit followed by a failed sheet refresh is
recovered from its receipt before another write is attempted.

Report the real committed receipt, accepted entry links and any remaining
unapplied items. Changed or unsupported content requires a revised review, not
silent rewriting. Harvest requires no frontend, server, background listener or
runtime-specific selection UI.

Invocation controls follow [OpenAI's Skill metadata](https://learn.chatgpt.com/docs/build-skills#optional-metadata)
and [Claude Code's invocation controls](https://code.claude.com/docs/en/skills#control-who-invokes-a-skill).
The built-in Codex quick validator currently rejects Claude's extension key;
validate the shared fields separately and check that extension as a boolean.

The two runtime manifests share the same Skills and workflow inventory.
`agent: null` means no specialized preset is required: execute in the current
agent. Named agents still refer to installed presets. Manifests describe assets,
not automatic triggers or a scheduler.

### Curate registered notes

Use `$curate-kgdistiller-notes` to extract one bounded source set (read through
`kgd scan --file SOURCE --base B`, which returns the source's base, its type and
that type's profile (`node_kinds`, `relation_kinds`, `epistemic`, `guidance`)
with numbered lines),
`$query-kgdistiller` to resolve the full candidate batch read-only
(`agent resolve`, `agent search`, `agent get`), and `$ingest-kgdistiller` to
plan and apply one reviewed transaction of entries and edges, then `check`.
Each candidate is classified `matched`, `ambiguous` or `unmatched`; ambiguity
blocks its own write, while content conflicts or enrichment of matched entries
require a separate source-backed review rather than inference from retrieval
scores.

### Set up, check and restore a base

Use `$deploy-kgdistiller` to register a base with `kgd base add PATH [--name N]`,
write its source globs under `bases.<name>.sources` in
`$KGDISTILLER_HOME/config.json` and its document types as
`$KGDISTILLER_HOME/types/<name>.md`, and run `check` and `agent status` with
`--base B` (or from inside the base root) after setup, a clone, a pull
or a source edit. `check --fix-lines` repairs the line ranges of entries whose
Evidence moved; stale entries need a reviewed re-capture. Git initialization,
commit, remote configuration, and push remain explicit separate actions.

### Refresh the Obsidian graph feed

Use `$deploy-kgdistiller` and open the base root as the editor vault. Registered sources and `.knowledge/entries/*.md` remain the knowledge.
The optional Obsidian plugin's semantic graph view reads only
`.knowledge/build/obsidian/semantic-graph.json`
(`kgdistiller-obsidian-graph-v1`), which `kgdistiller export obsidian` writes
atomically from every entry and every accepted edge. It shows typed semantic
edges and the source → entry definition edges. The feed lives under the hidden
`.knowledge/build/` directory, which no source glob ever matches; never rescan
it or ingest it back.

### Native indexing of a hidden knowledge folder

The Obsidian plugin also offers an optional desktop indexer for the vault-root
`.knowledge` folder (not configurable). **Index hidden knowledge folder** is
off by default. It exposes the folder through Obsidian's normal file and
metadata cache so supported files participate in editing, links, backlinks,
search and the native graph. Folders on its exclusion list, `build` by
default, stay out of that index. It does not move knowledge data, change the
semantic graph path, or create a new source authority. See the
[hidden knowledge folder guide](obsidian-hidden-knowledge.md) for settings,
exclusions, rescan behavior, desktop capability limits and upstream
attribution.

## Handoffs

Paper-reading handoffs lead with the explanation and linked artifacts. Lookup
results carry the entries actually found and their source citations; if lookup
was explicitly omitted or unavailable, report that state instead of fabricated
results. Transaction handoffs name the request id, the plan and the committed
receipt, and the `check` result after commit. Git and the Obsidian graph feed
each have distinct status; do not collapse them into a generic “deployed”
result.
