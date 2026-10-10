import type { KgGraphContract } from "../src/contract";

export function graphFixture(): KgGraphContract {
  return {
    schema: "kgdistiller-obsidian-graph-v1",
    counts: {
      concepts: 2,
      sources: 1,
      semantic_edges: 1,
      definitions: 2,
    },
    concepts: [
      {
        id: "measure",
        label: "Measure",
        kind: "definition",
        aliases: ["测度"],
        authority: ".knowledge/entries/measure.md",
        understanding: "not-yet-understood",
      },
      {
        id: "sigma-algebra",
        label: "Sigma algebra",
        kind: "definition",
        aliases: [],
        authority: ".knowledge/entries/sigma-algebra.md",
        understanding: "understood",
      },
    ],
    sources: [
      {
        authority: "notes/chapter.tex",
      },
    ],
    semantic_edges: [
      {
        source: "sigma-algebra",
        relation: "prerequisite-for",
        target: "measure",
        evidence: "A measure is defined on a sigma algebra.",
      },
    ],
    definitions: [
      {
        source_authority: "notes/chapter.tex",
        target: "measure",
        line_start: 5,
        line_end: 7,
      },
      {
        source_authority: "notes/chapter.tex",
        target: "sigma-algebra",
        line_start: 1,
        line_end: 3,
      },
    ],
  };
}
