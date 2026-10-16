import { describe, expect, it } from "vitest";

import type { CaptureFiles } from "../src/capture";
import {
  absoluteSource, acceptanceGroup, drawIntoDraft, editableRoles, FOREIGN_GROUP, groupProblems, linkFor, roleLinks, sourceGroups, valuesByRole,
  valueText, type Draft,
} from "../src/drafts";

const draft = (uid: string, extra: Partial<Draft> = {}): Draft => ({
  uid, base: uid.split(":")[0]!, id: uid.split(":")[1]!, path: `/v/.knowledge/drafts/${uid.split(":")[1]}.md`,
  label: uid.split(":")[1]!, with: [], linked_by: [], problems: [], ...extra,
});

describe("drafts accepted together", () => {
  it("are the draft and every draft it links, or none while one is not listed", () => {
    const best = draft("papers:best", { with: ["papers:mae", "papers:sampling"] });
    const listed = [best, draft("papers:sampling"), draft("papers:mae")];
    expect(acceptanceGroup(best, listed)?.map((item) => item.uid)).toEqual(["papers:best", "papers:mae", "papers:sampling"]);
    expect(acceptanceGroup(best, [best, draft("papers:mae")])).toBeUndefined();
  });

  it("report each member's problems, naming the linked draft a problem belongs to", () => {
    const group = [draft("papers:best", { problems: ["evidence is stale"] }), draft("papers:mae", { label: "Masked autoencoder", problems: ["kind x is not a kind"] })];
    expect(groupProblems(group)).toEqual(["evidence is stale", "Masked autoencoder: kind x is not a kind"]);
  });
});

describe("a draft's values on its card", () => {
  it("are grouped by role in kgd's order", () => {
    const values = [
      { role: "baseline", pos: 0, uid: "notes:block-wise-masking", state: "accepted" as const, label: "Block-wise masking" },
      { role: "baseline", pos: 1, uid: "papers:grid-wise-sampling", state: "accepted" as const, label: "Grid-wise sampling" },
      { role: "candidate", pos: 0, uid: "papers:sampling", state: "draft" as const, label: "Random sampling" },
      { role: "requires", pos: 0, uid: "papers:patches", state: "missing" as const, label: null },
    ];
    expect(valuesByRole(draft("papers:best", { out: values })).map(({ role, values: items }) => [role, items.length]))
      .toEqual([["baseline", 2], ["candidate", 1], ["requires", 1]]);
  });

  it("read as names with what they are, the base only when it is another, and never an id", () => {
    expect(valueText({ role: "requires", pos: 0, uid: "notes:autoencoder", state: "accepted", label: "Autoencoder" }, "papers"))
      .toEqual({ text: "Autoencoder", status: "accepted", note: "notes" });
    expect(valueText({ role: "requires", pos: 0, uid: "papers:encoder", state: "accepted", label: "Encoder" }, "papers"))
      .toEqual({ text: "Encoder", status: "accepted", note: "" });
    expect(valueText({ role: "setting", pos: 0, uid: "papers:mae", state: "draft", label: "Masked autoencoder" }, "papers"))
      .toEqual({ text: "Masked autoencoder", status: "draft", note: "draft" });
    expect(valueText({ role: "setting", pos: 0, uid: "papers:gone", state: "missing", label: null }, "papers"))
      .toEqual({ text: "a record that does not exist", status: "missing", note: "missing" });
  });
});

describe("the Capture view's source list", () => {
  const at = (root: string, id: string, source: string, extra: Partial<Draft> = {}): Draft =>
    draft(`b:${id}`, { path: `${root}/.knowledge/drafts/${id}.md`, source, lines: "1", ...extra });

  it("reads a local draft's source as an absolute path under its base root", () => {
    expect(absoluteSource(at("/vaults/papers", "mae", "papers/mae/mae_ch.md"))).toBe("/vaults/papers/papers/mae/mae_ch.md");
  });

  it("names each source by its file name when the names are unique, in kgd's order", () => {
    const groups = sourceGroups([
      at("/vaults/papers", "mae", "papers/mae/mae_ch.md"),
      at("/vaults/notes", "measure", "analysis/measure.tex"),
      at("/vaults/papers", "ratio", "papers/mae/mae_ch.md"),
    ]);
    expect(groups.map((group) => [group.name, group.title, group.drafts.map((item) => item.id)])).toEqual([
      ["mae_ch.md", "/vaults/papers/papers/mae/mae_ch.md", ["mae", "ratio"]],
      ["measure.tex", "/vaults/notes/analysis/measure.tex", ["measure"]],
    ]);
  });

  it("adds parent folders only to names that clash, across folders and bases", () => {
    const groups = sourceGroups([
      at("/vaults/papers", "a", "papers/mae/notes.md"),
      at("/vaults/papers", "b", "papers/beit/notes.md"),
      at("/vaults/notes", "c", "papers/mae/notes.md"),
      at("/vaults/notes", "d", "intro.md"),
    ]);
    expect(groups.map((group) => group.name)).toEqual(["papers/papers/mae/notes.md", "beit/notes.md", "notes/papers/mae/notes.md", "intro.md"]);
    expect(new Set(groups.map((group) => group.key)).size).toBe(4);
  });

  it("puts every foreign draft in one group, last", () => {
    const foreign = (id: string) => draft(`b:${id}`, { source: `https://arxiv.org/abs/${id}`, foreign: true });
    const groups = sourceGroups([foreign("2111.06377"), at("/vaults/papers", "mae", "papers/mae/mae_ch.md"), foreign("2106.08254")]);
    expect(groups.map((group) => [group.key, group.name, group.drafts.length])).toEqual([
      ["/vaults/papers/papers/mae/mae_ch.md", "mae_ch.md", 1],
      [FOREIGN_GROUP, "foreign", 2],
    ]);
    expect(sourceGroups([at("/vaults/papers", "mae", "a.md")]).map((group) => group.key)).not.toContain(FOREIGN_GROUP);
  });
});

describe("drawing a record into a draft", () => {
  const roots = new Map([["papers", "/vaults/papers"]]);
  const filesOf = (written: Map<string, string>): CaptureFiles => ({
    read: async (path) => {
      const text = written.get(path);
      if (text === undefined) throw new Error(`no file ${path}`);
      return text;
    },
    ids: async () => new Set(),
    create: async (path, text) => {
      if (written.has(path)) throw new Error("exists");
      written.set(path, text);
    },
    remove: async (path) => { written.delete(path); },
  });

  it("copies the record's file into a draft with its id", async () => {
    const written = new Map([["/vaults/papers/.knowledge/entries/mae.md", "---\nlabel: MAE\n---\nbody\n"]]);
    await expect(drawIntoDraft(roots, filesOf(written), "papers:mae")).resolves.toEqual({ path: "/vaults/papers/.knowledge/drafts/mae.md", created: true });
    expect(written.get("/vaults/papers/.knowledge/drafts/mae.md")).toBe("---\nlabel: MAE\n---\nbody\n");
  });

  it("keeps a draft already waiting under that id, and refuses an unknown base", async () => {
    const written = new Map([["/vaults/papers/.knowledge/entries/mae.md", "record"], ["/vaults/papers/.knowledge/drafts/mae.md", "edited"]]);
    await expect(drawIntoDraft(roots, filesOf(written), "papers:mae")).resolves.toMatchObject({ created: false });
    expect(written.get("/vaults/papers/.knowledge/drafts/mae.md")).toBe("edited");
    await expect(drawIntoDraft(roots, filesOf(written), "notes:mae")).rejects.toThrow("base notes has no registered root");
  });
});

describe("editing a draft's values", () => {
  const relation = draft("kb:sum", {
    kind: "implies",
    choices: { node_kinds: { definition: [], theorem: ["about"] }, relation_kinds: { implies: ["premise", "conclusion"] }, epistemic: ["stated"] },
    out: [
      { role: "premise", pos: 0, uid: "kb:subspace", state: "accepted", label: "Subspace" },
      { role: "premise", pos: 1, uid: "notes:span", state: "accepted", label: "Span" },
      { role: "requires", pos: 0, uid: "kb:vector-space", state: "draft", label: "Vector space" },
    ],
  });

  it("links a record of the draft's base locally and others across bases", () => {
    expect(linkFor("kb:subspace", "kb")).toBe("[[subspace]]");
    expect(linkFor("notes:span", "kb")).toBe("[[notes:span]]");
  });

  it("lists a role's links without the one removed", () => {
    expect(roleLinks(relation, "premise")).toEqual(["[[subspace]]", "[[notes:span]]"]);
    expect(roleLinks(relation, "premise", 0)).toEqual(["[[notes:span]]"]);
    expect(roleLinks(relation, "conclusion")).toEqual([]);
  });

  it("offers the kind's roles, the roles it holds, then requires", () => {
    expect(editableRoles(relation)).toEqual(["premise", "conclusion", "requires"]);
    expect(editableRoles(draft("kb:node", { kind: "definition", choices: relation.choices }))).toEqual(["requires"]);
    expect(editableRoles(draft("kb:odd", { kind: "unknown", out: [{ role: "witness", pos: 0, uid: "kb:x", state: "missing" }] })))
      .toEqual(["witness", "requires"]);
  });
});

