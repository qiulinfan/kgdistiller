---
name: query-kgdistiller
description: Query a kgdistiller external-brain graph without reading or mutating its graph files. Use when an Agent must batch-resolve canonical names and aliases, run deterministic lexical or graph retrieval, retrieve a bounded source-backed neighborhood, align a candidate note or paper graph, classify candidate identity as matched, ambiguous, or unmatched, or connect an isolated graph to a personal graph before authoring, including when the user asks to search or recall concepts from their kgdistiller (kgd/kgdt) knowledge base.
---

# Query kgdistiller

Treat kgdistiller as an opaque, read-only external brain. Return a bounded,
evidence-backed result; never load the complete personal graph into model
context.

## Align language

Match user-facing explanations, prompts, and handoffs to the user's language
unless the user requests another language. Keep commands, identifiers, schema
keys and action codes, and raw errors unchanged.

## Keep the boundary read-only

- Never open `knowledge/graph/*.jsonl`, derived entry shards, or atomic entry
  Markdown; use the bounded query interface.
- Never edit an authority, identity/alignment registry, or graph artifact.
- Never run `apply`, `sync`, `reconcile`, `ingest`, or another writer.
- Never promote lexical, acronym, translation, or topology similarity into
  identity.
- Preserve candidate and personal namespaces. A bridge is not a merge.

Use the read-only MCP tools when available. Otherwise use
`kgdistiller --repo-root PROJECT agent ...` and consume its JSON output. The
public query layer loads one generation-checked in-memory `GraphView`; do not
reimplement graph loading or indexing in the Skill.

Start with `kg_status` or:

```sh
kgdistiller --repo-root PROJECT agent status
```

Require `kgdistiller-query-status-v1`, `kgdistiller-agent-snapshot-v1`, `kgdistiller-graph-v1`,
`kgdistiller-alignments-v1`, and the `json-memory`/`read-only-query-v3` capabilities.
Record `snapshot_sha256`, `graph_sha256`, and `alignment_sha256` so a later
transactional writer can reject a stale decision.

## Plan deterministic retrieval

For retrieval beyond exact batch resolution, create one bounded
`kgdistiller-retrieval-plan-v1`:

- put canonical names and explicit identity-authoritative aliases in `identity_queries`;
  for paper candidates these must retain the paper identifier and version;
- put concise discriminating terms, including source-language forms, in
  `lexical_queries`;
- add `graph.seed_ids` only after identity is established;
- bound query counts, result limit, edge types, graph depth, direction, node
  types, stale/orphaned inclusion, and strategy.

There is no semantic/vector lane. Never add `semantic_queries`; the v1 plan
contract rejects it.
Execute once with `kg_search`, `kg_build_context`, or:

```sh
kgdistiller --repo-root PROJECT agent search --plan RETRIEVAL_PLAN.json
```

Require `kgdistiller-search-execution-v1` with a validated
`kgdistiller-search-result-v1`. Preserve each lane's status and stable reason. Exact
identity evidence takes precedence over score fusion; lexical and graph signals
only rank review candidates.

## Accept one bounded handoff

Accept either a batch of candidate names with source-local aliases and concise
evidence, or one valid `kgdistiller-agent-snapshot-v1` in an isolated namespace such
as `paper:<digest>`. Prefer an ignored local artifact path over pasted graph
content. Reject a candidate snapshot in the `personal` namespace.

Resolve the whole batch with `kg_resolve_concepts` or:

```sh
kgdistiller --repo-root PROJECT agent resolve "Concept A" "Concept B"
```

Use bounded `kg_search`, `kg_get_node`, `kg_expand`, or `kg_build_context` only
for unmatched or ambiguous cases. Require `kgdistiller-context-bundle-v1` from context
packing. Do not issue a broad context query for every obvious exact match.

### Paper-scoped candidates

Separate two operations in paper work: aligning the paper's scoped identities,
and looking up an existing prerequisite that is used at a specific source step.
For prerequisite lookup, accept a batch of precise names plus required statements,
domains and conditions. Resolve ordinary canonical names/aliases in the target
graph, then inspect bounded content for applicability. This is a use of existing
knowledge, not a request to merge an unqualified paper node into that identity.
Do not reject such a lookup merely because its query is not paper-qualified.

Return the use site, required formulation, verified existing handle and relevant
source content, and whether the entry actually supplies the needed step. For
example, distinguish an Lp from an ell-p setting or a weak from a strong law of
large numbers when the source requires that distinction. If a broad theorem
contains the needed special case, record why its conditions apply. Name matches
with a different sense remain unresolved.

Under the user's convention, an accurately matched applicable entry is mastered;
the author should link it and skip its tutorial, keeping only the paper-specific
application. Never generalize this to an entire discipline. An unavailable query
is not an unmatched concept, and an unmatched concept is not proof the user lacks
the surrounding subject. Do not query generic subject names as substitutes for
actual prerequisite uses.

For paper handoffs, require a qualified label such as
`Identity shortcut (arXiv:1512.03385v1)`, an isolated namespace, a source-digest-prefixed ID, and
paper provenance including version and source digest. Route bare terms and
paper-local aliases to lexical retrieval, not identity resolution. If a legacy
handoff uses unqualified labels or bare IDs, return it to the paper author for scoped
regeneration; do not repair the candidate or establish bridges here.

Within the same paper scope, resolve an existing qualified identity normally.
Verify the full source digest when reusing an ID. A raw ID hit or an explicit
`target_id` pointing to another paper/generic entry also requires the review below;
neither is an exemption from paper scope.
Across papers, revisions, or against a generic entry, spelling and aliases are
retrieval evidence only. Inspect bounded definitions, operations, formulas and
defining conditions before accepting a reviewed equivalence bridge. A fresh
reviewed mapping must have that source-backed semantic evidence; an exact label
hit alone is insufficient. Keep the engine's raw statuses unchanged and report
the semantic review and permitted caller action separately. If provenance or
meaning is insufficient, use caller action `review` without an accepted bridge.

Even equivalent mechanisms retain their paper-specific identities and content.
A cross-paper bridge does not authorize replacing the paper entry with a generic
ref or merging it into another paper's entry. This paper-specific gate qualifies
the general identity rules below; it does not change the query API's semantics.

Identity-authoritative evidence is limited to a machine ID, canonical label,
collision-free reviewed global alias, or fresh reviewed exact mapping. A fresh
reviewed `different-from` or rejected `exact-match` decision suppresses every
non-reviewed probe for that target; ignore a negative mapping whose candidate
or target fingerprint is stale. Candidate aliases, scoped abbreviations, and
translated lexical forms may retrieve a sense but cannot alone establish
identity. Failed exact lookup is not enough to call a candidate unmatched while
plausible senses remain; multiple plausible senses stay ambiguous.

## Compare an isolated graph

```sh
kgdistiller --repo-root PROJECT agent align CANDIDATE \
  --output knowledge/build/reviews/NAME.alignment.json
kgdistiller --repo-root PROJECT agent compare CANDIDATE
```

Alignment is evidence, not a write request. Do not reconcile mappings here.
Require `kgdistiller-alignment-report-v1`, preserve its `alignment_sha256`, fresh
`rejected_target_ids`, and registry evidence, then require
`kgdistiller-graph-comparison-v1`. Interpret node statuses conservatively:

- `matched`: return the exact personal handle and identity evidence; for papers,
  apply the scope and semantic-review gate above before accepting a bridge/ref;
- `ambiguous`: return ranked candidates and non-authoritative reasons for review;
- `unmatched`: retain the source-backed candidate without a personal identity.

`kgdistiller-graph-comparison-v1` reports only those node states and
`present`/`missing` candidate edges. It does not diagnose partial entries,
missing roles, or conflicting claims. Never invent those conclusions from
retrieval evidence.

If requested, `kgdistiller-agent-proposal-v1` is a deterministic review package bound
to the comparison and alignment digests. Its `delta_ready` may be false; do not
treat its operations or empty `delta_preview` as an actionable write delta.

## Return a compact handoff

Return target graph/snapshot/alignment digests and one record per candidate:
namespace and ID/name, status, established personal handle, mapping predicate
and authoritative evidence, bounded provenance, retrieval reasons, rejected
targets, and caller action (`ref`, `author-new`, or `review`).

For papers, return the paper scope, raw engine status, semantic comparison evidence,
and bridge decision separately. Preserve the paper's own definition and mechanism
in the author handoff; never copy a personal entry over it. Report operations used,
result counts, ambiguity, omitted context, and target digests. Make no
repository changes.
