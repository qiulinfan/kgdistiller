# Shared knowledge model

kgdistiller is a personal research knowledge base. Papers, mathematical notes,
computer-science notes, blogs and project documents supply knowledge through the
same model. The accepted knowledge lives in the project's visible `knowledge/`
root; source-scoped sheets are views of that metadata.

## Sources, metadata and views

Native Markdown, Typst and LaTeX markers establish explicit knowledge identity.
Source passages supply definitions, assumptions, proofs and evidence. Reviewed
atomic entries hold source-grounded knowledge content in `knowledge/entries/`;
accepted semantic relationships and registries are also durable knowledge state.
Do not infer identity from headings, names, document order or co-occurrence.

Extraction prepares a metadata update, including complete meanings, formulas,
conditions, evidence and relations. Review it against existing identities, then
apply it through a supported transaction. Unaccepted proposals belong in
`knowledge/build/reviews/`, not in the accepted entry collection.

A **def sheet** selects the knowledge associated with a source and displays its
names, types, precise source locations and links to actual accepted metadata.
The full definition stays at the linked entry. A **pending sheet** exposes the
source's unresolved dependencies and links to supported gap state. These views
can accompany any knowledge source, not only a paper. They do not create a
second set of independently edited definitions. Proposed edits made through a
view must be reviewed and applied to knowledge metadata before the view refreshes.

This preserves both directions: knowledge records link to their source evidence,
and source sheets link to the knowledge they explain or use. Nodes and complete
relations supply retrieval content; sheets provide source-based navigation.

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
an independently meaningful node. Existing user-marked identities remain intact
until an explicit review authorizes their change; this policy is not a migration.

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

The current canonical graph supports source-grounded atomic entries and direct
binary relations. Binary self-edges are representable. The read-only compiled
library retains authored scientific attributes but still uses node-centric
search and comparison-claim packing. It is not a canonical write protocol and
has no general first-class relation/application retrieval API.

A lossless adapter for full n-ary assertions, applications and dependency-gap
state is not yet implemented. Preserve unsupported proposals in the review area
and identify exactly what remains unapplied. Do not invent accepted records,
metadata links or commands, and do not claim a partial transaction synchronized
a complete extraction. The shared model is the authoring direction; new storage
fields or adapters require scoped implementation and round-trip validation.

Keep historical libraries, annotations and experiment results intact. Evaluate
new representations separately rather than retroactively changing earlier scores.
