import { describe, expect, it } from "vitest";

import {
  calculateBundleDigest,
  canonicalJson,
  parseGraphContract,
} from "../src/contract";
import { graphFixture } from "./fixture";

describe("kgdistiller Obsidian graph contract", () => {
  it("uses the same canonical JSON and digest as the Python contract", async () => {
    const value = { b: [2, 1], a: "é" };
    expect(canonicalJson(value)).toBe('{"a":"é","b":[2,1]}');
    expect(await calculateBundleDigest(value)).toBe(
      "265cdd44ca612f13fd2b8e14f6913a5513adf3142e6c82317e55ba51948f43f2",
    );
  });

  it("accepts a closed, source-backed typed graph", async () => {
    const graph = await graphFixture();
    const parsed = await parseGraphContract(JSON.stringify(graph));
    expect(parsed.schema).toBe("kgdistiller-obsidian-graph-v1");
    expect(parsed.semantic_edges[0]?.relation).toBe("prerequisite-for");
    expect(parsed.references[0]?.source_authority).toBe("notes/chapter.md");
  });

  it("accepts graph-v2", async () => {
    const parsed = await parseGraphContract(JSON.stringify(await graphFixture()));
    expect(parsed.source.graph_schema).toBe("kgdistiller-graph-v2");
  });

  it("rejects kgdistiller-graph-v1", async () => {
    const graph = (await graphFixture()) as unknown as { source: Record<string, unknown>; bundle_sha256: string };
    graph.source.graph_schema = "kgdistiller-graph-v1";
    graph.bundle_sha256 = await calculateBundleDigest({ ...graph });
    await expect(parseGraphContract(JSON.stringify(graph))).rejects.toThrow(
      "source.graph_schema must equal kgdistiller-graph-v2",
    );
  });

  it("rejects digest tampering", async () => {
    const graph = await graphFixture();
    graph.concepts[0]!.label = "Tampered";
    await expect(parseGraphContract(JSON.stringify(graph))).rejects.toThrow(
      "bundle_sha256 does not match",
    );
  });

  it("rejects unsafe paths and dangling semantic endpoints", async () => {
    const unsafe = (await graphFixture()) as unknown as Record<string, unknown>;
    const unsafeConcepts = unsafe.concepts as Array<Record<string, unknown>>;
    unsafeConcepts[0]!.authority = "../outside.md";
    unsafe.bundle_sha256 = await calculateBundleDigest(unsafe);
    await expect(parseGraphContract(JSON.stringify(unsafe))).rejects.toThrow(
      "safe vault-relative path",
    );

    const dangling = (await graphFixture()) as unknown as Record<string, unknown>;
    const edges = dangling.semantic_edges as Array<Record<string, unknown>>;
    edges[0]!.target = "unknown";
    dangling.bundle_sha256 = await calculateBundleDigest(dangling);
    await expect(parseGraphContract(JSON.stringify(dangling))).rejects.toThrow(
      "unknown endpoint",
    );
  });
});
