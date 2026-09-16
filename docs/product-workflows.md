# kgdistiller product workflows

kgdistiller owns the deterministic engine, native frontend, CLI/read-only MCP
server, JSON Schemas, product Skills, per-runtime agent presets, and the
runtime workflow manifests `workflows/manifest.json` (Codex) and
`workflows/claude-manifest.json` (Claude Code). Both manifests declare the
same Skills and workflows; only linkers and agent-preset formats differ. A
knowledge project owns its Markdown, Typst, and
LaTeX identity authorities, `knowledge/derived/` evidence,
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

### Independent paper workflows

Ordinary paper reading and explanation need no Skill. The former `read-paper`
orchestrator and its dedicated reader/context presets have been removed.
Distillation and harvesting run in the current agent. Related-work research can
delegate independent search directions:

| Command (Codex / Claude Code) | Result |
|---|---|
| `$distill-paper` / `/distill-paper` | HTML-first reading, short section guide, existing links and knowledge candidates in `paper-notes.md` |
| `$harvest-paper` / `/harvest-paper` | A static review note and native conversation choices, then import of confirmed candidates |
| `$paper-related-work` / `/paper-related-work` | Parallel searches for cited predecessors, citing successors and bounded online discussion |

These commands are independent. Distillation does not start a full explanation,
translation, candidate graph or research survey. Related-work search needs no
prepared archive and does not start knowledge lookup. Distillation and related-work
search do not import knowledge. Harvesting is a separate explicit command with human selection in the
current conversation; none of these commands repeats a long explanation through agent handoffs.

Distillation uses a deterministic HTML fetch/text helper (`read_html.py`) so the
source is not first rewritten by a WebFetch model. The source copy preserves
headings, math alternatives and anchors. `lookup.py` batches the public read-only
resolve/search/content calls and returns definitions/provenance for applicability
review; it does not decide semantic equivalence. The note is written once, and
the final reply only links it. The two-minute aim never excuses lost conditions
or treating retrieval errors as missing knowledge.

Distillation checks the established knowledge targets or registered default via
read-only queries. Look up methods and prerequisites at actual use sites, rather
than broad subjects. Verify definitions and conditions before treating a match
as mastered; link that entry and its paper use without reteaching it. An item not
found in the queried store is not proof the user does not know it. A lookup error
is not a negative match. Preserve paper/version meaning; shared vocabulary does
not merge identities. Store mutations remain separately authorized transactions.

All paper Skills are explicit-command-only in both runtimes: `distill-paper`,
`harvest-paper`, `paper-related-work`, `distill-paper-knowledge`,
`trace-concept-lineage` and `import-paper-knowledge`. Invoke related-work research
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
They are not yet mastered knowledge or imported graph nodes. Distillation ends
with saved candidates, without opening a review or prompting for import. Only
`$harvest-paper` / `/harvest-paper` starts that later phase: inspect bounded existing
matches, write `harvest-review.md`, and show the proposals in the current conversation.
Use a native question/choice tool when available; otherwise ask in ordinary chat.
The note holds detailed candidate text and before/after updates. Users can edit it
or request edits conversationally, then confirm the specific content to import.
Opening a file, preselected options, an edit timestamp or unanswered question is
not confirmation. Preserve the actual user decision and exact reviewed revision.

No custom web form, HTTP server, background listener or browser-specific receipt
is part of harvesting. A static note survives a stopped agent turn. Headless runs
without a human input channel should save the review and stop before import;
`claude -p` does not by itself validate interactive harness UI behavior. The
runtime's question panel is an optional interaction surface, not a custom node
editor supplied by the Skill. Existing confirmed decisions need no second approval.

After source/freshness checks, use transactional ingest and report its real receipt
in conversation. Keep edited node text and structured entry content consistent.
Changed or unsupported content requires a revised review, not silent rewriting.

Native input capabilities: [Codex app-server](https://learn.chatgpt.com/docs/app-server#api-overview)
and [Claude Code AskUserQuestion](https://code.claude.com/docs/en/tools-reference#askuserquestion-tool-behavior).

The legacy `import-paper-knowledge` command is
for existing validated graph packages; it is not a prerequisite for drafting
lightweight HTML-based candidates. No automatic write or candidate selection.

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

### Federate and selectively import a paper

These are advanced explicit commands, separate from ordinary reading and the explicit
reading and harvesting helpers. Invoke `$distill-paper-knowledge` on an existing versioned LaTeX
package (`source/`, `source.json`, one-URL `link.txt`) to build an isolated graph
of concrete methods and mechanisms. If source is missing, report the gap rather
than automatically invoking preparation. Experimental data, evaluation settings, performance explanations,
concept comparisons and review judgments stay in paper notes, not knowledge
nodes. Defining conditions belong with the mechanism. It can start from the
source-validated package without generated full texts, and produces one
verified existing-entry link or a readable gap entry per node. Trace actual
operations and derivation steps to the exact definitions, operators, inequalities
or theorem versions they use, with conditions and source locations. Query and
link those through `$query-kgdistiller` by default without importing. Resolve the
established target or registered default; unavailable lookup leaves linking
explicitly incomplete without blocking the reading. Only when the user selects exact
candidates and a registered native research authority should
`$import-paper-knowledge` produce a handoff for revalidation and
`$ingest-kgdistiller`.

Paper identity is part of knowledge identity: use a source-digest namespace,
source-digest-prefixed local IDs, paper/version-qualified labels, and full paper provenance.
Bare terms and local aliases are retrieval hints. Same-name mechanisms in
different papers or versions remain distinct; cross-paper equivalence requires
comparison of definitions, operations, formulas and conditions. Reviewed bridges
preserve scoped entries. Import keeps qualified native markers and paper-local
definitions instead of collapsing them into generic entries. See the
[paper graph contract](../skills/distill-paper-knowledge/references/research-paper-contract.md).

`$trace-concept-lineage` applies the same concrete-dependency rules to its learning
graph. Generic math/subject prerequisites and assumed mastery of whole disciplines
are not allowed. A specific mathematical prerequisite can be a node when a real
step uses it; an empirical outcome remains paper data. Evidence and review sections can be read without
becoming graph nodes. Do not automatically rewrite existing graphs when changing
these Skills; legacy artifacts need a separate scoped regeneration request.

The legacy graph-package workflows use their LaTeX contracts and create no
`evidence/` tree. The thin commands follow the HTML-first source order above. Cite source filenames, lines and LaTeX labels; explain any visual facts that
cannot be verified from captions and source text.

Reading, summarizing, distilling, aligning, or tracing a paper never authorizes
personal-graph mutation.

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
The optional Obsidian plugin consumes only the generated
`kgdistiller-obsidian-graph-v1` `semantic-graph.json`; it preserves typed
semantic edges and source definition/reference edges without changing this
authority boundary.

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
