---
node_kinds: [definition, algorithm, data-structure, architecture, invariant]
relation_kinds:
  requires-for: [prerequisite, dependent]
  implements: [implementation, specification]
  complexity-bound: [subject, resource]
  example: [uses, setting]
epistemic: [proved, stated, empirical]
---
This is a starting profile for computer-science notes. The owner may edit its
kinds, roles and guidance before registering sources with it.

Retain defined concepts, algorithms, data structures, architectures and
precisely stated invariants. Preserve preconditions, input/output contracts,
state transitions, pseudocode or formulas needed to understand each record.
Do not create a node from a term that is only named or cited. Any other
statement that can be true or false, such as a measured behaviour or a
comparison, is a relation binding the records it concerns, not a node.

Use requires-for for an explicit dependency and implements for an explicit
implementation/specification connection. Use complexity-bound for a stated
resource bound: resource binds the resource concept, and the formula,
input-size definition, computation model and worst-case, expected or
amortized qualification stay in the prose. Separate measured timings from
proved bounds. Examples bind the concept they work in as setting and keep the
concrete input and result in the prose. Role values are always knowledge
references, never literal descriptions.

The graph holds the knowledge the reader wants to retain from a source
already judged useful. Routine exposition, conventions and motivation need
not each become records. Preserve exceptions, tradeoffs, failure cases and
conditions that affect the retained knowledge. Section names, source syntax
and file extensions do not determine admission.

Relations may bind multiple participants; preserve jointly required
participants together. Record only connections the cited text states; a
connection inferred by combining passages is not a relation of these notes.
Keep direct understanding prerequisites that a record or draft holds in
requires. A concept these notes use but define elsewhere is a link to its
defining record, never a second node; compare definitions and conditions
before linking an existing record.

Copy Evidence quotes verbatim from the cited lines. Use proved, stated and
empirical according to the source's support, and set understanding to no
only from the owner's explicit statement.
