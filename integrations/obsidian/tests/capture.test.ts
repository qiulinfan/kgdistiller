import { describe, expect, it } from "vitest";

import {
  captureSelection,
  freeId,
  locateSelection,
  numberedPassage,
  passageRange,
  recordFile,
  selectionNames,
  shownText,
  slug,
  type CaptureFiles,
  type CaptureKgd,
  type Sheet,
} from "../src/capture";
import type { Draft } from "../src/drafts";
import type { ChatReply, ChatRequest } from "../src/model";
import type { RecordSummary } from "../src/lookup-model";

const SOURCE = "/vaults/papers/papers/mae/mae_ch.md";
const lines = [
  "# 4. Experiments",
  "",
  "**Mask Sampling Strategy。**表 1 比较不同 mask sampling strategies。",
  "",
  "简单的 random sampling 对 MAE 最好：允许更高 masking ratio \\[[2](#^reference-2)\\]。",
  "这比 block-wise masking 更好。",
  "",
  "**Training Schedule。**accuracy 随更长 training 持续改善。",
];

const sheet: Sheet = {
  base: "papers",
  source: "papers/mae/mae_ch.md",
  type: "ai-research",
  node_kinds: ["definition", "algorithm", "architecture"],
  relation_kinds: { compares: ["baseline", "candidate", "setting"], property: ["subject", "uses"] },
  epistemic: ["empirical", "stated"],
  guidance: "Nodes are noun-like objects.",
  line_count: lines.length,
  records: [{ uid: "papers:masked-autoencoder", label: "Masked autoencoder", kind: "architecture", class: "node", lines: "3", gloss: "MAE." }],
  drafts: [],
};

const summary = (uid: string, label: string, kind = "algorithm"): RecordSummary =>
  ({ uid, label, kind, class: "node", base: uid.split(":")[0]!, source: "x.md", lines: "1", gloss: `${label}.` });

const randomSampling = summary("papers:random-patch-sampling", "Random patch sampling");
const blockMasking = summary("notes:block-wise-masking", "Block-wise masking");

interface World {
  kgd: CaptureKgd;
  files: CaptureFiles;
  written: Map<string, string>;
  commands: string[][];
  resolved: string[][];
  /** The kg_drafts calls, by their arguments. */
  listed: Array<Record<string, unknown>>;
}

const draft = (uid: string, label: string, extra: Partial<Draft> = {}): Draft => ({
  uid, base: uid.split(":")[0]!, id: uid.split(":")[1]!, path: `/vaults/${uid.replace(":", "/.knowledge/drafts/")}.md`, label,
  class: "node", kind: "algorithm", lines: "5", with: [], linked_by: [], problems: [], ...extra,
});

/**
 * kgd and the file system for one capture: `senses` names the records kg_resolve reports for a term,
 * `named` the drafts kg_drafts finds by name, and the first `refuse` drafts written get a problem.
 */
function world(senses: Record<string, RecordSummary[]> = {}, refuse = 0, named: Draft[] = [], source: Partial<Sheet> = {}): World {
  const written = new Map<string, string>([[SOURCE, lines.join("\n")]]);
  const commands: string[][] = [];
  const resolved: string[][] = [];
  const listed: Array<Record<string, unknown>> = [];
  let refusals = refuse;
  const kgd: CaptureKgd = {
    async call<T>(tool: string, args: Record<string, unknown>): Promise<T> {
      if (tool === "kg_search") return { query: args.query, lanes: ["lexical"], results: [randomSampling], lag: {} } as T;
      if (tool === "kg_resolve") {
        const terms = args.terms as string[];
        resolved.push(terms);
        return { terms: terms.map((term) => ({ term, senses: senses[term] ?? [], pending: [] })), lag: {} } as T;
      }
      if (tool === "kg_drafts") {
        listed.push(args);
        if (args.names) return { drafts: named.filter((found) => (args.names as string[]).includes(found.label!)) } as T;
        // Listing the source's drafts after a write: every written draft, the first ones refused.
        const drafts = [...written.keys()].filter((path) => path.includes("/drafts/")).map((path) => {
          const id = path.split("/").pop()!.replace(/\.md$/, "");
          return draft(`papers:${id}`, id, { path, problems: refusals-- > 0 ? ["candidate[0]: unknown link"] : [] });
        });
        return { drafts } as T;
      }
      throw new Error(`unexpected tool ${tool}`);
    },
    async command(args: string[]) {
      commands.push(args);
      if (args[0] === "sheet") return { report: { ...sheet, ...source } as unknown as Record<string, unknown>, failed: false };
      return { report: {}, failed: false };
    },
    async baseRoots() {
      return new Map([["papers", "/vaults/papers"], ["notes", "/vaults/notes"]]);
    },
  };
  const files: CaptureFiles = {
    read: async (path) => written.get(path) ?? "",
    ids: async () => new Set(["masked-autoencoder", "random-sampling-is-best-for-mae"]),
    create: async (path, text) => {
      if (written.has(path)) throw new Error("exists");
      written.set(path, text);
    },
    remove: async (path) => { written.delete(path); },
  };
  return { kgd, files, written, commands, resolved, listed };
}

/** A model that submits each argument object in turn. */
function model(...submissions: Array<Record<string, unknown>>): { complete: (request: ChatRequest) => Promise<ChatReply>; requests: ChatRequest[] } {
  const requests: ChatRequest[] = [];
  return {
    requests,
    complete: async (request) => {
      requests.push(structuredClone(request));
      const args = submissions.shift();
      if (!args) throw new Error("no more submissions");
      const name = "names" in args ? "lookup" : "submit";
      return {
        message: { content: null, tool_calls: [{ id: `c${requests.length}`, type: "function", function: { name, arguments: JSON.stringify(args) } }] },
      };
    },
  };
}

const input = { path: SOURCE, from: 5, to: 5, selection: "简单的 random sampling 对 MAE 最好" };

const comparison = {
  item: "Random sampling is best for MAE.",
  decision: "new",
  label: "Random sampling is best for MAE",
  aliases: ["随机采样最好"],
  kind: "compares",
  roles: { candidate: ["[[papers:random-patch-sampling]]"], baseline: ["block-wise masking"], setting: ["[[papers:masked-autoencoder]]"] },
  statement: "简单的 random sampling 对 MAE 最好。",
  evidence: [{ lines: "5", quote: "简单的 random sampling 对 MAE 最好" }],
  epistemic: "empirical",
  report: "已收录。",
};

describe("the capture passage", () => {
  it("covers the selection's paragraph plus two non-blank lines each side", () => {
    expect(passageRange(lines, 5, 5)).toEqual([1, 8]);
    expect(passageRange(lines, 8, 8)).toEqual([5, 8]);
  });

  it("numbers the passage's non-blank lines and shows the nearest heading above it", () => {
    expect(numberedPassage(lines, [5, 6])).toBe(`1| # 4. Experiments\n…\n5| ${lines[4]}\n6| ${lines[5]}`);
  });
});

describe("a reading-view selection", () => {
  it("is found in the source as reading view shows it", () => {
    expect(shownText("文献 \\[[2](#^reference-2)\\] 提出的 **block-wise** masking ^figure-6")).toBe("文献 [2] 提出的 block-wise masking");
    expect(shownText("## 4.3. *Partial* Fine-Tuning")).toBe("4.3. Partial Fine-Tuning");
    expect(locateSelection("MAE 最好：允许更高 masking ratio [2]。 这比 block-wise", lines)).toEqual([5, 6]);
    expect(locateSelection("Mask Sampling Strategy。表 1", lines)).toEqual([3, 3]);
  });

  it("is not located when it occurs nowhere or twice", () => {
    expect(locateSelection("not in the source", lines)).toBeUndefined();
    expect(locateSelection("masking", lines)).toBeUndefined();
    expect(locateSelection(" ", lines)).toBeUndefined();
  });

  it("is placed by its block when its text occurs more than once", () => {
    expect(locateSelection("masking", lines, "这比 block-wise masking 更好。")).toEqual([6, 6]);
    expect(locateSelection("masking", lines, "简单的 random sampling 对 MAE 最好：允许更高 masking ratio [2]。")).toEqual([5, 5]);
    expect(locateSelection("masking", lines, "no such block in this source at all, long enough to count")).toBeUndefined();
  });
});

describe("ids and names", () => {
  it("slugs labels as the record format does and avoids taken ids", () => {
    expect(slug("Random sampling is best for MAE!")).toBe("random-sampling-is-best-for-mae");
    expect(slug("测度空间 (Measure)")).toBe("测度空间-measure");
    expect(slug(`${"word ".repeat(30)}`)).toHaveLength(79);
    expect(freeId("Masked autoencoder", new Set(["masked-autoencoder", "masked-autoencoder-2"]))).toBe("masked-autoencoder-3");
    expect(freeId("!!!", new Set())).toBeUndefined();
  });

  it("collects the selection's Latin-script names with singular forms", () => {
    expect(selectionNames("仅 fine-tune 一个 Transformer block，MAE 与 autoencoders")).toEqual(
      ["fine-tune", "Transformer block", "MAE", "autoencoders", "autoencoder"]);
  });

  it("writes every frontmatter scalar as a JSON string", () => {
    const file = recordFile(
      { label: "A \"quoted\" label", kind: "property", aliases: [], roles: { subject: ["[[x]]"] }, requires: [], statement: "S.", evidence: [] },
      "papers/a.md", [[5, 6], [9, 9]], ["q1", "q2"]);
    expect(file).toBe([
      "---", "label: \"A \\\"quoted\\\" label\"", "kind: \"property\"", "source: \"papers/a.md\"", "lines: \"5-6, 9\"",
      "subject:", "  - \"[[x]]\"", "---", "S.", "", "## Evidence", "", "> q1", "", "> q2", "",
    ].join("\n"));
  });
});

describe("capturing a selection", () => {
  it("writes a draft that kgd accept would take, and accepts nothing", async () => {
    const { kgd, files, written, commands, listed } = world();
    const { complete, requests } = model(comparison);
    const result = await captureSelection(complete, kgd, files, input);
    expect(result).toEqual({
      item: "Random sampling is best for MAE.", decision: "new", uid: "papers:random-sampling-is-best-for-mae-2",
      label: "Random sampling is best for MAE", draft: true, report: "已收录。",
    });
    expect(written.get("/vaults/papers/.knowledge/drafts/random-sampling-is-best-for-mae-2.md")).toBe([
      "---", "label: \"Random sampling is best for MAE\"", "kind: \"compares\"",
      "source: \"papers/mae/mae_ch.md\"", "lines: \"5\"", "epistemic: \"empirical\"",
      "candidate:", "  - \"[[random-patch-sampling]]\"", "baseline:", "  - \"block-wise masking\"", "setting:", "  - \"[[masked-autoencoder]]\"",
      "---", "简单的 random sampling 对 MAE 最好。", "", "## Evidence", "", "> 简单的 random sampling 对 MAE 最好", "",
    ].join("\n"));
    expect(commands).toEqual([["sheet", SOURCE, "--json"]]);
    expect(listed.at(-1)).toEqual({ base: ["papers"], source: ["papers/mae/mae_ch.md"] });
    expect(requests[0]).toMatchObject({ tool_choice: "required", reasoning_effort: "low" });
    const user = requests[0]!.messages[1]!.content as string;
    expect(user).toContain("Selected, lines 5:\n简单的 random sampling 对 MAE 最好");
    expect(user).toContain("5| 简单的 random sampling");
    expect(user).toContain("Records already citing this source:\n[papers:masked-autoencoder] Masked autoencoder · architecture node · L3 — MAE.");
    expect(user).toContain("Records a search finds for the selection:\n[papers:random-patch-sampling]");
    expect(user).toContain("Names in the selection, looked up in every base:\n\"random sampling\":\n  - no record has this name");
    expect(requests[0]!.messages[0]!.content).toContain("Relation kinds and their roles: compares (baseline, candidate, setting); property (subject, uses)");
  });

  it("looks up an unlooked name before writing and returns records the model had not seen", async () => {
    const { kgd, files, written } = world({ "block-wise masking": [blockMasking] });
    const linked = { ...comparison, roles: { ...comparison.roles, baseline: ["[[notes:block-wise-masking]]"] } };
    const { complete, requests } = model(comparison, linked);
    await captureSelection(complete, kgd, files, input);
    expect(requests).toHaveLength(2);
    const bounce = requests[1]!.messages.at(-1)!.content as string;
    expect(bounce).toMatch(/^Not filed yet\. These names belong to records or drafts you had not seen:/);
    expect(bounce).toContain("[notes:block-wise-masking] Block-wise masking");
    const draft = [...written.entries()].find(([path]) => path.includes("/drafts/"))![1];
    expect(draft).toContain("baseline:\n  - \"[[notes:block-wise-masking]]\"");
  });

  it("returns what kgd accept would refuse and removes the refused draft", async () => {
    const { kgd, files, written } = world({}, 1);
    const { complete, requests } = model(comparison, comparison);
    await captureSelection(complete, kgd, files, input);
    expect(requests[1]!.messages.at(-1)!.content).toBe(
      "error: kgd accept would refuse the record: candidate[0]: unknown link. Correct it and call submit again.");
    expect([...written.keys()].filter((path) => path.includes("/drafts/"))).toHaveLength(1);
  });

  it("shows the source's drafts, which a value may link like a record", async () => {
    const { kgd, files, written } = world({}, 0, [], { drafts: [{ id: "random-sampling", label: "Random sampling", kind: "algorithm", class: "node", lines: "4" }] });
    const linked = { ...comparison, roles: { ...comparison.roles, candidate: ["[[papers:random-sampling]]"] } };
    const { complete, requests } = model(linked);
    await captureSelection(complete, kgd, files, input);
    expect(requests[0]!.messages[1]!.content).toContain(
      "Drafts of this source awaiting the owner's review:\n[papers:random-sampling] Random sampling · algorithm node · papers L4 — a draft awaiting the owner's review");
    const written_ = [...written.entries()].find(([path]) => path.includes("/drafts/"))![1];
    expect(written_).toContain("candidate:\n  - \"[[random-sampling]]\"");
  });

  it("matches a draft as unchanged but never overwrites one, and links no draft of another base", async () => {
    const elsewhere = draft("notes:block-wise-masking", "block-wise masking");
    const unchanged = { item: "i", decision: "unchanged", record: "notes:block-wise-masking", report: "已是草稿。" };
    const { kgd, files } = world({}, 0, [elsewhere]);
    await expect(captureSelection(model({ names: ["block-wise masking"], phrases: [] }, unchanged).complete, kgd, files, input))
      .resolves.toEqual({ item: "i", decision: "unchanged", uid: "notes:block-wise-masking", label: "block-wise masking", draft: true, report: "已是草稿。" });
    const overwrite = { ...comparison, decision: "superseding", record: "notes:block-wise-masking" };
    const crossing = { ...comparison, roles: { ...comparison.roles, baseline: ["[[notes:block-wise-masking]]"] } };
    const second = world({}, 0, [elsewhere]);
    const { complete, requests } = model({ names: ["block-wise masking"], phrases: [] }, overwrite, crossing, comparison);
    await captureSelection(complete, second.kgd, second.files, input);
    const refusals = requests.slice(2).map((request) => request.messages.at(-1)!.content as string);
    expect(refusals[0]).toBe("error: [[notes:block-wise-masking]] is a draft awaiting the owner, not knowledge yet; decide unchanged when the item is that draft's item, or new");
    expect(refusals[1]).toBe("error: [[notes:block-wise-masking]] is a draft of another base, which only an accepted record can be linked across; write a plain term");
  });

  it("looks a name up among the drafts before writing it", async () => {
    const { kgd, files, written } = world({}, 0, [draft("papers:block-wise-masking", "block-wise masking")]);
    const linked = { ...comparison, roles: { ...comparison.roles, baseline: ["[[papers:block-wise-masking]]"] } };
    const { complete, requests } = model(comparison, linked);
    await captureSelection(complete, kgd, files, input);
    expect(requests).toHaveLength(2);
    const bounce = requests[1]!.messages.at(-1)!.content as string;
    expect(bounce).toMatch(/^Not filed yet\. These names belong to records or drafts you had not seen:/);
    expect(bounce).toContain("Drafts named so:\n  - [papers:block-wise-masking] block-wise masking");
    const written_ = [...written.entries()].find(([path]) => path.includes("/drafts/"))![1];
    expect(written_).toContain("baseline:\n  - \"[[block-wise-masking]]\"");
  });

  it("refuses links to records it never showed, kinds the type lacks and quotes the source lacks", async () => {
    const { kgd, files } = world();
    const bad = {
      ...comparison,
      kind: "compares",
      roles: { candidate: ["[[papers:invented]]"], winner: ["x"] },
      evidence: [{ lines: "5", quote: "block-wise masking 最好" }, { lines: "x", quote: "q" }],
    };
    const { complete, requests } = model(bad, { ...comparison, kind: "theorem" }, { ...comparison, evidence: [{ lines: "5", quote: "完全不同的一句话" }] }, comparison);
    await captureSelection(complete, kgd, files, input);
    const refusals = requests.slice(1).map((request) => request.messages.at(-1)!.content as string);
    expect(refusals[0]).toBe("error: [[papers:invented]] is not a record shown to you; link only shown uids, or write a plain term; "
      + "compares has no role \"winner\"; its roles are baseline, candidate, setting; a compares relation needs at least one role value");
    expect(refusals[1]).toMatch(/^error: kind must be one of definition, algorithm, architecture, compares, property$/);
    expect(refusals[2]).toBe(`error: evidence must be copied verbatim from the source.\nquote 1: it is not verbatim in lines 5. Those lines read:\n5| ${lines[4]}`);
  });

  it("files nothing for unchanged and borrowed, and only proposes for superseding", async () => {
    for (const [args, expected] of [
      [{ item: "i", decision: "unchanged", record: "papers:masked-autoencoder", report: "已有。" },
        { item: "i", decision: "unchanged", uid: "papers:masked-autoencoder", label: "Masked autoencoder", report: "已有。" }],
      [{ item: "i", decision: "borrowed", report: "出自 [2]。" }, { item: "i", decision: "borrowed", report: "出自 [2]。" }],
    ] as const) {
      const { kgd, files, commands } = world();
      await expect(captureSelection(model(args).complete, kgd, files, input)).resolves.toEqual(expected);
      expect(commands).toEqual([["sheet", SOURCE, "--json"]]);
    }
    const { kgd, files, commands } = world();
    const result = await captureSelection(model({ ...comparison, decision: "superseding", record: "papers:random-patch-sampling" }).complete, kgd, files, input);
    expect(result).toMatchObject({ decision: "superseding", uid: "papers:random-patch-sampling", label: "Random patch sampling" });
    expect(result.proposal).toContain("label: \"Random sampling is best for MAE\"");
    expect(commands).toEqual([["sheet", SOURCE, "--json"]]);
  });

  it("refuses unchanged without a shown record, and drops a relation's aliases", async () => {
    const { kgd, files, written } = world();
    const { complete, requests } = model({ item: "i", decision: "unchanged", record: "papers:nothing", report: "" }, comparison);
    await captureSelection(complete, kgd, files, input);
    expect(requests[1]!.messages.at(-1)!.content).toBe("error: record must be the uid of a record shown to you for unchanged");
    const draft = [...written.entries()].find(([path]) => path.includes("/drafts/"))![1];
    expect(draft).not.toContain("aliases");
  });
});
