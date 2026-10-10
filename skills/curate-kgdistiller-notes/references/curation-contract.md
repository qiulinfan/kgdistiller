# Registered-note curation contract

Use this contract for any registered text document in a kgdistiller project.
kgdistiller reads a source as UTF-8 text with 1-based line numbers and never
parses its syntax; `.md`, `.typ`, `.tex`, `.txt` and every other format are
handled identically. Prepare only `kgdistiller-agent-delta-v1` updates through
transactional ingest.

## Authority and identity

Use the selected source's registered `document_type` profile, exposed by
`scan --file`, for its `node_kinds` and `extraction_guidance`. Profiles are user
data in `.knowledge/sources.json`; file format does not choose them.

For source-level curation, treat one complete source file as the curation unit.
For one selected item, use `$capture-kgdistiller` and leave other knowledge
untouched. Read its statements, proofs, explanations, examples, and comparisons.
Keep any suspected conflict or bundled concept pending explicit identity review.
A classification policy alone never authorizes deleting or reclassifying an
existing entry.

The caller's `.knowledge/` holds canonical accepted content and semantic state:
`entries/<id>.md` (one node each) and `edges.jsonl` (accepted direct relations).
Notes, papers and projects can all supply evidence. Definition and pending
sheets are source-scoped link projections to committed entries. Keep full
proposed meanings, conditions, evidence, relations and applications in
`.knowledge/build/reviews/` until a supported transaction accepts them; a sheet
cannot override accepted content.

The repository's [shared model](../../../docs/concepts-and-relations.md)
defines this distinction across source types. Its essentials are included here
for packaged use. These examples do not define a fixed document-type registry:

- Mathematical definitions, axioms and theorems/lemmas can be knowledge nodes;
  computer-science algorithms, architectures and independently defined methods
  or components can also be nodes. Preserve their full defining conditions and
  formal assertions. A noun-only admission rule does not apply globally.
- Propositions and remarks normally express source-grounded relations; their
  labels alone do not make nodes. Existing entries remain until an explicit
  review decides otherwise.
- Examples and experiments normally express uses/applications, with applied
  knowledge, context and result, rather than creating new concepts for instances.

One entry denotes one reviewed knowledge identity. Split a bundled title only
when each part remains independently teachable, searchable, and reusable. Keep
genuine translations, aliases, abbreviations, and equivalent notation on one
entry as `aliases`. Labels and aliases are unique across the whole store
(compared after NFKC normalization, casefolding and whitespace collapsing); a
name already owned by another entry means an update of that entry or a reviewed
rename, never a second entry.

Prose may become a candidate only when it actually defines or teaches a stable
reusable knowledge object. A heading, theorem wrapper, repeated phrase, source
order, or retrieval score is never sufficient evidence. There is no node-count
cap for mathematical notes; the paper workflow's roughly twenty-concept
diagnostic is specific to paper extraction.

## Entries

An entry record has these fields:

| Field | Rule |
|---|---|
| `id` | Readable slug `[a-z0-9]+(-[a-z0-9]+)*`, at most 200 characters; defaults to the slug of the label. A label without an ASCII slug needs an explicit id. Never derived from a hash. |
| `label` | Single-line canonical name; the entry's H1. |
| `kind` | One of the source document type's `node_kinds` when the source has one. |
| `aliases` | Other names, possibly empty. |
| `source`, `line_start`, `line_end` | Project-relative source path and the 1-based inclusive line range that states the knowledge. |
| `understanding` | `unknown`, `not-yet-understood` or `understood`. |
| `summary` | Required Summary section. |
| `context`, `role` | Optional text sections. |
| `prerequisites`, `pending_prerequisites`, `common_confusions`, `open_questions` | Optional lists of single-line items. |
| `evidence` | Exactly the text of the cited lines, joined with LF. |

For every entry in the selected scope, write one to three compact summary
sentences that let a reader recognize the concept without loading the whole
source. Preserve essential hypotheses, distinctions, notation, units, and the
source's dominant language. Keep a theorem's assumptions and conclusion correct
even when the summary points to the full proof in the source. Synthesize rather
than copy a long span. Do not add external facts to a source-backed entry. The
Evidence quote carries the verbatim source text; choose a line range that
contains the complete statement and nothing unrelated.

## Personal understanding and direct gaps

Entries carry `understanding`: `unknown`, `not-yet-understood` or `understood`.
Preserve the user's stated status. Source coverage and definition availability
do not establish mastery. `pending_prerequisites` lists only direct
source-grounded gaps with the required meaning and use context. A definition may
be available while still unlearned. Finding it never automatically clears the
learning gap. Record the next layer only when the user chooses to study that
dependency. Do not create unexplained nodes or recursively seek all ancestors.

## Relations

First preserve the complete assertion: participant roles, unary/binary/n-ary
arity, repeated references and self-relations, changing states, conditions,
measurements, evidence and epistemic qualifications. A proposition or remark
can provide such an assertion without adding a node. Separate independent
assertions when conditions or epistemic states differ. Review status and
scientific epistemic status are distinct.

For assertions that the current direct-edge adapter can faithfully represent,
use the narrowest supported direct source-grounded relation:

- `prerequisite-for`: understanding the target directly requires the source;
- `implies`: the source assertion logically entails the target;
- `generalizes`: the source strictly extends the target;
- `derived-from`: the source construction or assertion is obtained from the
  target;
- `contrasts-with`: the source explicitly distinguishes the endpoints.

Read every edge literally as `source relation target`. Each edge records
`origin`, `confidence` (use the literal `high` only when the source states the
relation explicitly) and concrete `evidence` text. Do not store transitive
closure, chronology, topical proximity, keyword co-occurrence, or similarity.
Keep `prerequisite-for` acyclic. Both endpoints must be entries after the
delta is applied; removing an entry requires removing its edges in the same
delta.

A worked example or experiment is a typed use/application relation record:
retain its review reference and declared relation type, applied knowledge
references, input/context, conditions, steps, result, evidence and epistemic
status together. Preserve explicit roles and changing states; an instance does
not become a concept solely to hold these fields.

The current `kgdistiller-agent-delta-v1` direct-edge contract does not provide a
lossless full n-ary, application-record or rich gap-history adapter. Simple
direct gaps and understanding use the entry fields above. Retain unsupported
proposals in `.knowledge/build/reviews/`, identify the exact gap and defer their
persistence. Never flatten them into disconnected edges, hide them in unrelated
entries, drop fields or claim a smaller committed delta applied the whole
extraction. Apply a representable subset only when that partial scope has been
explicitly reviewed and authorized. Do not fabricate accepted records or links
for deferred content. Refresh any source-scoped link sheets only after verifying
the actual committed entries.

## Handoff

The extraction handoff contains:

1. the exact registered source paths and the line ranges cited;
2. one disposition (`add`, `update` with its target id, or deferred) per
   candidate, with the `agent resolve` / `agent search` evidence behind it;
3. one reviewed ingest request whose delta holds the entries and the
   evidence-backed direct edges;
4. unresolved decisions and unsupported relation/application records, with
   exact unapplied scope; ambiguity blocks its affected apply, and unsupported
   records remain deferred rather than becoming fabricated direct-edge writes.

Use `$query-kgdistiller` for identity and retrieval and `$ingest-kgdistiller`
for mutation. Never edit `.knowledge/edges.jsonl` or derived files under
`.knowledge/build/` to make a validation gate pass.
