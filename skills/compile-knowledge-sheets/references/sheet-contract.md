# Source-scoped link-sheet contract

## Accepted knowledge and projections

`knowledge/` holds canonical accepted content and semantic state, including the
node metadata used for retrieval. Registered native Markdown, Typst and LaTeX
markers retain identity authority; accepted atomic entries retain their content
contract. A sheet is a source-scoped navigation projection of this state. It
neither defines a new authority nor independently stores full meanings.

This applies to mathematical notes, computer-science notes, papers, blogs and
project documents. Preserve caller-selected locations and existing filenames;
otherwise use `def-sheet.md` and `pending-sheet.md` beside the selected source.
An existing paper sheet or user-authored source file is not disposable because
the workflow has been generalized.

Each sheet identifies its source/version, requested and actual coverage, missing
material, and the verified knowledge revision or receipt when available. Use
precise native anchors, equation labels, figure/table numbers or PDF pages.

## Definition view

A row contains only:

- The knowledge name and meaningful type.
- Its precise source locator.
- A real link to the corresponding committed metadata record.

Reuse existing explicit references where navigation needs them. Sheet row
references are navigation handles, not canonical node IDs. Resolve canonical
identity through the registered native source and query contract; do not infer
it from headings, row order, names or keyword overlap. Verify that the linked
record exists and matches the reviewed identity/source binding. Use a relative
link from the sheet or another caller-supported local link form.

Keep full definitions, formulas, conditions, relation assertions and evidence in
the metadata, rather than copying them into sheet rows. Preserve unrelated user
annotations when refreshing a view, but do not treat an annotation as an
accepted metadata change.

## Pending view

Link supported canonical dependency-gap records or metadata gap state, with the
source term, use locator and concise gap status. A source-found candidate is not
an accepted resolution until the required meaning and applicability have been
reviewed. A citation, familiar term or identical name cannot close a gap.

If a needed definition, relation, application or pending state cannot yet be
committed through a supported adapter, retain its draft in
`knowledge/build/reviews/` and report it as unapplied. Do not fabricate a
`knowledge/entries/` record, canonical gap state or metadata link. The pending
view may identify that a draft remains, but must clearly distinguish review
artifacts from committed metadata.

Derived request data and sheets are regenerated from their respective reviewed
proposal or accepted records; they are not independently edited parallel truths.
A valid link sheet certifies navigation, not scientific completeness or a live
knowledge update.
