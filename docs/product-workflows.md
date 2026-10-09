# kgdistiller product workflows

kgdistiller owns the deterministic engine, native frontend, CLI/read-only MCP
server, JSON Schemas, product Skills, per-runtime agent presets, and the
runtime workflow manifests `workflows/manifest.json` (Codex) and
`workflows/claude-manifest.json` (Claude Code). Both manifests declare the
same Skills and workflows; only linkers and agent-preset formats differ. A
knowledge project owns its Markdown, Typst, and
LaTeX identity authorities and directly linked source evidence,
`knowledge/entries/` atomic authorities, reviewed registries, `kgdistiller-graph-v1` graph,
optional `kgdistiller-store-v1` snapshot, and explicitly adopted downstream exports.

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
product changes; live link modes reflect source changes.

## Workflow boundaries

### Native LaTeX rendering and exports

For LaTeX knowledge sources, use the native `\kn{Name}` and `\knref{Name}`
markers. `sync` renders mathematical names through the local
`obsidian-latex-live` converter and stores passive HTML/MathML labels. The
default executable is `latex-live-export`; `KGDISTILLER_LATEX_HTML_COMMAND`
selects an explicit converter using a JSON argv array. A missing rich-name
renderer is an actionable setup error, not permission to rewrite identity
authorities into another format.

From a synchronized knowledge project, `kgdistiller export latex-registry
--output knowledge/build/knowledge-registry.tex` generates the native TeX
marker/ID/link definitions. `kgdistiller export latex notes/main.tex --output
knowledge/build/main.html` renders a complete native document directly. Use
`--replace` only for an existing generated result. Names, aliases and authored
reference spellings map to established graph IDs; rendered text never decides
identity. These commands do not ingest knowledge or publish a website.

The notes repository's LaTeX web adapter uses this direct route. Retain
separately requested format migration tools, but do not route LaTeX web export
through Typst or Pandoc. Whole-document export currently supports pdfLaTeX and
XeLaTeX and explicitly rejects LuaLaTeX. Read the packaged
[LaTeX source contract](latex-sources.md) for setup, provenance, protocol,
marker placement and failure behavior.

### Fast single-item capture

`$capture-kgdistiller` saves or updates one selected concept while reading. It
uses the selected passage and necessary nearby context, one identity comparison,
and supported transactional ingest. The deterministic preparation command builds
the required transaction artifacts; the caller supplies source-backed content
and reviewed identity intent. See [single-item capture](../skills/capture-kgdistiller/references/capture-contract.md).

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
`knowledge/` metadata; source sheets display names, types, locations and links.
New or changed content first forms a reviewed metadata proposal and uses supported
transactional ingest. Unsupported relations, applications or gap state remain
unapplied proposals. Ordinary source reading does not activate this Skill.
Prepared proposals may appear as clearly labeled draft links with Markdown task
checkboxes. The user selects them directly in Obsidian, then explicitly requests
harvest. Accepted rows link to real metadata; a draft link is visibly distinct.

The [shared model](concepts-and-relations.md) defines nodes, relations and
applications. [Paper sheet projections](paper-sheets-upstream.md) describe the
paper use case and current adapter limitations. Both runtime manifests install
the same generic Skill and bundled contracts. `knowledge/` remains visible for
Obsidian; native source markers and atomic-entry authority remain intact.

### Independent paper workflows

Ordinary paper reading and explanation need no Skill. The former `read-paper`
orchestrator and its dedicated reader/context presets have been removed.
Distillation and harvesting run in the current agent. Related-work research can
delegate independent search directions:

| Command (Codex / Claude Code) | Result |
|---|---|
| `$distill-paper` / `/distill-paper` | HTML-first reading, short section guide, existing links and knowledge candidates in `paper-notes.md` |
| `$harvest-paper` / `/harvest-paper` | Scripted synchronization of reviewed def-sheet candidates checked in Obsidian |
| `$paper-related-work` / `/paper-related-work` | Parallel searches for cited predecessors, citing successors and bounded online discussion |

These commands are independent. Distillation does not start a full explanation,
translation, candidate graph or research survey. Related-work search needs no
prepared archive and does not start knowledge lookup. Distillation and related-work
search do not import knowledge. Harvesting requires an explicit request after
human selection in the source def sheet; it does not repeat that selection in
chat. None of these commands repeats a long explanation through agent handoffs.

Distillation uses a deterministic HTML fetch/text helper (`read_html.py`) so the
source is not first rewritten by a WebFetch model. The source copy preserves
headings, math alternatives and anchors. `lookup.py` batches the public read-only
resolve/search calls and returns candidate titles and short summaries. The agent
screens relevance, refines weak searches, then uses `--read` for selected IDs and
checks definitions/conditions/provenance. No automatic first-N content retrieval
or semantic-equivalence decision is made by the helper. The note is written once, and
the final reply only links it. The two-minute aim never excuses lost conditions
or treating retrieval errors as missing knowledge.

Full-paper reading is a prerequisite, including proofs, substantive appendices,
active source includes and the bibliography. Trace three source-backed paths:
architecture components/interfaces and their reuse or changes; mathematical steps
and the exact definitions/theorems/conditions they use; citations and the specific
work borrowed or compared. Inspect architecture figures when text is insufficient.
Record a compact component/step → dependency/work → use-site → personal-link map.
Lookup candidates come from this coverage, not a title-derived keyword list.
A known paper does not establish mastery of every internal component. Resolve
needed citation details locally first; only inspect a cited source further to
clarify a real dependency, without recursive literature expansion. Missing source
coverage stays explicit. Plan for up to 30 concrete lookup terms per paper,
including rephrasings and borrowed-paper identities; technical coverage can justify
more. The helper accepts 30 terms or selected IDs per call, and follow-up searches
are allowed. Selected IDs are read separately for each vault.

Only citations that actually supply a reused method, component or mathematical
result get an additional bounded paper-title/arXiv/DOI lookup. Connect a verified
reading record to the specific borrowed part; a missing concept entry does not
prove a paper is unread. Do not query the entire bibliography, comparison-only
references or mere experiment tools. Familiar elementary steps can be recorded
without separate lookup when consistent with the user's stated background;
skipping that lookup is not the same as a verified knowledge-base match.

Distillation checks the established knowledge targets or registered default via
read-only queries. Look up methods and prerequisites at actual use sites, rather
than broad subjects. Verify definitions and conditions for applicability, then
report personal
understanding separately. Only the user's stated understanding allows treating
an entry as mastered; preserve unknown or not-yet-understood state. An item not
found in the queried store is not proof the user does not know it. A lookup error
is not a negative match. Preserve paper/version meaning; shared vocabulary does
not merge identities. Store mutations remain separately authorized transactions.

The existing paper Skills are explicit-command-only in both runtimes: `distill-paper`,
`harvest-paper` and `paper-related-work`. A direct request to harvest the checked
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

`distill-paper` produces paper/version-qualified candidates with definitions or
mechanisms, essential conditions and source locations, in the same short note.
They are not imported graph nodes; candidate status does not establish personal
understanding. Distillation ends with saved candidates, without prompting for
import. The source's partial or complete def sheet can link to clearly labeled
review drafts under `knowledge/build/reviews/`. Each selectable draft has an
ordinary Markdown task checkbox and records the complete proposal, source
evidence, target and reviewed identity decision. Updates show the relevant
before/after content. Full definitions remain in the linked metadata or draft,
not copied into every sheet row.

The user reviews or edits those drafts in Obsidian, checks the desired rows and
explicitly asks to harvest. That request authorizes the checked content and its
stated target. Do not ask the user to repeat selection or confirmation in a
conversation or native question panel. Preparing a sheet or checking a box alone
does not initiate a transaction. A checked task means selected for import and
never means `understood`; preserve the separate personal understanding field.

The harvest script parses the selection and prepared payloads, validates source
and target freshness, applies supported transactional ingest and refreshes
successful rows to real canonical links. It preserves unchecked rows, unrelated
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
not automatic triggers or a scheduler. Existing paper packages and knowledge
graphs are retained; changing these commands does not regenerate them.

### Curate registered notes

Use `$curate-kgdistiller-notes` to extract one bounded authority set,
`$query-kgdistiller` to resolve the full candidate batch through the
generation-checked read-only `GraphView`, and `$ingest-kgdistiller` to plan and
apply one reviewed transaction. Identity ambiguity blocks its own write path.
`kgdistiller-graph-comparison-v1` represents identity only as `matched`,
`ambiguous`, or `unmatched`; ambiguity blocks the write path, while content
conflicts or enrichment of matched identities require a separate source-backed
review rather than inference from comparison output.

### Back up or restore a portable store

Use `$deploy-kgdistiller` to run `check`, `agent status`, `store snapshot`, and
`store verify`. A `kgdistiller-store-v1` clone is file-based and immediately queryable;
there is no profile, provider, database, or materialization step. Git
initialization, commit, remote configuration, and push remain explicit separate
actions.

### Publish a static bundle

Use `$deploy-kgdistiller` after source/store checks. `export site` requires the
clean tracked instance inputs and exact producer/source provenance. Run the
bundled `verify_export.py`; a consumer adopts those verified bytes and receipt,
not the kgdistiller checkout.

### Export Obsidian

Use `$deploy-kgdistiller` and open the knowledge-project root as the editor
vault. Registered Markdown files and `knowledge/entries/*.md` remain non-lossy
authorities.
Create the managed `kgdistiller-obsidian-projection-v1` subtree as a lossy downstream
view; never register that subtree in `sources.json`, rescan it, feed it to
candidate/ingest, or treat projected-note edits as round-trip authority. An
external output is a browsing-only vault/projection. Rebuild either projection
with `--replace` from the authority graph.
The optional Obsidian plugin's semantic graph view consumes only the generated
`kgdistiller-obsidian-graph-v1` `semantic-graph.json`; it preserves typed
semantic edges and source definition/reference edges without changing this
authority boundary.

### Native indexing of a hidden knowledge folder

The Obsidian plugin also offers an optional desktop indexer for one configured
hidden folder, `.knowledge` by default. **Index hidden knowledge folder** is
off by default. It exposes the folder through Obsidian's normal file and
metadata cache so supported files participate in editing, links, backlinks,
search and the native graph. It does not move the current `knowledge/` store,
change the semantic graph path, or create a new source authority. See the
[hidden knowledge folder guide](obsidian-hidden-knowledge.md) for settings,
rescan behavior, desktop capability limits and upstream attribution.

### Serve the native frontend

`kgdistiller serve` uses self-contained packaged assets and binds to
`127.0.0.1` by default. Network exposure is outside the normal local workflow
and requires a separate explicit security decision.

## Handoffs

Paper-reading handoffs lead with the explanation and linked artifacts. The graph
branch carries concrete use records and graph, snapshot and alignment digests
from its default read-only lookup. If lookup was explicitly omitted or unavailable,
report that state instead of fabricated lookup results. Transaction
handoffs add canonical request/plan/receipt digests. Store, Git, site export,
Obsidian export, and network publication each have distinct status and
authority; do not collapse them into a generic “deployed” result.
