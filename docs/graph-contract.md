# kgdistiller graph contract

## Authority

Configured `.md`, `.typ`, and `.tex` files remain authoritative in their own
formats. Generated graph artifacts, search databases, HTML, and converted files
never become a second authority.

One authored knowledge name has at most one active definition marker across the
whole project:

```text
Typst    #kn[Name]        #ref[Name]
Markdown --[[Name]]--     [[Name]] or [[Name|display]]
LaTeX    \kn{Name}        \knref{Name}
```

The authored name is resolved to a stable machine ID stored outside the source.
Removing or moving a definition may temporarily orphan its node; accumulated
Agent metadata is preserved until the same identity is rehomed or explicitly
removed.

Changing an authored name is an explicit identity decision. The deterministic
scanner does not infer it from document order, proximity, a matching Git hunk,
or textual similarity. `kgdistiller reconcile rename-node <id> <new-name>`
records the new canonical name and prior aliases in the optional
`qlkg-identities-v1` registry before the source is synchronized.

## Node selection

A knowledge node should be independently teachable, searchable, reusable across
documents, and specific enough to retain a stable identity. Definitions,
axioms, theorems, propositions, lemmas, and corollaries are common candidates.

Sections, proofs, examples, exercises, equations, figures, and remarks are not
nodes merely because they exist. An author or Agent must explicitly mark a
genuinely reusable concept.

`field` nodes form a flat overlapping facet layer. `topic` nodes are curated
clusters. Subject names and directory names do not automatically become graph
nodes. Multiple field memberships are valid.

## References

References are backlink occurrences, not graph nodes and not semantic edges.
When one file directly uses an immediate prerequisite whose authority is another
file, an Agent should place at least one native reference marker at a meaningful
use. Same-file concepts and merely transitive foundations do not require global
references.

## Entries and provenance

Every curated active knowledge node has a concise, source-grounded entry. The
entry improves search and explanation but does not replace the authoritative
statement or proof. Provenance continues to record the authority file, line,
source format, source name, and canonical location.

Research nodes may carry structured dossiers with summary, context, role,
prerequisites, common confusions, open questions, and sources. Before creating a
new research node, an Agent searches canonical names and aliases in the existing
graph.

## Relations

Supported relations are:

- `contains`: field/topic classification only;
- `prerequisite-for`: a direct learning dependency;
- `implies`: direct logical entailment;
- `generalizes`: the target is recovered as a special case;
- `contrasts-with`: an explicit symmetric comparison;
- `derived-from`: the source is directly constructed or proved from the target.

Agents store direct, high-confidence claims rather than transitive closure,
document order, co-occurrence, or generic association. Every Agent edge records
origin, confidence, and source-grounded evidence.

## Agent delta

Semantic curation uses a reviewable `qlkg-agent-delta-v2` document:

```json
{
  "schema": "qlkg-agent-delta-v2",
  "remove_nodes": [],
  "nodes": [
    {
      "id": "measure-space",
      "type": "knowledge",
      "label": "Measure space",
      "text": "A measurable space equipped with a measure.",
      "properties": {"aliases": [], "origin": "agent"}
    }
  ],
  "edges": [
    {
      "source": "sigma-algebra",
      "relation": "prerequisite-for",
      "target": "measure-space",
      "confidence": "high",
      "evidence": "The definition of a measure space requires a sigma-algebra."
    }
  ],
  "remove_edges": []
}
```

Source marker edits and graph deltas are reviewed together. Applying a delta can
add entries and semantic relations, but cannot create a second active authority
for a knowledge name.

## Incremental workflow

The high-level write path is the transactional API documented in
[`transactional-ingest.md`](transactional-ingest.md). It applies the reviewed
source patch and delta together, rejects stale preconditions, and returns a
canonical receipt. The commands below remain deterministic low-level
primitives for development and compatibility; Agent Skills must not compose
them into a substitute transaction.

```sh
kgdistiller scan --file notes/chapter.typ
kgdistiller apply knowledge/reviews/chapter.delta.json
kgdistiller sync --file notes/chapter.typ
kgdistiller curate-check --file notes/chapter.typ
kgdistiller check
```

Repository, subject, course, directory, and file scopes are supported. A scoped
sync replaces only definition and reference occurrences from the selected
authorities and retains unrelated state. An explicit file must match exactly
one bounded registry pattern; a shared directory root alone is not source
registration, and overlapping source ownership is rejected.

The graph manifest records the last usable Git revision alongside the complete
source hash map. A later sync includes deleted authorities and both sides of a
staged Git rename; full sync also compares the previous source map, so rename
handling does not depend on Git similarity detection. An exact-content rename
can be paired before staging. A file path is provenance, never graph identity.

Every `source_hashes` value uses the authority-text boundary: read the
Markdown, Typst, or LaTeX file as UTF-8 with universal-newline translation,
represent CRLF and lone CR as LF, then SHA-256 the resulting UTF-8 bytes. All
other characters, including a final newline, remain significant. Scan/rename
matching, sync, transactional ingest, `check`, portable-store verification,
and static export share this one function, so Git's checkout newline policy
cannot create a false source change. Raw-byte hashing remains reserved for
binary and byte-stable artifacts.

Generated graph JSON/JSONL and entry shards are serialized with LF.
Hydration and `check` read those text projections with the same universal-
newline behavior before comparing their manifest digests and canonical
serialization. Thus an otherwise clean CRLF checkout still represents the
same graph generation; semantic text changes continue to invalidate it.

Every definition stores a hash and source span for its enclosing authored
statement (or the smallest conservative source block when no formal statement
wrapper exists). If that hash changes, an existing curated entry becomes
`needs-review`. Semantic edges retain the endpoint hashes against which their
evidence was reviewed and likewise become `needs-review` if an endpoint changes
or becomes orphaned. The data is retained for review, while `curate-check` and
publication reject stale curation. Reapplying reviewed node and edge deltas
refreshes those fingerprints.

## Required invariants

- at most one active authority marker per global knowledge name;
- deterministic graph artifacts and stable IDs;
- no dangling semantic edge endpoints;
- no cycles in `contains` or `prerequisite-for`;
- no field-to-field `contains` edges;
- every active knowledge node resolves to at least one field;
- Typst label HTML contains no active or unsafe content;
- entry shards are bounded and referenced from the manifest;
- unresolved references and orphaned nodes remain visible diagnostics;
- changed definitions and their affected semantic edges remain visible review
  diagnostics rather than being silently trusted or deleted;
- a scoped sync never rewrites unrelated source state;
- an explicit file scope has exactly one bounded registry owner;
- examples and headings create no implicit nodes.

Run `kgdistiller audit` for entry coverage, topology, relation counts,
cross-course bridges, field memberships, and edge metadata completeness.
