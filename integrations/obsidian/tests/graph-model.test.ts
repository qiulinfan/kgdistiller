import { readFileSync } from "node:fs";

import type { ElementDefinition } from "cytoscape";
import { describe, expect, it } from "vitest";

import {
  defaultFilters,
  detailsModel,
  focusPaths,
  graphElements,
  graphModel,
  graphOptions,
  kindColors,
  neighbourhood,
  selectRecords,
  type GraphElementData,
  type GraphFilters,
} from "../src/graph-model";
import {
  firstLine,
  fold,
  nameKey,
  parseValue,
  recordBody,
  recordClass,
  resolveLocal,
  sheetPath,
  sourceOfSheet,
} from "../src/records";
import { recordFixture } from "./fixture";

const ALL: GraphFilters = defaultFilters(true);
const PENDING: GraphFilters = { ...ALL, showPending: true };
const record = (id: string): string => `record:.knowledge/entries/${id}.md`;
const draft = (id: string): string => `record:.knowledge/drafts/${id}.md`;
const entryFile = (id: string): string => `.knowledge/entries/${id}.md`;

function elements(filters: GraphFilters = ALL): Map<string, ElementDefinition> {
  return byId(graphElements(graphModel(recordFixture()), filters));
}
const byId = (list: readonly ElementDefinition[]): Map<string, ElementDefinition> =>
  new Map(list.map((element) => [String(element.data.id), element]));
const data = (element: ElementDefinition | undefined): GraphElementData => element!.data as GraphElementData;
const classes = (element: ElementDefinition | undefined): string[] => String(element?.classes ?? "").split(" ");
const nodeIds = (list: Iterable<ElementDefinition>): string[] =>
  [...list].filter((element) => element.data.source === undefined).map((element) => String(element.data.id)).sort();

describe("record model", () => {
  it("reads only entries and drafts, keyed by path", () => {
    const model = graphModel(recordFixture());
    expect([...model.records.keys()].every((path) => /^\.knowledge\/(entries|drafts)\//.test(path))).toBe(true);
    expect(model.records.size).toBe(13);
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
  it("draws node records as nodes ringed by understanding, unknown included", () => {
    const all = elements();
    expect(data(all.get(record("measure")))).toMatchObject({ element: "node", kind: "definition", label: "Measure" });
    expect(classes(all.get(record("measure")))).toContain("understanding-not-yet-understood");
    expect(classes(all.get(record("sigma-algebra")))).toContain("understanding-understood");
    expect(classes(all.get(record("measure-space")))).toContain("understanding-unknown");
    expect(classes(all.get(record("measure-space-triple")))).toEqual(["kgd-relation", "understanding-unknown"]);
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

  it("draws requires links as dashed arrows and leaves pending terms out by default", () => {
    const all = elements();
    const requires = [...all.values()].filter((element) => data(element).element === "requires");
    expect(requires.map((element) => [data(element).source, data(element).target])).toEqual([
      [draft("null-set"), record("measure")],
      [record("measure"), record("sigma-algebra")],
    ]);
    expect([...all.values()].some((element) => data(element).element === "pending")).toBe(false);
    expect([...all.values()].some((element) => /measurable space|finite sets/i.test(data(element).label))).toBe(false);
  });

  it("draws one ghost per name key when pending terms are shown", () => {
    const all = elements(PENDING);
    const ghosts = [...all.values()].filter((element) => data(element).element === "pending");
    expect(ghosts.map((element) => [data(element).id, data(element).label]).sort()).toEqual([
      ["pending:finite sets", "finite sets"],
      ["pending:measurable space", "measurable space"],
    ]);
    expect(classes(all.get("pending:measurable space"))).toEqual(["kgd-pending"]);
    const incoming = [...all.values()].map(data).filter((item) => item.target === "pending:measurable space");
    expect(incoming.map((item) => [item.element, item.source]).sort()).toEqual([
      ["requires", record("measure")],
      ["requires", record("sigma-finite")],
    ]);
    // A relation with a term becomes a diamond so the term has its incidence edge.
    expect(data(all.get(record("kl-divergence-example")))).toMatchObject({ element: "relation" });
    const roles = [...all.values()].map(data).filter((item) => item.source === record("kl-divergence-example"));
    expect(roles.map((item) => [item.role, item.target])).toEqual([
      ["uses", "stub:notes:kl-divergence"],
      ["uses", record("measure")],
      ["setting", "pending:finite sets"],
    ]);
    // Relations without terms keep the binary rule.
    expect(data(all.get(record("missing-premise")))).toMatchObject({ element: "relation-edge" });
  });

  it("gives a ghost no node when its name key is empty", () => {
    const model = graphModel([{
      path: ".knowledge/entries/a.md", frontmatter: { label: "A", kind: "definition", requires: ["∑ + ∏"] },
    }]);
    expect(graphElements(model, PENDING).map((element) => element.data.id)).toEqual(["record:.knowledge/entries/a.md"]);
  });

  it("draws drafts dashed with a draft line, resolves draft links to drafts and hides them with the toggle", () => {
    const all = elements();
    expect(data(all.get(draft("null-set")))).toMatchObject({ element: "node", draft: true, display: "Null set\ndraft" });
    expect(data(all.get(record("measure"))).display).toBe("Measure");
    expect(classes(all.get(draft("null-set")))).toContain("kgd-draft");
    const draftEdge = all.get(draft("null-set-implies-measure-zero"));
    expect(data(draftEdge)).toMatchObject({ element: "relation-edge", source: draft("null-set"), target: record("measure") });
    expect(classes(draftEdge)).toContain("kgd-draft");
    const hidden = elements({ ...ALL, showDrafts: false });
    expect([...hidden.values()].some((element) => data(element).draft)).toBe(false);
    expect(hidden.has(record("measure"))).toBe(true);
  });

  it("resolves an accepted record's link only to accepted records", () => {
    const records = [
      { path: ".knowledge/entries/a.md", frontmatter: { label: "A", kind: "definition", requires: ["[[proposal]]"] } },
      { path: ".knowledge/drafts/proposal.md", frontmatter: { label: "Proposal", kind: "definition" } },
    ];
    const all = byId(graphElements(graphModel(records), ALL));
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
});

describe("filters", () => {
  const model = graphModel(recordFixture());
  const selected = (filters: Partial<GraphFilters>): string[] =>
    selectRecords(model, { ...ALL, ...filters }).map((item) => item.id).sort();

  it("offers every kind of the model, drafts included", () => {
    expect(graphOptions(model)).toEqual([
      "composes", "contrasts", "definition", "equivalent", "example", "generalizes", "implies", "prerequisite-for",
    ]);
  });

  it("filters by kind and keeps the participants of the selected records", () => {
    const composes = elements({ ...ALL, kind: "composes" });
    expect([...composes.keys()].sort()).toEqual([
      record("measure"), record("measure-space"), record("measure-space-triple"), record("sigma-algebra"),
      `role:.knowledge/entries/measure-space-triple.md:part:0`,
      `role:.knowledge/entries/measure-space-triple.md:part:1`,
      `role:.knowledge/entries/measure-space-triple.md:whole:0`,
    ].sort());
  });

  it("filters by class", () => {
    expect(selected({ recordClass: "node" })).toEqual(["measure", "measure-space", "null-set", "sigma-algebra", "sigma-finite"]);
    const relations = elements({ ...ALL, recordClass: "relation" });
    expect([...relations.values()].some((element) => data(element).element === "requires")).toBe(false);
    expect(relations.has(record("measure-space-triple"))).toBe(true);
    const nodes = elements({ ...ALL, recordClass: "node" });
    expect([...nodes.values()].some((element) => ["relation", "relation-edge", "role"].includes(data(element).element))).toBe(false);
  });

  it("filters by understanding, unknown included", () => {
    expect(selected({ understanding: "understood" })).toEqual(["sigma-algebra", "sigma-finite-generalizes-measure"]);
    expect(selected({ understanding: "not-yet-understood" })).toEqual(["measure"]);
    expect(selected({ understanding: "unknown" })).not.toContain("measure");
    expect(selected({ understanding: "unknown" })).toContain("measure-space");
    expect(nodeIds(elements({ ...ALL, understanding: "understood" }).values())).toEqual([
      record("measure"), record("sigma-algebra"), record("sigma-finite"),
    ]);
  });

  it("filters by source prefix", () => {
    expect(selected({ sourcePrefix: "papers/" })).toEqual(["sigma-finite", "sigma-finite-generalizes-measure"]);
    expect(selected({ sourcePrefix: "notes/" })).not.toContain("sigma-finite");
    const papers = elements({ ...ALL, sourcePrefix: "papers/" });
    expect(data(papers.get(record("sigma-finite-generalizes-measure")))).toMatchObject({
      element: "relation-edge", source: record("sigma-finite"), target: record("measure"),
    });
  });

  it("filters drafts", () => {
    expect(selected({ showDrafts: false })).not.toContain("null-set");
    expect(selected({})).toContain("null-set");
  });

  it("colours kinds from a fixed palette over the model's sorted kinds, stable under filters", () => {
    const colors = kindColors(model);
    expect([...colors.keys()]).toEqual(graphOptions(model));
    expect(new Set(colors.values()).size).toBe(colors.size);
    const fill = (filters: GraphFilters): string | undefined => data(elements(filters).get(record("measure"))).color;
    expect(fill(ALL)).toBe(colors.get("definition"));
    expect(fill({ ...ALL, kind: "definition" })).toBe(fill(ALL));
    expect(fill({ ...ALL, showDrafts: false })).toBe(fill(ALL));
    expect(data(elements().get(record("measure-space-triple"))).color).toBe(colors.get("composes"));
    const roles = [...elements().values()].map(data).filter((item) => item.element === "role");
    expect(roles.every((item) => item.color !== undefined)).toBe(true);
  });
});

describe("neighbourhood", () => {
  const model = graphModel(recordFixture());
  const all = graphElements(model, ALL);
  const around = (seeds: string[], depth: number): Map<string, ElementDefinition> => byId(neighbourhood(all, seeds, depth));

  it("keeps the records within one or two hops of a node", () => {
    const one = around([entryFile("measure-space")], 1);
    expect(nodeIds(one.values())).toEqual([
      "dangling:missing-record", record("measure-space"), record("measure-space-triple"),
    ].sort());
    expect(one.has(record("missing-premise"))).toBe(true);
    expect(one.has("role:.knowledge/entries/measure-space-triple.md:whole:0")).toBe(true);
    expect(one.has("role:.knowledge/entries/measure-space-triple.md:part:1")).toBe(false);
    const two = around([entryFile("measure-space")], 2);
    expect(nodeIds(two.values())).toEqual(expect.arrayContaining([
      record("measure"), record("sigma-algebra"), record("sigma-algebra-prerequisite-for-measure"),
    ]));
    expect(two.has("role:.knowledge/entries/measure-space-triple.md:part:1")).toBe(true);
    expect(two.has(record("sigma-finite"))).toBe(false);
  });

  it("seeds both endpoints of a relation drawn as an edge", () => {
    const one = around([entryFile("missing-premise")], 1);
    expect(one.has(record("missing-premise"))).toBe(true);
    expect(nodeIds(one.values())).toEqual([
      "dangling:missing-record", record("measure-space"), record("measure-space-triple"),
    ].sort());
  });

  it("focuses a record, the records of a source and the records of a sheet's source", () => {
    expect(focusPaths(model, entryFile("measure"))).toEqual([entryFile("measure")]);
    expect(focusPaths(model, entryFile("absent"))).toEqual([]);
    const papers = [entryFile("sigma-finite"), entryFile("sigma-finite-generalizes-measure")].sort();
    expect(focusPaths(model, "papers/a.md").sort()).toEqual(papers);
    expect(focusPaths(model, sheetPath("papers/a.md")).sort()).toEqual(papers);
    const chapter = focusPaths(model, ".knowledge/sheets/notes/chapter.tex.md");
    expect(chapter).toHaveLength(11);
    expect(chapter).not.toContain(entryFile("sigma-finite"));
    expect(focusPaths(model, "notes/other.md")).toEqual([]);
    expect(focusPaths(model, undefined)).toEqual([]);
  });

  it("draws the neighbourhood of a source and of its sheet", () => {
    for (const active of ["papers/a.md", sheetPath("papers/a.md")]) {
      const one = around(focusPaths(model, active), 1);
      expect(one.has(record("sigma-finite-generalizes-measure"))).toBe(true);
      expect(nodeIds(one.values())).toEqual(expect.arrayContaining([
        record("sigma-finite"), record("measure"), record("sigma-algebra"), record("measure-space-triple"),
      ]));
      expect(one.has(record("measure-space"))).toBe(false);
    }
  });

  it("draws nothing for an empty seed set and leaves its input unchanged", () => {
    expect(neighbourhood(all, [], 2)).toEqual([]);
    expect(graphElements(model, ALL)).toEqual(all);
  });
});

describe("details", () => {
  const model = graphModel(recordFixture());
  const all = elements(PENDING);
  const none = (): boolean => false;

  it("parses the first line, sheet paths and their sources", () => {
    expect(firstLine("3-7")).toBe(3);
    expect(firstLine("9")).toBe(9);
    for (const bad of ["0", "7-3", "a", "", "3-", undefined]) expect(firstLine(bad)).toBeUndefined();
    expect(sheetPath("notes/chapter.tex")).toBe(".knowledge/sheets/notes/chapter.tex.md");
    expect(sourceOfSheet(".knowledge/sheets/notes/chapter.tex.md")).toBe("notes/chapter.tex");
    expect(sourceOfSheet(".knowledge/entries/measure.md")).toBeUndefined();
    expect(sourceOfSheet(".knowledge/sheets/notes/chapter.tex")).toBeUndefined();
  });

  it("splits a record body into prose and Evidence", () => {
    const text = "---\r\nlabel: X\r\nkind: definition\r\n---\r\nProse with [[link]].\r\n\r\n## Search terms\r\n" +
      "what is x\r\n\r\n## Evidence\r\n> quote one\r\n\r\n> quote two\r\n";
    expect(recordBody(text)).toEqual({
      prose: "Prose with [[link]].\n\n## Search terms\nwhat is x",
      evidence: "> quote one\n\n> quote two",
    });
    expect(recordBody("---\nlabel: X\n---\nA\n## Evidence\n> early\n## Evidence\n> last\n"))
      .toEqual({ prose: "A\n## Evidence\n> early", evidence: "> last" });
    expect(recordBody("No frontmatter.\n")).toEqual({ prose: "No frontmatter.", evidence: "" });
  });

  it("shows a record's fields with the 0-based first source line and the sheet only when it exists", () => {
    const measure = detailsModel(model, data(all.get(record("measure"))), none);
    expect(measure).toEqual({
      element: "record", label: "Measure", kind: "definition", recordClass: "node", draft: false,
      understanding: "not-yet-understood", epistemic: undefined, roles: [],
      requires: ["Sigma algebra", "measurable space (pending)"], recordPath: entryFile("measure"),
      source: { path: "notes/chapter.tex", lines: "3-7", line: 2 },
    });
    const withSheet = detailsModel(model, data(all.get(record("measure"))),
      (path) => path === ".knowledge/sheets/notes/chapter.tex.md");
    expect(withSheet).toMatchObject({ sheet: ".knowledge/sheets/notes/chapter.tex.md" });
    expect(detailsModel(model, data(all.get(record("measure-space"))), none))
      .toMatchObject({ understanding: "unknown", source: { lines: "9", line: 8 } });
    expect(detailsModel(model, data(all.get(record("missing-premise"))), none)).toMatchObject({
      recordClass: "relation",
      roles: [{ role: "premise", values: ["missing-record (missing)"] }, { role: "conclusion", values: ["Measure space"] }],
    });
  });

  it("shows the owner of a role edge, the owners of a ghost, a stub's uid and a missing path", () => {
    const role = [...all.values()].map(data).find((item) => item.element === "role" && item.role === "whole")!;
    expect(detailsModel(model, role, none)).toMatchObject({ element: "record", label: "Measure space triple" });
    expect(detailsModel(model, data(all.get("pending:measurable space")), none)).toEqual({
      element: "pending", term: "measurable space", key: "measurable space", owners: [
        { label: "Measure", path: entryFile("measure"), role: "requires" },
        { label: "Sigma-finite measure", path: entryFile("sigma-finite"), role: "requires" },
      ],
    });
    expect(detailsModel(model, data(all.get("stub:notes:kl-divergence")), none))
      .toEqual({ element: "stub", uid: "notes:kl-divergence" });
    expect(detailsModel(model, data(all.get("dangling:missing-record")), none))
      .toEqual({ element: "dangling", id: "missing-record", path: ".knowledge/entries/missing-record.md" });
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
    expect(fold("ᾳ ΐ ǰ ẖ ꭰ Ꭰ")).toBe("\u03b1\u03b9 \u03b9\u0308\u0301 j\u030c h\u0331 \u13a0 \u13a0");
  });

  it("applies every entry of the case-folding table", () => {
    const table = JSON.parse(
      readFileSync(new URL("../src/case-folding.json", import.meta.url), "utf8"),
    ) as Record<string, string>;
    const stable = Object.entries(table).filter(([char]) => char.normalize("NFKC") === char);
    expect(stable.length).toBeGreaterThan(200);
    for (const [char, folded] of stable) expect(fold(char)).toBe(folded);
  });
});

describe("name key parity with kgd", () => {
  const rows = JSON.parse(
    readFileSync(new URL("../../../tests/fixtures/name-key.json", import.meta.url), "utf8"),
  ) as Array<{ input: string; key: string }>;

  it.each(rows.map((row) => [JSON.stringify(row.input), row] as const))("%s", (_name, row) => {
    expect(nameKey(row.input)).toBe(row.key);
  });
});
