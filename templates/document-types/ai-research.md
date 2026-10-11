---
node_kinds: [definition, algorithm, architecture, objective, theorem]
relation_kinds:
  implies: [premise, conclusion]
  requires-for: [prerequisite, dependent]
  compares: [baseline, candidate, setting]
  evaluates: [method, setting, metric]
  property: [subject, uses]
  example: [uses, setting]
epistemic: [proved, empirical, stated, conjectured]
---
This is a starting profile for AI research sources. The owner may edit its
kinds, roles and guidance before registering sources with it.

Nodes are the noun-like objects the source defines: concepts, algorithms,
architectures, objective functions and quantities, plus theorems the source
states precisely. Preserve mathematical definitions, input/output contracts,
update rules and the scope in which they apply. A name, acronym, citation or
mention alone does not define a new node, and background the source only
restates, such as a standard metric or an earlier method, stays a pending term
or a link to its existing record unless this source explains it in its own
right. A paper rarely has more than about twenty concept nodes; when a pass
proposes more, re-check each for a fact turned into a noun, a needless split
or restated background.

A statement that can be true or false is a relation, not a node: measured
results, comparisons, ablation findings, limitations and explanatory
hypotheses bind their participants to roles. Use compares for a source-backed
comparison, evaluates for a reported evaluation and property for a finding
about one concept, which may be its only participant. An ablation or
limitation that changes a result's meaning is knowledge too.

Role values are knowledge references: links to records, or pending terms for
concepts not yet recorded. Datasets, tasks and metrics are concepts and may
fill setting and metric. Resolutions, splits, training settings,
hyperparameters, units, uncertainty and measured values belong in the
relation's prose and evidence, never in a role; numbers are never structured,
and comparing them means reading the cited passages.

The reader has already judged the source worth studying. General motivation,
sales claims and routine exposition need not become separate records. Retain
mechanisms, qualifications and evidence needed for the selected knowledge,
wherever they occur; do not select by section name or file extension.

Distinguish proved results, empirical observations, stated assertions and
conjectures. Do not turn an observed association into a causal implication or
a proposed explanation into an established result. Record a connection only
when the cited text itself states it; shared names and nearby mentions
establish no relation. A connection inferred by combining passages, or between
this source and another one that it does not state, is a compiler judgment for
the compiler guide, not a relation of this source.

Use requires for direct understanding prerequisites. Unexplained terms remain
plain pending values. Compare meanings and conditions before reusing a record,
especially for same-named mechanisms from different papers.

Copy Evidence quotes verbatim from the cited lines and preserve conditions,
formulas, exceptions and negative results. Set understanding only from the
owner's explicit statement.
