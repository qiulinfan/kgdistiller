# Registered-note curation contract

Use this contract for Markdown, Typst, and LaTeX authorities registered in a
kgdistiller project.

Require a current `kgdistiller-graph-v1` graph and prepare only `kgdistiller-agent-delta-v1`.
Superseded core registries and graphs belong to the explicit deployment/rebuild
workflow, not curation.

## Authority and identity

Use the selected source's registered `document_type` profile, exposed by
`scan --file`, for its `node_kinds` and `extraction_guidance`. Profiles are
user data in `knowledge/sources.json`; file format and fields/topics do not
choose them. Read native sources directly. Markdown atomic entries can cite
`.md`, `.typ` or `.tex` evidence without a converted source copy.

For source-level curation, treat one complete source file as the curation unit.
For one selected item, use `$capture-kgdistiller` and preserve unselected entries. Read its statements,
proofs, explanations, examples, and comparisons. Preserve user-authored markers
and keep any suspected conflict or bundled concept pending explicit identity
review. A classification policy alone never authorizes deleting or reclassifying
an existing marked identity.

The caller's `knowledge/` holds canonical accepted content and semantic state.
Registered native source markers remain identity authority and source-grounded
atomic entries retain content authority. Notes, papers and projects can all
supply evidence. Definition and pending sheets are source-scoped link
projections to committed metadata. Keep full proposed meanings, conditions,
evidence, relations and applications in `knowledge/build/reviews/` until a
supported transaction accepts them; a sheet cannot override accepted content.

The repository's [shared model](../../../docs/concepts-and-relations.md)
defines this distinction across source types. Its essentials are included here
for packaged use. These examples do not define a fixed document-type registry:

- Mathematical definitions, axioms and theorems/lemmas can be knowledge nodes;
  computer-science algorithms, architectures and independently defined methods
  or components can also be nodes. Preserve their full defining conditions and
  formal assertions. A noun-only admission rule does not apply globally.
- Propositions and remarks normally express source-grounded relations; their
  labels alone do not make nodes. Existing marked identities remain until an
  explicit review decides otherwise.
- Examples and experiments normally express uses/applications, with applied
  knowledge, context and result, rather than creating new concepts for instances.

One authority marker denotes one reviewed knowledge identity. Split a bundled
title only when each part remains independently teachable, searchable, and
reusable. Keep genuine
translations, aliases, abbreviations, and equivalent notation on one node. An
existing identity defined elsewhere must appear as a native ref, never as a
second authority.

Unmarked prose may become a candidate only when it actually defines or teaches
a stable reusable knowledge object. A heading, theorem wrapper, repeated
phrase, source order, or retrieval score is never sufficient evidence.
There is no node-count cap for mathematical notes; the paper workflow's roughly
twenty-concept diagnostic is specific to paper extraction.

## Entries

For every active authority marker in the selected scope, write one to three
compact sentences that let a reader recognize the concept without loading the
whole source. Preserve essential hypotheses, distinctions, notation, units, and
the source's dominant language. Keep a theorem's assumptions and conclusion
correct even when a compact entry points to the full proof in the source.
Synthesize rather than copy a long span. Do not add external facts to an
authority entry.

Use `properties.entry_origin: agent-extracted` for a new agent-authored entry.
Use `properties.kind` for its reviewed semantic kind, following the source's
registered `node_kinds` when it has a document profile. Preserve reviewed kinds
on later sync; native statement syntax is not a semantic reclassification.
Keep longer dossiers outside node properties; the engine stores reviewed entry
bodies in authority-scoped shards.

## Personal understanding and direct gaps

Atomic entries may carry `understanding`: `unknown`, `not-yet-understood` or
`understood`; absence means unknown. Preserve the user's stated status. Source
coverage, definition availability and curation status do not establish mastery.
`pending_prerequisites` lists only direct source-grounded gaps with the required
meaning and use context. A definition may be available while still unlearned.
Finding it never automatically clears the learning gap. Record the next layer
only when the user chooses to study that dependency. Do not create unexplained
nodes or recursively seek all ancestors.

## References

Add a file-level native ref when the file materially uses a direct prerequisite
whose canonical authority is another registered file. Put it at the first
meaningful use. Do not add refs for transitive ancestors, passing mentions, or
unrepresented generic vocabulary. A ref creates provenance and a backlink; it
does not create a semantic edge.

## Relations

First preserve the complete assertion: participant roles, unary/binary/n-ary
arity, repeated references and self-relations, changing states, conditions,
measurements, evidence and epistemic qualifications. A proposition or remark
can provide such an assertion without adding a node. Separate independent
assertions when conditions or epistemic states differ. Review status and
scientific epistemic status are distinct.

For assertions that the current graph-v1 edge adapter can faithfully represent,
use the narrowest supported direct source-grounded relation:

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

A worked example or experiment is a typed use/application relation record:
retain its review reference and declared relation type, applied knowledge
references, input/context, conditions, steps, result, evidence and epistemic
status together. Preserve explicit roles and changing states; an instance does
not become a concept solely to hold these fields.

The current `kgdistiller-agent-delta-v1` direct-edge contract does not provide a
lossless full n-ary, application-record or rich gap-history adapter. Simple
direct gaps and understanding use the entry fields above. Retain
unsupported proposals in `knowledge/build/reviews/`, identify the exact gap and
defer their persistence.
Never flatten them into disconnected edges, hide them in unrelated nodes, drop
fields or claim a smaller committed delta applied the whole extraction. Apply
a representable subset only when that partial scope has been explicitly
reviewed and authorized.
Do not fabricate accepted records or metadata links for deferred content.
Refresh any source-scoped link sheets only after verifying the actual committed
records and identity/source bindings.

## Handoff

The extraction handoff contains:

1. exact registered authority paths and expected source hashes;
2. one `matched`, `ambiguous`, or `unmatched` identity decision from
   `kgdistiller-graph-comparison-v1` for every candidate;
3. the target graph, snapshot, and alignment digests;
4. the reviewed source patch and complete marker/ref state;
5. one independently reviewed `kgdistiller-agent-delta-v1` with entries and
   evidence-backed direct edges; `kgdistiller-agent-proposal-v1` is only a review
   package and may have `delta_ready: false`;
6. unresolved decisions and unsupported relation/application records, with
   exact unapplied scope; ambiguity blocks its affected apply, and unsupported
   records remain deferred rather than becoming fabricated graph-v1 writes.

Use `$query-kgdistiller` for identity and retrieval and
`$ingest-kgdistiller` for mutation. Never edit generated graph artifacts or
other derived runtime state to make a validation gate pass.
