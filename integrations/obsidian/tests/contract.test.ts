import { describe, expect, it } from "vitest";

import { parseGraphContract } from "../src/contract";
import { graphFixture } from "./fixture";

function mutated(change: (graph: Record<string, unknown>) => void): string {
  const graph = graphFixture() as unknown as Record<string, unknown>;
  change(graph);
  return JSON.stringify(graph);
}

describe("kgdistiller Obsidian graph contract", () => {
  it("accepts the entries-and-edges feed", () => {
    const parsed = parseGraphContract(JSON.stringify(graphFixture()));
    expect(parsed.schema).toBe("kgdistiller-obsidian-graph-v1");
    expect(parsed.concepts[0]).toMatchObject({ kind: "definition", aliases: ["测度"], understanding: "not-yet-understood" });
    expect(parsed.semantic_edges[0]?.relation).toBe("prerequisite-for");
    expect(parsed.definitions[0]?.source_authority).toBe("notes/chapter.tex");
  });

  it("accepts a source document of any format", () => {
    for (const authority of ["notes/chapter.txt", "notes/chapter.typ", "papers/paper", "notes/README.md"]) {
      const text = mutated((graph) => {
        (graph.sources as Array<Record<string, unknown>>)[0]!.authority = authority;
        for (const definition of graph.definitions as Array<Record<string, unknown>>) {
          definition.source_authority = authority;
        }
      });
      expect(parseGraphContract(text).sources[0]?.authority).toBe(authority);
    }
  });

  it("rejects unknown top-level feed fields", () => {
    for (const key of ["unexpected", "extra"]) {
      expect(() => parseGraphContract(mutated((graph) => { graph[key] = []; }))).toThrow(
        "document has unsupported or missing properties",
      );
    }
    expect(() => parseGraphContract(mutated((graph) => {
      (graph.concepts as Array<Record<string, unknown>>)[0]!.unexpected = "value";
    }))).toThrow("concepts[0] has unsupported or missing properties");
  });

  it("rejects an unknown understanding state", () => {
    expect(() => parseGraphContract(mutated((graph) => {
      (graph.concepts as Array<Record<string, unknown>>)[0]!.understanding = "partially";
    }))).toThrow("concepts[0].understanding is invalid");
  });

  it("rejects unsafe paths, dangling endpoints and wrong counts", () => {
    expect(() => parseGraphContract(mutated((graph) => {
      (graph.concepts as Array<Record<string, unknown>>)[0]!.authority = "../outside.md";
    }))).toThrow("safe vault-relative path");
    expect(() => parseGraphContract(mutated((graph) => {
      (graph.semantic_edges as Array<Record<string, unknown>>)[0]!.target = "unknown";
    }))).toThrow("unknown endpoint");
    expect(() => parseGraphContract(mutated((graph) => {
      (graph.definitions as Array<Record<string, unknown>>).pop();
      (graph.counts as Record<string, unknown>).definitions = 1;
    }))).toThrow("exactly one definition edge");
    expect(() => parseGraphContract(mutated((graph) => {
      (graph.counts as Record<string, unknown>).sources = 2;
    }))).toThrow("counts.sources does not match its array");
  });
});
