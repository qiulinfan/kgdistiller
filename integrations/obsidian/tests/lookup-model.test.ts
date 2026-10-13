import { describe, expect, it } from "vitest";

import {
  MAX_QUERY_LENGTH,
  chainTree,
  inboundGroups,
  lagging,
  linksByRole,
  lookupQuery,
  neighbourGraph,
  rankText,
  type NeighborsResult,
} from "../src/lookup-model";

const LAG = { changed_files: 0, unavailable_bases: [], unembedded: 0, embedding_changed: false };

describe("the lookup query", () => {
  it("collapses whitespace and caps long selections", () => {
    expect(lookupQuery("  masked\n autoencoder\t(MAE) ")).toBe("masked autoencoder (MAE)");
    expect(lookupQuery("   ")).toBe("");
    expect(lookupQuery("x".repeat(MAX_QUERY_LENGTH + 10))).toHaveLength(MAX_QUERY_LENGTH);
  });
});

describe("result shaping", () => {
  it("reports lag only when the index is behind or the model changed", () => {
    expect(lagging(undefined)).toBe(false);
    expect(lagging(LAG)).toBe(false);
    expect(lagging({ ...LAG, changed_files: 1 })).toBe(true);
    expect(lagging({ ...LAG, unembedded: 2 })).toBe(true);
    expect(lagging({ ...LAG, embedding_changed: true })).toBe(true);
    expect(lagging({ ...LAG, unavailable_bases: ["papers"] })).toBe(false);
  });

  it("lists lane ranks in the order the lanes ran, with a dash for a miss", () => {
    expect(rankText(["lexical", "dense", "name"], { lexical: 3, dense: 1, name: null })).toBe("lexical 3 · dense 1 · name –");
  });

  it("groups out-links by role in kgd's order", () => {
    expect(linksByRole([
      { role: "subject", pos: 0, uid: "papers:mae", label: "MAE", exists: true },
      { role: "uses", pos: 0, uid: "notes:dae", label: "DAE", exists: true },
      { role: "uses", pos: 1, term: "masking ratio" },
      { role: "requires", pos: 0, term: "ViT" },
    ]).map((group) => [group.role, group.links.length])).toEqual([["subject", 1], ["uses", 2], ["requires", 1]]);
  });
});

describe("the requires chain", () => {
  const neighbors = (edges: NeighborsResult["edges"], records: NeighborsResult["records"]): NeighborsResult =>
    ({ edges, records, missing: [], lag: LAG });
  const record = (label: string, kind = "definition") => ({ label, kind, class: "node", exists: true });

  it("follows requires across bases, with pending terms and missing records as leaves", () => {
    const tree = chainTree(neighbors([
      { from: "papers:mae", role: "requires", to: "notes:autoencoder", depth: 1 },
      { from: "papers:mae", role: "requires", term: "mask token", depth: 1 },
      { from: "notes:autoencoder", role: "requires", term: "latent variable", depth: 2 },
      { from: "notes:autoencoder", role: "requires", to: "notes:gone", depth: 2 },
    ], {
      "papers:mae": record("Masked autoencoder", "architecture"),
      "notes:autoencoder": record("Autoencoder", "architecture"),
      "notes:gone": { label: null, kind: null, class: null, exists: false },
    }), "papers:mae");
    expect(tree).toMatchObject({ uid: "papers:mae", label: "Masked autoencoder", exists: true });
    expect(tree.children.map((child) => child.label)).toEqual(["Autoencoder", "mask token"]);
    expect(tree.children[1]).toEqual({ term: "mask token", label: "mask token", exists: false, repeated: false, children: [] });
    const autoencoder = tree.children[0]!;
    expect(autoencoder.children.map((child) => [child.label, child.exists])).toEqual([["latent variable", false], ["notes:gone", false]]);
  });

  it("expands each record once and marks later occurrences and cycles as repeated", () => {
    const tree = chainTree(neighbors([
      { from: "a", role: "requires", to: "b", depth: 1 },
      { from: "a", role: "requires", to: "c", depth: 1 },
      { from: "b", role: "requires", to: "c", depth: 2 },
      { from: "c", role: "requires", to: "a", depth: 2 },
    ], { a: record("A"), b: record("B"), c: record("C") }), "a");
    const [b, c] = tree.children;
    expect(c).toMatchObject({ label: "C", repeated: false });
    expect(c!.children).toMatchObject([{ label: "A", repeated: true, children: [] }]);
    expect(b!.children).toMatchObject([{ label: "C", repeated: true, children: [] }]);
  });
});

describe("inbound links", () => {
  it("groups the relations about a record by kind, apart from the records built on it", () => {
    const groups = inboundGroups([
      { uid: "papers:r1", label: "Masking ratio is high", kind: "property", role: "subject" },
      { uid: "papers:c1", label: "Beats training from scratch", kind: "compares", role: "candidate" },
      { uid: "papers:r2", label: "Decoder is independent", kind: "property", role: "uses" },
      { uid: "notes:dae", label: "Denoising autoencoder", kind: "architecture", role: "requires" },
    ]);
    expect(groups.about.map((group) => [group.kind, group.links.map((link) => `${link.label} as ${link.role}`)])).toEqual([
      ["property", ["Masking ratio is high as subject", "Decoder is independent as uses"]],
      ["compares", ["Beats training from scratch as candidate"]],
    ]);
    expect(groups.builtOn.map((link) => link.label)).toEqual(["Denoising autoencoder"]);
  });
});

describe("the neighbour graph", () => {
  const record = (label: string, kind: string, recordClass = "node") => ({ label, kind, class: recordClass, exists: true });
  // A walk around MAE: requires, a binary relation, an n-ary relation, a statement about MAE alone,
  // a statement that another statement is about, a relation to a missing record, and a far chain.
  const around: NeighborsResult = {
    edges: [
      { from: "papers:mae", role: "requires", to: "notes:ae", depth: 1 },
      { from: "papers:mae", role: "requires", term: "mask token", depth: 1 },
      { from: "papers:form", role: "subject", to: "papers:mae", depth: 1 },
      { from: "papers:form", role: "uses", to: "notes:dae", depth: 2 },
      { from: "papers:design", role: "subject", to: "papers:mae", depth: 1 },
      { from: "papers:design", role: "uses", to: "papers:encoder", depth: 2 },
      { from: "papers:design", role: "uses", to: "papers:decoder", depth: 2 },
      { from: "papers:ratio", role: "subject", to: "papers:mae", depth: 1 },
      { from: "papers:ratio", role: "uses", term: "masking ratio", depth: 2 },
      { from: "papers:infers", role: "subject", to: "papers:mae", depth: 1 },
      { from: "papers:reasoning", role: "subject", to: "papers:infers", depth: 2 },
      { from: "notes:dae", role: "requires", to: "notes:ae", depth: 2 },
      { from: "papers:broken", role: "subject", to: "papers:decoder", depth: 3 },
      { from: "papers:broken", role: "uses", to: "papers:gone", depth: 4 },
      { from: "notes:vae", role: "requires", to: "notes:ae", depth: 2 },
      { from: "notes:objective", role: "requires", to: "notes:vae", depth: 3 },
      { from: "notes:far", role: "requires", to: "notes:objective", depth: 4 },
    ],
    records: {
      "papers:mae": record("Masked autoencoder", "architecture"),
      "notes:ae": record("Autoencoder", "architecture"),
      "notes:dae": record("Denoising autoencoder", "architecture"),
      "papers:encoder": record("MAE encoder", "architecture"),
      "papers:decoder": record("MAE decoder", "architecture"),
      "notes:vae": record("Variational autoencoder", "architecture"),
      "notes:objective": record("VAE training objective", "definition"),
      "notes:far": record("Far record", "definition"),
      "papers:form": record("MAE is a form of DAE", "property", "relation"),
      "papers:design": record("Asymmetric design cuts cost", "property", "relation"),
      "papers:ratio": record("Optimal masking ratio is high", "property", "relation"),
      "papers:infers": record("MAE infers missing patches", "property", "relation"),
      "papers:reasoning": record("Reasoning-like inference", "property", "relation"),
      "papers:broken": record("Decoder beats a missing record", "compares", "relation"),
      "papers:gone": { label: null, kind: null, class: null, exists: false },
    },
    missing: [],
    lag: LAG,
  };
  type Element = ReturnType<typeof neighbourGraph>["elements"][number];
  const nodes = (elements: Element[]) => elements.filter((element) => element.group === "nodes");
  const edges = (elements: Element[]) => elements.filter((element) => element.group === "edges");
  const ends = (element: Element) => `${element.data.source} -${element.data.label}-> ${element.data.target}`;

  it("draws records within three concept steps, relations as edges and statements about one record left out", () => {
    const { elements, shown, total } = neighbourGraph(around, "papers:mae");
    expect(nodes(elements).map((element) => [element.data.id, element.classes])).toEqual([
      ["papers:mae", "kgd-focus"],
      ["notes:ae", ""],
      ["notes:dae", ""],
      ["papers:decoder", ""],
      ["papers:encoder", ""],
      ["papers:infers", "kgd-relation"],
      ["notes:vae", ""],
      ["papers:gone", "kgd-dangling"],
      ["notes:objective", ""],
      ["papers:design", "kgd-relation"],
    ]);
    expect([shown, total]).toEqual([9, 9]);
    expect(edges(elements).map(ends)).toEqual([
      "papers:mae --> notes:ae",
      "papers:infers -subject-> papers:mae",
      "notes:dae --> notes:ae",
      "notes:vae --> notes:ae",
      "notes:objective --> notes:vae",
      "papers:mae -property-> notes:dae",
      "papers:decoder -compares-> papers:gone",
      "papers:design -subject-> papers:mae",
      "papers:design -uses-> papers:encoder",
      "papers:design -uses-> papers:decoder",
    ]);
    const form = edges(elements).find((element) => element.data.id === "relation-papers:form")!;
    expect(form.data).toMatchObject({ uid: "papers:form", element: "relation-edge", label: "property" });
    expect(form.classes).toBe("kgd-relation-edge");
    expect(edges(elements).find((element) => element.data.id === "relation-papers:broken")!.classes).toBe("kgd-relation-edge kgd-dangling");
  });

  it("keeps a statement in focus as a record of its own", () => {
    const { elements } = neighbourGraph(around, "papers:form", 1);
    expect(nodes(elements).map((element) => [element.data.id, element.classes]))
      .toEqual([["papers:form", "kgd-relation kgd-focus"], ["notes:dae", ""], ["papers:mae", ""]]);
    expect(edges(elements).map(ends)).toEqual(["papers:form -subject-> papers:mae", "papers:form -uses-> notes:dae"]);
  });

  it("draws at most the limit, nearest first, and only what joins drawn records", () => {
    const { elements, shown, total } = neighbourGraph(around, "papers:mae", 3, 3);
    expect(nodes(elements).map((element) => element.data.id)).toEqual(["papers:mae", "notes:ae", "notes:dae"]);
    expect(edges(elements).map(ends)).toEqual(["papers:mae --> notes:ae", "notes:dae --> notes:ae", "papers:mae -property-> notes:dae"]);
    expect([shown, total]).toEqual([3, 9]);
  });
});
