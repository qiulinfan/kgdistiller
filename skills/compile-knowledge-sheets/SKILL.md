---
name: compile-knowledge-sheets
description: Create or refresh partial or complete source-scoped definition and pending link sheets to accepted knowledge metadata, and prepare reviewed metadata updates when needed. Use for requested def-sheet or pending-sheet views of mathematical notes, computer-science notes, papers, blogs, or project documents; ordinary source reading does not require this Skill.
---

# Compile knowledge sheets

Create source-scoped link views of the caller's canonical `.knowledge/` metadata.
Definitions, conditions, evidence, relations and applications belong in accepted
knowledge records. A definition sheet shows names, types, source locators and
links to those records; a pending sheet links their supported dependency-gap
state. Papers are one use case of this general workflow.

Match explanations, prompts and handoffs to the user's language unless another
language is requested. Retain technical names, identifiers, structured values,
formulas and raw errors.

## Establish the source and target

Read [references/sheet-contract.md](references/sheet-contract.md) before
creating or refreshing a view. Establish the caller's source files, source
version/locators, registered knowledge project, output location and requested
coverage. Respect existing filenames; otherwise use `def-sheet.md` and
`pending-sheet.md` beside the selected source or in its caller-selected folder.
Do not assume a paper corpus, paper-reading vault or prior sheet layout.

Run `kgdistiller --repo-root PROJECT scan --file RELATIVE_AUTHORITY` for each
selected source. Read its `sources[].document_type` and matching `document_types`
profile: `node_kinds` and `extraction_guidance` are user-registered extraction
rules, independent of `.md`/`.typ`/`.tex` and fields/topics. Do not impose a fixed
catalog of document classes. An unassigned source keeps the explicitly requested
scope until the user registers a profile. Read the native source directly;
source conversion is not a prerequisite for metadata or its link sheets.

Partial sheets are normal. Use the explicitly selected concepts or passages as
the scope; do not fill every missing row or initiate full-source distillation.
For a request to save one item while reading, use `$capture-kgdistiller`. Read
enough local context to preserve the complete meaning of selected knowledge. For a requested complete source extraction,
read substantive proofs, examples, appendices and experiments too. Full-source
distillation requires an explicit request. Report actual coverage and unavailable
material; a partial reading cannot claim completeness. Preserve unselected rows.
Full distillation is usually appropriate for the user's own notes or familiar
articles. New-article reading normally uses local `$capture-kgdistiller` updates.

Use `$query-kgdistiller` to resolve existing identities and accepted records.
Preserve native Markdown, Typst and LaTeX markers and atomic-entry authority.
A heading, theorem wrapper, sheet row or navigation reference does not establish
identity. Reclassification or removal of existing marked identities needs an
explicit review.

## Prepare metadata when needed

If the selected source needs new or changed metadata, read
[references/sync-contract.md](references/sync-contract.md). Prepare the complete
source-grounded proposal in `.knowledge/build/reviews/`, including meanings,
conditions, formal content, evidence, factual relations, applications and
unresolved decisions. Keep it distinct from committed metadata. `build/` is
excluded from Obsidian hidden-folder indexing by default; remove `build` from
the kgdistiller plugin's exclusion list to open drafts there.

Treat source coverage, available definitions and user understanding separately.
Read `entry.understanding` as `unknown`, `not-yet-understood` or `understood`;
absence means unknown. Preserve existing status unless the user states a change.
Finding a definition or finishing a paper never establishes understanding.
Store only direct gaps in `pending_prerequisites`, retaining the term, required
meaning and use context. When the user chooses to learn a pending concept, its
own entry may expose the next layer; do not recursively resolve the chain now.

Apply the registered profile's node kinds and extraction guidance. For example,
the shared model admits mathematical definitions, axioms and theorems, and
computer-science algorithms and architectures as knowledge nodes. Propositions
and remarks express relations; examples and experiments express typed
applications. The bundled sync contract provides the preservation rules.

Use `$curate-kgdistiller-notes`, `$query-kgdistiller` and
`$ingest-kgdistiller` within their current supported contracts. Compilation alone
does not authorize live writes; apply only the concrete reviewed content and
target already authorized by the user, or obtain confirmation for that scope.
Do not bootstrap or migrate a knowledge project or edit raw graph files.

Atomic entries support simple direct pending prerequisites and understanding.
Current `kgdistiller-agent-delta-v1` ingest does not support full n-ary/application records or rich
gap history. Keep unsupported proposals in the review area, report the exact adapter
gap and defer them. Do not invent storage formats, commands, accepted entries or
links to make a sheet look complete. A read-only compiled library is not a write
API.

## Generate and verify the link views

Accepted rows link only to records that exist. Verify each metadata link against
the committed record and current source/identity binding. Review candidates may
instead have explicitly labeled draft links and Markdown task checkboxes, using
the `$harvest-paper` and its checkbox contract.
The user can review those drafts in Obsidian, select
items and explicitly request `$harvest-paper` for scripted synchronization. That
request authorizes the selected reviewed content and target; no second chat
selection is needed. A checkbox never establishes personal understanding. Keep
sheet rows lightweight; do not copy full definitions or claims into a second
editable knowledge store. Regenerate projections after a verified update,
preserving unrelated user annotations and previously created source files.

Return the sheet paths, coverage, linked records, review proposal/receipt paths
when relevant, and unsupported or unapplied scope. A canonical update requires
a committed ingest receipt and fresh target verification; a draft or successful
projection refresh is not evidence of a metadata commit.
