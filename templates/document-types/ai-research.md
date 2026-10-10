---
node_kinds: [definition, algorithm, architecture, objective, result]
relation_kinds:
  implies: [premise, conclusion]
  requires-for: [prerequisite, dependent]
  compares: [baseline, candidate, setting]
  evaluates: [method, setting, metric]
  example: [uses, setting]
epistemic: [proved, empirical, stated, conjectured]
---
This is a starting profile for AI research sources. The owner may edit its
kinds, roles and guidance before registering sources with it.

Retain concepts the source actually defines, algorithms, architectures,
objective functions and precise reusable results. Preserve mathematical
definitions, input/output contracts, update rules and the scope in which they
apply. A name, acronym, citation or mention alone does not define a new node.

Represent explicit connections as relations with all participants bound to
their roles. Use compares for a source-backed comparison and evaluates for a
reported evaluation. Keep datasets, splits, baselines, training settings,
metrics, units, uncertainty and measured outcomes in the relation's prose.
An ablation or limitation that changes the result's meaning is knowledge too.

The reader has already judged the source worth studying. General motivation,
sales claims and routine exposition need not become separate records. Retain
mechanisms, qualifications and evidence needed for the selected knowledge,
wherever they occur; do not select by section name or file extension.

Distinguish proved results, empirical observations, stated assertions and
conjectures. Do not turn an observed association into a causal implication or
a proposed explanation into an established result. Store only connections
the source states; shared names and nearby mentions establish no relation.

Use requires for direct understanding prerequisites. Unexplained terms remain
plain pending values. Compare meanings and conditions before reusing a record,
especially for same-named mechanisms from different papers.

Copy Evidence quotes verbatim from the cited lines and preserve conditions,
formulas, exceptions and negative results. Set understanding only from the
owner's explicit statement.
