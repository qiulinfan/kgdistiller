# kgdistiller Agentic Knowledge Base Specification

Status: Implemented v0.3 deterministic baseline; Phases 1-8 complete
Contract owner: kgdistiller deterministic core  
Initial implementation target: local single-user research repositories

## 1. Purpose

This specification defines the knowledge-serving layer built on top of a
kgdistiller graph. Its job is to let an Agent resolve, retrieve, traverse,
compare, and cite a researcher's knowledge without loading the complete graph
into the model context.

The system is a **Graph Context Engine**, not a second authoring system and not
an autonomous source of graph truth. Markdown, Typst, and LaTeX authority files
remain authoritative. The `qlkg-v2` graph remains their deterministic,
source-backed representation. Every search database, vector index, cache,
summary, and Agent-facing bundle defined here is derived and disposable.

The specification uses the terms **MUST**, **MUST NOT**, **SHOULD**, **SHOULD
NOT**, and **MAY** in their normative sense.

## 2. Goals

The system MUST support these primary workflows:

1. An Agent retrieves a small, relevant, source-backed subgraph under an
   explicit token budget.
2. An Agent distills a paper into a candidate graph and compares it with a
   personal graph without silently mutating the personal graph.
3. The system identifies concepts that are known, partially known, new,
   conflicting, or uncertain.
4. An Agent can generate reviewable knowledge-entry and graph-edge proposals
   for missing knowledge.
5. Every returned claim can be traced to a node, edge, reference occurrence,
   entry, or authority location.
6. The deterministic core remains independent of model, embedding, reranking,
   vector-store, graph-store, and Agent vendors.
7. The default deployment is local, single-user, offline-capable, and usable
   through CLI and MCP.

## 3. Non-goals

The first stable version does not attempt to provide:

- a collaborative team wiki;
- chat-history or episodic Agent memory;
- multi-tenant authorization;
- automatic entity identity derived from similarity;
- automatic semantic edges derived from document order or co-occurrence;
- an independently editable graph database;
- silent writes to authority files;
- distributed indexing or a cloud control plane;
- a mandatory LLM, embedding model, reranker, or external database;
- a replacement for the existing kgdistiller graph contract.

## 4. Trust and authority model

The system has four layers with strictly decreasing authority:

```text
Authority documents
  -> qlkg-v2 graph
    -> qlkg-agent-snapshot-v1
      -> search indexes, caches, context bundles, and Agent responses
```

### 4.1 Authority documents

Configured Markdown, Typst, and LaTeX files are the only authorities for active
knowledge definitions. PDF and other formats MAY be inputs to a candidate paper
graph, but MUST NOT become a personal knowledge authority without an explicit
reviewed import workflow.

### 4.2 Authority graph

`qlkg-v2` owns stable node IDs, supported node and edge types, source
provenance, curation state, and graph validation. The rules in
`docs/graph-contract.md` remain normative.

### 4.3 Agent snapshot

`qlkg-agent-snapshot-v1` is a deterministic, self-contained projection of one
validated graph. It is the only supported input contract for Agent indexes and
external retrieval adapters. Consumers MUST NOT scrape internal graph files or
authority documents to infer a second graph identity system.

Host extractors construct isolated snapshots through the
`qlkg-candidate-graph-v1` builder. The builder validates explicit candidate
IDs, bounded source locations, typed relations and evidence, endpoints, schema,
ordering, counts, and digests. It MUST NOT infer identity from labels or query
the personal graph.

### 4.4 Derived state

SQLite tables, FTS indexes, embeddings, ranker caches, graph neighborhoods,
community summaries, and context bundles are derived state. They MUST be safe
to delete and rebuild from an Agent snapshot.

## 5. Identity and namespace rules

### 5.1 Node identity

A node is addressed by the pair `(namespace, node_id)`.

- `node_id` comes exclusively from the source marker and kgdistiller identity
  registry rules.
- Similar labels, aliases, definitions, embeddings, or graph neighborhoods
  MUST NOT merge two nodes.
- A model MAY propose an alignment, but the result remains `uncertain` until an
  explicit deterministic mapping or reviewed decision exists.
- File path, heading position, chunk index, and document order MUST NOT define
  node identity.

### 5.2 Namespaces

The reserved personal namespace is `personal`. Candidate graphs SHOULD use a
content-addressed namespace such as `paper:<sha256-prefix>`.

Namespaces MUST match:

```text
^[a-z0-9][a-z0-9._-]*(?::[a-z0-9][a-z0-9._-]*)*$
```

The namespace is part of retrieval and comparison identity, but MUST NOT be
prepended to the original qlkg node ID in stored source data.

### 5.3 Three name and identity layers

The implementation MUST keep these records distinct:

1. **Global names and aliases** belong to one qlkg node and participate in
   deterministic identity resolution. They MUST remain collision-free inside
   their namespace.
2. **Scoped aliases** are explicit abbreviations defined by source text, such
   as `absolutely continuous (AC)` or `AC denotes absolutely continuous`.
   They are indexed with namespace, node, source field/span, and a bounded
   evidence quote. They MAY retrieve candidates but MUST NOT enter the global
   name table or independently create identity.
3. **Cross-namespace mappings** relate two already-existing nodes. They are
   review data, not aliases and not qlkg semantic edges.

This separation is required because a surface such as `AC` can mean
`absolutely continuous`, `alternating current`, or another concept depending
on the paper and field.

## 6. Agent snapshot contract

### 6.1 Envelope

A snapshot has this top-level shape:

```json
{
  "schema": "qlkg-agent-snapshot-v1",
  "namespace": "personal",
  "graph": {
    "schema": "qlkg-v2",
    "sha256": "<authority graph digest>",
    "counts": {
      "nodes": 0,
      "edges": 0,
      "references": 0
    }
  },
  "nodes": [],
  "edges": [],
  "references": [],
  "diagnostics": {
    "errors": [],
    "warnings": []
  },
  "snapshot_sha256": "<snapshot digest>"
}
```

The export MUST reject:

- an absent or unsupported authority graph manifest;
- an invalid namespace;
- graph validation errors;
- missing authority graph digest;
- counts that disagree with the exported arrays.

Warnings, including orphaned nodes and stale curation, remain visible and do
not prevent export. Consumers SHOULD exclude stale semantic claims by default
unless a query policy explicitly asks for them.

### 6.2 Canonicalization and digest

The arrays MUST have deterministic order:

- nodes by `id`;
- edges by `(source, relation, target)`;
- references by `(authority, line, target, id)`.

`snapshot_sha256` is SHA-256 over the UTF-8 compact canonical JSON of every
top-level field except `snapshot_sha256`, with object keys sorted and no
insignificant whitespace. Export MUST NOT add timestamps, random IDs, host
paths, model names, or other nondeterministic values.

Two exports of unchanged graph artifacts and namespace MUST be byte-identical.

### 6.3 Node records

Snapshot nodes are hydrated `qlkg-v2` node records. Entry shards MUST be loaded
before export so a consumer does not need access to the original shard files.

Every node MUST contain:

- `id`: stable machine ID;
- `type`: `knowledge`, `field`, or `topic`;
- `label`: human-readable label.

A node MAY contain:

- `text`: concise source-backed entry text;
- `entry`: structured research dossier;
- `properties`: aliases, curation status, source status, fields, origin, and
  other qlkg metadata;
- `provenance`: authority path, source format, source line/span, source ID,
  definition digest, and canonical web location.

Export MUST preserve unknown qlkg fields so compatible schema extensions are
not silently discarded.

### 6.4 Edge records

Every edge MUST contain `source`, `relation`, and `target`. Semantic edges MUST
retain origin, confidence, evidence, endpoint definition fingerprints, and
curation state when present.

Indexes MUST preserve parallel semantic distinctions. They MUST NOT collapse
two future edge records merely because they share endpoints. `contains` is a
taxonomy edge and is not evidence that two concepts are semantically
equivalent.

### 6.5 Reference records

References are source occurrences and backlinks, not semantic edges. Export
MUST preserve their occurrence IDs, authority paths, lines, display labels,
contexts, formats, and canonical links when present.

## 7. Derived index contract

The default index SHOULD use SQLite because kgdistiller is local-first and
already produces a SQLite artifact. Alternative backends MAY implement the same
logical contract.

The `qlkg-agent-index-v2` logical index contains:

```text
index_meta(
  schema, snapshot_sha256, graph_sha256, namespace, provider_config_sha256
)

nodes(
  namespace, id, type, label, text, aliases_json,
  properties_json, provenance_json, curation_status, source_status
)

edges(
  namespace, source, relation, target, evidence,
  confidence, origin, curation_status, payload_json
)

references(
  namespace, id, target, authority, line, context, payload_json
)

node_fts(id, label, aliases, text)

embeddings(
  namespace, node_id, provider, model, dimensions,
  content_sha256, vector
)

scoped_aliases(
  alias_id, namespace, node_id, surface, normalized_surface,
  expansion, authority, payload_json
)

alignment_mappings(
  mapping_id, subject_namespace, subject_id, subject_sha256,
  predicate, object_namespace, object_id, object_sha256,
  status, payload_json
)

similarity_edges(
  namespace, source, target, provider, model, score, evidence_json
)
```

An implementation MAY add materialized adjacency, degree, community, or cache
tables. Those tables are never part of authority.

### 7.1 Incremental invalidation

- A changed `snapshot_sha256` invalidates snapshot-wide caches.
- A node embedding is reusable only when its canonical embedding input digest,
  provider, model, and dimensions are unchanged.
- A changed edge set invalidates affected adjacency and graph-ranking caches.
- A reviewed mapping becomes stale when either stored endpoint fingerprint no
  longer matches the current node content.
- Changing an embedding provider MUST NOT change node IDs or graph structure.
- Provider credentials and generated vectors MUST NOT be committed to the
  kgdistiller repository.

## 8. Retrieval contract

Retrieval is a staged process. Implementations MAY tune ranking, but MUST retain
the stages and explanations.

### 8.1 Stage A: deterministic resolution

Resolve exact machine IDs, canonical labels, and registered aliases first.
Resolution returns zero, one, or multiple candidates. An ambiguous name MUST
remain ambiguous and MUST NOT be resolved by document order.

Explicit scoped aliases form a lower-authority resolution lane. A single
scoped-alias result MUST be labeled `scoped-alias`, not `exact` or `alias`.
Multiple senses MUST remain `ambiguous`.

### 8.2 Stage B: lexical retrieval

FTS/BM25 searches IDs, labels, aliases, entry text, and selected structured
entry fields. The query layer MUST escape or parameterize user input safely.

### 8.3 Stage C: optional semantic retrieval

Embeddings MAY retrieve additional candidate nodes. Semantic similarity is
candidate evidence only. It MUST NOT create nodes, edges, aliases, or identity
mappings.

### 8.4 Stage D: typed graph expansion

Expansion starts from resolved or retrieved seed nodes. A query policy controls:

- allowed edge types;
- incoming, outgoing, or both directions;
- maximum depth;
- per-relation cost or weight;
- whether taxonomy edges are included;
- whether stale or orphaned material is included.

The implementation supports bounded breadth-first traversal and weighted
Personalized PageRank (PPR). PPR MUST start from explicit retrieval seeds. It
uses reviewed qlkg semantic edges as the trusted topology and MAY include
embedding-similarity edges at a lower weight. Similarity edges MUST carry
`identity_authority: false`, remain disposable, and never enter `qlkg-v2`.

The query policy selects `bfs`, `ppr`, or `hybrid`. The hybrid default fuses
both graph lanes and retains a separate explanation for each.

### 8.5 Stage E: fusion and diversity

Results from exact, lexical, semantic, BFS, and PPR lanes SHOULD be combined by
a rank-fusion method such as reciprocal rank fusion. Diversity-aware reranking
MAY reduce redundant neighboring results. Provider-specific reranking MUST live
behind an adapter and can reorder only the already retrieved candidate set.

### 8.6 Explanation

Every result MUST include why it was selected:

```json
{
  "namespace": "personal",
  "node_id": "measure-space",
  "score": 0.84,
  "reasons": [
    {"method": "alias", "rank": 1},
    {"method": "fts", "rank": 2, "score": 7.4},
    {
      "method": "graph",
      "seed": "sigma-algebra",
      "relation": "prerequisite-for",
      "depth": 1
    }
  ]
}
```

Raw scores from unrelated retrieval methods MUST NOT be presented as directly
comparable probabilities.

## 9. Context bundle contract

`build_context` produces `qlkg-context-bundle-v1`. It is a bounded evidence
package, not an answer generated by an LLM.

```json
{
  "schema": "qlkg-context-bundle-v1",
  "snapshot_sha256": "...",
  "query": "...",
  "budget": {
    "requested_tokens": 6000,
    "estimated_tokens": 4321,
    "estimator": "..."
  },
  "seeds": [],
  "nodes": [],
  "edges": [],
  "references": [],
  "sources": [],
  "omissions": [],
  "retrieval": {
    "policy": {},
    "explanations": []
  }
}
```

The builder MUST:

- never exceed the requested budget according to the selected estimator;
- reserve space for IDs, edge evidence, and citations before optional prose;
- prefer direct evidence over repeated summaries;
- include edge endpoints when including an edge;
- report omitted high-ranking material and the reason for omission;
- remain deterministic when all providers are disabled;
- return structured JSON without calling an answer-generation model.

The default packing priority is:

1. exact/resolved seed nodes;
2. their authoritative provenance;
3. direct edges with evidence;
4. immediate prerequisites and definitions;
5. relevant backlinks;
6. deeper graph neighbors;
7. optional supporting excerpts.

## 10. Agent API and MCP contract

The initial server is read-only and exposes these tools:

### `kg_status`

Returns schema versions, namespace, graph and snapshot digests, counts,
available retrieval lanes, provider status, and warnings.

### `kg_resolve_concepts`

Input: a batch of names or IDs.  
Output: exact, alias, ambiguous, missing, and optional semantic candidates.

Batch resolution is required so paper distillation does not issue one full
retrieval call per concept.

### `kg_search`

Input: query, node-type filters, namespace, limit, and retrieval policy.  
Output: ranked nodes and per-lane explanations.

### `kg_get_node`

Input: namespace, node ID, and requested evidence level.  
Output: the node, direct typed edges, backlinks, and provenance.

### `kg_expand`

Input: seed IDs, direction, edge types, maximum depth, result limit, and stale
content policy.  
Output: a bounded subgraph with traversal paths.

### `kg_ppr`

Input: explicit seed IDs, node/edge filters, result limit, and similarity-edge
policy.
Output: `qlkg-ppr-result-v1` with convergence information, ranked nodes, seed
flags, topology counts, and policy.

### `kg_build_context`

Input: query, token budget, filters, and retrieval policy.  
Output: `qlkg-context-bundle-v1`.

### `kg_align_graph`

Input: an isolated candidate snapshot, target namespace, and per-node candidate
limit.
Output: `qlkg-alignment-report-v1`, including scoped aliases, candidate signals,
review proposals, rejected targets, and the report digest. The tool is
read-only.

### `kg_compare_graph`

Input: a candidate Agent snapshot and target namespace.  
Output: `qlkg-graph-comparison-v1` as defined below.

The stdio transport is the default. An optional HTTP transport MUST bind to
`127.0.0.1` by default, validate all file paths, impose request and response
size limits, and require explicit configuration before accepting non-loopback
connections.

## 11. Paper graph comparison

Candidate paper graphs MUST live in a namespace distinct from `personal`.
Comparison never mutates either graph.

Each candidate node receives one status:

- `known`: an explicit mapping or high-confidence deterministic label/alias
  resolution identifies an existing node and no material contradiction is
  present;
- `partial`: related personal knowledge exists, but the candidate contains a
  missing definition component, condition, role, or direct relation;
- `new`: deterministic resolution finds no personal node;
- `conflict`: the candidate and personal graph make incompatible
  source-grounded claims;
- `uncertain`: similarity suggests possible alignment but identity evidence is
  insufficient.

The comparison record is:

```json
{
  "schema": "qlkg-graph-comparison-v1",
  "candidate_snapshot_sha256": "...",
  "target_snapshot_sha256": "...",
  "results": [
    {
      "candidate": {"namespace": "paper:...", "id": "..."},
      "status": "uncertain",
      "matches": [],
      "missing": [],
      "conflicts": [],
      "evidence": []
    }
  ],
  "summary": {}
}
```

Only explicit identity registries or reviewed comparison decisions can promote
an `uncertain` match into a durable cross-namespace mapping.

### 11.1 Alignment registry

Durable cross-namespace decisions use `qlkg-alignments-v1`. Each mapping is
SSSOM-inspired and contains:

```json
{
  "id": "<endpoint-addressed mapping id>",
  "subject": {
    "namespace": "paper:example",
    "node_id": "ac",
    "node_sha256": "<candidate node fingerprint>"
  },
  "predicate": "exact-match",
  "object": {
    "namespace": "personal",
    "node_id": "absolutely-continuous",
    "node_sha256": "<personal node fingerprint>"
  },
  "status": "reviewed",
  "mapping_justification": ["explicit abbreviation and matching definition"],
  "evidence": [{"kind": "review", "text": "..."}]
}
```

Supported predicates are `exact-match`, `close-match`, `broad-match`,
`narrow-match`, `related-match`, and `different-from`. Supported states are
`proposed`, `reviewed`, `rejected`, `ambiguous`, and `deprecated`.

Only a `reviewed` + `exact-match` mapping with both endpoint fingerprints fresh
is identity authority. Fresh `rejected` decisions and fresh reviewed
`different-from` mappings exclude the recorded target. A stale positive or
negative decision remains visible as evidence but MUST NOT anchor or exclude an
identity. Reviewed and rejected mappings require both a human justification
and evidence.

### 11.2 Candidate generation and decision boundary

Alignment candidate generation MAY combine:

1. deterministic ID, label, registered-alias, and explicit target resolution;
2. fresh reviewed mappings;
3. explicit scoped-alias expansions;
4. lexical retrieval and acronym matching;
5. optional embedding similarity;
6. node-type compatibility;
7. consistency with edges whose neighboring endpoints are already hard
   anchors;
8. an optional provider-neutral `CandidateAnalyzer` proposal.

Every signal MUST say whether it has identity authority. Lexical, acronym,
embedding, type, graph-consistency, and model-analyzer signals never do. They
rank candidates for review; they cannot merge nodes. When multiple candidates
remain, the result is `ambiguous` even if the first candidate scores much
higher.

`qlkg-alignment-report-v1` is deterministic when optional providers are absent.
Its proposals contain endpoint fingerprints, evidence signals, mapping
justifications, and scores, but remain `proposed` until explicitly reconciled.

## 12. Write proposals and transactional ingestion

Read-only retrieval and authority mutation are separate capabilities. A writer
MAY emit `qlkg-agent-proposal-v1`, but MUST NOT edit source files or apply graph
deltas without an explicit review/apply action.

A proposal can contain:

- a source-marker edit;
- a new or revised source-grounded entry;
- a typed semantic edge with confidence and evidence;
- an explicit identity reconciliation;
- a cross-namespace import decision.

Applying a proposal MUST pass the existing kgdistiller scan, graph validation,
curation checks, and stale-definition rules. The proposal format never bypasses
`qlkg-agent-delta-v2`; it packages review intent around existing deterministic
operations.

Personal-graph writes use `qlkg-ingest-request-v1` and return
`qlkg-ingest-receipt-v1`. The transaction MUST verify the query target, source,
candidate, report, and alignment digests; acquire a repository single-writer
lock; validate a complete staged state; and either install all authority,
graph, alignment, registry, and index results or restore their prior hashes.
Requests are content-addressed and idempotent. Interrupted installation is
recovered from a durable journal before the next writer runs. The read-only MCP
remains read-only; the initial writer is a local Python/CLI capability. See
`docs/transactional-ingest.md`.

## 13. Provider adapters

Optional intelligence belongs behind four narrow interfaces:

```text
EmbeddingProvider.embed(texts) -> vectors
RerankProvider.rerank(query, candidates) -> ordered candidates
TokenEstimator.count(text) -> integer
CandidateAnalyzer.compare(candidate, target, evidence) -> proposal
```

The deterministic core MUST function without any provider. Provider names,
models, dimensions, configuration digests, and failures MUST be observable.
Secrets MUST be read from runtime configuration and MUST NOT enter snapshots,
graph artifacts, logs, or committed configuration.

Model output MUST NOT directly create graph identity or trusted semantic edges.

## 14. Security and privacy

- Local HTTP servers bind to `127.0.0.1` unless explicitly overridden.
- Source excerpt reads MUST resolve paths beneath configured repository roots
  and reject traversal.
- SQLite queries MUST be parameterized.
- MCP and HTTP inputs MUST have bounded lengths, list sizes, depth, and token
  budgets.
- Snapshot import MUST validate schema, counts, digest, node IDs, endpoints,
  and namespace before indexing.
- External model calls are opt-in and SHOULD expose which fields leave the
  machine.
- Authority content, personal snapshots, generated indexes, embeddings, keys,
  and query logs MUST NOT be committed to the kgdistiller engine repository.
- Read-only MCP tools SHOULD be the default capability set.

## 15. Determinism and failure behavior

Without optional providers, identical snapshot, query, policy, and token
estimator inputs MUST produce byte-equivalent structured results.

Failures use stable machine-readable codes. Initial codes are:

```text
unsupported-schema
invalid-namespace
invalid-snapshot-digest
snapshot-count-mismatch
invalid-node-id
dangling-edge
ambiguous-concept
unknown-concept
invalid-query-policy
budget-too-small
provider-unavailable
stale-index
unsafe-source-path
```

Provider failure SHOULD degrade to deterministic lanes when possible and MUST
be reported. It MUST NOT silently change authority data.

## 16. Observability and evaluation

Each query SHOULD record locally, when enabled:

- snapshot digest and policy;
- latency per retrieval lane;
- candidate counts per lane;
- graph nodes visited;
- context tokens requested and packed;
- omitted results;
- provider/model identifiers without secrets.

The evaluation suite MUST cover:

1. exact ID and alias resolution;
2. ambiguous-name refusal;
3. lexical recall;
4. direct and multi-hop prerequisite retrieval;
5. evidence and provenance retention;
6. stale-curation filtering;
7. token-budget compliance;
8. deterministic output;
9. paper-to-personal `known/partial/new/conflict/uncertain` classification;
10. resistance to false identity merges.
11. scoped abbreviations with multiple domain senses;
12. graph-consistency ranking without automatic identity promotion;
13. reviewed mapping acceptance, rejection, and fingerprint invalidation;
14. PPR retrieval over trusted and disposable soft edges.

The `solvablemodel` paper workflow SHOULD become the first end-to-end benchmark.
Quality measurements SHOULD include retrieval recall, false alignment rate,
evidence coverage, context size, indexing time, and query latency.

## 17. Compatibility and versioning

- Schema names are versioned and immutable once released.
- Additive optional fields MAY be introduced without a new schema name.
- Removing fields, changing identity, digest, ordering, or status semantics
  requires a new schema version and explicit migration.
- Consumers MUST ignore unknown optional fields and MUST reject unknown major
  schemas.
- The engine MUST continue to accept Markdown, Typst, and LaTeX authorities.

## 18. Implementation phases

### Phase 1: Agent snapshot foundation

- define and document `qlkg-agent-snapshot-v1`;
- export a deterministic, self-contained snapshot;
- hydrate entry shards;
- include full nodes, typed edges, references, provenance, and diagnostics;
- validate namespace, graph schema, graph digest, counts, and graph errors;
- expose `kgdistiller snapshot` with stdout and atomic file output;
- add deterministic and rejection tests.

### Phase 2: Complete derived SQLite index

- version `index_meta` against snapshot digest;
- store edges and references in queryable tables;
- add safe FTS/BM25 querying;
- add adjacency indexes and batch concept resolution;
- retain exact retrieval explanations.

### Phase 3: Retriever and context builder

- implement typed bounded traversal;
- implement deterministic rank fusion;
- implement `qlkg-context-bundle-v1` and token packing;
- add optional embedding and reranking adapters;
- benchmark on the personal graph.

### Phase 4: Read-only MCP

- implement status, resolve, search, get-node, expand, and build-context tools;
- default to stdio;
- test request bounds and optional loopback HTTP transport.

### Phase 5: Paper comparison

- import candidate snapshots into isolated namespaces;
- implement deterministic batch resolution;
- add optional semantic alignment proposals;
- emit `qlkg-graph-comparison-v1`;
- benchmark with `solvablemodel`.

### Phase 6: Reviewable knowledge production

- emit `qlkg-agent-proposal-v1` for new and partial knowledge;
- integrate existing scan, delta, reconcile, sync, and curation checks;
- keep application explicit and reviewable.

### Phase 7: Scoped aliases, graph alignment, and GraphRAG

- add evidence-backed scoped abbreviation extraction;
- add `qlkg-alignments-v1` and reviewed mapping reconciliation;
- generate multi-lane alignment candidates without similarity-based merging;
- invalidate reviewed identity decisions when endpoint fingerprints change;
- add provider-neutral embedding, reranking, and candidate-analyzer interfaces;
- add disposable similarity edges and weighted PPR retrieval;
- expose alignment and PPR through CLI and read-only MCP;
- integrate alignment reports into paper comparison and proposal generation.

### Phase 8: Transactional ingest

- define and package `qlkg-ingest-request-v1` and
  `qlkg-ingest-receipt-v1` JSON Schemas;
- expose matching Python APIs and `ingest plan` / `ingest apply` commands;
- validate optimistic graph, alignment, source, candidate, and query
  preconditions;
- stage source patches, delta application, synchronization, curation, and
  global validation before installation;
- serialize writers, journal installation, recover interrupted transactions,
  and make canonical requests idempotent;
- rebuild the disposable index only after committed graph installation;
- test stale requests, lock conflicts, every installation failure stage, and
  real process termination/recovery.

The stable candidate builder shipped with this phase accepts
`qlkg-candidate-graph-v1` and emits a validated
`qlkg-agent-snapshot-v1`, so host Skills do not reproduce snapshot envelope or
digest logic.

## 19. Phase 1 acceptance criteria

Phase 1 is complete when:

- unchanged exports are byte-identical;
- the snapshot digest changes when a hydrated entry, edge evidence, reference,
  or node changes;
- every snapshot node, edge, and reference count matches its envelope;
- structured entries are present without reading entry shards separately;
- validation errors prevent export while warnings are retained;
- invalid namespaces and absent graphs fail clearly;
- stdout is clean machine-readable JSON;
- file output is atomic;
- existing qlkg graph artifacts remain unchanged;
- the complete unit test suite and package build pass.

## 20. Design influences

The architecture intentionally borrows ideas without adopting another
project's authority model:

- [Engraph](https://github.com/devwhodevs/engraph): local SQLite/FTS/vector
  operation, graph-expanded retrieval, MCP, and token-budget context bundles;
- [DataStax Graph RAG](https://github.com/datastax/graph-rag): framework-neutral
  traversal, edge, strategy, and vector-store adapter boundaries;
- [HippoRAG](https://github.com/OSU-NLP-Group/HippoRAG): associative retrieval
  through Personalized PageRank;
- [Graphiti](https://github.com/getzep/graphiti): hybrid keyword/semantic/graph
  retrieval, provenance, and graph-distance reranking;
- [SSSOM](https://mapping-commons.github.io/sssom/): explicit mapping
  predicates, justification, evidence, and review metadata;
- [DeepOnto](https://github.com/KRR-Oxford/DeepOnto): lexical, semantic, and
  structural evidence for ontology alignment;
- [LightRAG](https://github.com/HKUDS/LightRAG): local/global/mixed retrieval
  modes, custom graph import, and explicit context budgets;
- [Cognee](https://github.com/topoteretes/cognee): Agent-facing MCP and pluggable
  graph/vector persistence.

These projects are retrieval and interface references. None may override
kgdistiller's explicit marker identity, evidence, or authority rules.
