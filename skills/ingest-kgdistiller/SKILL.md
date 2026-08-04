---
name: ingest-kgdistiller
description: Apply a reviewed, source-backed knowledge update to a kgdistiller project and return a validation receipt. Use after query-kgdistiller or another extractor has already decided identities, authority markers, refs, entries, aliases, and direct semantic edges; when changed Markdown, Typst, or LaTeX knowledge must enter the personal graph; when an explicitly selected paper concept must be imported with provenance; or when a reviewed cross-namespace alignment must be persisted.
---

# Ingest into kgdistiller

Be the only Skill that mutates the personal knowledge base. Execute an already
reviewed write decision; do not rediscover concepts or repeat graph retrieval.

## Require a complete handoff

Before writing, require:

- the exact authority file scope and its unique source registration;
- the reviewed format-native source edit or its already-applied diff;
- the query target `snapshot_sha256` and `graph_sha256`;
- a decision for every candidate: reuse, add, update, reject, or defer;
- a reviewed `qlkg-agent-delta-v2`, or a candidate snapshot from which
  `agent propose` can create one;
- source evidence for every entry and semantic edge;
- explicit user authorization for paper import or alignment persistence.

Reject unresolved `uncertain` or `conflict` identities. Do not convert them into
new nodes. Reject a write based on a stale target digest and send it back to
`$query-kgdistiller`.

Read `docs/graph-contract.md` completely before the first write in a task. In
qlblog use `vendor/kgdistiller/docs/graph-contract.md` and the command
`python3 knowledge/kgd.py`; elsewhere use
`kgdistiller --repo-root PROJECT`.

## Keep semantic authorship upstream

The caller owns concept discovery and the semantic source patch. Apply only the
exact reviewed marker/ref changes supplied by the caller. Preserve unrelated
prose and user-authored markers.

This Skill may validate and commit:

- a native authority marker or ref;
- a source-grounded entry or structured research dossier;
- a reviewed alias on an existing node;
- a direct typed edge with confidence and evidence;
- an explicit removal or identity reconciliation;
- a reviewed fingerprint-bound cross-namespace mapping.

It must not invent any of them from headings, co-occurrence, document order,
similarity, or an unreviewed proposal.

## Select the write mode

- **Changed note:** normal ingestion is authorized by a request to update or
  publish that note. Known concepts become refs; only new identities receive
  entries.
- **Paper snapshot:** do nothing by default. Import only explicitly selected
  `new` or missing `partial` knowledge into a registered research authority.
  Known paper concepts remain refs.
- **Alignment only:** persist a reviewed or rejected mapping only when evidence
  and justification are supplied. This does not merge either graph.
- **Rename/removal:** require an explicit identity decision; never infer one
  from a Git move or matching text.

## Apply the guarded workflow

1. Run `agent status` and compare both target digests with the query handoff.
2. Confirm every authority matches exactly one bounded source glob.
3. Apply or verify the reviewed source patch, then run scoped `scan`.
4. For new source markers, run scoped `sync` once to materialize their stable
   IDs before generating the final proposal.
5. When a candidate snapshot is present, run `agent propose` with the exact
   target authority and inspect both `qlkg-agent-proposal-v1` and the generated
   delta. Never apply conflict or review operations.
6. Apply the reviewed delta, synchronize the same scope, and run file-level
   curation plus the global graph check:

   ```sh
   python3 knowledge/kgd.py apply REVIEWED_DELTA
   python3 knowledge/kgd.py sync --file AUTHORITY
   python3 knowledge/kgd.py curate-check --file AUTHORITY
   python3 knowledge/kgd.py check
   ```

7. Rebuild or inspect the disposable Agent index with `agent status` and record
   the resulting digests.

If the write contains refs only and no delta, skip `apply` but still run scoped
`sync`, `curate-check`, and `check`. If any command fails, stop; never weaken a
validator, delete evidence, or add a contextless ref to obtain a green result.

For a reviewed mapping, use `reconcile alignment` with the candidate snapshot,
both node IDs, predicate, evidence, and justification, then verify `agent
status`. Paper-local abbreviations never become global aliases.

## Return the receipt

Return a compact ingestion receipt containing:

- engine/index/schema versions;
- authority paths and source hashes;
- before and after graph/snapshot digests;
- nodes added, reused, updated, orphaned, or removed;
- refs, entries, aliases, alignments, and edges changed;
- every validation command and result;
- warnings and any unapplied review operation.

Use the shape planned as `qlkg-ingest-receipt-v1` even while the compatibility
workflow consists of the guarded commands above. Do not read the whole graph to
construct the receipt; use command outputs and scoped diffs.
