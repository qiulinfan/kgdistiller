---
node_kinds: [definition, axiom, theorem]
relation_kinds:
  implies: [premise, conclusion]
  requires-for: [prerequisite, dependent]
  example: [uses, setting]
epistemic: [proved, stated]
---
This is a starting profile for mathematics notes. The owner may edit its
kinds, roles and guidance before registering sources with it.

Retain explicitly defined mathematical objects, stated axioms and precisely
stated theorems. Keep domains, hypotheses, quantifiers, notation conventions,
formulas and conclusions together in the record's prose. Use a theorem node
for a reusable precise result. Any other statement that can be true or false,
such as a proposition, remark or consequence, is a relation binding the
records it connects, recorded when the source explicitly supplies that
connection. A connection inferred by combining passages is a compiler
judgment for the compiler guide, not a relation of these notes.

Use implies for a stated implication and requires-for for a stated proof or
derivation dependency. Bind all jointly required premises to the same relation;
do not replace a joint premise with separate implications. Examples and
counterexamples use example: setting binds the space, structure or object the
example works in, as a link or a pending term, and the concrete construction
and outcome stay in the prose. Role values are always knowledge references,
never literal descriptions.

Read relevant proofs and examples when they establish conditions or scope.
Retain proof steps as knowledge when the owner needs the method or dependency;
routine exposition and motivation need not each become a record. Decide by
meaning, not by section names, markers or the source's file extension.

Set proved only when the cited text supplies a proof; otherwise use stated.
Record direct understanding prerequisites in requires. A term used without a
definition remains a plain pending value; do not invent a definition or bind
it by name. Compare existing definitions and conditions before reusing an id.

Copy Evidence quotes verbatim from the cited source lines. Preserve formulas,
exceptions and limitations. Set understanding only from the owner's explicit
statement; extracting a record does not establish that it is understood.
