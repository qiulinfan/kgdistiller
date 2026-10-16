---
node_kinds:
  definition: []
  axiom: []
  theorem: [about]
  example: [uses, setting]
relation_kinds:
  implies: [premise, conclusion]
  requires-for: [prerequisite, dependent]
epistemic: [proved, stated]
---
This is a starting profile for mathematics notes. The owner may edit its
kinds, roles and guidance before registering sources with it.

Retain explicitly defined mathematical objects, stated axioms, precisely
stated results and worked examples. Keep domains, hypotheses, quantifiers,
notation conventions, formulas and conclusions together in the record's
prose. Every precisely stated result is a theorem node, whatever the notes
call it (theorem, proposition, lemma, corollary, remark); its label may keep
that name, and about links the objects it is a statement about. How important
a result is follows from how many records it connects, never from its kind.
A relation records a connection the notes state between two or more records,
such as one result implying another, and is itself a record of the graph. A connection inferred by combining passages is not a relation of
these notes. A concept these notes use but define elsewhere is a link to its
defining record, never a second node.

Use implies for a stated implication between results and requires-for for a
stated proof or derivation dependency. Bind all jointly required premises to
the same relation; do not replace a joint premise with separate implications.
An example or counterexample is an example node: uses links the objects it
illustrates, setting the space, structure or object it works in, and the
concrete construction and outcome stay in its prose. Role values are always knowledge references,
never literal descriptions.

Read relevant proofs and examples when they establish conditions or scope.
Retain proof steps as knowledge when the owner needs the method or dependency;
routine exposition and motivation need not each become a record. Decide by
meaning, not by section names, markers or the source's file extension.

Set proved only when the cited text supplies a proof; otherwise use stated.
Record direct understanding prerequisites that a record or draft holds in
requires; do not invent a definition or bind a concept by name. Compare existing definitions and conditions before reusing an id.

Copy Evidence quotes verbatim from the cited source lines. Preserve formulas,
exceptions and limitations. Set understanding to no only from the owner's
explicit statement.
