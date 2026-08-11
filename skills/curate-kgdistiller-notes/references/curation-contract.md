# Registered-note curation contract

Use this contract for Markdown, Typst, and LaTeX authorities registered in a
kgdistiller project.

## Authority and identity

Treat one complete source file as the normal curation unit. Read its statements,
proofs, explanations, examples, and comparisons. Preserve user-authored markers
unless they conflict with an established identity or bundle independently
reusable concepts.

One authority marker denotes one concept. Split a bundled title only when each
part remains independently teachable, searchable, and reusable. Keep genuine
translations, aliases, abbreviations, and equivalent notation on one node. An
existing identity defined elsewhere must appear as a native ref, never as a
second authority.

Unmarked prose may become a candidate only when it actually defines or teaches
a stable reusable concept. A heading, theorem wrapper, repeated phrase, source
order, or retrieval score is never sufficient evidence.

## Entries

For every active authority marker in the selected scope, write one to three
compact sentences that let a reader recognize the concept without loading the
whole source. Preserve essential hypotheses, distinctions, notation, units, and
the source's dominant language. Synthesize rather than copy a long span. Do not
add external facts to an authority entry.

Use `properties.entry_origin: agent-extracted` for a new agent-authored entry.
Keep longer dossiers outside node properties; the engine stores reviewed entry
bodies in authority-scoped shards.

## References

Add a file-level native ref when the file materially uses a direct prerequisite
whose canonical authority is another registered file. Put it at the first
meaningful use. Do not add refs for transitive ancestors, passing mentions, or
unrepresented generic vocabulary. A ref creates provenance and a backlink; it
does not create a semantic edge.

## Relations

Use the narrowest direct source-supported relation:

- `prerequisite-for`: understanding the target directly requires the source;
- `implies`: the source assertion logically entails the target;
- `generalizes`: the source strictly extends the target;
- `derived-from`: the source construction or assertion is obtained from the
  target;
- `contrasts-with`: the source explicitly distinguishes the endpoints;
- `contains`: configured field/topic classification only.

Read every edge literally as `source relation target`. Record concrete evidence.
Do not store transitive closure, chronology, topical proximity, keyword
co-occurrence, or similarity. Keep `contains` and `prerequisite-for` acyclic.

## Handoff

The extraction handoff contains:

1. exact registered authority paths and expected source hashes;
2. a decision for every candidate;
3. the target graph, snapshot, and alignment digests;
4. the reviewed source patch and complete marker/ref state;
5. one reviewed delta with entries and evidence-backed direct edges;
6. unresolved decisions, which block automated apply.

Use `$query-kgdistiller` for identity and retrieval and
`$ingest-kgdistiller` for mutation. Never edit generated graph artifacts or
SQLite to make a validation gate pass.
