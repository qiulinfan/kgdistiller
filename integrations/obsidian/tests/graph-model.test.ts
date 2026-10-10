import { describe, expect, it } from "vitest";

import {
  graphElements,
  openTarget,
  relationOptions,
  type GraphElementData,
} from "../src/graph-model";
import { graphFixture } from "./fixture";

const ALL = { relation: "", showSources: true, showDefinitions: true };

describe("typed graph model", () => {
  it("keeps concepts, sources, semantic and definition edges distinct", () => {
    const kinds = graphElements(graphFixture(), ALL).map((element) => element.data.kind);
    expect(kinds.filter((kind) => kind === "concept")).toHaveLength(2);
    expect(kinds.filter((kind) => kind === "source")).toHaveLength(1);
    expect(kinds.filter((kind) => kind === "semantic")).toHaveLength(1);
    expect(kinds.filter((kind) => kind === "definition")).toHaveLength(2);
  });

  it("styles concepts by understanding and carries their kind", () => {
    const concepts = graphElements(graphFixture(), ALL).filter((element) => element.data.kind === "concept");
    expect(concepts.map((element) => element.classes)).toEqual([
      "understanding-not-yet-understood",
      "understanding-understood",
    ]);
    expect(concepts[0]?.data).toMatchObject({ conceptKind: "definition", understanding: "not-yet-understood" });
  });

  it("filters by relation and can hide the provenance layer", () => {
    const semanticOnly = graphElements(graphFixture(), {
      relation: "prerequisite-for",
      showSources: false,
      showDefinitions: true,
    });
    expect(semanticOnly.map((element) => element.data.kind).sort()).toEqual([
      "concept",
      "concept",
      "semantic",
    ]);
  });

  it("builds stable filter options", () => {
    expect(relationOptions(graphFixture())).toEqual(["prerequisite-for"]);
  });

  it("opens entries as files and sources at their cited lines, never build/ projections", () => {
    const elements = graphElements(graphFixture(), ALL).map((element) => element.data as GraphElementData);
    const byId = new Map(elements.map((data) => [data.id, data]));
    const graphPath = ".knowledge/build/obsidian/semantic-graph.json";
    expect(openTarget(byId.get("concept:measure")!, graphPath)).toEqual({ path: ".knowledge/entries/measure.md" });
    expect(openTarget(byId.get("source:notes/chapter.tex")!, graphPath)).toEqual({ path: "notes/chapter.tex" });
    expect(openTarget(byId.get("definition:measure")!, graphPath)).toEqual({ path: "notes/chapter.tex", line: 5 });
    const semantic = elements.find((data) => data.kind === "semantic")!;
    expect(openTarget(semantic, graphPath)).toBeUndefined();
    for (const data of elements) expect(JSON.stringify(data)).not.toContain("build/");
  });

  it("has open targets only for a graph in the vault-root .knowledge tree", () => {
    const concept: GraphElementData = {
      id: "concept:measure", label: "Measure", kind: "concept", conceptId: "measure",
      authority: ".knowledge/entries/measure.md",
    };
    const definition: GraphElementData = { id: "definition:measure", label: "defines", kind: "definition", authority: "notes/chapter.tex", line: 5 };
    for (const graphPath of [
      "projects/probability/.knowledge/build/obsidian/semantic-graph.json",
      "exports/semantic-graph.json",
    ]) {
      expect(openTarget(concept, graphPath)).toBeUndefined();
      expect(openTarget(definition, graphPath)).toBeUndefined();
    }
  });
});
