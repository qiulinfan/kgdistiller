# Performance and resource protocol

kgdistiller uses validated JSON generations, deterministic in-memory indexes,
and bounded source/blob reads. It has no SQLite, embedding model, vector scan,
approximate-nearest-neighbor service, CDN, or remote API dependency.

No repository measurement is a universal service-level objective. Report
machine, Python/Node versions, operating system/filesystem, Vault/graph/ledger
shape, configured limits, cold/warm state, and exact generation tokens with
every result.

## Workload inventory

Use disposable synthetic Vaults only. Record:

- registered, healthy, missing, and incomplete Vault counts;
- native note, field/topic, graph node/edge/reference/alias/entry counts;
- source document/version/derivation/blob and durable receipt counts/bytes;
- full/no-op native compile and `knowledge check` wall time/peak memory;
- cold Vault graph hydration and warm per-generation index construction;
- federated status/roots/children/resolve/search/get/expand/context latency;
- source capture/status/diff and predecessor-chain depth;
- native ingest plan/apply/idempotency/stale/fault/recovery latency;
- in-place and external store snapshot/verify/clone latency and bytes;
- `/api/v1` request latency, snapshot single-flight contention, cache weight,
  304 rate, and active-handler saturation;
- frontend boot, route transition, list/SVG layout, and bundle bytes.

Always distinguish capture/compile time from query time and cold index creation
from reuse of the same generation.

## Required workload classes

Cover at least:

1. two healthy Vaults plus one explicitly missing Vault;
2. exact canonical/reviewed-alias resolution, ambiguity, Unicode/RTL, and equal
   local IDs in different Vaults;
3. multi-parent taxonomy roots/children and scoped lexical/graph retrieval;
4. bounded 1/2-hop expansion and context packing with omissions;
5. source first capture, unchanged capture, newline-only carry-forward,
   semantic change, predecessor diff, and long derivation chain rejection;
6. native plan/apply, concurrent old/new readers, idempotency, stale bases,
   lock conflict, fault injection, rollback, and recovery;
7. store in-place refresh, external copy, cold pure verify, clone/add/query, and
   all durable receipt/reference integrity;
8. API generation 428/409 recovery, ETag/304, aborted/late response rejection,
   source history/diff/excerpt caps, and handler overload 503;
9. deterministic frontend graph/list fallback, partial/truncation UI, and two
   clean production builds with identical bytes.

## Coherence evidence

Read-only evidence is valid only when each result binds one federation and
per-Vault generation and no controlled byte changes during the request. A
concurrent ingest benchmark must observe entirely the old or entirely the new
note/ledger/graph generation.

The service retains one ready federation snapshot; stale-index reuse is keyed
to the exact target Vault generation and conservatively weight-bounded. Capture
failure clears ready/derived state rather than serving an old snapshot. Count
temporary construction memory and parsed-object overhead, not only serialized
key bytes.

## Resource boundaries

Patch production limits downward in unit fixtures to exercise boundaries
without allocating maximum data. Test:

- file/path/depth/count/byte caps before unbounded sorting or materialization;
- source text/diff/excerpt input and output caps before expensive diff work;
- recall candidate, lane, evidence, omission, expansion, and context budgets;
- portable store normalized and actual raw aggregate bytes, including
  `store.json`, receipts, paths, files, and directories;
- API response bytes, cache weight/eviction, stale-index temporary/retained
  memory, request single-flight timeout, and fixed active handler slots;
- frontend response cache aggregate weight, taxonomy/neighborhood node/edge
  caps, client omissions, and Unicode-safe label truncation.

A bounded output does not by itself prove bounded computation. Measure scan,
sort, diff, parse, and temporary collection peaks.

## Token estimate

Recall context `estimated_tokens` is a provider-neutral safety bound: canonical
JSON UTF-8 bytes are recomputed until the estimate reaches its serialization
fixed point. It intentionally overestimates many tokenizers while avoiding the
severe undercount of Latin-oriented character heuristics for CJK text.

## Reporting

For each run report command, fixture seed/generator, limits, counts/bytes,
p50/p95/max where repeated, peak resident memory, generation/store/bundle
digests before and after, skips, and platform limitations. When a target is
missed, preserve the result and workload instead of relaxing a correctness or
resource contract. Any future index, storage engine, vector lane, or distributed
service requires a separate explicit design and immutable contract.
