# Compiled knowledge retrieval

A compiled library preserves definitions, conditions, formulas, input/output
contracts, meanings, explicit dependencies and source-backed claims. Its search
text includes authored Chinese and English retrieval expressions. Lookup
references, paper keys, machine paths and hashes are excluded from semantic
text. The same text can be passed to an embedding adapter.

The core uses local BM25 and has no model dependency. An external agent may
navigate the source and sense views, compare claim positions, and select a
complete evidence set. Search scores do not establish concept identity or
scientific truth. Missing authored targets remain visible; names do not silently
resolve them.

Compilation follows the [shared knowledge model](concepts-and-relations.md)
across papers, mathematical notes, CS notes and other sources. Definitions,
axioms, precise theorems, algorithms and architectures can be knowledge nodes.
Propositions, remarks, observations and hypotheses express relations; examples
and experiments express applications. Preserve existing explicit identities.
Source-side def/pending sheets are link projections to accepted `knowledge/`
metadata, not a second store of full definitions.

A source must actually explain a local concept before it is admitted as a new
node. A merely named, cited or used concept remains a dependency gap, with its
use site and required meaning. Trace it to an applicable primary defining source
before linking. Local accounts of inherited concepts preserve their scope and do
not establish first origin. Shared names cannot close gaps or merge meanings.
This admission review belongs to compilation; the deterministic retriever
preserves the caller's reviewed nodes and dependencies.

Review initial evidence by retaining it and adding relevant context first.
Inspect source, sense and claim branches before removing a reference. Remove
confirmed wrong senses, out-of-scope content or duplicate support under the same
conditions and source scope; broader applicable conditions, exceptions and
counterexamples may remain when they fit the caller's limits. An explicit budget
can require pruning, but a shorter list is not itself a retrieval-quality goal.
The [OMP adapter](omp-compiled-tools.md) checks actual question-scoped candidate
search and branch observations before submission, separately from scientific
completeness.

The input is an explicitly supplied JSON library containing a `nodes` mapping.
Node records contain `name`, `statement`, `conditions` and, when available,
`formal`, `inputs_outputs`, `notation`, `evidence`, `epistemic`, `depends_on`,
`relations`, `distinguish_from` and authored retrieval `surfaces`. Optional
`papers`, `terms`, `claims` and `edges` retain the author's navigation and
comparisons. Existing references address records; this read-only view does not
create identities or write the canonical graph.

Scientific subrecords (`evidence`, `epistemic`, notation, distinctions,
dependencies, relations, instances, claims and claim positions, and edges) retain
additional authored finite JSON attributes. Existing text-field validation still
applies. Conditions, support-specific source titles, units and qualifiers stay
at their authored scope in `get` and packing; unsupported non-JSON values fail
explicitly instead of being silently discarded. Structural `target`, position
`id` and edge endpoints remain exact lookup declarations.

The semantic projection excludes those lookup addresses at their declared
locations and indexes scientific JSON, including meaningful nested fields named
`id`, `path`, `hash`, `reference` or `provenance`. It does not recursively classify
words as machine metadata. The explicit acquisition object at
`evidence[].provenance` remains in `get` and packing but is excluded from the
semantic index. Direct edge `origin` records acquisition/derivation metadata;
it remains navigable in `get` and is excluded from evidence packing and indexing.
Other node/source machine metadata remains outside the existing scientific
projection.

```sh
kgdistiller agent compiled --library library.json search 'What does this definition require?'
kgdistiller agent compiled --library library.json browse
kgdistiller agent compiled --library library.json browse 'an existing source or sense reference'
kgdistiller agent compiled --library library.json get 'an existing knowledge reference'
kgdistiller agent compiled --library library.json inventory 'an authored term or group name'
kgdistiller agent compiled --library library.json pack 'first reference' 'second reference' --budget 24000
```

Paths are supplied by the caller. These commands do not require vault
registration. MCP exposes the same operations through `kg_compiled_knowledge`
with an absolute `library_path` and `operation: search|browse|get|inventory|pack`.
The inventory operation requires `term` and accepts no ranking limit or packing
budget.

`inventory(term)` enumerates all explicitly declared members of matching term
groups and all nodes whose authored `surfaces.head_terms` contain an exact match.
Name comparison applies Unicode NFKC, case folding and whitespace normalization;
it does not infer aliases, equivalence or additional uses from names, prose,
embeddings or search rankings. A group may match its authored name or existing
navigation handle. Matching groups and declared uses remain separate, and every
available member preserves the complete `get` entry with its source context.
Missing references remain `available: false` with gaps; missing sources and
dependency targets in available entries also remain visible. Group disambiguation
is authored navigation data and supplies no proof of distinct concept meanings.

The result declares `scope: "compiled declarations"` and
`source_corpus_completeness: "not-certified"`. `matched` describes a declaration
name or handle match, not concept identity or definition equivalence. An unmatched
name produces an explicit gap and does not establish absence from original
sources. The operation neither counts meanings nor certifies a complete census
of the source corpus.

Packing preserves whole scientific entries, including definitions, conditions,
formulas, input/output contracts, evidence and epistemic qualifications, within
the serialized UTF-8 JSON byte budget. Repeated claims and edges are shared once
at the top level, preserving all positions, timelines, endpoints and evidence.
Retrieval expressions stay in the search index; sense and confusion guards
remain in the evidence. Entries that cannot fit produce gaps; external
dependencies and dependencies absent from the selection remain visible.
Derived endpoint bibliography stays compact; explicitly authored relationship
source and year qualifications remain in the packet. Shared scientific records
compare JSON structure with Booleans distinct from numbers, so different
qualifications cannot merge through Python's Boolean/integer equality.
Evaluate the actual packed text, formulas and qualifications against necessary
facts, conditions, intended senses and source scopes derived independently from
the question and original sources. Alternative witnesses must support the same
fact under agreeing conditions, scopes, units and conventions. Whole entries
and declared inventory membership supply no completeness certificate.
The [evaluation protocol](retrieval.md#evaluation) defines complete-evidence
task success separately from retrieval and packing diagnostics.

These stages have different failure boundaries. A known compiled entry absent
from a candidate list is a retrieval/selection diagnostic. A selected entry
omitted for size has an explicit `byte-budget` gap; missing declared targets or
sources have unresolved-reference/source gaps. An authored fact absent from the
compiled input cannot be identified from ranking results alone and requires
comparison with independent original-source requirements. Reading an entry
without selecting it is not projection loss, and a complete transport packet
does not establish scientific completeness.

Implementation provenance: the input shape and posting-list retrieval pattern
come from `qiulinfan/kgdistiller-experiment`, commit `2876f19`. The product module
contains no private knowledge, benchmark answers, paper-specific query rules or
machine-local configuration.
