import { describe, expect, it } from "vitest";

import {
  fieldOptions,
  graphElements,
  projectionPath,
  relationOptions,
} from "../src/graph-model";
import { graphFixture } from "./fixture";

describe("typed graph model", () => {
  it("keeps semantic, definition, and reference edges distinct", async () => {
    const graph = await graphFixture();
    const elements = graphElements(graph, "knowledge/build/obsidian/semantic-graph.json", {
      relation: "",
      field: "",
      showSources: true,
      showDefinitions: true,
      showReferences: true,
    });
    const kinds = elements.map((element) => element.data.kind);
    expect(kinds.filter((kind) => kind === "concept")).toHaveLength(2);
    expect(kinds.filter((kind) => kind === "source")).toHaveLength(1);
    expect(kinds.filter((kind) => kind === "semantic")).toHaveLength(1);
    expect(kinds.filter((kind) => kind === "definition")).toHaveLength(2);
    expect(kinds.filter((kind) => kind === "reference")).toHaveLength(1);
  });

  it("filters by field and can hide the provenance layer", async () => {
    const graph = await graphFixture();
    const probability = graphElements(graph, "semantic-graph.json", {
      relation: "",
      field: "probability",
      showSources: true,
      showDefinitions: true,
      showReferences: true,
    });
    expect(probability.map((element) => element.data.kind).sort()).toEqual([
      "concept",
      "definition",
      "reference",
      "source",
    ]);

    const semanticOnly = graphElements(graph, "semantic-graph.json", {
      relation: "prerequisite-for",
      field: "",
      showSources: false,
      showDefinitions: true,
      showReferences: true,
    });
    expect(semanticOnly.map((element) => element.data.kind).sort()).toEqual([
      "concept",
      "concept",
      "semantic",
    ]);
  });

  it("builds stable filter options and projection-relative paths", async () => {
    const graph = await graphFixture();
    expect(relationOptions(graph)).toEqual(["prerequisite-for"]);
    expect(fieldOptions(graph)).toEqual(["mathematics", "probability"]);
    expect(
      projectionPath(
        "knowledge/build/obsidian/semantic-graph.json",
        "concepts/Measure.md",
      ),
    ).toBe("knowledge/build/obsidian/concepts/Measure.md");
  });
});
