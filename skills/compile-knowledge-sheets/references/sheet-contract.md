# Source-scoped link-sheet contract

## Accepted knowledge and projections

`knowledge/` holds canonical accepted content and semantic state, including the
node metadata used for retrieval. Registered native Markdown, Typst and LaTeX
markers retain identity authority; accepted atomic entries retain their content
contract. A sheet is a source-scoped navigation projection of this state. It
neither defines a new authority nor independently stores full meanings.
The source's registered `document_type` selects user-authored extraction rules;
it is independent of source format and knowledge domain. Keep original `.md`,
`.typ` and `.tex` evidence links. Entries and sheet projections can be Markdown
without converting the source itself.

This applies to mathematical notes, computer-science notes, papers, blogs and
project documents. Preserve caller-selected locations and existing filenames;
otherwise use `def-sheet.md` and `pending-sheet.md` beside the selected source.
An existing paper sheet or user-authored source file is not disposable because
the workflow has been generalized.

A sheet may intentionally cover one item or selected passages. Mark that coverage
partial and preserve unrelated rows. Omitted items are unprocessed or outside
scope, not evidence that the source lacks those concepts. Full distillation is
an explicitly requested operation. Sheet coverage never certifies understanding.

Each sheet identifies its source/version, requested and actual coverage, missing
material, and the verified knowledge revision or receipt when available. Use
precise native anchors, equation labels, figure/table numbers or PDF pages.

## Definition view

A row contains only:

- The knowledge name and meaningful type.
- Its precise source locator.
- A real link to the corresponding committed metadata record.
- Optionally the entry's stated understanding; omission means unknown.

A sheet can also present prepared review candidates as ordinary Markdown task
items. Label their links as review drafts and point to the complete proposed
metadata under `knowledge/build/reviews/`. These candidates are distinct from
accepted rows; source coverage can stay partial in either state. The linked
draft includes the target, source evidence and reviewed identity decision so
the user can select the actual change rather than just a title.
Generate the task bindings with the
[harvest helper](../../harvest-paper/references/checkbox-contract.md); ordinary
todos and arbitrary handwritten task rows do not authorize metadata changes.

The user checks desired items in Obsidian and explicitly asks to harvest.
That combination authorizes synchronization of those reviewed items. No native
question UI or separate conversational selection is required. The deterministic
harvest script reads the prepared selections and updates successful rows to real
canonical metadata links. Preserve unchecked rows and unrelated annotations.
A checkbox means selected for import, never understood, globally complete or
already committed. The script's receipt establishes commit state.
Edits to a linked draft require a targeted re-review and regenerated prepared
item before apply; changed review text must not silently import an old payload.

Reuse existing explicit references where navigation needs them. Sheet row
references are navigation handles, not canonical node IDs. Resolve canonical
identity through the registered native source and query contract; do not infer
it from headings, row order, names or keyword overlap. Verify that the linked
accepted record exists and matches the reviewed identity/source binding; verify
review links against their draft files instead. Use a relative
link from the sheet or another caller-supported local link form.

Keep full definitions, formulas, conditions, relation assertions and evidence in
the metadata, rather than copying them into sheet rows. Preserve unrelated user
annotations when refreshing a view, but do not treat an annotation as an
accepted metadata change.

## Pending view

Link the owning committed entry's `pending_prerequisites`, with the source term,
use locator and concise gap description. These direct gaps may reflect a missing
definition or an existing definition the user has not understood. Keep that
reason explicit; definition resolution and understanding are independent.
`understanding` uses `unknown`, `not-yet-understood` or `understood`. Only an
explicit user statement changes personal understanding; lookup and source
completion do not. Keep absent state unknown.

Record only immediate dependencies. The next layer belongs to the dependency's
own entry when the user chooses to study it. Do not search recursively or invent
nodes for unexplained terms. A full-source sheet can still contain pending
learning gaps. A source-found candidate is not
an accepted resolution until the required meaning and applicability have been
reviewed. A citation, familiar term or identical name cannot close a gap.

If a needed definition, full relation/application or richer pending state cannot be
committed through a supported adapter, retain its draft in
`knowledge/build/reviews/` and report it as unapplied. Do not fabricate a
`knowledge/entries/` record, canonical gap state or metadata link. The pending
view may identify that a draft remains, but must clearly distinguish review
artifacts from committed metadata.

Derived request data and sheets are regenerated from their respective reviewed
proposal or accepted records; they are not independently edited parallel truths.
A valid link sheet certifies navigation, not scientific completeness or a live
knowledge update.
