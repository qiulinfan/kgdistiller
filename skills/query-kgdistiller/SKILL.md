---
name: query-kgdistiller
description: Query a kgdistiller external-brain index without reading or mutating its graph files. Use when an Agent must batch-resolve concept names and aliases, retrieve a bounded source-backed neighborhood, align a candidate note or paper graph, classify concepts as known, partial, new, conflicting, or uncertain, or connect an isolated graph to a personal graph before deciding which entries or refs to author.
---

# Query kgdistiller

Treat kgdistiller as an opaque external brain. Return a small evidence-backed
result; never load the complete personal graph into model context.

## Keep the boundary read-only

- Never open `knowledge/graph/*.jsonl`, entry shards, or the SQLite database.
- Never edit an authority, global alias, alignment registry, or graph artifact.
- Never run `apply`, `sync`, `reconcile`, or another mutating command.
- Never promote lexical, acronym, embedding, or graph similarity into identity.
- Preserve candidate and personal namespaces. A bridge is not a merge.

Use the read-only MCP tools when available. Otherwise use the corresponding
`kgdistiller --repo-root PROJECT agent ...` CLI commands and consume their JSON
output. Do not depend on a host wrapper, vendored checkout, or repository-local
Python path.

Start with `kg_status` or `agent status`. Require `qlkg-agent-index-v2`,
`qlkg-agent-snapshot-v1`, `qlkg-alignments-v1`, and the
`transactional-ingest-v1` capability when a later write is possible. Record the
target `snapshot_sha256`, `graph_sha256`, and `alignment_sha256` in the handoff
so a later writer can reject a stale decision.

## Plan each retrieval lane

For a question that needs retrieval rather than exact batch resolution, create
one bounded `qlkg-retrieval-plan-v1` artifact. Keep channel intent separate:

- put only canonical names and explicit aliases in `identity_queries`;
- put concise discriminating terms in `lexical_queries`;
- put full semantic conditions in `semantic_queries`;
- add graph seeds only after identity is established;
- bound query counts, result limit, edge types, graph depth, and node filters.

Execute the plan once with `kg_search`, `kg_build_context`, or:

```sh
kgdistiller --repo-root PROJECT agent search --plan RETRIEVAL_PLAN.json
```

Return every lane's `enabled`, `disabled`, `degraded`, or `error` state and
stable reason. A disabled semantic lane must remain visible and must not block
valid identity, lexical, or graph results.

## Accept one bounded handoff

Accept either:

1. a batch of candidate names with paper/note-local aliases and short source
   evidence; or
2. one valid `qlkg-agent-snapshot-v1` in an isolated namespace such as
   `paper:<digest>`.

Prefer passing an ignored local artifact path over pasting a full candidate
snapshot into the conversation. Reject a candidate snapshot in the `personal`
namespace.

## Resolve a concept batch

Resolve every candidate in one batch with `kg_resolve_concepts` or:

```sh
kgdistiller --repo-root PROJECT agent resolve "Concept A" "Concept B"
```

Use bounded `kg_search`, `kg_get_node`, `kg_expand`, or `kg_build_context` only
for missing, partial, ambiguous, or conflicting cases. Do not issue one broad
context query per obvious exact match.

Interpret evidence conservatively:

- a machine ID, canonical label, collision-free global alias, or fresh reviewed
  exact mapping may establish identity;
- a scoped abbreviation may retrieve a sense but cannot establish identity;
- lexical, acronym, embedding, PPR, and neighboring-edge signals only rank
  review candidates;
- a failed exact lookup is not enough to call a concept new when related
  candidates remain;
- multiple plausible senses stay uncertain even when one ranks first.

## Compare an isolated graph

For a note or paper candidate snapshot, run alignment and comparison once:

```sh
kgdistiller --repo-root PROJECT agent align CANDIDATE \
  --output knowledge/build/reviews/NAME.alignment.json
kgdistiller --repo-root PROJECT agent compare CANDIDATE
```

The alignment report is evidence, not a write request. Do not reconcile a
mapping in this Skill. Return every unresolved candidate to the caller.

Use the comparison statuses as follows:

- `known`: return the personal node handle and an exact bridge; the caller uses
  a ref and does not create another entry;
- `partial`: return the existing node plus the precise missing condition,
  relation, role, or claim;
- `new`: return no personal identity and retain the source-backed candidate;
- `conflict`: return both incompatible claims and their provenance;
- `uncertain`: return ranked candidates and the non-authoritative signals that
  produced them.

Never downgrade `conflict` or `uncertain` merely to keep an automated workflow
moving.

## Return a compact handoff

Return the target graph and snapshot digests, then one record per candidate
containing:

- candidate namespace and ID or source-local name;
- status;
- matched personal node handle when established;
- mapping predicate and identity-authoritative evidence;
- missing or conflicting material;
- bounded provenance and retrieval reasons;
- caller action: `ref`, `author-new`, `author-missing-part`, or `review`.

For a paper, keep known candidates in its learning graph only as paper-local
roles and bridge endpoints. Do not copy the personal entry into the paper
artifact and do not ask the paper extractor to explain it again.

Report which MCP/CLI operations were used, result counts, ambiguities, omitted
context, and the target digests. Make no repository changes.
