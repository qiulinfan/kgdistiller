# kgdistiller product workflows

kgdistiller owns the deterministic engine, CLI/read-only MCP server, JSON
Schemas, product Skills, per-runtime agent presets, and the
runtime workflow manifests `workflows/manifest.json` (Codex) and
`workflows/claude-manifest.json` (Claude Code). Both manifests declare the
same Skills and workflows; only linkers and agent-preset formats differ.

A knowledge project owns its registered source documents, its reviewed entries
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
current agent. Related-work research can delegate independent search
directions:

| Command (Codex / Claude Code) | Result |
|---|---|
| `$harvest-paper` / `/harvest-paper` | Scripted ingestion of reviewed def-sheet candidates checked in Obsidian |
| `$paper-related-work` / `/paper-related-work` | Parallel searches for cited predecessors, citing successors and bounded online discussion |

These commands are independent. Related-work search needs no prepared archive,
does not start knowledge lookup and does not import knowledge. Harvesting
requires an explicit request after human selection in the source def sheet; it
does not repeat that selection in chat. Neither command repeats a long
explanation through agent handoffs. Knowledge lookup during reading uses
`$query-kgdistiller` at the actual use sites; an item not found is not proof the
user does not know it, and a lookup error is not a negative match. Only the
user's stated understanding allows treating an entry as mastered.

The paper Skills are explicit-command-only in both runtimes: `harvest-paper`
and `paper-related-work`. A direct request to harvest the checked
sheet is explicit harvest intent; ordinary reading or merely checking a box is
not. Invoke related-work research
with `$paper-related-work` (Codex) or `/paper-related-work` (Claude Code).
Natural-language requests for related papers, predecessors/successors, reviews or
online discussion do not activate this Skill. Codex sets
`allow_implicit_invocation: false`; Claude Code sets `disable-model-invocation: true`.
General note curation, query, ingest and deployment keep their existing triggers.


Related-work has no default wall-clock deadline. Dispatch the requested branches
and let each complete its bounded research and explanation. Do not create countdown
tasks or cancel workers merely because a duration has elapsed. The parent waits
for actual branch returns or concrete failures and delivers one self-contained
synthesis; unfinished synthesis is not evidence of an empty citation search.
Respect user cancellation and report source-access failures accurately.

The `related-work-scout` preset follows the fixed
[resource methods](../skills/paper-related-work/references/resource-methods.md) and
returns flexible ranked lists of up to eight predecessors and eight successors,
plus at most two online-discussion findings. Each paper gets a direct source and
an explanation proportionate to its importance; key successors may need several
sentences. Select by relevance, reading value and
complementary coverage, not citation count alone; eight is a ceiling, not a quota. Resources follow bounded identity correction and one focused successor
scholarly search; access errors stop that provider. No retry loops or broad searches. Claude Code exposes only Read/WebSearch/WebFetch/ToolSearch to
it, blocking delegation, shell parsing and file writes. Codex has a matching
instruction-scoped preset; the parent enforces the no-recursion rule there. Do not describe the Codex counterpart as a proven tool sandbox.
The parent is the orchestrator; the manifest's preset association is the worker,
not a reason to give orchestration/delegation tools to the scout.

Related-work research has three independent branches: the target's own references
with citing passages, articles citing the target, and bounded online discussion.
A broad request uses all three; a narrow request only uses requested directions.
Start workers promptly, with the parent optionally owning one branch; no recursive
delegation or branch dependency. Merge sourced findings and explain important research relationships in conversation.
File output requires an explicit request.
This parallelism belongs to research discovery, not the paper-reading pipeline.

OpenAlex cites is the successor index, with bounded identity correction. One
focused scholarly search complements the citation page even when it is nonempty,
so a high-count application-heavy sample is not the only candidate pool. Read the original abstracts and relevant passages of shortlisted successors as
needed for verification and explanation, without expanding their citation graphs. Rank at most eight
successors total by research relationship: core advances, evaluation/criticism,
applications/transfer, and surveys/background. Same-subfield membership is useful
context, not a strict filter; cross-field theoretical or methodological advances
can also receive priority. Use citation counts only as a secondary tie-breaker,
show only populated groups, and keep uncertain classifications explicit. Semantic Scholar
has been removed. Any citing paper qualifies as a successor, including surveys, comparisons and
background citations. Method use is optional annotation, not an inclusion gate.
Indexed citations do not by themselves establish method use. A source search must
not exclude whole scholarly domains to remove the target's own pages.
Google Scholar or visual graphs are only for explicit requests; similarity edges
are not citation or influence. See the
[citation discovery note](../skills/paper-related-work/references/citation-discovery.md).

Online discussion checks at most two applicable sources: an already known public
review entry, Hacker News, or readable Hugging Face Papers comments. Public reviews
are part of this branch, not a fourth default search. Use the
[quick peer-review recipe](../skills/paper-related-work/references/quick-peer-reviews.md)
when an official entry is known or reviews are explicitly requested. Reddit/Zhihu
are excluded. A paper having only an arXiv source and no discussion is a normal
completed outcome: report no discussion found within these sources and stop.
Do not expand forums or manufacture criticism to make every branch nonempty.
Keep access failures, unresolved identities, empty indexes and useful content distinct.

For paper source reading, prefer HTML, then LaTeX packages, then PDF. Do not
require TeX manifests for HTML/PDF reading. Local source acquisition and original
knowledge extraction are not redistribution: a no-redistribution notice alone
does not justify blocking ordinary local reading. Keep full source copies local,
separate from original knowledge notes and short necessary excerpts.

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
`scan --file`, which returns the document-type profile and numbered lines),
`$query-kgdistiller` to resolve the full candidate batch read-only
(`agent resolve`, `agent search`, `agent get`), and `$ingest-kgdistiller` to
plan and apply one reviewed transaction of entries and edges, then `check`.
Each candidate is classified `matched`, `ambiguous` or `unmatched`; ambiguity
blocks its own write, while content conflicts or enrichment of matched entries
require a separate source-backed review rather than inference from retrieval
scores.

### Set up, check and restore a project

Use `$deploy-kgdistiller` to initialize a project, register sources and
document types, and run `check` and `agent status` after setup, a clone, a pull
or a source edit. `check --fix-lines` repairs the line ranges of entries whose
Evidence moved; stale entries need a reviewed re-capture. Git initialization,
commit, remote configuration, and push remain explicit separate actions.

### Refresh the Obsidian graph feed

Use `$deploy-kgdistiller` and open the knowledge-project root as the editor
vault. Registered sources and `.knowledge/entries/*.md` remain the knowledge.
The optional Obsidian plugin's semantic graph view reads only
`.knowledge/build/obsidian/semantic-graph.json`
(`kgdistiller-obsidian-graph-v1`), which `kgdistiller export obsidian` writes
atomically from every entry and every accepted edge. It shows typed semantic
edges and the source → entry definition edges. Never register the feed in
`sources.json`, rescan it, or ingest it back.

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
