# Shared knowledge model

kgdistiller is a personal research knowledge base. Papers, mathematical notes,
computer-science notes, blogs and project documents supply knowledge through the
same model. The accepted knowledge lives in each base's hidden `.knowledge/`
tree; source-scoped sheets are views of that metadata.

## Source document types: user-owned extraction rules

The source model separates three independent concerns:

| Concern | Meaning |
|---|---|
| File format | How the original document is stored. kgdistiller ignores it: every registered source is UTF-8 text read as numbered lines |
| Document type | A user-registered extraction profile: which knowledge objects to extract and how to recognize them |
| Knowledge domain | The subject matter of the source or an individual knowledge entry |

Document types belong to the user's knowledge base, not a built-in enumeration
in kgdistiller. A user might register `airesearch`, `math-notes` and `cs-notes`;
these are examples, not required names or product defaults. Each profile declares
the node types to look for and extraction rules, including what should instead
be represented as a relation/application or left as a direct pending dependency.
The shared evidence and identity requirements still apply.

The minimum registration is a type name, its intended node kinds, and
human-readable extraction guidance. A source is associated with a type only
through the glob that registers it. A node has its own `kind`, chosen from
that profile's `node_kinds`; it is not typed merely as `airesearch` because its
source uses that profile.
For example, an AI research source may explain an architecture and also state
a theorem if its registered profile allows both. The profile guides extraction;
it neither supplies missing scientific content nor creates knowledge identity.

Document type selection must not be inferred from the extension, folder name
or topic alone. Mathematical notes and AI research documents can both be `.md`
and discuss linear algebra while using different extraction rules. An agent
can propose a source classification from its content; the selected profile and
its user-owned rules must be explicit when applying structured extraction.
Registering a new type requires editing knowledge-base data, not changing the
product code or adding a hardcoded extraction branch.

Sources remain in their original files and formats. kgdistiller never parses
their syntax and never converts them: `.md`, `.typ`, `.tex`, `.txt` and any
other text document are treated identically. Evidence is a file path, a line
range and the quoted text of those lines. A def sheet displays links and is not
a copy of the source.

Each type is one file, `$KGDISTILLER_HOME/types/<name>.md` (default
`~/.knowledge/types/`), for example `types/worked-notes.md`:

```markdown
---
node_kinds: [definition, theorem]
relation_kinds:
  implies: [premise, conclusion]
epistemic: [proved, stated]
---
Extract explained definitions and precise theorems; represent worked examples
as applications.
```

The file stem is the type name and matches `^[a-z0-9][a-z0-9-]*$`. The
frontmatter is read with PyYAML's `BaseLoader` and has only these keys:
`node_kinds` (required, a non-empty list of unique kind slugs),
`relation_kinds` (optional, mapping each kind slug to a non-empty list of
unique role slugs matching `^[a-z][a-z0-9-]*$`, none of them a fixed entry key)
and `epistemic` (optional, a list of unique slugs). No kind may be both a node
kind and a relation kind. The body is the guidance and must not be empty. Names
and kinds are caller-supplied; an unknown key, a malformed list or an empty
body is an error.

Sources map to types through globs relative to the base root under
`bases.<name>.sources` in `$KGDISTILLER_HOME/config.json`:

```json
{"bases": {"research": {"path": "~/research",
  "sources": {"notes/**/*.md": "worked-notes"}}}, "embedding": null}
```

A file matched by at least one glob is a registered source. Globs follow
Python's `glob` semantics, and hidden files and directories never match. Every
registered source has exactly one type: globs that map one file to two
different types are an error, so there is no unclassified source and no
profile-less extraction.

`kgd scan --file SOURCE --base B` returns, for each requested file, its path
relative to the base root, the base, its `type`, that type's `profile`
(`node_kinds`, `relation_kinds`, `epistemic`, `guidance`) and every line with
its 1-based number. It writes nothing and works before the source has any
entries, so an agent reads the guidance and the exact line numbers before
choosing nodes. Every entry's `kind` must belong to its source type's
`node_kinds`; `check` and ingest enforce this. Entries are still nodes in this
release, so `relation_kinds` is listed for extraction but is not an entry kind.

RAG architecture is still open. Source types must not silently impose hard
retrieval filters or choose an embedding/index backend.

## Minimum stored metadata

A base's knowledge lives only in `<root>/.knowledge/`:

```text
.knowledge/
├── entries/<id>.md         # one reviewed entry per knowledge node
├── edges.jsonl             # accepted typed relationships
└── build/                  # rebuildable local work (ignored)
```

Source globs and document types live in the home
(`$KGDISTILLER_HOME/config.json` and `types/`), not in the base. Source
registration does not create knowledge nodes.

An entry carries everything its node needs: id, label, kind, aliases, source
path and line range, understanding, the human sections and the verbatim
Evidence quote. `edges.jsonl` holds the accepted relations. Together they are
the durable knowledge; nothing else under `.knowledge/` is. `build/` holds
ingest journals, plans, receipts, review drafts, retrieval caches and the
Obsidian plugin's graph feed, which `kgdistiller export obsidian` rebuilds.

## Sources, metadata and views

Source passages supply definitions, assumptions, proofs and evidence. Reviewed
entries hold source-grounded knowledge content in `.knowledge/entries/`;
accepted semantic relationships are also durable knowledge state. Knowledge
nodes come from reviewed capture or curation guided by the source's document
type. Do not infer identity from headings, names, document order or
co-occurrence.

Extraction prepares a metadata update, including complete meanings, formulas,
conditions, evidence and relations. Review it against existing identities, then
apply it through a supported transaction. Unaccepted proposals belong in
`.knowledge/build/reviews/`, not in the accepted entry collection. `build/` is
excluded from Obsidian hidden-folder indexing by default; remove `build` from
the plugin's exclusion list to open review drafts there.

A **def sheet** selects the knowledge associated with a source and displays its
names, types, precise source locations and links to actual accepted metadata.
The full definition stays at the linked entry. A **pending sheet** exposes the
source's unresolved dependencies and links to supported gap state. These views
can accompany any knowledge source, not only a paper. They do not create a
second set of independently edited definitions. Proposed edits made through a
view must be reviewed and applied to knowledge metadata before the view refreshes.

Before acceptance, a sheet can also show clearly labeled review-draft links as
Markdown tasks. The user selects those tasks in Obsidian and requests harvest.
The script imports only the checked reviewed proposals and replaces their draft
links with real metadata links. Unchecked rows and annotations remain. A
selection checkbox never changes the separate understanding status.

This preserves both directions: knowledge records link to their source evidence,
and source sheets link to the knowledge they explain or use. Nodes and complete
relations supply retrieval content; sheets provide source-based navigation.

## Partial coverage and personal understanding

A def sheet can cover one selected concept, a section or a whole source. State
that scope explicitly and preserve rows outside it. An omitted concept means
unprocessed or outside the selected scope, not absent from the source. Reading
or fully distilling a source does not establish mastery of its knowledge.

Keep three questions independent: what source material was processed, whether
a definition has been found and explained, and whether the user understands it.
Atomic entries support `understanding` with `unknown`, `not-yet-understood` or
`understood`; absence means unknown. Record the user's stated understanding and
preserve it on unrelated updates. Neither retrieval matches, complete curation,
a new defining source nor an agent-generated explanation automatically promotes
it to understood.

`pending_prerequisites` records only the current entry's direct gaps as concise
source-grounded text: the term, required meaning and use context, including why
it remains pending. A definition can already exist while the user still needs
to learn it. Finding or linking it does not clear that learning gap. Unexplained
terms have no invented canonical definition node.

Each entry knows its immediate prerequisites. When the user later studies one
of them, that entry can acquire its own direct gaps. Do not pre-expand the chain,
copy all ancestors into the original entry, or recursively search their sources
during ordinary capture. Existing graph traversal can follow reviewed direct
links when a later task actually needs more context.

## Two writing scopes

Full source distillation is explicitly requested, generally for the user's own
notes or familiar material, and checks the selected source
as a whole, while still allowing unresolved dependencies and incomplete personal
understanding. `$capture-kgdistiller` saves or updates one selected knowledge item
while reading new material. It uses just the local evidence and necessary nearby context,
compares identity once, then hands one bounded transaction to ingest. Additional
reading or lookup is justified only by a concrete ambiguity or missing defining
condition. The transaction may leave unrelated sibling entries pending.

Both paths write the same knowledge entries and refresh the same source link
views. Single-item capture leaves source coverage partial. There is no separate
inbox knowledge model or later mandatory full-source rewrite.

## Knowledge nodes

A node denotes a stable, independently meaningful knowledge object. Its content
must preserve what it means and the conditions under which it applies.

| Source example | Typical nodes |
|---|---|
| Mathematical notes | Definitions, axioms, precisely stated theorems and lemmas |
| Computer-science notes | Algorithms, architectures, data structures, interfaces |
| Papers | Explained methods, objectives, components, quantities, defined phenomena |
| Blogs and project documents | Explicitly explained reusable concepts and constructions |

A theorem includes its assumptions, conclusion and proof/source status. A
method or algorithm includes its inputs, outputs, defining steps and conditions.
A heading, theorem wrapper or nominalized observation alone does not establish
an independently meaningful node. Existing entries remain intact until an
explicit review authorizes their change.

A source-local definition is admitted when that source actually explains the
object. It need not be the first source to introduce the term. Preserve source
scope and the distinction between a local operational definition and an
originating definition. Same-named entries remain separate until their meanings
and conditions have been compared. A merely mentioned or used external term
remains pending, with its use site and required meaning. Trace it to an applicable
primary defining source before proposing resolution; a name match cannot close
it. For older mathematical foundations, an authoritative textbook may be the
appropriate source rather than a uniquely originating paper.

Around twenty concepts is a paper-extraction diagnostic for redundant components
or nominalized facts. It is not a hard quota or a general limit on notes.

## Relations and applications

Propositions, remarks, empirical observations, comparisons and explanatory
hypotheses express relations. Preserve the complete assertion, its participants,
conditions, evidence and epistemic qualifications. They do not acquire concept
identity simply because a source gives them a heading.

Examples and experiments are **use/application relations**: they record how
knowledge is applied to a concrete case. Keep the applied concepts, input or
context, checked assumptions, relevant steps, result and evidence together. A
worked example, successful experiment, failed application or counterexample can
all carry such a record. A reusable method introduced by an example may merit
its own node; the example itself remains an application of that knowledge.

Participants have explicit roles, not just membership in an unordered set.
A property observation may have one participant; an assertion may involve two
or many. Preserve repeated targets in different roles, states or configurations,
including self-relations. A self-relation or cycle does not establish a circular
proof. Split independent findings when their conditions or epistemic status
differ. Do not flatten one assertion into disconnected pairwise edges or
turn uncertain association into equivalence or causation.

Predicate and role meanings must remain stable and declared. A relation may
have a lookup reference without becoming a concept definition. This distinction
is consistent with the informative
[W3C n-ary relation patterns](https://www.w3.org/TR/swbp-n-aryRelations/); it does
not require adopting RDF/OWL or adding a graph service.

## Retrieval and current implementation boundary

Retrieval should expose selected nodes together with their applicable relations,
applications, conditions and evidence. Packing must preserve complete assertions
and report unresolved participants. Reducing the number of concept nodes must
not remove factual support from complete-evidence tasks.

The current knowledge store supports source-grounded entries and direct binary
relations. Binary self-edges are representable (except for the acyclic `prerequisite-for`). The read-only compiled
library retains authored scientific attributes but still uses node-centric
search and comparison-claim packing. It is not a canonical write protocol and
has no general first-class relation/application retrieval API.

Simple direct dependency gaps and personal understanding are supported in
atomic entries. A lossless adapter for full n-ary assertions, applications and
rich dependency-gap history is not yet implemented. Preserve unsupported proposals in the review area
and identify exactly what remains unapplied. Do not invent accepted records,
metadata links or commands, and do not claim a partial transaction synchronized
a complete extraction. The shared model is the authoring direction; new storage
fields or adapters require scoped implementation and round-trip validation.

Keep historical libraries, annotations and experiment results intact. Evaluate
new representations separately rather than retroactively changing earlier scores.
