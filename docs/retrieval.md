# Text, model, and graph retrieval

The deterministic core ranks candidates and validates graph-generation bindings.
Model inference is optional and belongs to adapters. None of these scores creates
an identity, alias, knowledge node, or semantic edge.

For exact enumeration of authored sense groups and head-term uses, the read-only
[compiled inventory](compiled-retrieval.md) returns complete entries without
ranking or a result limit. Its scope is compiled declarations, and source-corpus
completeness is not certified.

## BM25 text entry

`kgdistiller agent search` uses BM25 with fixed `k1=1.2`, `b=0.75`. Scientific
`display_name` is used when supplied, otherwise the authored label. Names have
weight3, explicit global/paper-local/scoped alias text weight2, and complete
definition, conditions, supported entry text and paper key weight1. Identical
strings are deduplicated. Machine provenance paths and hashes are not indexed.

Unicode word tokenization splits compound hyphens/apostrophes. Query terms remain
bounded at128; document text has no query-term truncation. Identity normalization
and exact/alias resolution are unchanged.

## Local model adapter

The optional extra needs Python3.10+:

```sh
python -m pip install 'kgdistiller[retrieval]'
kgdistiller --repo-root VAULT agent search 'QUESTION' --embedding
kgdistiller --repo-root VAULT agent search 'QUESTION' --embedding --rerank
kgdistiller --repo-root VAULT agent context 'QUESTION' --embedding --rerank --budget 6000
kgdistiller --repo-root VAULT mcp --embedding --rerank --models-offline
```

Source checkouts may use `pip install -e '.[retrieval]'`. Plain search, status,
identity tools and `--help` do not import/load model inference. The optional
dependency is Sentence Transformers6.1.0; no training extra, FAISS, service or
daemon is required.

Default models are fixed upstream revisions:

| Operation | Model | Revision | Upstream license |
|---|---|---|---|
| Embedding | `BAAI/bge-m3` | `5617a9f61b028005a4858fdac845db406aefb181` | MIT |
| Reranker | `BAAI/bge-reranker-v2-m3` | `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e` | Apache-2.0 |

Weights remain in the Hugging Face cache and are not shipped in this product.
Inference runs locally; loading uses `trust_remote_code=False`, `token=False`.
The explicit model mode permits downloading missing public weights unless
`--models-offline` is selected. Offline missing files fail explicitly.

Options shared by search/context and MCP launch:

- `--model-device cpu|mps|cuda`: defaultsCPU. An unavailable requested device is
  an error; no transparent CPU fallback.
- `--model-batch-size N`: defaults4, bounded1–64.
- `--model-max-length N`: defaults8192, bounded1–8192 and checked against model
  support. Documents and query/document pairs are token-counted before inference;
  excessive input fails instead of silently truncating a late condition.
- `--embedding-model`, `--embedding-revision`, `--reranker-model`,
  `--reranker-revision`: the local Hub adapter requires explicit40-character
  commit revisions; mutable branch/tag names are rejected.
- `--rerank-candidates N`: defaults50, bounded1–500. Requires both embedding and
  reranker selection; unused model options are rejected.
- `--model-cache-dir PATH`: defaults to `knowledge/build/retrieval` beside the
  selected graph, never inside the authority graph directory.

Embedding uses the complete source projection and original `plan.question`,
including Chinese text. BGE-M3 uses its revision-defined prompts and pooling,
normalized embeddings, float32 and cosine scoring. No v1.5 instruction prefix is
added. BM25 and embedding candidates are combined by the existing RRF. Embedding
matches do not automatically become authoritative identity matches. Graph
exploration uses them as candidate roots only when explicitly selected below.

Reranker uses `CrossEncoder` over the original question and complete candidate
projection. Its raw logits are relevance scores, not truth probabilities. The
fused pool is cut to the configured candidate count before applying the request's
result limit. Final ordering uses fixed RRF of the base pool rank and raw model
rank, `1/(60+base_rank)+1/(60+model_rank)`, declaring `rrf-base-reranker` in v2
provenance. Raw logits remain recorded separately. Exact/alias identity priority
remains ahead of model ordering.

## Contracts and cache

Input plans remain `kgdistiller-retrieval-plan-v1`. Plain execution/result remain
v1. Explicit model selection returns `kgdistiller-search-execution-v2` containing
`kgdistiller-search-result-v2`; it records embedding/reranker lanes, descriptors,
effective inference settings, question hash, node/document hashes, graph snapshot,
and vector-cache content hash. Context records the actual execution/result schema.
Do not describe model scores as lexical scores or add undeclared fields to v1.

The provider-neutral `SemanticRankingService` accepts an adapter with
`metadata(kind)`, `encode_documents`, `encode_queries` and, for reranking,
`score_pairs`. Core checks count, dimensions, finite non-boolean numbers and
nonzero vector norms. Unknown/duplicate IDs, changing descriptors or source
fingerprints, malformed/tampered caches and model failures are explicit errors.

The document universe respects current/stale/orphan/type filters before model
inference, not only when returning scores.

Vector caches use atomic files bound to namespace, snapshot and graph hashes,
projection version, ordered node fingerprints/document hashes, and complete
model revision/inference settings. They are derived and untrusted as authority.
Changing reranker settings does not invalidate unchanged embedding vectors.
MCP reuses its selected service within the existing process; it introduces no
separate daemon. Explicit model failures never silently switch to BM25.

Exact document/query vectors and cross-encoder pair scores are also stored under
the selected cache directory's `exact-inputs-v1`. Keys contain operation, full
immutable model/inference descriptor, projection version and exact UTF-8 input
hashes. They contain no identity inference. A changed graph generation is still
bound in a fresh generation manifest; unchanged input values can be reused while
current node/document fingerprints and source digests are revalidated.

Adapters must produce each value from its corresponding input and declared
settings; any corpus-dependent preprocessing state must be part of the immutable
descriptor. Document and query encoding are distinct operations. Duplicate
inputs are inferred once. Scores remain relevance values, never truth scores.

Service-local caches are bounded: generation matrices use at most4 entries and
64MiB, exact records at most16384 entries and16MiB, and source signatures at most8
entries. Reads still check file type and device/inode/size/mtime/ctime signatures;
changes trigger full digest/type validation. Canonical source snapshots are
checked at request start/end and before/after actual model inference. Tampered,
nonfinite, stale, symlinked or malformed inputs fail explicitly.

## Opt-in graph exploration

```sh
kgdistiller --repo-root VAULT agent search 'QUESTION' --graph-retrieval
kgdistiller --repo-root VAULT agent context 'QUESTION' --graph-retrieval --budget 24000
kgdistiller --repo-root VAULT agent search 'QUESTION' --embedding --rerank --graph-retrieval
```

`--graph-seed-candidates N` selects the first N filtered text/embedding RRF
candidates, default5, bounded1–32. These are navigation hypotheses with
`identity_authority: false`; they never create aliases, change identity matches,
or rewrite the input plan. Explicit and resolved identity seeds remain separately
recorded. Graph traversal contributes support paths, not query relevance scores.
V3 answer ranking and its reranker pool use identity/text/embedding evidence;
graph neighbors remain separately visible as bounded navigation candidates.
Pure graph-only requests return navigation rows with relevance score0.

`--graph-edge-policy high-confidence|current` defaults to high-confidence. The
default requires a fresh edge, declared `confidence: high`, and nonempty evidence.
This is a declaration gate, not an independent scientific audit. `current`
permits fresh unverified edges for visible exploratory navigation. Unused graph
policy controls are rejected without `--graph-retrieval`.

When source review warrants that declaration, author the literal `high` value.
Keep qualifications such as "source-explicit, not an independent proof" in the
edge evidence; a descriptive confidence string is preserved but does not pass
the high-confidence gate. Verify actual permitted-edge counts and returned
support paths after ingest. Enabling graph retrieval alone does not establish
that any edge contributed evidence.

Planned requests also select their relation types explicitly: an empty
`graph.edge_types` array selects no edges. To traverse semantic relations,
enumerate the permitted types in the plan as well as choosing a positive depth.
An exploration policy does not silently rewrite those plan filters.

Existing graph strategy, relation, direction and depth controls still apply.
BFS and PPR use the same permitted, depth-bounded graph. PPR runs at most256
iterations and reports its final L1 residual; nonconverged scores are discarded
from navigation ordering with a degraded-lane diagnostic. Disconnected components do not
consume PPR iteration work. `contrasts-with` traverses symmetrically while paths
retain the authored edge direction.

Opt-in execution uses `kgdistiller-search-execution-v3` and result v3. Provenance
binds policy, separate seed origins, source document/node fingerprints, graph
generation, convergence and full typed path evidence. Input plan v1 and ordinary
v1/v2 execution remain supported. MCP exposes the equivalent per-call
`graph_retrieval`, `graph_seed_candidates`, and `graph_edge_policy` controls on
`kg_search` and `kg_build_context`.

Graph context uses `kgdistiller-context-bundle-v2`. Each support packet includes
its root, complete path nodes, node conditions and edge evidence as one budgeted
unit. Support neighbors are distinct from ranked answers. A packet that cannot
fit produces an explicit gap/omission count. Complete path packets contain
every endpoint and edge source. Direct-source packets make
no relationship-closure claim. Learning prerequisites, derivation and comparison
are navigation purposes, not logical entailment. This mode does not certify all
necessary premises for a theorem or research claim. The historical `--budget`
and `estimated_tokens` names denote a conservative canonical UTF-8 byte estimate,
not a model tokenizer count. Large source records and full multi-node paths
require a larger budget; definitions and conditions are never truncated to fit.

`agent context --context-projection compact` (MCP `context_projection: compact`)
returns `kgdistiller-context-bundle-v3`. Plain v1/v2 searches become direct-source
packets; graph v3 retains its validated typed support routes. Original node and
document hashes remain separate from the projection digest. Nodes and complete
edge evidence are stored once; route steps reference node IDs and edge indexes.
Unknown semantic fields, full definitions, conditions and provenance remain.
Only known bookkeeping hashes and byte-identical repeated entry content are
omitted. `full` remains the default with its previous schema.

Standalone compact validation proves projection integrity and closure. Verify
against the original GraphView to confirm omitted-source correspondence; a
projection hash does not certify scientific truth. Both modes atomically omit a
support packet that cannot fit and report its gap.

## Explicit query support

An agent can supply a `kgdistiller-support-selection-v1` manifest when a bounded
query plan selects additional existing source nodes. The helper
`make_support_selection(view, execution, plan, items)` binds the manifest to the
namespace, graph generation, question, plan and exact node/document fingerprints.
Each item includes a node ID, requirement IDs and a reason. Optional producer
trace hashes identify the selection run; they do not prove relevance or truth.

```sh
kgdistiller --repo-root VAULT agent context 'QUESTION' --support-selection selection.json --budget 24000
```

MCP `kg_build_context` accepts the equivalent `support_selection` object. The
manifest permits at most32 unique, currently eligible nodes and128KiB of JSON.
Stale bindings, changed sources and malformed manifests fail explicitly.
Selection is marked `identity_authority: false`; it changes neither graph
identity nor answer ranking. Requirement IDs describe caller intent, not measured
coverage. These direct-source packets precede ordinary direct-source packets;
they may consume the budget and omit another answer source. Use the reported
gaps to assess that tradeoff.

Supplying a manifest returns context bundle v3 with either `full` or `compact`
node projection. Full projection retains the original node content. The bundle
binds the manifest hash and retains each selected packet's origin and requirement
IDs. Calls without a manifest retain their existing default schemas.

## Raw source evidence

`agent evidence` and MCP `kg_search_source_evidence` retrieve original UTF-8 text
without requiring a graph. They use an explicitly supplied
`kgdistiller-source-evidence-manifest-v1` containing a source root (absolute or
relative to the manifest) and
document IDs, relative POSIX paths, expected hashes, source URLs and versions.
An optional `normalized-utf8` hash mode normalizes CRLF/CR to LF for verification;
returned offsets always refer to the unchanged raw source bytes.

```sh
kgdistiller agent evidence 'QUESTION' --manifest sources.json --doc-id PAPER --limit 10 --budget 24000
```

MCP accepts `query`, absolute `manifest_path`, optional `doc_ids`, `limit`, and
`byte_budget`. Paths cannot traverse the manifest root or use symlinks; derived
graph files are not original evidence. Reads are bounded, and source or manifest
changes invalidate an existing index. Sources are never modified.

The in-memory index shares the BM25 tokenizer and constants, with heading text
weight2. Structural paragraphs, tables and algorithms retain exact text, byte
spans and source/version/content hashes. Large blocks are split on line or UTF-8
boundaries and marked partial. Results omit complete fragments that cannot fit
the byte budget and report those omissions. No inference model is needed.

Every fragment declares `source_kind: raw-source-evidence`,
`identity_authority: false` and `complete_definition: false`. Fragment IDs identify
source spans, not canonical knowledge nodes. Headings supply navigation metadata;
they do not define concepts, aliases or semantic relations. A retrieved fragment
can support an answer, but adding it to the graph still requires source review
and transactional ingest.

`agent evidence --context-projection compact` (MCP
`kg_search_source_evidence` with `context_projection: compact`) returns
`kgdistiller-source-evidence-context-v1`. It searches the requested candidate
limit with a bounded200000-byte intermediate result, then packs under the user's
response byte budget. Sources and headings are shared once; fragment text, raw
spans, conditions and source/projection hashes remain intact. Numeric heading
references resolve within the bundle. Endpoints check the original files again
after packing. The default `full` result contract remains unchanged.

The provider-neutral `build_source_context(results, byte_budget=24000,
selected_fragment_ids=None)` also accepts1–32 real result objects with at most
4MiB of canonical input. Inputs must share the manifest generation and source
projection. The default retains their first-seen candidate order; an explicit
unique ID list selects a subset and its packing order. Selection is a caller
hypothesis, never measured claim coverage. Query summaries bind the complete
original result hashes; packed fragments retain each query's original rank and
score separately. Scores are never presented as a cross-query BM25 ordering.

Whole fragments that do not fit are omitted. Counts cover all unselected and
budget-omitted candidates; detailed omission IDs use only remaining space, with
`diagnostics_truncated` when necessary. Keep original results outside the model
context for complete candidate traces. `validate_source_context` checks internal
hash, span, reference, origin-count and byte-budget closure. Supplying original
`source_results` additionally verifies exact deterministic replay. These checks
do not certify relevance, scientific truth, or current-file correspondence;
current correspondence requires an index/file check, as the CLI/MCP perform.

Source document selectors are resolved separately from knowledge identity:

```sh
kgdistiller agent evidence-resolve 'Adam v8' --manifest sources.json
```

MCP exposes `kg_resolve_source_references` with absolute `manifest_path` and a
bounded `references` array. The provider-neutral
`resolve_source_references(index, hints)` matches normalized registered document
IDs, literal declared full versions, or a registered document ID followed by its
literal full version or trailing `vN` suffix. Space/tab, `-`, and `@` separators
are supported. A request for `Adam v9` cannot match a document declared as
`1412.6980v8`. Titles, method names, keywords and undeclared aliases are not
inferred. Multiple declared matches remain ambiguous.

Results use `kgdistiller-source-reference-result-v1` and bind manifest, hint and
result digests. `matched_doc_ids` includes only unique matches, in first-seen
order. Matches are source-document references with `identity_authority: false`,
not canonical concept identities or scientific conclusions. The API checks
current source files before and after resolution. Standalone validation checks
declaration/selector/digest closure; an index additionally proves exact current
manifest correspondence. Callers decide whether to search matched documents,
global candidates, or both; resolution does not rewrite search filters silently.

## Evaluation

Keep original sources, question IDs, relevant units and mappings fixed during
ranking comparison. Separate document truncation, field/tokenization projection,
BM25 scoring, embedding fusion and reranking; do not tune with heldout gold.
Candidate ranking and packed evidence completeness require separate metrics.

The Oct4 twenty-paper set has1–4 relevant units per positive query, so its fixed
P@5 has a ceiling115/(42*5)=54.76%. Retain its historical scoring rather than
claiming95% by redefining the denominator. Report Recall@5 and complete support
packets, including missing/partial units and negative controls.
