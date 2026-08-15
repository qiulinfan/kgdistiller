# Performance protocol

kgdistiller 0.4 performs deterministic identity, lexical, and graph queries
against an in-memory `GraphView` loaded from one validated JSON generation. It
does not use SQLite, embeddings, vector scans, or an approximate-nearest-
neighbor service.

No repository measurement is a universal service-level objective. Results
depend on graph size and shape, entry text, source count, storage, Python,
memory, operating system, and query mix.

## What to measure

Use disposable synthetic authority fixtures only. For each run record:

- kgdistiller version and commit, Python/OS/architecture, CPU, and memory;
- authority, node, edge, reference, alias, and entry counts;
- full and no-op sync wall time and peak resident memory;
- cold `GraphView` load time and warm operation time separately;
- p50, p95, and maximum latency for batch resolve, lexical search, bounded
  expansion, PPR, and context construction;
- configured limits, graph depth, edge filters, result limit, and context
  budget;
- graph manifest and snapshot digests before and after every read-only run.

Read-only evidence is valid only when graph artifacts remain byte-identical and
each result reports one consistent snapshot/graph generation. Benchmark a
concurrent reader during transactional apply and require observations to bind
entirely to either the old or the new generation.

## Required workload classes

Cover at least:

1. exact canonical-name and reviewed-alias resolution, including ambiguous
   scoped aliases and cross-language Unicode normalization;
2. lexical queries over labels, reviewed aliases, node text, and curated entry
   fields;
3. bounded BFS/hybrid graph expansion and PPR with explicit seeds and edge
   types;
4. context packing at multiple budgets;
5. ingest plan/apply, stale-precondition rejection, fault injection, rollback,
   recovery, and concurrent readers;
6. `store snapshot`, `store verify`, cold clone verification, and immediate
   query without a materialization step;
7. native frontend and MCP smoke tests over the same generation.

Report measurements as machine-specific baselines. When a target is missed,
record the finding and workload instead of silently relaxing it. A future index
or retrieval architecture requires a separate explicit design and contract; it
must not be inferred from benchmark pressure.

Context `estimated_tokens` is a provider-neutral safety bound: the canonical
JSON UTF-8 byte count after its own estimate reaches a serialization fixed
point. It deliberately overestimates many provider tokenizers, especially for
ASCII, while avoiding the severe undercount that a Latin-oriented characters/
four heuristic creates for Chinese, Japanese, and Korean text.
