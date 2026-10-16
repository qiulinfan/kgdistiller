---
node_kinds:
  definition: []
  algorithm: []
  architecture: []
  objective: []
  theorem: [about]
  finding: [about]
  example: [uses, setting]
relation_kinds:
  implies: [premise, conclusion]
  requires-for: [prerequisite, dependent]
  compares: [baseline, candidate, setting]
  evaluates: [method, setting, metric]
epistemic: [proved, empirical, stated, conjectured]
---
This is a starting profile for AI research sources. The owner may edit its
kinds, roles and guidance before registering sources with it.

Nodes are the objects the source defines (concepts, algorithms,
architectures, objective functions and quantities) and the statements it
makes about them. A precisely stated result is a theorem, whatever the paper
calls it; a measured result, ablation finding, limitation or hypothesis about
a method is a finding; a worked example is an example. Each links what it is
about through about (uses and setting for an example). Preserve mathematical definitions, input/output contracts,
update rules and the scope in which they apply. Each concept has one record,
owned by the paper that defines or originates it. A concept this source only
uses, such as a standard metric or an earlier method, is never a node of this
source, even when the paper explains it at length: link its record, or, when
the owner captures it, draft it as foreign knowledge from the defining paper. A paper rarely has more than
about twenty concept nodes; when a pass proposes more, re-check each for a
fact turned into a noun, a needless split or a borrowed concept.

A relation records a connection the source states between two or more
records, and is a record of the graph like any node: compares for a
source-backed comparison, evaluates for a reported evaluation of a method on
a setting with a metric, implies and requires-for for stated implications and
dependencies. A result the source states is a node whose roles link what it
is about, one record or several, never a relation. How
important a node is follows from how many records it connects, never from
its kind. An ablation or limitation that changes a result's meaning is
knowledge too.

Role values are knowledge references: links to records or drafts; a concept
nothing records is left out of the values, and a theorem or finding links at
least one record. Datasets, tasks and metrics are concepts and may
fill setting and metric. Resolutions, splits, training settings,
hyperparameters, units, uncertainty and measured values belong in the
record's prose and evidence, never in a role; numbers are never structured,
and comparing them means reading the cited passages.

The reader has already judged the source worth studying. General motivation,
sales claims and routine exposition need not become separate records. Retain
mechanisms, qualifications and evidence needed for the selected knowledge,
wherever they occur; do not select by section name or file extension.

Distinguish proved results, empirical observations, stated assertions and
conjectures by the cited passage's own support: a result this source only
reports from other work is stated. Do not turn an observed association into a
causal implication or a proposed explanation into an established result.
Record a connection only when the cited text itself states it; shared names
and nearby mentions establish no relation. A connection inferred by combining
passages, or between this source and another one that it does not state, is
not a relation of this source.

Use requires for direct understanding prerequisites that a record or draft
holds. Compare meanings and conditions before reusing a record,
especially for same-named mechanisms from different papers.

Copy Evidence quotes verbatim from the cited lines and preserve conditions,
formulas, exceptions and negative results. Set understanding to no only from
the owner's explicit statement.
