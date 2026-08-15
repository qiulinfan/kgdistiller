# kgdistiller product workflows

kgdistiller owns the deterministic engine, native frontend, CLI/read-only MCP
server, JSON Schemas, product Skills, Codex agent presets, and
`workflows/manifest.json`. A knowledge project owns its Markdown, Typst, and
LaTeX authorities, reviewed registries, `qlkg-v3` graph, optional
`qlkg-store-v2` snapshot, and explicitly adopted downstream exports.

The manifest is the portable asset/workflow inventory. Install and validate it
from a source checkout or package with:

```sh
kgdistiller codex link
kgdistiller codex doctor
```

The portable entry is
`$CODEX_HOME/workflow-products/kgdistiller/workflows/manifest.json`; resolve
`workflow_guide` relative to that canonical product root. The linker manages
only manifest-declared kgdistiller assets and namespaced state. It must not
replace global `AGENTS.md`, `config.toml`, unrelated Skills, or unrelated agent
presets. Explicit copy mode is a snapshot and must be refreshed after product
changes; live link modes reflect source changes.

## Workflow boundaries

### Curate registered notes

Use `$curate-kgdistiller-notes` to extract one bounded authority set,
`$query-kgdistiller` to resolve the full candidate batch through the
generation-checked read-only `GraphView`, and `$ingest-kgdistiller` to plan and
apply one reviewed transaction. Identity ambiguity blocks its own write path.
Query comparison v2 represents identity only as `matched`,
`ambiguous`, or `unmatched`; ambiguity blocks the write path, while content
conflicts or enrichment of matched identities require a separate source-backed
review rather than inference from comparison output.

### Federate and selectively import a paper

Use `$extract-paper-markdown` for a complete traceable package and
`$distill-paper-knowledge` for an isolated candidate graph. Align it through
`$query-kgdistiller` without importing. Only when the user selects exact
candidates and a registered native research authority should
`$import-paper-knowledge` produce a handoff for revalidation and
`$ingest-kgdistiller`.

Reading, summarizing, distilling, aligning, or tracing a paper never authorizes
personal-graph mutation.

### Back up or restore a portable store

Use `$deploy-kgdistiller` to run `check`, `agent status`, `store snapshot`, and
`store verify`. A `qlkg-store-v2` clone is JSON-only and immediately queryable;
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
vault. Registered Markdown files there remain non-lossy native authorities.
Create the managed `qlkg-obsidian-projection-v1` subtree as a lossy downstream
view; never register that subtree in `sources.json`, rescan it, feed it to
candidate/ingest, or treat projected-note edits as round-trip authority. An
external output is a browsing-only vault/projection. Rebuild either projection
with `--replace` from the native authority graph.

### Serve the native frontend

`kgdistiller serve` uses self-contained packaged assets and binds to
`127.0.0.1` by default. Network exposure is outside the normal local workflow
and requires a separate explicit security decision.

## Handoffs

Read-only handoffs carry graph, snapshot, and alignment digests. Transaction
handoffs add canonical request/plan/receipt digests. Store, Git, site export,
Obsidian export, and network publication each have distinct status and
authority; do not collapse them into a generic “deployed” result.
