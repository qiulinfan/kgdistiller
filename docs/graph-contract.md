# kgdistiller graph contract

## Authority

Authority is layered. Configured `.md`, `.typ`, and `.tex` markers define
identity. Original source files provide evidence directly, and every
curated atomic entry is an Obsidian-compatible Markdown authority under
`.knowledge/entries/`. Graph records retain durable identity state and accepted semantic relationships;
they are not a disposable cache or a second editable copy of entry content.
In-memory query views, HTML, static sites and managed Obsidian projections do
not become another authority.

Generated source-side definition sheets declare
`<!-- kgdistiller-projection: definition-sheet -->` as the first nonblank content
line after optional YAML frontmatter. Source discovery excludes these views, so
checkbox edits and canonical-link refreshes do not change authority generations.
The marker is explicit; filenames do not determine this boundary. Projection
files cannot contain native identity/reference markers or replace an existing
active knowledge authority. Existing plain sheets without knowledge occurrences
can be adopted without treating their old source-file tracking as knowledge.

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
`kgdistiller-identities-v1` registry before the source is synchronized.

## Node selection

The [shared authoring model](concepts-and-relations.md) applies to papers,
mathematical notes, CS notes, blogs and other registered knowledge sources.
Nodes denote independently meaningful definitions, axioms, precise theorems and
lemmas, algorithms, architectures or other reusable knowledge objects.
Propositions and remarks express relations; examples and experiments express
uses/applications. Preserve complete assumptions, assertions and evidence.

Sections, proofs, exercises, equations and figures do not become nodes merely
because they exist. A new concept requires explicit reviewed identity markers.
Existing user-authored markers remain supported; this authoring policy does not
automatically remove or reclassify legacy nodes.

A local entry requires the source to explain its meaning, not to be its first
historical origin. Same-name source-scoped terms remain retrieval candidates
until their definitions and conditions have been compared. Unexplained external
terms remain pending dependencies. Paper extraction uses around twenty concepts
as a diagnostic, not a general node limit.

Accepted entries live in `.knowledge/`; source def sheets are lightweight links
to those entries. Rich n-ary relations, applications and complete gap-state
records are authoring targets, not additional `kgdistiller-agent-delta-v1` capabilities.
Unsupported updates remain review proposals rather than invented graph data.

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

Every curated active knowledge node has a concise, source-grounded
`.knowledge/entries/<node-id>.md` file using the
`kgdistiller-entry-v1` frontmatter contract. The entry improves search and
explanation but does not replace the identity marker or its statement/proof.
The entry records its native or explicitly selected evidence, evidence digest, and reviewed
definition digest. Graph v2 keeps the body only here. Its manifest binds the
entry Markdown and source evidence; the loader validates and reads that content
to populate the hydrated `text` and `entry` API fields; the graph holds no
copy of entry bodies. `kgdistiller-graph-v2` is the only graph schema that is
read or written; every other discriminator fails closed.

```markdown
---
kgd_schema: "kgdistiller-entry-v1"
kgd_id: "measure-space"
kgd_label: "Measure space"
kgd_entry_origin: "agent-extracted"
kgd_source: "notes/chapter.typ"
kgd_kind: "definition"
kgd_source_sha256: "..."
kgd_definition_sha256: "..."
---

# Measure space

## Summary

A measurable space equipped with a measure.
```

Optional `kgd_kind` preserves the reviewed semantic knowledge type. In the
graph this is `properties.kind` with `kind_origin: reviewed`; only these reviewed
nodes retain `source_kind` for the native scanner's syntax type. A source sync
must not overwrite a reviewed kind with a statement-environment or heading
label. Without a reviewed kind, `kind` already records that source type and
`source_kind` is omitted. Removing `kgd_kind` restores the source type as `kind`
then drops the separate `source_kind`. Entries without this optional field
remain readable.
A type-only edit of an existing entry preserves its content-review fingerprints
and stale status. A reviewed type needs an accepted entry or simultaneously
reviewed content; it is not stored solely on an uncurated scanner node.

Optional entry learning metadata uses `understanding` (`unknown`,
`not-yet-understood`, `understood`) and `pending_prerequisites` (direct gap
descriptions). Absence of understanding means unknown; compilation and
`curation_status` do not certify personal mastery. These fields survive content
updates when omitted. Explicitly passing an empty pending list clears it.

The remaining supported level-two sections are `Context`, `Role`,
`Understanding`, `Prerequisites`, `Pending prerequisites`, `Common confusions`,
`Open questions`, and `Sources`. The prerequisite, confusion, question and source
sections use Markdown `- ` list items. Unknown frontmatter fields or sections fail
closed instead of becoming invisible graph data. Normal IDs use
`<node-id>.md`; Windows-reserved or overlong IDs use a deterministic `_kgd-...`
filename while `kgd_id` remains the stable graph identity.

Markdown, Typst and LaTeX entries default to their original identity source as
evidence. Capture and ingest do not require a converted Markdown companion.
Explicit existing Markdown evidence links remain supported and checked;
conversion is never an automatic fallback when a source is missing.
Rich LaTeX knowledge names use the local obsidian-latex-live converter to
produce passive HTML/MathML labels while preserving native TeX spellings.
Native TeX registries and direct HTML document exports are derived views; see
[the LaTeX source contract](latex-sources.md). They do not change identity or
entry authority and do not route documents through Typst.
The older explicitly invoked derivation command remains available for existing
imports, including PDFs; these are not native PDF graph authorities. Its
Markdown result must be explicitly registered if it is to be scanned. New
project initialization creates no derived source roots or registrations.

An internal derived Markdown file uses `kgdistiller-derived-markdown-v1`
frontmatter to bind the upstream vault-relative source path, source format, and
digest. If the original file is outside every vault, a target vault is
mandatory and the persisted `.knowledge/derived/imports/*.md` deliberately omits
the external path and digest. That Markdown is the first persisted source in
the chain.

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

Semantic curation uses a reviewable `kgdistiller-agent-delta-v1` document:

```json
{
  "schema": "kgdistiller-agent-delta-v1",
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
create/update entry Markdown and semantic relations, but cannot create a second
active identity authority for a knowledge name.

## Incremental workflow

The high-level write path is the transactional API documented in
[`transactional-ingest.md`](transactional-ingest.md). It applies the reviewed
source patch and delta together, rejects stale preconditions, and returns a
canonical receipt. The commands below remain deterministic low-level
primitives for development and compatibility; Agent Skills must not compose
them into a substitute transaction.

```sh
kgdistiller scan --file notes/chapter.typ
kgdistiller apply .knowledge/build/reviews/chapter.delta.json
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
source hash map and canonical digests of the source registry and optional
identity registry. Registry ownership, subject/origin metadata, authored-name
changes, and reviewed aliases therefore belong to the same generation as the
hydrated graph. Store and downstream export operations fail closed when those
registries are newer than the graph. A later sync includes deleted authorities
and both sides of a staged Git rename; full sync also compares the previous
source map, so rename handling does not depend on Git similarity detection. An
exact-content rename can be paired before staging. A file path is provenance,
never graph identity.

Every `source_hashes` value uses the authority-text boundary: read the
Markdown, Typst, or LaTeX file as UTF-8 with universal-newline translation,
represent CRLF and lone CR as LF, then SHA-256 the resulting UTF-8 bytes. All
other characters, including a final newline, remain significant. Scan/rename
matching, sync, transactional ingest, `check`, portable-store verification,
and static export share this one function, so Git's checkout newline policy
cannot create a false source change. Raw-byte hashing remains reserved for
binary and byte-stable artifacts.

Generated graph JSON/JSONL is serialized with LF. Hydration and `check` read
those records and the bound entry Markdown with universal-newline behavior
before comparing their manifest digests and canonical serialization. Thus an otherwise clean CRLF checkout still represents the
same graph generation; semantic text changes continue to invalidate it.

Every definition stores a hash and source span for its enclosing authored
statement (or the smallest conservative source block when no formal statement
wrapper exists). If that hash changes, an existing curated entry becomes
`needs-review`. Semantic edges retain the endpoint hashes against which their
evidence was reviewed and likewise become `needs-review` if an endpoint changes
or becomes orphaned. The data is retained for review, while `curate-check` and
publication reject stale curation. Reapplying reviewed node and edge deltas
refreshes those fingerprints.

## Read-only graph view

CLI, MCP, and the native frontend query the committed JSON artifacts through a
fully hydrated `GraphView`; they do not maintain a secondary database. The
loader validates the manifest before and after loading the graph, snapshot, and
alignments. If the generation changes, it retries a bounded number of times or
fails without exposing a mixed view.

Cross-language identity resolution relies on canonical labels, reviewed global
and scoped aliases, Unicode NFKC/casefold normalization, and explicit alignment
evidence. Lexical score, acronym expansion, and graph proximity retrieve or
rank candidates but never create identity or semantic edges.

`kgdistiller-retrieval-plan-v1` has deterministic identity, lexical, and bounded graph
lanes. It has no semantic/vector lane. Read-only results bind their snapshot
and graph digests so a later transaction can reject a stale decision.

## Derived projections

`kgdistiller-static-export-v1` is a privacy-filtered consumer bundle.
`kgdistiller-obsidian-projection-v1` is a lossy, disposable managed downstream view.
The project root may be the editor vault, where registered Markdown and
`.knowledge/entries/*.md` remain native authorities; the managed projection
subtree is not authority. It must never be registered in
`.knowledge/sources.json`, scanned, or ingested back; regenerate it from the
Markdown, Typst, or LaTeX authorities and the deterministic graph.

Every Obsidian projection includes one `kgdistiller-obsidian-graph-v1` JSON
artifact. It closes concept IDs, source authorities, semantic endpoints,
definition endpoints, and reference endpoints; binds the originating graph,
snapshot, and source inventory digests; and has its own canonical
`bundle_sha256`. This is the only supported input to the Obsidian plugin. The
plugin must not parse `nodes.jsonl`, `edges.jsonl`, or `references.jsonl`
directly. The native graph remains a no-plugin, lossy link projection, while
the plugin graph retains semantic relation labels, direction, and evidence.

## Required invariants

- at most one active authority marker per global knowledge name;
- deterministic graph artifacts and stable IDs;
- one generation-consistent `GraphView` per independent query;
- no dangling semantic edge endpoints;
- no cycles in `contains` or `prerequisite-for`;
- no field-to-field `contains` edges;
- when field classifications are configured, active knowledge nodes satisfy the
  explicit classification policy; unclassified projects need no invented field;
- Typst label HTML contains no active or unsafe content;
- entry Markdown authorities are manifest-bound and are the only persisted entry
  bodies in graph v2; hydration is read-only and rejects missing or changed entries;
- unresolved references and orphaned nodes remain visible diagnostics;
- changed definitions and their affected semantic edges remain visible review
  diagnostics rather than being silently trusted or deleted;
- a scoped sync never rewrites unrelated source state;
- an explicit file scope has exactly one bounded registry owner;
- examples and headings create no implicit nodes.

Diagnostics are computed at load; the graph persists no diagnostics file or
unused manifest classification counters.

Run `kgdistiller audit` for entry coverage, topology, relation counts,
cross-course bridges, field memberships, and edge metadata completeness.
