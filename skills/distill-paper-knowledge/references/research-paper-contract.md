# Federated paper graph contract

## Required input

Consume one complete `qlpaper-markdown-v1` package. Require resolved source and
object markers, declared attachments, source hashes, page or stable HTML-anchor
coverage, and no unresolved region that changes a central claim. Outside sources
may disambiguate terminology but cannot silently become evidence for a paper
claim.

## Candidate selection

Represent established concepts and named direct prerequisites, paper-specific
mechanisms and objectives, validity-changing assumptions, interpretation-critical
metrics, central results, negative results, and material limitations. Each node
must have a stable paper-local ID, searchable label, aliases, role, importance,
precise source locations, and short evidence.

Allowed `properties.paper_role` values are `foundation`, `concept`, `method`,
`assumption`, `metric`, `result`, and `boundary`. Before alignment, do not add
general teaching prose or a personal identity bridge.

## Candidate graph

Use a non-`personal` namespace derived from the source digest. Represent every
candidate as a knowledge node. Use only source-supported
`prerequisite-for`, `implies`, `derived-from`, `generalizes`,
`contrasts-with`, and optional hierarchy `contains`. Include evidence for every
semantic relation and read direction literally. Mere co-occurrence, section
order, and outside background knowledge are not edges.

## Status-sensitive payload

After one whole-snapshot alignment:

- known nodes contain paper role and provenance, not copied personal entries;
- partial nodes contain only the gap needed for this paper;
- new nodes contain a paper-grounded explanation, assumptions, mechanism or
  result, direct prerequisites, likely confusion, and locations;
- conflict and uncertain nodes preserve alternatives and remain unbridged.

An exact bridge is navigation evidence between namespaces. It is not a semantic
paper edge and never merges node identity.

## Human-readable graph

Write `paper-graph.md` with source coverage, the argument chain, candidate index,
paper edges, bridges, prerequisite-ordered learning stages, status-sensitive
explanations, and a coverage audit mapping every central result and limitation to
at least one node and source location.

Completion requires valid candidate/snapshot digests, coverage of the main
argument and limitations, visible unresolved identities, and unchanged personal
graph/snapshot/alignment digests.
