# Text, model, and graph retrieval

The deterministic core ranks candidates over the reviewed entries and accepted
edges. Model inference is optional and belongs to adapters. None of these
scores creates an identity, alias, knowledge node, or semantic edge. Every
entry and every accepted edge is retrievable: an entry that `check` reports as
moved, stale or ambiguous is never filtered out.

For exact enumeration of authored sense groups and head-term uses, the read-only
[compiled inventory](compiled-retrieval.md) returns complete entries without
ranking or a result limit. Its scope is compiled declarations, and source-corpus
completeness is not certified.

## BM25 text entry

`kgdistiller agent search` uses BM25 with fixed `k1=1.2`, `b=0.75` over three
fields of each entry: the label (weight 3), the aliases (weight 2), and the
body (weight 1): kind, Summary, Context, Role, Prerequisites, Pending
prerequisites, Common confusions, Open questions and the Evidence quote.
Identical strings are indexed once. Paths and line numbers are not indexed.

Tokenization is shared with the compiled library (`kgdistiller.tokens`): text is
NFKC-normalized and casefolded, split into Unicode words (compound hyphens and
apostrophes split), and every CJK run yields each character and each adjacent
character pair. No domain lexicon is used, so a query for `测度` or `度论`
matches an entry containing `测度论`, while ASCII words behave as plain words.
Query terms are bounded at 128; document text is not truncated. Identity
resolution (`agent resolve`) is separate: it matches ids, labels and aliases
exactly after NFKC/casefold/whitespace normalization.

## Local model adapter

Install the optional extra:

```sh
python -m pip install 'kgdistiller[retrieval]'
kgdistiller --repo-root VAULT agent search 'QUESTION' --embedding
kgdistiller --repo-root VAULT agent search 'QUESTION' --embedding --rerank
kgdistiller --repo-root VAULT agent context 'QUESTION' --embedding --rerank --budget 6000
kgdistiller --repo-root VAULT mcp --embedding --rerank --models-offline
```

Source checkouts may use `pip install -e '.[retrieval]'`. Plain search, status,
identity tools and `--help` do not import/load model inference. The optional
dependency is Sentence Transformers 6.1.0; no training extra, FAISS, service or
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

- `--model-device cpu|mps|cuda`: defaults to CPU. An unavailable requested device is
  an error; no transparent CPU fallback.
- `--model-batch-size N`: defaults to 4, bounded 1–64.
- `--model-max-length N`: defaults to 8192, bounded 1–8192 and checked against model
  support. Documents and query/document pairs are token-counted before inference;
  excessive input fails instead of silently truncating a late condition.
- `--embedding-model`, `--embedding-revision`, `--reranker-model`,
  `--reranker-revision`: the local Hub adapter requires explicit 40-character
  commit revisions; mutable branch/tag names are rejected.
- `--rerank-candidates N`: defaults to 50, bounded 1–500. Requires both embedding and
  reranker selection; unused model options are rejected.
- `--model-cache-dir PATH`: defaults to `.knowledge/build/retrieval`; it must be
  a real directory outside `.knowledge/entries/`.

Embedding uses the complete entry projection (`kgdistiller-search-document-v1`:
the label, aliases and the same body fields as BM25, including Evidence) and the
original `plan.question`,
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
`kgdistiller-search-result-v2`; it records the embedding/reranker lanes, model
descriptors, effective inference settings, projection version, document count,
cache status and reranker candidates. Context records the actual
execution/result schema. Do not describe model scores as lexical scores or add
undeclared fields to v1.

The provider-neutral `SemanticRankingService` accepts an adapter with
`metadata(kind)`, `encode_documents`, `encode_queries` and, for reranking,
`score_pairs`. Core checks count, dimensions, finite non-boolean numbers and
nonzero vector norms. Unknown/duplicate IDs, malformed caches and model
failures are explicit errors. If the model descriptor changes while the model
is running, the request fails with `model-descriptor-changed` instead of mixing
inputs.

The document vector cache keeps one file per embedding model,
`<model-cache-dir>/vectors-<model name slug>.json`:

```json
{
  "schema": "kgdistiller-vector-cache-v1",
  "model": {"provider": "...", "model": "BAAI/bge-m3", "revision": "...", "inference": {}},
  "records": {"measure-space": {"text": "Name: Measure space\nAliases: ...", "vector": [0.01]}}
}
```

Records are keyed by entry id and store the exact projected text that was
embedded. A different model descriptor discards every record. An entry is
re-embedded when its current projection differs from the stored text or it has
no record; records of vanished ids are dropped; the file is rewritten
atomically only when something changed, and is bounded at 256 MiB. There is no
query or rerank persistence. The cache is derived, never authority, and can be
deleted at any time; only re-embedding costs time. Changing reranker settings
does not invalidate document vectors. MCP reuses its selected service within
the existing process; it introduces no separate daemon. Explicit model failures
never silently switch to BM25.

Adapters must produce each value from its corresponding input and declared
settings; any corpus-dependent preprocessing state must be part of the
descriptor. Document and query encoding are distinct operations. Duplicate
inputs are inferred once. Scores remain relevance values, never truth scores.

## Opt-in graph exploration

```sh
kgdistiller --repo-root VAULT agent search 'QUESTION' --graph-retrieval
kgdistiller --repo-root VAULT agent context 'QUESTION' --graph-retrieval --budget 24000
kgdistiller --repo-root VAULT agent search 'QUESTION' --embedding --rerank --graph-retrieval
```

`--graph-seed-candidates N` selects the first N filtered text/embedding RRF
candidates, default 5, bounded 1–32. These are navigation hypotheses with
`identity_authority: false`; they never create aliases, change identity matches,
or rewrite the input plan. Explicit and resolved identity seeds remain separately
recorded. Graph traversal contributes support paths, not query relevance scores.
V3 answer ranking and its reranker pool use identity/text/embedding evidence;
graph neighbors remain separately visible as bounded navigation candidates.
Pure graph-only requests return navigation rows with relevance score 0.

`--graph-edge-policy high-confidence|all` defaults to high-confidence, which
admits edges that declare `confidence: high` and carry nonempty evidence. This
is a declaration gate, not an independent scientific audit. `all` admits every
accepted edge for exploratory navigation. Edge freshness is never a gate.
Unused graph policy controls are rejected without `--graph-retrieval`.

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
BFS and PPR use the same permitted, depth-bounded graph. PPR runs at most 256
iterations and reports its final L1 residual; nonconverged scores are discarded
from navigation ordering with a degraded-lane diagnostic. Disconnected components do not
consume PPR iteration work. `contrasts-with` traverses symmetrically while paths
retain the authored edge direction.

Opt-in execution uses `kgdistiller-search-execution-v3` and result v3. Provenance
records the policy, separate seed origins, convergence and full typed path
evidence. Input plan v1 and ordinary v1/v2 execution remain supported. MCP exposes the equivalent per-call
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

When a stored execution is turned into context, every referenced entry and
edge is checked against the current store: a missing entry, or an edge whose
confidence or evidence text changed, fails with `stale-execution`.

## Evaluation

The primary metric is complete-evidence task success: the fraction of declared
tasks whose actual returned evidence jointly supports every necessary fact,
condition, intended sense and source scope, without unsupported scientific
assertions. Its theoretical ceiling is 100%, independent of a fixed ranking
cutoff. Check source availability and evidence-budget feasibility before
freezing a benchmark. Keep failed tasks in the declared denominator; report
unsupported requests, ambiguity and source limitations explicitly rather than
silently dropping or relabelling cases after evaluation.

Derive and freeze necessary facts and conditions from the question and original
sources before inspecting the selected packet or system answer. Source-selection
hints do not establish an exhaustive source scope. Alternative witnesses may
satisfy the same requirement only when meaning, conditions, source scope, units
and conventions agree. Node references, declared graph links and storage
metadata do not establish scientific coverage or definition equivalence.
A correctly stated scientific limitation can satisfy a request for qualifications;
unsettled support actually required by the question remains an evidence gap.

Use development cases for iteration and keep heldout questions and requirements
out of compilation and tuning. Report ranking, candidate coverage, packing gaps,
answer correctness, latency and model cost separately. Negative controls and
false abstention remain separate checks. Whole entries and complete declared
inventory membership do not by themselves certify question-level completeness.
Keep sources, questions, relevant units and mappings fixed across comparisons.

Preserve the historical Oct 4 twenty-paper P@5 report and its original denominator:
its 1–4 relevant units per positive query give a fixed-set ceiling of
115/(42*5)=54.76%. This is a ranking diagnostic; use complete-evidence task
success to evaluate whether the required evidence was actually delivered.
