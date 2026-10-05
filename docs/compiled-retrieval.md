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

The input is an explicitly supplied JSON library containing a `nodes` mapping.
Node records contain `name`, `statement`, `conditions` and, when available,
`formal`, `inputs_outputs`, `notation`, `evidence`, `epistemic`, `depends_on`,
`relations`, `distinguish_from` and authored retrieval `surfaces`. Optional
`papers`, `terms`, `claims` and `edges` retain the author's navigation and
comparisons. Existing references address records; this read-only view does not
create identities or write the canonical graph.

```sh
kgdistiller agent compiled --library library.json search 'What does this definition require?'
kgdistiller agent compiled --library library.json browse
kgdistiller agent compiled --library library.json browse 'an existing source or sense reference'
kgdistiller agent compiled --library library.json get 'an existing knowledge reference'
kgdistiller agent compiled --library library.json pack 'first reference' 'second reference' --budget 24000
```

Paths are supplied by the caller. These commands do not require vault
registration. MCP exposes the same operations through `kg_compiled_knowledge`
with an absolute `library_path` and `operation: search|browse|get|pack`.

Packing preserves whole scientific entries, including definitions, conditions,
formulas, input/output contracts, evidence and epistemic qualifications, within
the serialized UTF-8 JSON byte budget. Repeated claims and edges are shared once
at the top level, preserving all positions, timelines, endpoints and evidence.
Retrieval expressions stay in the search index; sense and confusion guards
remain in the evidence. Entries that cannot fit produce gaps; external
dependencies and dependencies absent from the selection remain visible.
Fitting all selected entries does not establish that the selection satisfies
every requirement of a research question. Complete-evidence task success is
evaluated separately against source-backed requirements.

Implementation provenance: the input shape and posting-list retrieval pattern
come from `qiulinfan/kgdistiller-experiment`, commit `2876f19`. The product module
contains no private knowledge, benchmark answers, paper-specific query rules or
machine-local configuration.
