# Selected paper import contract

## Authorization record

Record the exact candidate IDs selected by the user, the federated snapshot
digest, the alignment target digests, and the registered authority destination.
An instruction to process all `new` candidates is valid only when stated
explicitly; absence of a selection imports nothing.

## Provenance minimum

Each imported entry or edge must point to the research authority and retain the
paper title, authors, selected version, stable identifier or URL, and at least one
source-file/line/section/equation/theorem/figure/table location. Preserve the paper's
method definition, operations, defining conditions, local terminology and source
digest. Negative results and assessments remain unmarked paper notes. A bibliography entry or
paper-level URL alone is insufficient provenance for a specific claim.

## Identity rules

- Same paper-qualified identity already present: write a ref, or author only a
  separately reviewed gap in that same identity.
- Scoped identity absent: add a marker only after checking qualified identity
  and plausible duplicates within that paper/version. Bare-name lookup is not
  identity evidence.
- Other paper/version or a generic entry: preserve separate scoped identities,
  even when a source-backed comparison establishes equivalent mechanisms.
- Unresolved duplicate within the same scope or insufficient source definition:
  defer that candidate for review.
- Similarity, shared spelling, acronym ranking or graph proximity: retrieval only.

Use paper-qualified marker names in Markdown, Typst and LaTeX, for example
`Identity shortcut (arXiv:1512.03385v1)`. Keep the paper identity and full source
digest in the research authority's prose and the entry's supported `Context` or
`Sources` sections. Do not invent entry-frontmatter keys; candidate `properties`
are not automatically valid native entry metadata. Never add an unqualified
global alias that erases paper scope.

The engine's `matched`/`ambiguous`/`unmatched` states and the reviewed authoring
actions are separate records. Accept cross-paper bridges only with definition,
operation/formula and condition comparison evidence; a bridge is not a merge.

## Transaction handoff

Require expected authority hashes, full post-patch marker/ref state, candidate
and query artifact digests, a reviewed delta, and review evidence. The plan must
predict only the selected candidates. The committed receipt must show matching
after-digests and leave unselected nodes absent.

Keep the paper package immutable. Keep the research authority separate from
generated paper artifacts and generated static exports.
