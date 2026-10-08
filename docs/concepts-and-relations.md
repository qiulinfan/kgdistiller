# Concept definitions and factual relations

This is the proposed authoring model for the compiled library. The current
retriever still has node-centric search, binary edges and comparison claims;
first-class relation lookup and selection are not yet implemented.

A definition node denotes an independently meaningful, noun-like object:
a method, architecture, component, objective, operation, evaluation protocol,
quantity or precisely defined phenomenon. Its content explains what the object
is, with its defining conditions and inputs/outputs. Merely turning a sentence
into a noun phrase does not make its empirical proposition a concept.

Empirical observations, comparisons, claims and explanatory hypotheses belong
to relations. Their conditions, measurements, units, evidence, source scope
and epistemic qualifications remain attached to the whole assertion. A
precisely stated, important mathematical theorem/lemma may be a definition
node when its assumptions, conclusion and proof/source status are explicit.
An informal hypothesis is not promoted through that exception.

The paper must actually explain a local concept before it becomes a local
definition. Unexplained external participants remain dependency gaps to trace
to their defining authority. A paper usually has around twenty or fewer real
concepts; a larger count triggers review for nominalized facts, redundant
components and configuration records. This is a diagnostic, not a hard quota.

## One assertion, all participant roles

A factual relation can use a small record:

```json
{
  "predicate": "comparison",
  "participants": [
    {"role": "subject", "target": "A"},
    {"role": "baseline", "target": "B"},
    {"role": "measurement", "target": "M"}
  ],
  "statement": "The source-scoped assertion.",
  "conditions": ["The shared experimental setting."],
  "evidence": [],
  "epistemic": {"status": "empirical"}
}
```

The names and predicates above are caller-supplied data. Participants are
role bindings, not an unordered set of nodes. Preserve repeated targets when
one concept has distinct roles or states. One participant is sufficient for a
property observation; two make a binary relation, and more make an n-ary
relation. A comparison can include the same method in two configurations,
forming a self-relation without creating two method concepts.

Do not flatten an assertion into unrelated pairwise edges: that loses which
subject, baseline, measurement and conditions jointly support one result.
Split independent findings when their conditions or epistemic status differ;
one relation must not become an unstructured container for a whole paragraph.
Keep uncertainty on the relation, so a proposed role or association does not
become an equivalence or a proven causal mechanism. A cyclic relation does
not establish a circular proof; dynamic input/output states remain explicit.
Predicates and participant roles need stable, declared meanings. Review a
new predicate before treating it as canonical; a sentence-specific label is
not itself a new logical relation type.

This use of participant roles and relation attributes is consistent with the
informative [W3C n-ary relation patterns](https://www.w3.org/TR/swbp-n-aryRelations/).
It does not require adopting RDF/OWL or adding a graph service.

## Retrieval must retain the facts

Concepts and relations must both be searchable and directly readable. A
relation may have a lookup handle without becoming a concept definition.
Concept navigation should expose incident relations with their complete roles,
conditions and evidence. Packing must preserve the whole selected assertion
and report unresolved or omitted participant concepts; a requirement for two
distinct selected endpoints would incorrectly drop unary and self-relations.

Existing complete-evidence tasks still require the factual support and its
qualifications. Fewer definition nodes does not mean less evidence. Preserve
historical libraries, reference annotations and scores; evaluate a revised
representation as a new condition rather than retroactively changing old
retrieval results.
