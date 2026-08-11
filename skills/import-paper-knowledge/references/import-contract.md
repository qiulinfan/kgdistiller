# Selected paper import contract

## Authorization record

Record the exact candidate IDs selected by the user, the federated snapshot
digest, the alignment target digests, and the registered authority destination.
An instruction to process all `new` candidates is valid only when stated
explicitly; absence of a selection imports nothing.

## Provenance minimum

Each imported entry or edge must point to the research authority and retain the
paper title, authors, selected version, stable identifier or URL, and at least one
section/page/equation/theorem/figure/table location. Preserve claim scope,
assumptions, negative results, and material uncertainty. A bibliography entry or
paper-level URL alone is insufficient provenance for a specific claim.

## Identity rules

- known: write a ref only;
- partial: reuse the personal identity and author only the reviewed gap;
- new: add a marker only after exact and alias resolution remains empty and no
  plausible unresolved sense exists;
- conflict or uncertain: do not import until reviewed;
- similarity, acronym ranking, or graph proximity: retrieval evidence only.

## Transaction handoff

Require expected authority hashes, full post-patch marker/ref state, candidate
and query artifact digests, a reviewed delta, and review evidence. The plan must
predict only the selected candidates. The committed receipt must show matching
after-digests and leave unselected nodes absent.

Keep the paper package immutable. Keep the research authority separate from
generated paper artifacts and generated static exports.
