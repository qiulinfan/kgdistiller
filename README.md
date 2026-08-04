# kgdistiller

`kgdistiller` turns Markdown, Typst, and LaTeX authority sources into a
deterministic, source-backed knowledge graph. Authors may mark concepts
themselves, or let a coding agent propose knowledge nodes, references, entries,
and semantic relations under the same validation contract.

The project is local-first. Source files and graph data stay in your repository,
and the bundled browser listens on loopback by default. Publishing a website is
optional.

## Source markers

Each global knowledge name has at most one authoritative definition. References
can occur anywhere.

| Format | Definition | Reference |
| --- | --- | --- |
| Typst | `#kn[Measure space]` | `#ref[Measure space]` |
| Markdown | `--[[Measure space]]--` | `[[Measure space]]` |
| LaTeX | `\kn{Measure space}` | `\knref{Measure space}` |

Aliases can be used for Markdown display text: `[[Measure space|spaces]]`.
Ordinary headings, examples, equations, and unmarked statements do not become
nodes automatically.

## What it produces

- stable global node identities;
- field and topic classification;
- source provenance and backlinks;
- direct semantic relations such as `prerequisite-for`, `implies`,
  `generalizes`, `contrasts-with`, and `derived-from`;
- deterministic JSONL graph artifacts;
- a local SQLite full-text index;
- a generated Typst reference registry;
- a dependency-free local graph browser.

The committed graph is an index of the authority sources, not a replacement for
them. Agent-authored entries add searchable context while provenance continues
to point to the source.

## Install

For development:

```sh
git clone https://github.com/qiulinfan/kgdistiller.git
cd kgdistiller
uv sync
uv run kgdistiller --help
```

As a tool:

```sh
uv tool install git+https://github.com/qiulinfan/kgdistiller.git
```

Typst is required when a graph contains Typst-authored node labels. Markdown and
LaTeX scanning use the Python standard library.

## Start a local project

```sh
cd your-notes-repository
kgdistiller init --source-root notes
```

This creates `knowledge/sources.json`, an empty
`knowledge/alignments.json` review registry, a bounded source registry, and the
first graph snapshot. Adjust fields, sources, file globs, topics, and canonical
web locations in the source registry.

Then run:

```sh
kgdistiller sync
kgdistiller check
kgdistiller search "measure space"
kgdistiller snapshot --output knowledge/build/agent-snapshot.json
kgdistiller serve
```

Synchronization remembers the Git revision and source hashes behind the last
snapshot. Deleted paths and both sides of a Git rename are included in the next
scope, so an unchanged `kn` is rehomed instead of duplicated and obsolete refs
are retired. An exact-content rename is also recognized before it is staged.

Each definition occurrence carries a hash of its enclosing authored statement.
Changing only a ref outside that statement rebuilds backlinks without affecting
node curation. Changing the definition keeps the stable node and its Agent data,
but marks its entry and affected semantic edges `needs-review` until a reviewed
delta refreshes them.

An authored-name change is intentionally never inferred from line position or a
Git hunk. Record that identity decision explicitly before synchronizing:

```sh
kgdistiller reconcile rename-node sigma-algebra "sigma field"
kgdistiller sync --file notes/chapter.typ
```

The command writes `knowledge/identities.json` using the
`qlkg-identities-v1` schema. The old machine ID remains stable, the old authored
name becomes an alias, and the definition change still requires curation review.

`serve` opens <http://127.0.0.1:8765/>. It exposes the graph and bounded source
excerpts only to the local machine unless another host is explicitly selected.

## Agent snapshot

Agent indexes and retrieval adapters consume a deterministic, self-contained
snapshot instead of rereading the complete authority repository:

```sh
kgdistiller snapshot > /tmp/personal-knowledge.json
kgdistiller snapshot \
  --namespace paper:example \
  --output knowledge/build/paper-example.snapshot.json
```

The `qlkg-agent-snapshot-v1` envelope contains hydrated node entries, typed
edges with evidence, reference occurrences, provenance, diagnostics, authority
graph identity, and its own canonical digest. It is derived data: deleting it
does not affect source documents or the `qlkg-v2` graph.

Every `sync` also atomically rebuilds the provider-neutral Agent index. Inspect
and query it without loading the graph into an Agent context:

```sh
kgdistiller agent status
kgdistiller agent resolve "Measure space" "sigma algebra"
kgdistiller agent search "countably additive measure" --type knowledge \
  --graph-strategy hybrid --limit 10
kgdistiller agent get measure-space
kgdistiller agent expand measure-space --direction incoming --depth 2
kgdistiller agent ppr measure-space --limit 20
kgdistiller agent context "How does a measure depend on a sigma algebra?" \
  --budget 6000 --depth 2
kgdistiller agent align knowledge/build/paper.snapshot.json \
  --output knowledge/reviews/paper.alignment.json
kgdistiller agent compare knowledge/build/paper.snapshot.json
kgdistiller agent propose knowledge/build/paper.snapshot.json \
  --target-authority notes/research/paper.md \
  --output knowledge/reviews/paper.proposal.json \
  --delta-output knowledge/reviews/paper.delta.json
```

The `qlkg-agent-index-v2` index stores nodes, normalized IDs/labels/global
aliases, explicit scoped abbreviations, reviewed cross-namespace mappings,
structured entry text, typed edges with evidence, reference occurrences, and
optional disposable embeddings/similarity edges. Exact and alias resolution
refuses ambiguous identities; FTS input is tokenized and quoted before reaching
SQLite.

Agent search fuses exact/scoped resolution, FTS, bounded typed traversal, and
weighted Personalized PageRank with deterministic reciprocal-rank fusion.
`--graph-strategy` selects `bfs`, `ppr`, or the `hybrid` default. Every result
explains which retrieval lane selected it. `agent context` returns a
`qlkg-context-bundle-v1` evidence package whose nodes, edge evidence, backlinks,
sources, omissions, and retrieval paths fit the requested conservative token
budget; it does not ask an LLM to generate an answer.

### Connect an Agent over MCP

Start the read-only stdio server with the repository and generated index paths:

```sh
kgdistiller --repo-root /absolute/path/to/notes mcp
```

A typical MCP client entry is:

```json
{
  "mcpServers": {
    "kgdistiller": {
      "command": "kgdistiller",
      "args": ["--repo-root", "/absolute/path/to/notes", "mcp"]
    }
  }
}
```

The server exposes `kg_status`, `kg_resolve_concepts`, `kg_search`,
`kg_get_node`, `kg_expand`, `kg_ppr`, `kg_build_context`, `kg_align_graph`,
`kg_compare_graph`, and `kg_create_proposal`. All tools are declared read-only and return both
structured JSON and a backwards-compatible text content block. The
implementation uses newline-delimited stdio JSON-RPC, supports stable MCP
revisions through `2025-11-25`, bounds messages and tool arguments, and never
writes logs to protocol stdout.

Paper snapshots use an isolated namespace such as `paper:<digest>`. Comparison
never imports them into `personal`: deterministic ID/label/alias resolution,
missing entries and aligned relations, and optional structured claims produce
`known`, `partial`, `new`, `conflict`, or `uncertain` results with evidence.
Ambiguous labels and abbreviations remain uncertain; similarity is never
promoted into identity. For example, `AC` may retrieve both `absolutely
continuous` and `alternating current`. Explicit local definitions and graph
consistency rank these senses for review without silently merging them.

Approved decisions live in `knowledge/alignments.json`, not in global aliases.
Record one review decision and atomically rebuild the derived index with:

```sh
kgdistiller reconcile alignment knowledge/build/paper.snapshot.json ac \
  absolutely-continuous --predicate exact-match --status reviewed \
  --evidence "The paper explicitly defines AC and uses the same measure relation."
```

A reviewed exact mapping or rejection is trusted only while both endpoint
fingerprints still match. If either paper or personal concept changes, the
decision automatically returns to the candidate-review path. Fresh rejections
remain persisted so the same false match is not repeatedly proposed.

`agent propose` turns the comparison into `qlkg-agent-proposal-v1`. New concepts
receive native marker suggestions but are blocked from the delta until an
authority marker is reviewed. Conflicts and uncertain identities become review
operations. Safe entry and edge candidates are copied into a separate
`qlkg-agent-delta-v2` preview. The command does not apply it; after review, use
the existing guarded workflow explicitly:

```sh
kgdistiller apply knowledge/reviews/paper.delta.json
kgdistiller sync
kgdistiller curate-check --file notes/research/paper.md
kgdistiller check
```

See [the Agentic Knowledge Base specification](docs/agentic-knowledge-base-spec.md)
for the snapshot contract, planned retrieval pipeline, token-budget context
bundles, MCP tools, paper comparison statuses, security boundaries, and phased
implementation plan.

## Agentic distillation

The deterministic engine does not silently ask a hosted model to rewrite your
notes. Agent integrations use a reviewable workflow:

1. scan one changed authority and inspect its existing graph neighborhood;
2. preserve author-written markers;
3. propose any missing `kn` and meaningful cross-file `ref` markers;
4. distill source-grounded entries and direct semantic edges;
5. validate the source diff and graph delta;
6. apply, synchronize, and run file-level curation checks.

The reusable
[`kgdistiller-distill` Agent Skill](https://github.com/qiulinfan/qiulinfan.github.io/tree/main/skills/kgdistiller-distill)
is maintained in the author's public knowledge repository, which is the single
authority for all of their Skills. Local Codex and kgdistiller development
checkouts consume it through symlinks. The Skill works with repository-aware
coding agents instead of coupling the graph engine to one model provider.
Review-first operation is the default; fully automatic application is an
explicit user choice.

## Use as a Git submodule

Keeping the engine separate from personal data is a first-class workflow:

```sh
git submodule add -b main https://github.com/qiulinfan/kgdistiller.git vendor/kgdistiller
git submodule update --remote --merge vendor/kgdistiller
PYTHONPATH=vendor/kgdistiller/src python3 -m kgdistiller --repo-root . sync
```

The host repository owns `knowledge/sources.json`, `knowledge/graph/`, the source
documents, Skills, and any site integration. The submodule owns scanners,
schemas, validation, and the local browser. A host that wants a moving engine
can track `main` and update the submodule before local use or deployment.

## Compatibility

The initial public release reads and writes the existing `qlkg-v2`,
`qlkg-sources-v2`, and `qlkg-agent-delta-v2` schemas. Preserving that stable data
contract allows existing graphs to move to the standalone engine without a
destructive migration. Future schema changes will require explicit migration.

See [the graph contract](docs/graph-contract.md) for identity, curation, and
validation rules.

## Development

```sh
uv run python -m unittest discover -s tests -v
uv build
```

Licensed under Apache-2.0.
