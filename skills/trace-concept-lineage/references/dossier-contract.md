# Batch concept dossier contract

Write one self-contained Markdown file for one admitted method, mechanism or
concrete prerequisite definition, operation, theorem or inequality actually
needed by a specific paper step.
Do not create a dossier solely for an experimental result, performance explanation,
concept comparison or paper limitation. Do not assume mastery of calculus,
linear algebra or probability. Explain the particular knowledge required by
the paper without expanding into a subject-wide textbook. Under the user's
convention, exact applicable personal entries are mastered: do not create new
dossiers for them or repeat their explanation; retain their links and use steps.

## 1. Node metadata and scope

Include:

- node ID;
- paper-qualified canonical name and any translation requested by the learner;
- paper identifier, version, title and full source digest;
- local name and aliases used by the paper, kept separate from other papers' terms;
- selected meaning and nearby meanings excluded;
- exact paper locations;
- direct prerequisite and neighboring node IDs;
- each dependency's location, exact form and conditions, the step it enables,
  and a verified personal knowledge handle with lookup evidence, or an explicit
  unresolved/unavailable/user-excluded lookup state;
- one-sentence scope.

Preserve the actual source of a prerequisite; distinguish its use in the target
paper from its original definition. The root supplies the default read-only
`$query-kgdistiller` lookup results. Do not treat a matching title as a verified
link; compare the statement and conditions against returned source-backed content.
An exact applicable match is mastered under the user's convention. Link its
existing entry and role here without reteaching or copying it. A name-only,
partial or condition-mismatched entry does not qualify, and one concrete match
never implies mastery of a mathematical subject.

## 2. The problem it solves

Explain what was difficult, ambiguous, inefficient, or impossible before this
concept. Do not use the target concept as part of its own definition.

## 3. Historical lineage

| Date | Milestone | What changed | Evidence |
|---|---|---|---|

Include only milestones that explain a conceptual transition. Cover relevant
predecessor methods, the seminal formulation, and important refinements.
Qualify disputed priority. Preserve each cited paper's meaning when names recur;
compare definitions and operations before claiming that two formulations are
equivalent. Historical or later formulations do not replace the target definition.

## 4. Core intuition and smallest useful example

Give a compact analogy or mental model and immediately state where it breaks.
Then use the smallest concrete example that exposes the mechanism or prerequisite.
State what the example intentionally omits.

## 5. Step-by-step mechanism

Number the causal, algorithmic or derivation steps. For a definition or theorem,
explain its precise statement and how it is applied here. At each step answer:

- What information or object is available?
- What operation occurs?
- What changes?
- Why is the step needed?

## 6. Formal account

Define the notation needed by the paper and its concrete prerequisites before use:

| Symbol | Paper-local or domain meaning | Shape or domain |
|---|---|---|

State or derive only the mathematics necessary to expose the mechanism. Link
each equation to the preceding steps or example. Distinguish definitions,
assumptions, and consequences.

Explain the exact operations, definitions or theorem versions invoked in these
steps, including shapes, domains and assumptions. For example, identify the
specific convolution computation, norm inequality or convergence theorem rather
than a broad subject name. Do not add these examples unless the paper uses them.
Show where each prerequisite is applied; distinguish the author's explicit use
from a dependency inferred by your derivation. For an exact applicable mastered
entry, give its link and use step instead of repeating its explanation. Explain
unmatched or inapplicable prerequisites. Stop at the detail needed to understand
the identified paper steps rather than traversing all background mathematics.

## 7. Variants, contrasts, and misconceptions

Compare nearby concepts and predecessor methods. State what changes, what
remains invariant, and what the concept is not. Use a table when there are at
least three meaningful comparison dimensions.

## 8. Role in the target paper

This section is paper context, not a source of additional knowledge nodes.

Point to exact passages, equations, definitions, theorems, figures, or
experiments. Explain:

- what object in the paper instantiates the concept;
- what result or argument depends on it;
- what would change if it were removed or replaced;
- whether the paper proves, assumes, or merely motivates the connection.

## 9. Assumptions, limits, and failure modes

Keep defining conditions with the mechanism. Empirical limitations and review
assessments remain prose and are not promoted into nodes.

Separate:

- mathematical or modeling assumptions;
- implementation choices;
- empirical limitations;
- known counterexamples or failure regimes;
- limits of the dossier's explanation or evidence.

## 10. Proposed graph relations

Propose only local, evidence-backed relations between admitted methods/mechanisms
and concrete prerequisites for root-agent integration:

| From node | Relation | To node | Why valid | Source or inference |
|---|---|---|---|---|

Use only:

- `prerequisite-for`
- `motivates`
- `implemented-by`
- `special-case-of`
- `generalizes`
- `contrasts-with`
- `used-by`
- `produces`
- `paper-instantiates`

Do not create a standalone Mermaid graph. The root agent owns the global
graph and may reject or reverse proposed edges.
Keep verified personal-entry links separate from the local relation table;
never merge paper-scoped identities because they share a name or linked entry.

## 11. Sources and reading pointers

Keep citations adjacent to claims throughout. End with three to eight
annotated sources ordered from accessible to rigorous. For each source, state
what to learn and which source files, sections or labels matter.

Prefer:

1. original papers and official specifications;
2. peer-reviewed surveys or monographs;
3. authoritative textbooks and university course notes;
4. official documentation for implementation details;
5. high-quality explanatory sources for intuition only.

## 12. Uncertainty and handoff

List:

- unresolved or disputed claims;
- inaccessible sources;
- synthesis not stated explicitly by a source;
- notation mismatches between the literature and the target paper;
- prerequisite or successor nodes that the root agent should reconsider.

Do not add understanding questions or model answers.
