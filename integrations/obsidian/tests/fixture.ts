import {
  calculateBundleDigest,
  type KgGraphContract,
} from "../src/contract";

export async function graphFixture(): Promise<KgGraphContract> {
  const graph: Record<string, unknown> = {
    schema: "kgdistiller-obsidian-graph-v1",
    source: {
      graph_schema: "kgdistiller-graph-v1",
      graph_sha256: "a".repeat(64),
      snapshot_sha256: "b".repeat(64),
      source_hashes_sha256: "c".repeat(64),
    },
    counts: {
      concepts: 2,
      sources: 1,
      semantic_edges: 1,
      definitions: 2,
      references: 1,
    },
    concepts: [
      {
        id: "sigma-algebra",
        label: "Sigma algebra",
        note_path: "concepts/Sigma algebra.md",
        authority: "notes/chapter.md",
        curation_status: "current",
        aliases: ["Sigma algebra"],
        fields: ["mathematics"],
      },
      {
        id: "measure",
        label: "Measure",
        note_path: "concepts/Measure.md",
        authority: "notes/chapter.md",
        curation_status: "pending",
        aliases: ["Measure"],
        fields: ["probability"],
      },
    ],
    sources: [
      {
        authority: "notes/chapter.md",
        note_path: "sources/notes/chapter.md.md",
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
        source_authority: "notes/chapter.md",
        target: "sigma-algebra",
        line_start: 1,
        line_end: 3,
      },
      {
        source_authority: "notes/chapter.md",
        target: "measure",
        line_start: 5,
        line_end: 7,
      },
    ],
    references: [
      {
        id: "notes/chapter.md:9:measure",
        source_authority: "notes/chapter.md",
        target: "measure",
        label: "Measure",
        line: 9,
        context: "A reference to Measure.",
      },
    ],
    bundle_sha256: "0".repeat(64),
  };
  graph.bundle_sha256 = await calculateBundleDigest(graph);
  return graph as unknown as KgGraphContract;
}
