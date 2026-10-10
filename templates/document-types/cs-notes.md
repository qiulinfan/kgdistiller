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
Do not create a node from a term that is only named or cited.

Use requires-for for an explicit dependency and implements for an explicit
implementation/specification connection. Use complexity-bound for a stated
resource bound, keeping its formula, input-size definition, computation model
and worst-case, expected or amortized qualification in the prose. Separate
measured timings from proved bounds. Examples retain their setting and result.

The graph holds the knowledge the reader wants to retain from a source
already judged useful. Routine exposition, conventions and motivation need
not each become records. Preserve exceptions, tradeoffs, failure cases and
conditions that affect the retained knowledge. Section names, source syntax
and file extensions do not determine admission.

Relations may bind multiple participants; preserve jointly required
participants together. Record only source-stated connections. Keep direct
understanding prerequisites in requires and unexplained terms as plain pending
values. Compare definitions and conditions before reusing an existing record.

Copy Evidence quotes verbatim from the cited lines. Use proved, stated and
empirical according to the source's support, and set understanding only from
the owner's explicit statement.
