---
name: import-paper-knowledge
description: Import an explicitly selected subset of new or partial concepts from a validated federated paper graph into a registered kgdistiller research authority, preserving full paper provenance and using query-kgdistiller plus ingest-kgdistiller for stale-safe reviewed mutation. Use only when the user explicitly authorizes paper knowledge import and identifies candidate IDs and the target authority.
---

# Import selected paper knowledge

This is the authorization boundary between read-only paper federation and a
personal knowledge project. Never interpret a request to read, summarize,
distill, compare, or trace a paper as permission to import it.

## Require an exact reviewed handoff

Read [references/import-contract.md](references/import-contract.md). Require:

- a validated paper package and deterministic federated snapshot;
- the alignment response and exact personal graph, snapshot, and alignment
  digests it used;
- user-selected `new` or `partial` candidate IDs;
- a registered Markdown, Typst, or LaTeX research authority destination;
- title, authors, version, DOI/arXiv/URL when available, and precise paper
  locations for every selected claim;
- an explicit decision for every conflict or uncertain candidate.

If the destination is not registered, propose a bounded source entry with
`knowledge_origin: research` and stop for review before changing the registry or
creating the authority. Do not select candidates on the user's behalf.

## Revalidate identity and author the research authority

Run `$query-kgdistiller` again against the selected candidates. Reject stale
target digests. A formerly new candidate may now be known; use a native ref
instead of creating a duplicate identity.

Write refs for known concepts. For selected partial concepts, author only the
missing material. For selected new concepts, write one native authority marker
and a source-grounded entry. Add only direct relations supported by precise
paper evidence. Do not copy the full paper, figures, screenshots, or long table
contents into the authority.

Unselected candidates remain outside the personal graph. Conflict and uncertain
candidates block their own import and never become new identities by default.

## Plan and apply one transaction

Build the reviewed source patch, post-patch marker/ref state, candidate and query
digests, and `kgdistiller-agent-delta-v1`. Hand them to `$ingest-kgdistiller`. Review
the plan before apply and accept only a canonical committed receipt whose
after-digests match `agent status`.

Optionally produce a static host export after scoped and global checks pass. Git
commit, remote push, public visibility, and host adoption are separate actions
requiring their own authorization.

Return selected and skipped IDs, source provenance coverage, target authority,
query digests, transaction receipt, optional export receipt, and every blocked
decision.
