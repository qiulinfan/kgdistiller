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

### Read and explain a paper

`$read-paper` is the main entry for a normal paper-reading request with a graph.
The parent prepares and validates the versioned TeX source, then launches three
independent workers on that same source:

| Worker | Work | Owned outputs |
|---|---|---|
| Translator | Full transcription and aligned translation | `paper.md`, `paper_ch.md` |
| Independent reader | Ordinary source-led explanation without Skills | `reading.md` |
| Graph extractor | Paper methods, precise dependency uses, default personal-graph lookup and links | `knowledge/` |

Source readiness is the only shared prerequisite. Reader and graph extraction
do not wait for translation. The parent presents the reader's substantive answer
first, then the graph and entries, with bilingual sources as supporting links.
It checks accuracy without reducing the reading to a graph summary. Experiments,
results and limitations remain part of an ordinary explanation; only graph nodes
are subject to the method/mechanism admission rule. A slow or failed support
branch does not suppress a valid reading, and its completion status is explicit.

The reader uses a fresh context and the `paper-reader` preset or a general-purpose
worker. It does not load Skills, this guide, or a personal knowledge inventory.
The graph worker queries the personal knowledge base by default. By the user's
convention an accurately matched applicable entry is mastered: link it with its
paper use instead of re-explaining it. Only specific unmatched or non-equivalent
parts need explanation. Do not extrapolate mastery of a broad subject. This
lookup never gates the independent reader; honor an explicit user opt-out.
Full historical dossiers via `$trace-concept-lineage` are also a separate request.

This workflow applies to both Codex and Claude Code through their isolated
subagent facilities. The manifest is an asset inventory, not a parallel scheduler:
its `read-paper` association identifies the parent Skill and the independent
reader preset. The parent loads the Skill; the reader does not. Scheduling and
disjoint ownership are defined by the Skill, not by the manifest step order.

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

Use `$extract-paper-markdown` for a lightweight versioned arXiv LaTeX package
(`source/`, `source.json`, one-URL `link.txt`, complete `paper.md` and term-preserving `paper_ch.md`) and
`$distill-paper-knowledge` for an isolated graph of concrete methods and
mechanisms. Experimental data, evaluation settings, performance explanations,
concept comparisons and review judgments stay in paper notes, not knowledge
nodes. Defining conditions belong with the mechanism. It can start from the
source-only validated package while translation is pending, and produces one
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

The paper pipeline neither acquires nor renders PDFs and creates no `evidence/`
tree. Cite source filenames, lines and LaTeX labels; explain any visual facts that
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
