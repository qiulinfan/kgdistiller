# Batch concept dossier contract

Write one self-contained Markdown file for one domain concept. The learner is
assumed to know foundational calculus, linear algebra, and probability.
Explain the domain idea carefully without reteaching those foundations.

## 1. Node metadata and scope

Include:

- node ID;
- canonical literature name and any translation requested by the learner;
- aliases used by the paper or literature;
- selected meaning and nearby meanings excluded;
- exact paper locations;
- direct prerequisite and neighboring node IDs;
- one-sentence scope.

State explicitly: `MATH-FOUNDATION is assumed.`

## 2. The problem it solves

Explain what was difficult, ambiguous, inefficient, or impossible before this
concept. Do not use the target concept as part of its own definition.

## 3. Historical lineage

| Date | Milestone | What changed | Evidence |
|---|---|---|---|

Include only milestones that explain a conceptual transition. Cover relevant
predecessor methods, the seminal formulation, and important refinements.
Qualify disputed priority.

## 4. Core intuition and smallest useful example

Give a compact analogy or mental model and immediately state where it breaks.
Then use the smallest concrete example that exposes the domain mechanism.
State what the example intentionally omits.

## 5. Step-by-step mechanism

Number the causal or algorithmic steps. At each step answer:

- What information or object is available?
- What operation occurs?
- What changes?
- Why is the step needed?

## 6. Formal account

Define domain-specific and paper-local notation before use:

| Symbol | Paper-local or domain meaning | Shape or domain |
|---|---|---|

State or derive only the mathematics necessary to expose the mechanism. Link
each equation to the preceding steps or example. Distinguish definitions,
assumptions, and consequences.

Do not explain generic derivatives, integrals, matrix multiplication,
eigenvalues, expectations, covariance, conditioning, Gaussian distributions,
or similar foundations. A short reminder of their role in this mechanism is
allowed.

## 7. Variants, contrasts, and misconceptions

Compare nearby concepts and predecessor methods. State what changes, what
remains invariant, and what the concept is not. Use a table when there are at
least three meaningful comparison dimensions.

## 8. Role in the target paper

Point to exact passages, equations, definitions, theorems, figures, or
experiments. Explain:

- what object in the paper instantiates the concept;
- what result or argument depends on it;
- what would change if it were removed or replaced;
- whether the paper proves, assumes, or merely motivates the connection.

## 9. Assumptions, limits, and failure modes

Separate:

- mathematical or modeling assumptions;
- implementation choices;
- empirical limitations;
- known counterexamples or failure regimes;
- limits of the dossier's explanation or evidence.

## 10. Proposed graph relations

Propose only local, evidence-backed relations for root-agent integration:

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

## 11. Sources and reading pointers

Keep citations adjacent to claims throughout. End with three to eight
annotated sources ordered from accessible to rigorous. For each source, state
what to learn and which sections or pages matter.

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
