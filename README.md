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
- a Git-friendly portable store with source snapshots and exact embeddings;
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
Source hashes are SHA-256 over UTF-8 authority text after CRLF/CR is normalized
to LF, and that same boundary is used by sync, ingest, store, check, and export.
Consequently Git checkout newline conversion alone is not a knowledge change.
An explicit `--file` must match exactly one configured source pattern; merely
living below a source root is not registration. Overlapping source patterns are
rejected instead of silently assigning the file to whichever source was read
first.

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

## Machine-local profile

Put machine-specific paths and provider selection in the ignored
`knowledge/build/local-profile.json` file:

```json
{
  "schema": "qlkg-local-profile-v1",
  "database": "knowledge.sqlite",
  "portable_store": "/absolute/path/to/private-store",
  "embedding_profile": "primary",
  "provider_profiles": {
    "primary": {
      "adapter": "openai-compatible",
      "model": "example-embedding-model",
      "dimensions": 1536,
      "base_url": "https://provider.example/v1",
      "credential_env": "EMBEDDING_API_KEY"
    }
  }
}
```

Relative profile paths resolve from the profile file's directory. Inspect the
effective selection with `kgdistiller profile status`; the output includes a
non-secret provider configuration digest and credential availability, never the
credential or provider response. `--database`, `--store`, and
`--embedding-profile` override profile values, and `--local-profile` selects a
non-default profile file. Without a profile, existing repository defaults
continue to apply.

The bundled `openai-compatible` adapter uses only the Python standard library,
batches document or query text through `/embeddings`, bounds requests,
responses, and timeouts, and reads its bearer credential only from the declared
environment variable. Configuring a provider does not grant it graph identity
or relation authority. The separately named `deterministic-fixture` adapter is
credential-free and intended only for reproducible tests.

Provider endpoints must use HTTPS. Plain HTTP is accepted only for numeric
loopback addresses such as `127.0.0.1` and `::1`, so a local fixture can be
tested without permitting bearer credentials over a remote plaintext link;
`localhost` is deliberately not treated as the numeric-loopback exception.
Equivalent HTTPS spellings are normalized before computing the non-secret
provider configuration digest.

Credentials must be bounded ASCII bearer tokens without whitespace or control
characters. Network operations have an inactivity timeout; after operating-system
name resolution and socket setup, status lines, headers, response bodies, and
slow-drip responses also share one monotonic wall-clock deadline. Resolver and
multi-address connection latency remain operating-system governed, but are
reported as a timeout if the deadline has elapsed. Malformed framing, oversized
or deeply nested JSON, and invalid vectors return stable, structured provider
errors without retaining the credential, provider response body, or raw
exception chain.

## Explicit embedding status and sync

Put the credential-free vector-space policy at
`knowledge/embedding-policy.json` (or select another repository-relative file
with `--embedding-policy`):

```json
{
  "schema": "qlkg-embedding-policy-v1",
  "profiles": [
    {
      "name": "primary",
      "provider": "openai-compatible",
      "model": "example-embedding-model",
      "dimensions": 1536,
      "required_node_types": ["knowledge"],
      "minimum_coverage": 1.0,
      "required": true
    }
  ]
}
```

The policy profile name and provider/model/dimensions must match the
machine-local provider profile. Inspect every policy profile without creating
a provider or making a network request:

```sh
kgdistiller embedding status
```

Status groups eligible active nodes by profile and node type and classifies
their local vectors as `ready`, `missing`, `stale`, or `incompatible`;
unmapped rows are `unmanaged`. Coverage is reported against each policy
threshold. A profile with no eligible nodes is `not-applicable`, never an
invented 100% coverage result.

Document vectors are created or updated only by an explicit sync:

```sh
kgdistiller embedding sync
kgdistiller embedding sync --batch-size 32 --max-retries 2 --max-nodes 10000
kgdistiller embedding sync --profile primary --profile secondary
```

Without `--profile`, sync uses the selected machine-local embedding profile.
It sends only eligible `missing` or `stale` canonical inputs, preserves ready
vector bytes, and makes zero document-provider calls on an unchanged second
run. Provider calls are bounded by batch, retry, input, output, batch-count,
and total-pending-node limits. Results are validated before one atomic publication; if
the graph generation changes while a provider is working, the staged vectors
are discarded with `stale-generation`.

Query paths never call document embedding or trigger an implicit sync. A query
vector, where a semantic query lane explicitly uses one, is separate from
document synchronization. `embedding status` describes the local disposable
index only: this release does not yet make policy coverage a `store snapshot`
or `store verify` readiness gate.

## Portable Git store

For backup and multi-machine use, make the knowledge project—not SQLite—the
portable unit:

```sh
kgdistiller store snapshot
kgdistiller store verify
git add notes knowledge/sources.json knowledge/alignments.json \
  knowledge/.gitignore knowledge/embedding-policy.json \
  knowledge/graph knowledge/documents.jsonl \
  knowledge/embeddings knowledge/store.json
```

Also stage `knowledge/identities.json` when that optional reviewed registry
exists. `init` and `store snapshot` ensure `knowledge/.gitignore` has an
effective `build/` rule, preserving existing rules and line endings while
repairing a missing or later-negated rule.

`store snapshot` writes the versioned `qlkg-store-v1` manifest, a canonical
JSONL inventory of every ingested authority, and a content-addressed embedding
bundle using `qlkg-embedding-bundle-v2` and `qlkg-embedding-record-v2` (while
readers retain v1 compatibility). Vectors are the exact little-endian float32 bytes already in the
current index; the command never calls a provider. Provider/model,
dimensions, canonical embedding-input digest, configuration digest, and vector
digest remain attached to every record. Embeddings are portable retrieval
artifacts, never graph identity or semantic authority.

Index rebuilds carry forward exact valid vector bytes for still-existing nodes
so status can identify stale inputs; deleted-node vectors are removed. Query
paths consume only active rows whose configuration and canonical input digest
are current, and portable snapshots omit stale inputs.

If the current notes repository should not be the backup unit, bootstrap a
separate store with `kgdistiller store snapshot --output /path/to/private-kb`.
The destination receives registered Markdown, Typst, and LaTeX sources plus
the registries, graph, inventory, and embedding objects. It must not be nested
inside the source project.

On another machine:

```sh
git clone PRIVATE_REMOTE personal-kb
cd personal-kb
kgdistiller store verify
kgdistiller store materialize
kgdistiller agent status
```

`materialize` verifies all content digests and atomically rebuilds the ignored
`knowledge/build/knowledge.sqlite`; no document parsing or re-embedding is
required. Re-running it is a no-op when SQLite already records the same store
generation. Keep the store private when its sources or embeddings contain
sensitive information.

完整的原始需求、SQL/JSONL/向量存储方案比较、数据契约、失败边界和第一版
实现说明见 [portable store development notes](docs/portable-store-development.md)。

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

Domain extractors do not hand-write the snapshot envelope. They emit a bounded,
source-located `qlkg-candidate-graph-v1`, then use the deterministic builder:

```sh
kgdistiller candidate build knowledge/build/paper.candidate.json \
  --output knowledge/build/paper.snapshot.json
kgdistiller candidate validate knowledge/build/paper.snapshot.json
```

The builder validates the packaged JSON Schema, isolated namespace, node IDs,
source locations, typed edge evidence, endpoints, counts, ordering, and both
graph and snapshot digests. Similar names remain separate candidate nodes; the
builder performs no identity inference or personal-graph query.

Every `sync` also atomically rebuilds the provider-neutral Agent index. Inspect
and query it without loading the graph into an Agent context:

```sh
kgdistiller agent status
kgdistiller agent resolve "Measure space" "sigma algebra"
kgdistiller agent search "countably additive measure" --type knowledge \
  --graph-strategy hybrid --limit 10
kgdistiller agent search --plan knowledge/build/retrieval-plan.json
kgdistiller agent get measure-space
kgdistiller agent expand measure-space --direction incoming --depth 2
kgdistiller agent ppr measure-space --limit 20
kgdistiller agent context "How does a measure depend on a sigma algebra?" \
  --budget 6000 --depth 2
kgdistiller agent context --plan knowledge/build/retrieval-plan.json \
  --budget 6000
kgdistiller agent align knowledge/build/paper.snapshot.json \
  --output knowledge/reviews/paper.alignment.json
kgdistiller agent compare knowledge/build/paper.snapshot.json
kgdistiller agent propose knowledge/build/paper.snapshot.json \
  --target-authority notes/research/paper.md \
  --output knowledge/reviews/paper.proposal.json \
  --delta-output knowledge/reviews/paper.delta.json
```

The SQLite index remains disposable, but query commands are strictly read-only.
If it is absent or stale relative to the committed graph, `agent search`,
`agent context`, and MCP return a stable `index-unavailable` or `stale-index`
error and publish no files. Refresh it explicitly with `sync`, or restore a
verified portable generation with `store materialize`; the SQLite file itself
is never committed.

The `qlkg-agent-index-v2` index stores nodes, normalized IDs/labels/global
aliases, explicit scoped abbreviations, reviewed cross-namespace mappings,
structured entry text, typed edges with evidence, reference occurrences, and
optional locally materialized embeddings/similarity edges. Exact and alias
resolution refuses ambiguous identities; FTS input is tokenized and quoted
before reaching SQLite.

Agent search accepts either the compatible single query or a bounded
`qlkg-retrieval-plan-v1`. A plan keeps identity, lexical, semantic, and graph
expressions separate. Search executes identity, FTS, already-materialized
vectors, bounded typed BFS, and weighted Personalized PageRank, then applies
deterministic weighted reciprocal-rank fusion. `--graph-strategy` selects
`bfs`, `ppr`, or the `hybrid` default for a legacy query; a plan carries its
own graph policy.
The response is a `qlkg-search-execution-v1` envelope whose
`qlkg-search-result-v2` records each lane as enabled, disabled, degraded, or
error with a stable reason and per-result evidence. Exact/alias identities are
protected from score pressure, and an over-budget ambiguous candidate set is
not collapsed by lexical, semantic, or inferred graph ranking.

The semantic lane is created only when the selected machine-local profile has
current materialized vectors in the requested vector space. All semantic
expressions in one plan use one query-only batch; no query path embeds document
or node content. Missing credentials, adapters, coverage, vector space, or
profile agreement degrade only that lane. `agent context` returns a
`qlkg-context-bundle-v1` evidence package whose nodes, edge evidence, backlinks,
sources, omissions, and retrieval paths fit the requested conservative token
budget. It preserves the plan's original question and does not ask an LLM to
generate an answer. Machine-readable identity resolutions remain in its policy;
ambiguous candidate groups are packed atomically or omitted as a whole when the
budget cannot hold them.

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

`kg_search` and `kg_build_context` accept exactly one legacy `query` or one
inline retrieval `plan`. Provider configuration and credentials are injected
from the machine-local server profile; they are not accepted as MCP tool
arguments. CLI and MCP apply the same depth, node-type, ambiguity, generation,
and response bounds.

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
`qlkg-agent-delta-v2` preview. The command does not apply it. After reviewing
the native authority patch, candidate decisions, delta, mappings, and evidence,
package them as `qlkg-ingest-request-v1` and run the transactional writer:

```sh
kgdistiller ingest plan knowledge/reviews/paper.ingest.json \
  --output knowledge/build/paper.ingest-plan.json
# Change mode to apply, recompute request_sha256, and review again.
kgdistiller ingest apply knowledge/reviews/paper.ingest.json \
  --receipt knowledge/build/paper.ingest-receipt.json
```

`ingest plan` runs source patching, marker verification, delta application,
synchronization, curation, and global validation in isolated staging without
changing the project. `ingest apply` revalidates all graph, alignment, query,
candidate, and source digests under a single-writer lock, installs the complete
result, rebuilds disposable SQLite, and returns `qlkg-ingest-receipt-v1`.
Canonical requests are idempotent. A journal restores authority, graph, and
alignment bytes after failure or process termination. The read-only MCP remains
read-only.

See [the transactional ingest contract](docs/transactional-ingest.md) for the
versioned JSON Schemas, Python API, bounds, stable errors, rollback behavior,
and request example. Low-level `apply`, `sync`, `reconcile`, and validation
commands remain available for engine development and compatibility, but Agent
Skills do not compose them into a write transaction.

See [the Agentic Knowledge Base specification](docs/agentic-knowledge-base-spec.md)
for the snapshot contract, retrieval pipeline, token-budget context bundles,
MCP tools, paper comparison statuses, transaction boundary, security rules, and
implementation phases.

The historical
[Agentic knowledge base closure specification](docs/agentic-knowledge-base-closure-spec.md)
records earlier portable-RAG, document-upsert, Skill, and paper-import
requirements. The current product workflow authority is
[`workflows/manifest.json`](workflows/manifest.json).

See [the active repair specification](docs/agentic-knowledge-base-repair-spec.md)
for the 2026-08-09 audited baseline, the strictly sequential implementation
slices, and the evidence ledger used to complete those requirements one at a
time.

## Agent Skills

kgdistiller ships the complete provider-neutral knowledge and paper Skill suite
alongside the engine:

- [`query-kgdistiller`](skills/query-kgdistiller/SKILL.md) performs only batch
  resolution, bounded GraphRAG retrieval, candidate alignment, and graph
  comparison. It never reads raw graph files into model context and never
  mutates a knowledge project.
- [`ingest-kgdistiller`](skills/ingest-kgdistiller/SKILL.md) is the guarded write
  boundary for already reviewed authority markers, refs, entries, mappings,
  and semantic edges. It does not discover concepts or decide ambiguous
  identity.
- [`deploy-kgdistiller`](skills/deploy-kgdistiller/SKILL.md) creates, refreshes,
  verifies, and restores the portable store, including exact cached embeddings
  and the local-only/committed/remote-confirmed Git state.
- [`curate-kgdistiller-notes`](skills/curate-kgdistiller-notes/SKILL.md)
  extracts a bounded registered note set and prepares a reviewed handoff.
- [`extract-paper-markdown`](skills/extract-paper-markdown/SKILL.md) acquires a
  complete traceable paper package without recreating its visual layout.
- [`distill-paper-knowledge`](skills/distill-paper-knowledge/SKILL.md) builds a
  validated isolated paper graph and aligns it read-only.
- [`import-paper-knowledge`](skills/import-paper-knowledge/SKILL.md) is the
  explicit authorization boundary for selected paper candidates.
- [`trace-concept-lineage`](skills/trace-concept-lineage/SKILL.md) produces
  cited concept dossiers and a prerequisite-ordered learning route.

The former mixed note/paper exporter is intentionally split: note curation,
paper distillation, and explicitly authorized paper import have different
mutation and review boundaries. Each Skill is self-contained, carries its own
`agents/openai.yaml` and local references/scripts, and contains no personal
path, credential, site style, or repository wrapper assumption.

This separation keeps model-specific semantic reading outside the deterministic
core while preventing each Skill from loading or reimplementing the whole
knowledge graph. Review-first operation remains the default.

The portable workflow manifest and project agent presets live at
[`workflows/manifest.json`](workflows/manifest.json) and
[`.codex/agents`](.codex/agents). Install or diagnose only the kgdistiller
namespace with:

```sh
kgdistiller codex link
kgdistiller codex doctor
```

The default `auto` installation stays live: POSIX uses symbolic links, while
Windows can use directory junctions for Skills/product workflow files and
same-volume hardlinks for agent files when symbolic-link privileges are
unavailable. It never silently falls back to a copy. Explicit `--mode copy`
creates a non-live snapshot that must be refreshed after changes. The canonical
entry at
`$CODEX_HOME/workflow-products/kgdistiller/workflows/manifest.json` anchors the
complete workflow from any knowledge-project current directory. The linker
removes renamed or retired product assets only when its state still proves
ownership, refuses unmanaged or modified collisions, and never writes global
`AGENTS.md`, `config.toml`, unrelated Skills, or unrelated agent presets. See
[product workflows](docs/product-workflows.md) for the complete role and
workflow contract.

As an additional guard, the Codex home cannot equal, contain, or be contained
by the product root. The namespaced state must be an ordinary non-reparse file,
and its source paths must match the active manifest before any managed target
is replaced or removed. Before any mkdir or state publication, the linker also
rejects a symlink, Junction, or other reparse parent at the Codex home,
`skills`, `agents`, `workflow-products`, or its recovery namespace. When an
editor atomically replaces a hardlinked agent source, `doctor` reports the
detached link; rerunning `link` repairs it only if the target bytes still equal
the state receipt's install digest. A mismatch remains untouched.

## Independent product and static consumers

Install kgdistiller as a versioned package or tool. A knowledge repository is
an instance: it owns only authorities and generated knowledge data, and does
not vendor the engine or product Skills.

```sh
uv tool install kgdistiller==0.3.0
kgdistiller --repo-root /path/to/knowledge-project check
```

For a static consumer, produce one already hydrated, privacy-filtered bundle:

```sh
kgdistiller --repo-root /path/to/knowledge-project export site \
  --output knowledge/export/site \
  --product-commit FULL_PRODUCT_COMMIT \
  --source-repository https://example.invalid/owner/knowledge
python knowledge/export/site/verify_export.py knowledge/export/site
```

`--product-commit` is verified against any commit discoverable from a clean
product checkout or installed `direct_url.json`; it cannot be used to relabel
another revision. A source-checkout export rejects all tracked or untracked
product changes. Only an installation with no discoverable VCS provenance may
use the explicit full commit as its release assertion.

The knowledge instance must also be a clean Git checkout, including untracked
files. Every hashed authority must still match the private graph, and the
registry, four core graph projections, manifest-declared entry shards, and
hashed authorities must all be tracked at the current `HEAD`; that proven
commit becomes `source.revision` and cannot be overridden. Use two commits:
first commit and verify all instance inputs (and the old bundle during a
refresh), export from that clean commit, then commit the exact verified
successor bundle as the adoption commit. This avoids attributing dirty export
inputs to an older revision. `--source-repository` remains required.

To advance an already adopted bundle, repeat the command with `--replace`.
Replacement is allowed only when the old four-file bundle verifies; the new
bundle is built and verified in staging before a recoverable directory swap.
Any build or verification failure leaves every old bundle byte unchanged, and
the new manifest records `replaces_export_sha256`. The directory swap is the
commit point. If verified-predecessor cleanup then fails, the command returns
the committed successor with `cleanup_status: pending`, a warning, and a
managed path below ignored `knowledge/build/.kgd-export-recovery/`. The next
export verifies that receipt and completes cleanup before proceeding; do not
pre-delete either path.

The four-file bundle contains `manifest.json`, `graph.json`,
`knowledge-registry.typ`, and a dependency-free `verify_export.py`. Its receipt
binds product repository/version/commit, source repository/revision/digests,
private and public graph digests/counts, published source hashes, visibility
policy, and artifact hashes/bytes. Source hashes and artifact records use
canonical LF UTF-8 text, so a Windows CRLF checkout verifies identically.
The immutable v1 receipt has exactly three artifact records: the site graph,
Typst registry, and standalone verifier at their fixed paths.
`graph.json` includes privacy-filtered
`diagnostics.errors`, `diagnostics.warnings`, and `diagnostics.info`, all inside
the public graph digest. Every public edge is reduced to the exact structural
triple `source`, `relation`, and `target`; edge evidence, fingerprints, origin,
confidence, curation data, and other source-derived fields never cross the
privacy boundary, even when both endpoints are public. A consumer validates
and adopts these static bytes without installing kgdistiller, following a
submodule pointer, or recomputing the graph.

## Compatibility

The initial public release reads and writes the existing `qlkg-v2`,
`qlkg-sources-v2`, and `qlkg-agent-delta-v2` schemas. Preserving that stable data
contract allows existing graphs to move to the standalone engine without a
destructive migration. Future schema changes will require explicit migration.

See [the graph contract](docs/graph-contract.md) for identity, curation, and
validation rules.

For a personal installation, backup/restore drill, MCP configuration, loopback
boundary, Git synchronization, and upgrades, see
[local-first deployment](docs/deployment.md). For schema compatibility,
migration rules, distribution checks, and publication order, see
[the public release policy](docs/release.md). Security boundaries and private
reporting guidance are in [SECURITY.md](SECURITY.md). The reproducible
100,000-node measurements and release envelope are in
[the performance baseline](docs/performance.md).

## Development

```sh
uv run python -m unittest discover -s tests -v
uv build

# Query/sync profile. Generated data stays outside the repository.
PYTHONPATH=src python3 scripts/stress_workflow.py --nodes 100000 \
  --skip-transaction --query-samples 20 \
  --report /tmp/kgdistiller-stress-100k-query.json

# Slower full transaction, fault-injection, and reader-isolation profile.
PYTHONPATH=src python3 scripts/stress_workflow.py --nodes 100000 \
  --report /tmp/kgdistiller-stress-100k-report.json
```

Licensed under Apache-2.0.
