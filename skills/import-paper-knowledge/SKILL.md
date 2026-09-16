---
name: import-paper-knowledge
description: Explicit command only. Import explicitly selected methods and mechanisms from a validated paper graph into a registered kgdistiller research authority, preserving paper-qualified identity and definitions. Use query-kgdistiller and ingest-kgdistiller for stale-safe reviewed mutation only when the user authorizes import and selects candidate IDs and the target authority.
disable-model-invocation: true
---

Run only when the user explicitly invokes `$import-paper-knowledge` or `/import-paper-knowledge`.
Do not start this workflow from an ordinary paper-reading request or another paper command.

# Import selected paper knowledge

This is the authorization boundary between read-only paper federation and a
personal knowledge project. Never interpret a request to read, summarize,
distill, compare, or trace a paper as permission to import it.

## Align language

Match explanations and handoffs to the user's language; preserve commands,
identifiers and raw errors.

## Require an exact reviewed handoff

Require a validated LaTeX source package (`source/`, `source.json`, `link.txt`);
no PDF or evidence directory. Read [references/import-contract.md](references/import-contract.md). Require:

- a validated paper package and deterministic federated snapshot;
- the alignment response and exact personal graph, snapshot, and alignment
  digests it used;
- user-selected method/mechanism candidate IDs and their reviewed authoring actions;
- a registered Markdown, Typst, or LaTeX research authority destination;
- title, authors, version, arXiv URL from link.txt, and precise LaTeX source
  locations for every selected claim;
- an explicit decision for every conflict or uncertain candidate.

If the destination is not registered, propose a bounded source entry with
`knowledge_origin: research` and stop for review before changing the registry or
creating the authority. Do not select candidates on the user's behalf.

## Revalidate identity and author the research authority

Read `references/research-paper-contract.md` from the installed
`$distill-paper-knowledge` Skill to recheck method/mechanism admission and paper scope.
Concrete prerequisite definitions/theorems with an evidenced use are eligible;
reuse verified existing entries by ref, without copying their tutorials. Importing
a new prerequisite still requires its explicit selection. Reject result, protocol,
comparison or assessment nodes in legacy handoffs. Do
not silently import them or rewrite the old paper graph during import.

Run `$query-kgdistiller` again against the selected candidates. Reject stale
target digests. Preserve raw engine identity statuses separately from the
source-backed authoring decision. If the same paper-qualified identity already
exists with matching scope, use a native ref or add only the reviewed missing
material. Do not infer content completeness from `matched`.

For a selected mechanism not yet represented in this paper scope, write one
paper-qualified native authority marker and source-grounded entry. A name match
or even a reviewed equivalent mechanism from a different paper is not a duplicate
of this scoped entry. Preserve the paper's own definition, operations, conditions,
local terminology and source provenance. Keep any reviewed cross-paper bridges
separate and within the authorized transaction scope; do not add bare global
aliases or merge paper identities.

Add only direct mechanism relations supported by precise source evidence.
Experimental data and assessments remain unmarked paper notes. Do not copy the
full paper, figures, screenshots, or long table contents into the authority.

Unselected candidates remain outside the personal graph. Unresolved identity
conflicts block their own import; do not invent a new identity to evade a
possible duplicate within the same paper scope. Distinct paper scopes alone do
not constitute an identity conflict.

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
