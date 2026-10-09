import { describe, expect, it } from "vitest";

import {
  fieldOptions,
  graphElements,
  openTarget,
  relationOptions,
  type GraphElementData,
} from "../src/graph-model";
import { graphFixture } from "./fixture";

describe("typed graph model", () => {
  it("keeps semantic, definition, and reference edges distinct", async () => {
    const graph = await graphFixture();
    const elements = graphElements(graph, {
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
    const probability = graphElements(graph, {
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

    const semanticOnly = graphElements(graph, {
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

  it("builds stable filter options", async () => {
    const graph = await graphFixture();
    expect(relationOptions(graph)).toEqual(["prerequisite-for"]);
    expect(fieldOptions(graph)).toEqual(["mathematics", "probability"]);
  });

  it("opens concept entries and source authorities, never build/ projections", async () => {
    const graph = await graphFixture();
    const elements = graphElements(graph, {
      relation: "",
      field: "",
      showSources: true,
      showDefinitions: true,
      showReferences: true,
    }).map((element) => element.data as GraphElementData);
    const byId = new Map(elements.map((data) => [data.id, data]));
    const graphPath = ".knowledge/build/obsidian/semantic-graph.json";
    expect(openTarget(byId.get("concept:measure")!, graphPath)).toEqual({ path: ".knowledge/entries/measure.md" });
    expect(openTarget(byId.get("source:notes/chapter.md")!, graphPath)).toEqual({ path: "notes/chapter.md" });
    expect(openTarget(byId.get("definition:measure")!, graphPath)).toEqual({ path: "notes/chapter.md", line: 5 });
    expect(openTarget(byId.get("reference:notes/chapter.md:9:measure")!, graphPath)).toEqual({ path: "notes/chapter.md", line: 9 });
    const semantic = elements.find((data) => data.kind === "semantic")!;
    expect(openTarget(semantic, graphPath)).toBeUndefined();
    expect(openTarget({ id: "definition:x", label: "defines", kind: "definition", authority: "notes/chapter.tex", line: 4 }, graphPath))
      .toEqual({ path: "notes/chapter.tex" });
    for (const data of elements) expect(JSON.stringify(data)).not.toContain("build/");
  });

  it("has open targets only for a graph in the vault-root .knowledge tree", () => {
    const concept: GraphElementData = { id: "concept:measure", label: "Measure", kind: "concept", conceptId: "measure" };
    const definition: GraphElementData = { id: "definition:measure", label: "defines", kind: "definition", authority: "notes/chapter.md", line: 5 };
    for (const graphPath of [
      "projects/probability/.knowledge/build/obsidian/semantic-graph.json",
      "exports/semantic-graph.json",
    ]) {
      expect(openTarget(concept, graphPath)).toBeUndefined();
      expect(openTarget(definition, graphPath)).toBeUndefined();
    }
  });
});
