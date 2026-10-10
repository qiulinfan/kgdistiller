import { readFileSync } from "node:fs";

import type { ElementDefinition } from "cytoscape";
import { describe, expect, it } from "vitest";

import {
  graphElements,
  graphModel,
  kindColors,
  kindOptions,
  openTarget,
  type GraphElementData,
} from "../src/graph-model";
import { fold, parseValue, recordClass, resolveLocal } from "../src/records";
import { recordFixture } from "./fixture";

const ALL = { kind: "", showDrafts: true };
const record = (id: string): string => `record:.knowledge/entries/${id}.md`;
const draft = (id: string): string => `record:.knowledge/drafts/${id}.md`;

function elements(filters = ALL): Map<string, ElementDefinition> {
  return new Map(graphElements(graphModel(recordFixture()), filters).map((element) => [String(element.data.id), element]));
}
const data = (element: ElementDefinition | undefined): GraphElementData => element!.data as GraphElementData;
const classes = (element: ElementDefinition | undefined): string[] => String(element?.classes ?? "").split(" ");

describe("record model", () => {
  it("reads only entries and drafts, keyed by path", () => {
    const model = graphModel(recordFixture());
    expect([...model.records.keys()].every((path) => /^\.knowledge\/(entries|drafts)\//.test(path))).toBe(true);
    expect(model.records.size).toBe(11);
    expect(model.records.get(".knowledge/drafts/null-set.md")).toMatchObject({ id: "null-set", draft: true });
    expect(model.records.get(".knowledge/entries/measure-space.md")?.lines).toBe("9");
  });

  it("applies the shared class rule", () => {
    expect(recordClass({ label: "x", kind: "definition", requires: ["[[a]]"], aliases: ["y"], tags: ["t"] })).toBe("node");
    expect(recordClass({ label: "x", kind: "implies", premise: [] })).toBe("node");
    expect(recordClass({ label: "x", kind: "implies", premise: null })).toBe("node");
    expect(recordClass({ label: "x", kind: "implies", premise: ["term"] })).toBe("relation");
    const model = graphModel(recordFixture());
    expect(model.records.get(".knowledge/entries/measure.md")?.recordClass).toBe("node");
    expect(model.records.get(".knowledge/entries/measure-space-triple.md")?.recordClass).toBe("relation");
  });

  it("keeps roles in frontmatter order and resolves values textually", () => {
    const triple = graphModel(recordFixture()).records.get(".knowledge/entries/measure-space-triple.md")!;
    expect(triple.roles.map((role) => role.role)).toEqual(["whole", "part"]);
    expect(triple.roles[1]!.values.map((value) => value.path)).toEqual([
      ".knowledge/entries/sigma-algebra.md", ".knowledge/entries/measure.md",
    ]);
    const measure = graphModel(recordFixture()).records.get(".knowledge/entries/measure.md")!;
    expect(measure.requires).toEqual([
      { raw: "[[sigma-algebra]]", path: ".knowledge/entries/sigma-algebra.md" },
      { raw: "measurable space", term: "measurable space" },
    ]);
  });

  it("reports unquoted wikilinks and grammar errors instead of resolving them", () => {
    const model = graphModel([{
      path: ".knowledge/entries/bad.md",
      frontmatter: { label: "Bad", kind: "implies", premise: [["x"]], conclusion: ["[[x#heading]]"] },
    }]);
    const bad = model.records.get(".knowledge/entries/bad.md")!;
    expect(bad.roles.map((role) => role.values[0]!.error)).toEqual(["nested-list", "fragment"]);
  });
});

describe("graph elements", () => {
  it("draws node records as nodes ringed by understanding", () => {
    const all = elements();
    expect(data(all.get(record("measure")))).toMatchObject({ element: "node", kind: "definition", label: "Measure" });
    expect(classes(all.get(record("measure")))).toContain("understanding-not-yet-understood");
    expect(classes(all.get(record("sigma-algebra")))).toContain("understanding-understood");
  });

  it("draws a binary relation as a typed edge from the first role's value to the second", () => {
    const edge = elements().get(record("triple-contrasts-prerequisite"));
    expect(data(edge)).toMatchObject({
      element: "relation-edge", label: "contrasts",
      source: record("measure-space-triple"), target: record("sigma-algebra-prerequisite-for-measure"),
    });
    expect(classes(edge)).toContain("kgd-directed");
  });

  it("draws two values in one role as an undirected edge and the same record twice as a loop", () => {
    const all = elements();
    const loop = all.get(record("measure-equals-itself"));
    expect(data(loop)).toMatchObject({ element: "relation-edge", source: record("measure"), target: record("measure") });
    expect(classes(loop)).toEqual(expect.arrayContaining(["kgd-loop"]));
    expect(classes(loop)).not.toContain("kgd-directed");
    const example = all.get(record("kl-divergence-example"));
    expect(data(example)).toMatchObject({ source: "stub:notes:kl-divergence", target: record("measure") });
    expect(classes(example)).not.toContain("kgd-directed");
  });

  it("draws n-ary relations and relations that are participants as diamonds with role edges", () => {
    const all = elements();
    expect(data(all.get(record("measure-space-triple")))).toMatchObject({ element: "relation", kind: "composes" });
    expect(classes(all.get(record("measure-space-triple")))).toContain("kgd-relation");
    const roles = [...all.values()].map(data).filter((item) => item.element === "role" && item.source === record("measure-space-triple"));
    expect(roles.map((item) => [item.role, item.target])).toEqual([
      ["whole", record("measure-space")],
      ["part", record("sigma-algebra")],
      ["part", record("measure")],
    ]);
    // A binary relation that another relation links to keeps its own node.
    expect(data(all.get(record("sigma-algebra-prerequisite-for-measure")))).toMatchObject({ element: "relation" });
    const incidence = [...all.values()].map(data)
      .filter((item) => item.source === record("sigma-algebra-prerequisite-for-measure"));
    expect(incidence.map((item) => item.role)).toEqual(["prerequisite", "dependent"]);
  });

  it("draws requires links as dashed arrows and leaves pending terms out", () => {
    const all = elements();
    const requires = [...all.values()].filter((element) => data(element).element === "requires");
    expect(requires.map((element) => [data(element).source, data(element).target])).toEqual([
      [draft("null-set"), record("measure")],
      [record("measure"), record("sigma-algebra")],
    ]);
    expect([...all.values()].some((element) => data(element).label === "measurable space")).toBe(false);
    expect([...all.values()].some((element) => data(element).label === "finite sets")).toBe(false);
  });

  it("draws drafts dashed, resolves draft links to drafts and hides them with the toggle", () => {
    const all = elements();
    expect(data(all.get(draft("null-set")))).toMatchObject({ element: "node", draft: true });
    expect(classes(all.get(draft("null-set")))).toContain("kgd-draft");
    const draftEdge = all.get(draft("null-set-implies-measure-zero"));
    expect(data(draftEdge)).toMatchObject({ element: "relation-edge", source: draft("null-set"), target: record("measure") });
    expect(classes(draftEdge)).toContain("kgd-draft");
    const hidden = elements({ kind: "", showDrafts: false });
    expect([...hidden.values()].some((element) => data(element).draft)).toBe(false);
    expect(hidden.has(record("measure"))).toBe(true);
  });

  it("resolves an accepted record's link only to accepted records", () => {
    const records = [
      { path: ".knowledge/entries/a.md", frontmatter: { label: "A", kind: "definition", requires: ["[[proposal]]"] } },
      { path: ".knowledge/drafts/proposal.md", frontmatter: { label: "Proposal", kind: "definition" } },
    ];
    const all = new Map(graphElements(graphModel(records), ALL).map((element) => [String(element.data.id), element]));
    expect(data(all.get("dangling:proposal"))).toMatchObject({ element: "dangling" });
    const exists = (path: string): boolean => path === ".knowledge/drafts/proposal.md";
    expect(resolveLocal("proposal", false, exists)).toBeUndefined();
    expect(resolveLocal("proposal", true, exists)).toBe(".knowledge/drafts/proposal.md");
  });

  it("draws foreign targets as grey stubs and missing local targets as dangling", () => {
    const all = elements();
    expect(data(all.get("stub:notes:kl-divergence"))).toMatchObject({ element: "stub", label: "notes:kl-divergence" });
    expect(classes(all.get("stub:notes:kl-divergence"))).toContain("kgd-stub");
    expect(data(all.get("dangling:missing-record"))).toMatchObject({ element: "dangling", label: "missing-record" });
    expect(classes(all.get(record("missing-premise")))).toContain("kgd-dangling");
  });

  it("filters by kind and keeps the participants of the selected records", () => {
    const model = graphModel(recordFixture());
    expect(kindOptions(model)).toEqual([
      "composes", "contrasts", "definition", "equivalent", "example", "implies", "prerequisite-for",
    ]);
    const composes = elements({ kind: "composes", showDrafts: true });
    expect([...composes.keys()].sort()).toEqual([
      record("measure"), record("measure-space"), record("measure-space-triple"), record("sigma-algebra"),
      `role:.knowledge/entries/measure-space-triple.md:part:0`,
      `role:.knowledge/entries/measure-space-triple.md:part:1`,
      `role:.knowledge/entries/measure-space-triple.md:whole:0`,
    ].sort());
  });

  it("colours kinds from a fixed palette in sorted kind order", () => {
    const colors = kindColors(["implies", "contrasts", "implies", "composes"]);
    expect([...colors.keys()]).toEqual(["composes", "contrasts", "implies"]);
    expect(kindColors(["implies"]).get("implies")).toBe(kindColors(["composes"]).get("composes"));
    expect(new Set(colors.values()).size).toBe(3);
  });

  it("opens the record behind nodes, diamonds and typed edges only", () => {
    const all = elements();
    expect(openTarget(data(all.get(record("measure"))))).toEqual({ path: ".knowledge/entries/measure.md" });
    expect(openTarget(data(all.get(record("missing-premise"))))).toEqual({ path: ".knowledge/entries/missing-premise.md" });
    expect(openTarget(data(all.get("stub:notes:kl-divergence")))).toBeUndefined();
    expect(openTarget(data(all.get("dangling:missing-record")))).toBeUndefined();
  });
});

interface GrammarCase {
  value: string;
  base: string;
  expect: { uid?: string; term?: string; error?: string };
}

describe("link grammar parity with kgd", () => {
  const fixture = JSON.parse(
    readFileSync(new URL("../../../tests/fixtures/link-grammar.json", import.meta.url), "utf8"),
  ) as { cases: GrammarCase[] };

  it.each(fixture.cases.map((item) => [JSON.stringify(item.value), item] as const))("%s", (_name, item) => {
    const parsed = parseValue(item.value, item.base);
    const actual = parsed.kind === "local" ? { uid: `${item.base}:${parsed.id}` }
      : parsed.kind === "foreign" ? { uid: `${parsed.base}:${parsed.id}` }
      : parsed.kind === "term" ? { term: parsed.term }
      : { error: parsed.error };
    expect(actual).toEqual(item.expect);
  });

  it("folds as Python casefold does for the full foldings", () => {
    expect(fold("Straße GROẞ ΣΑΣ ς ＡＢ ﬁ")).toBe("strasse gross σασ σ ab fi");
  });
});
