import { describe, expect, it } from "vitest";

import {
  SIDE_SYSTEM,
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
  type CaptureInput,
  type CaptureKgd,
  type Profile,
} from "../src/capture";
import type { Draft } from "../src/drafts";
import type { ChatReply, ChatRequest } from "../src/model";
import type { FullRecord, RecordSummary } from "../src/lookup-model";
import type { OriginServices } from "../src/origin";
import type { WebSource } from "../src/web";

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

const profile: Profile = {
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

const BEIT_URL = "https://arxiv.org/abs/2106.08254";
const BEIT_ABSTRACT = "We introduce a self-supervised vision representation model BEiT. "
  + "Block-wise masking masks a block of image patches at a time instead of single patches.";

interface World {
  kgd: CaptureKgd;
  files: CaptureFiles;
  origin: OriginServices;
  written: Map<string, string>;
  commands: string[][];
  /** The kg_drafts calls, by their arguments. */
  listed: Array<Record<string, unknown>>;
  /** The kg_get calls, by the uids asked for. */
  got: string[][];
  /** The URLs the origin search fetched. */
  fetched: string[];
}

const draft = (uid: string, label: string, extra: Partial<Draft> = {}): Draft => ({
  uid, base: uid.split(":")[0]!, id: uid.split(":")[1]!, path: `/vaults/${uid.replace(":", "/.knowledge/drafts/")}.md`, label,
  class: "node", kind: "algorithm", lines: "5", with: [], linked_by: [], problems: [], ...extra,
});

interface WorldOptions {
  /** The records kg_resolve reports for a term. */
  senses?: Record<string, RecordSummary[]>;
  /** The first this many drafts written get a problem. */
  refuse?: number;
  /** The drafts kg_drafts finds by name. */
  named?: Draft[];
  /** Drafts already waiting in the base before the capture writes any. */
  existing?: Draft[];
  /** What `kgd source` reports, over `profile`. */
  source?: Partial<Profile>;
  /** The full records kg_get returns. */
  records?: Array<Partial<FullRecord> & { uid: string }>;
  /** The origin judge's answers, in turn. */
  judged?: Array<Record<string, unknown>>;
  /** The web results of the origin search. */
  results?: WebSource[];
}

/** A model that answers one tool call per reply, `name` chosen from the arguments. */
function scripted(name: (args: Record<string, unknown>) => string, answers: Array<Record<string, unknown>>):
  { complete: (request: ChatRequest) => Promise<ChatReply>; requests: ChatRequest[] } {
  const requests: ChatRequest[] = [];
  return {
    requests,
    complete: async (request) => {
      requests.push(structuredClone(request));
      const args = answers.shift();
      if (!args) throw new Error("no more answers");
      return {
        message: { content: null, tool_calls: [{ id: `c${requests.length}`, type: "function", function: { name: name(args), arguments: JSON.stringify(args) } }] },
      };
    },
  };
}

/** kgd, the file system and the origin search for one capture. */
function world(options: WorldOptions = {}): World {
  const { senses = {}, named = [], existing = [], source = {}, records = [], judged = [], results = [] } = options;
  const written = new Map<string, string>([[SOURCE, lines.join("\n")]]);
  const commands: string[][] = [];
  const listed: Array<Record<string, unknown>> = [];
  const got: string[][] = [];
  const fetched: string[] = [];
  let refusals = options.refuse ?? 0;
  const kgd: CaptureKgd = {
    async call<T>(tool: string, args: Record<string, unknown>): Promise<T> {
      if (tool === "kg_search") return { query: args.query, lanes: ["lexical"], results: [randomSampling], lag: {} } as T;
      if (tool === "kg_resolve") {
        const terms = args.terms as string[];
        return { terms: terms.map((term) => ({ term, senses: senses[term] ?? [] })), lag: {} } as T;
      }
      if (tool === "kg_get") {
        const uids = args.uids as string[];
        got.push(uids);
        return { records: records.filter((record) => uids.includes(record.uid)), missing: [], lag: {} } as T;
      }
      if (tool === "kg_drafts") {
        listed.push(args);
        if (args.names) return { drafts: named.filter((found) => (args.names as string[]).includes(found.label!)) } as T;
        // Listing the base's drafts: those already waiting, then every draft written, the first ones refused.
        const drafts = [...written.keys()].filter((path) => path.includes("/drafts/")).map((path) => {
          const id = path.split("/").pop()!.replace(/\.md$/, "");
          return draft(`papers:${id}`, id, { path, problems: refusals-- > 0 ? ["candidate[0]: unknown link"] : [] });
        });
        return { drafts: [...existing, ...drafts] } as T;
      }
      throw new Error(`unexpected tool ${tool}`);
    },
    async command(args: string[]) {
      commands.push(args);
      if (args[0] === "source") return { report: { ...profile, ...source } as unknown as Record<string, unknown>, failed: false };
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
  const origin: OriginServices = {
    complete: scripted(() => "origin", judged).complete,
    // Wikipedia has no article for the term; arXiv answers with BEiT's abstract.
    fetch: async (url) => {
      fetched.push(url);
      if (url.startsWith("https://export.arxiv.org/")) {
        return `<feed><entry><id>http://arxiv.org/abs/2106.08254v2</id><published>2021-06-15T00:00:00Z</published>`
          + `<title>BEiT: BERT Pre-Training of Image Transformers</title><summary>${BEIT_ABSTRACT}</summary></entry></feed>`;
      }
      return JSON.stringify({ query: { pages: [] } });
    },
    webSearch: async () => results,
  };
  return { kgd, files, origin, written, commands, listed, got, fetched };
}

/** The capture model: lookups for arguments with names, submissions otherwise. */
const model = (...answers: Array<Record<string, unknown>>) => scripted((args) => ("names" in args ? "lookup" : "submit"), answers);

const capture = (w: World, complete: (request: ChatRequest) => Promise<ChatReply>, at: CaptureInput = input) =>
  captureSelection(complete, w.kgd, w.files, w.origin, at);

const drafts = (w: World) => [...w.written.entries()].filter(([path]) => path.includes("/drafts/"));
/** The last message of each request from the n-th on: the tool results the model saw. */
const seen = (requests: ChatRequest[], from = 1) => requests.slice(from).map((request) => request.messages.at(-1)!.content as string);

const input: CaptureInput = { path: SOURCE, from: 5, to: 5, selection: "简单的 random sampling 对 MAE 最好" };

const comparison = {
  item: "Random sampling is best for MAE.",
  decision: "new",
  label: "Random sampling is best for MAE",
  aliases: ["随机采样最好"],
  kind: "compares",
  roles: { candidate: ["[[papers:random-patch-sampling]]"], setting: ["[[papers:masked-autoencoder]]"] },
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
});

describe("the record file", () => {
  const node = { label: "Block-wise masking", kind: "algorithm", aliases: ["分块掩码"], roles: {}, requires: ["[[masked-autoencoder]]"], statement: "S.", evidence: [] };

  it("writes every frontmatter scalar as a JSON string", () => {
    const file = recordFile(
      { label: "A \"quoted\" label", kind: "property", aliases: [], roles: { subject: ["[[x]]"] }, requires: [], statement: "S.", evidence: [] },
      "papers/a.md", [[5, 6], [9, 9]], ["q1", "q2"]);
    expect(file).toBe([
      "---", "label: \"A \\\"quoted\\\" label\"", "kind: \"property\"", "source: \"papers/a.md\"", "lines: \"5-6, 9\"",
      "subject:", "  - \"[[x]]\"", "---", "S.", "", "## Evidence", "", "> q1", "", "> q2", "",
    ].join("\n"));
  });

  it("cites a foreign draft's page without lines", () => {
    expect(recordFile({ ...node, epistemic: "stated" }, BEIT_URL, undefined, ["Block-wise masking masks a block."])).toBe([
      "---", "label: \"Block-wise masking\"", "kind: \"algorithm\"", "aliases:", "  - \"分块掩码\"", `source: "${BEIT_URL}"`,
      "epistemic: \"stated\"", "requires:", "  - \"[[masked-autoencoder]]\"", "---", "S.", "", "## Evidence", "", "> Block-wise masking masks a block.", "",
    ].join("\n"));
  });

  it("writes understanding only when it is no, and the search terms before the evidence", () => {
    expect(recordFile(node, "a.md", [[3, 3]], ["q"], "yes")).not.toContain("understanding");
    expect(recordFile(node, "a.md", [[3, 3]], ["q"], "no", "  block masking\nBEiT masking  ")).toBe([
      "---", "label: \"Block-wise masking\"", "kind: \"algorithm\"", "aliases:", "  - \"分块掩码\"", "source: \"a.md\"", "lines: \"3\"",
      "understanding: \"no\"", "requires:", "  - \"[[masked-autoencoder]]\"", "---", "S.", "",
      "## Search terms", "", "block masking\nBEiT masking", "", "## Evidence", "", "> q", "",
    ].join("\n"));
  });
});

describe("capturing a selection", () => {
  it("writes a draft that kgd accept would take, and accepts nothing", async () => {
    const w = world();
    const { complete, requests } = model(comparison);
    const result = await capture(w, complete);
    expect(result).toEqual({
      item: "Random sampling is best for MAE.", decision: "new", uid: "papers:random-sampling-is-best-for-mae-2",
      label: "Random sampling is best for MAE", draft: true, report: "已收录。",
    });
    expect(w.written.get("/vaults/papers/.knowledge/drafts/random-sampling-is-best-for-mae-2.md")).toBe([
      "---", "label: \"Random sampling is best for MAE\"", "kind: \"compares\"",
      "source: \"papers/mae/mae_ch.md\"", "lines: \"5\"", "epistemic: \"empirical\"",
      "candidate:", "  - \"[[random-patch-sampling]]\"", "setting:", "  - \"[[masked-autoencoder]]\"",
      "---", "简单的 random sampling 对 MAE 最好。", "", "## Evidence", "", "> 简单的 random sampling 对 MAE 最好", "",
    ].join("\n"));
    expect(w.commands).toEqual([["source", SOURCE]]);
    expect(w.listed.at(-1)).toEqual({ base: ["papers"] });
    expect(requests[0]).toMatchObject({ tool_choice: "required", reasoning_effort: "low" });
    const user = requests[0]!.messages[1]!.content as string;
    expect(user).toContain("Selected, lines 5:\n简单的 random sampling 对 MAE 最好");
    expect(user).toContain("5| 简单的 random sampling");
    expect(user).toContain("Records already citing this source:\n[papers:masked-autoencoder] Masked autoencoder · architecture node · L3 — MAE.");
    expect(user).toContain("Records a search finds for the selection:\n[papers:random-patch-sampling]");
    expect(user).toContain("Names in the selection, looked up in every base:\n\"random sampling\":\n  - no record has this name");
    expect(requests[0]!.messages[0]!.content).toContain("Relation kinds and their roles: compares (baseline, candidate, setting); property (subject, uses)");
    expect(requests[0]!.messages[0]!.content).not.toContain(SIDE_SYSTEM);
  });

  it("refuses a value that is not a link to a shown record or draft", async () => {
    const w = world();
    const plain = { ...comparison, roles: { ...comparison.roles, baseline: ["block-wise masking"] } };
    const { complete, requests } = model(plain, comparison);
    await capture(w, complete);
    expect(seen(requests)).toEqual([
      "error: \"block-wise masking\" is not a link \"[[uid]]\"; leave a concept no record or draft shown holds out of the values"]);
    expect(drafts(w)).toHaveLength(1);
    expect(drafts(w)[0]![1]).not.toContain("block-wise masking");
  });

  it("looks up an unlooked name before writing and returns records the model had not seen", async () => {
    const w = world({ senses: { "Random sampling is best for MAE": [blockMasking] } });
    const linked = { ...comparison, roles: { ...comparison.roles, baseline: ["[[notes:block-wise-masking]]"] } };
    const { complete, requests } = model(comparison, linked);
    await capture(w, complete);
    expect(requests).toHaveLength(2);
    const bounce = seen(requests)[0]!;
    expect(bounce).toMatch(/^Not filed yet\. These names belong to records or drafts you had not seen:/);
    expect(bounce).toContain("[notes:block-wise-masking] Block-wise masking");
    expect(drafts(w)[0]![1]).toContain("baseline:\n  - \"[[notes:block-wise-masking]]\"");
  });

  it("returns what kgd accept would refuse and removes the refused draft", async () => {
    const w = world({ refuse: 1 });
    const { complete, requests } = model(comparison, comparison);
    await capture(w, complete);
    expect(seen(requests)).toEqual(["error: kgd accept would refuse the record: candidate[0]: unknown link. Correct it and call submit again."]);
    expect(drafts(w)).toHaveLength(1);
  });

  it("shows the source's drafts, which a value may link like a record", async () => {
    const w = world({ source: { drafts: [{ id: "random-sampling", label: "Random sampling", kind: "algorithm", class: "node", lines: "4" }] } });
    const linked = { ...comparison, roles: { ...comparison.roles, candidate: ["[[papers:random-sampling]]"] } };
    const { complete, requests } = model(linked);
    await capture(w, complete);
    expect(requests[0]!.messages[1]!.content).toContain(
      "Drafts of this source awaiting the owner's review:\n[papers:random-sampling] Random sampling · algorithm node · papers L4 — a draft awaiting the owner's review");
    expect(drafts(w)[0]![1]).toContain("candidate:\n  - \"[[random-sampling]]\"");
  });

  it("matches a draft as unchanged but never overwrites one, and links no draft of another base", async () => {
    const elsewhere = draft("notes:block-wise-masking", "block-wise masking");
    const unchanged = { item: "i", decision: "unchanged", record: "notes:block-wise-masking", report: "已是草稿。" };
    await expect(capture(world({ named: [elsewhere] }), model({ names: ["block-wise masking"], phrases: [] }, unchanged).complete))
      .resolves.toEqual({ item: "i", decision: "unchanged", uid: "notes:block-wise-masking", label: "block-wise masking", draft: true, report: "已是草稿。" });
    const overwrite = { ...comparison, decision: "superseding", record: "notes:block-wise-masking" };
    const crossing = { ...comparison, roles: { ...comparison.roles, baseline: ["[[notes:block-wise-masking]]"] } };
    const { complete, requests } = model({ names: ["block-wise masking"], phrases: [] }, overwrite, crossing, comparison);
    await capture(world({ named: [elsewhere] }), complete);
    expect(seen(requests, 2)).toEqual([
      "error: [[notes:block-wise-masking]] is a draft awaiting the owner, not knowledge yet; decide unchanged when the item is that draft's item, or new",
      "error: [[notes:block-wise-masking]] is a draft of another base, which only an accepted record can be linked across; leave it out",
    ]);
  });

  it("looks a name up among the drafts before writing it", async () => {
    const w = world({ named: [draft("papers:block-wise-masking", "Random sampling is best for MAE")] });
    const linked = { ...comparison, roles: { ...comparison.roles, baseline: ["[[papers:block-wise-masking]]"] } };
    const { complete, requests } = model(comparison, linked);
    await capture(w, complete);
    expect(requests).toHaveLength(2);
    const bounce = seen(requests)[0]!;
    expect(bounce).toMatch(/^Not filed yet\. These names belong to records or drafts you had not seen:/);
    expect(bounce).toContain("Drafts named so:\n  - [papers:block-wise-masking] Random sampling is best for MAE");
    expect(drafts(w)[0]![1]).toContain("baseline:\n  - \"[[block-wise-masking]]\"");
  });

  it("refuses links to records it never showed, kinds the type lacks and quotes the source lacks", async () => {
    const bad = {
      ...comparison,
      kind: "compares",
      roles: { candidate: ["[[papers:invented]]"], winner: ["[[papers:masked-autoencoder]]"] },
      evidence: [{ lines: "5", quote: "block-wise masking 最好" }, { lines: "x", quote: "q" }],
    };
    const { complete, requests } = model(bad, { ...comparison, kind: "theorem" }, { ...comparison, evidence: [{ lines: "5", quote: "完全不同的一句话" }] }, comparison);
    await capture(world(), complete);
    const refusals = seen(requests);
    expect(refusals[0]).toBe("error: [[papers:invented]] is not a record or draft shown to you; link only shown uids; "
      + "compares has no role \"winner\"; its roles are baseline, candidate, setting; a compares relation needs at least one role value");
    expect(refusals[1]).toMatch(/^error: kind must be one of definition, algorithm, architecture, compares, property$/);
    expect(refusals[2]).toBe(`error: evidence must be copied verbatim from the source.\nquote 1: it is not verbatim in lines 5. Those lines read:\n5| ${lines[4]}`);
  });

  it("files nothing for unchanged", async () => {
    const w = world();
    await expect(capture(w, model({ item: "i", decision: "unchanged", record: "papers:masked-autoencoder", report: "已有。" }).complete))
      .resolves.toEqual({ item: "i", decision: "unchanged", uid: "papers:masked-autoencoder", label: "Masked autoencoder", report: "已有。" });
    expect(w.commands).toEqual([["source", SOURCE]]);
    expect(drafts(w)).toEqual([]);
  });

  it("refuses unchanged without a shown record, and drops a relation's aliases", async () => {
    const w = world();
    const { complete, requests } = model({ item: "i", decision: "unchanged", record: "papers:nothing", report: "" }, comparison);
    await capture(w, complete);
    expect(seen(requests)).toEqual(["error: record must be the uid of a record shown to you for unchanged"]);
    expect(drafts(w)[0]![1]).not.toContain("aliases");
  });
});

describe("a changed meaning of a recorded item", () => {
  const superseding = { ...comparison, decision: "superseding", record: "papers:random-patch-sampling", report: "更一般。" };
  const recorded = { uid: "papers:random-patch-sampling", understanding: "no" };

  it("is written as a replacement draft with the record's id, keeping its understanding", async () => {
    const w = world({ records: [recorded] });
    const result = await capture(w, model(superseding).complete);
    expect(result).toEqual({
      item: comparison.item, decision: "superseding", uid: "papers:random-patch-sampling", label: "Random sampling is best for MAE",
      draft: true, replaces: true, report: "更一般。",
    });
    expect(w.got).toEqual([["papers:random-patch-sampling"]]);
    expect(drafts(w).map(([path]) => path)).toEqual(["/vaults/papers/.knowledge/drafts/random-patch-sampling.md"]);
    const file = drafts(w)[0]![1];
    expect(file).toContain("label: \"Random sampling is best for MAE\"\n");
    expect(file).toContain("lines: \"5\"\nunderstanding: \"no\"\n");
  });

  it("writes no understanding when the record is understood", async () => {
    const w = world({ records: [{ ...recorded, understanding: "yes" }] });
    await capture(w, model({ ...superseding, decision: "conflicting" }).complete);
    expect(drafts(w)[0]![1]).not.toContain("understanding");
  });

  it("does not overwrite a replacement already waiting", async () => {
    const waiting = draft("papers:random-patch-sampling", "Random patch sampling, replaced", { replaces: "papers:random-patch-sampling" });
    const w = world({ records: [recorded], existing: [waiting] });
    const result = await capture(w, model(superseding).complete);
    expect(result).toEqual({
      item: comparison.item, decision: "superseding", uid: "papers:random-patch-sampling", label: "Random patch sampling",
      draft: true, replaces: true, report: "更一般。 A replacement of this record already waits for you.",
    });
    expect(drafts(w)).toEqual([]);
    expect(w.got).toEqual([]);
  });

  it("of another base is only proposed, and nothing is written", async () => {
    const w = world({ senses: { "block-wise masking": [blockMasking] } });
    const conflicting = { ...comparison, decision: "conflicting", record: "notes:block-wise-masking", report: "含义不同。" };
    const result = await capture(w, model({ names: ["block-wise masking"], phrases: [] }, conflicting).complete);
    expect(result).toMatchObject({ item: comparison.item, decision: "conflicting", uid: "notes:block-wise-masking", label: "Block-wise masking", report: "含义不同。" });
    expect(result).not.toHaveProperty("draft");
    expect(result.proposal).toContain("label: \"Random sampling is best for MAE\"");
    expect(result.proposal).toContain("source: \"papers/mae/mae_ch.md\"\nlines: \"5\"");
    expect(drafts(w)).toEqual([]);
    expect(w.listed.filter((args) => args.base)).toEqual([]);
  });
});

describe("a borrowed concept", () => {
  const borrowedInput: CaptureInput = { path: SOURCE, from: 6, to: 6, selection: "block-wise masking" };
  const asked = { item: "Block-wise masking, from BEiT.", decision: "borrowed", label: "Block-wise masking", report: "出自 BEiT。" };
  const page = {
    ...asked, page: 1, kind: "algorithm", aliases: ["分块掩码"], roles: {}, requires: ["[[papers:masked-autoencoder]]"],
    statement: "Block-wise masking masks a block of image patches at a time.",
    evidence: [{ lines: "", quote: "Block-wise masking masks a block of image patches  at a time" }], epistemic: "stated",
  };
  const beit: WebSource = { title: "BEiT: BERT Pre-Training of Image Transformers", url: BEIT_URL, snippet: "BEiT masks blocks of patches." };
  const fromBeit = { choice: "web", number: 1, title: "BEiT: BERT Pre-Training of Image Transformers", reason: "BEiT 提出。" };

  it("shows the pages it comes from, then is drafted as foreign knowledge citing the page without lines", async () => {
    const w = world({ judged: [fromBeit], results: [beit] });
    const { complete, requests } = model(asked, page);
    const result = await capture(w, complete, borrowedInput);
    expect(result).toEqual({
      item: asked.item, decision: "borrowed", uid: "papers:block-wise-masking", label: "Block-wise masking", draft: true, foreign: true, report: "出自 BEiT。",
    });
    const shown = seen(requests)[0]!;
    expect(shown).toContain(`Where "Block-wise masking" comes from, by page:\n\n[1] BEiT: BERT Pre-Training of Image Transformers — ${BEIT_URL}\n`);
    expect(shown).toContain(BEIT_ABSTRACT);
    expect(w.fetched.filter((url) => url.startsWith("https://export.arxiv.org/"))).toEqual(["https://export.arxiv.org/api/query?id_list=2106.08254&max_results=1"]);
    expect(w.written.get("/vaults/papers/.knowledge/drafts/block-wise-masking.md")).toBe([
      "---", "label: \"Block-wise masking\"", "kind: \"algorithm\"", "aliases:", "  - \"分块掩码\"", `source: "${BEIT_URL}"`,
      "epistemic: \"stated\"", "requires:", "  - \"[[masked-autoencoder]]\"", "---", "Block-wise masking masks a block of image patches at a time.", "",
      "## Evidence", "", "> Block-wise masking masks a block of image patches at a time", "",
    ].join("\n"));
  });

  it("refuses a quote that is not on the page, and a page that was not shown", async () => {
    const w = world({ judged: [fromBeit], results: [beit] });
    const invented = { ...page, evidence: [{ lines: "", quote: "Block-wise masking was invented by MAE" }] };
    const { complete, requests } = model(asked, { ...page, page: 2 }, invented, page);
    await capture(w, complete, borrowedInput);
    expect(seen(requests, 2)).toEqual([
      "error: page must be the number of a page shown to you, 1 to 1",
      `error: quote 1 does not occur in page ${BEIT_URL}; copy it character for character from the page's text`,
    ]);
    expect(drafts(w).map(([path]) => path)).toEqual(["/vaults/papers/.knowledge/drafts/block-wise-masking.md"]);
  });

  it("drafts nothing when no page originates or explains it", async () => {
    const w = world({ judged: [{ choice: "none", reason: "A general term." }] });
    const { complete, requests } = model(asked);
    const result = await capture(w, complete, borrowedInput);
    expect(result).toEqual({
      item: asked.item, decision: "borrowed", label: "Block-wise masking",
      report: "出自 BEiT。 No page that originates or explains it was found, so nothing was drafted. A general term.",
    });
    expect(requests).toHaveLength(1);
    expect(drafts(w)).toEqual([]);
  });
});

describe("a selection made in a side pane", () => {
  const side: CaptureInput = { path: SOURCE, selection: "block-wise masking", context: "MAE is compared with block-wise masking." };

  it("is only unchanged or borrowed, without a numbered passage", async () => {
    const w = world();
    const { complete, requests } = model(comparison, { item: "i", decision: "unchanged", record: "papers:random-patch-sampling", report: "已有。" });
    await expect(capture(w, complete, side)).resolves.toMatchObject({ decision: "unchanged", uid: "papers:random-patch-sampling" });
    expect(seen(requests)).toEqual(["error: a selection made in a side pane can only be unchanged or borrowed"]);
    expect(requests[0]!.messages[0]!.content).toContain(SIDE_SYSTEM);
    const user = requests[0]!.messages[1]!.content as string;
    expect(user).toContain("Selected in a side pane:\nblock-wise masking\n\nAround it:\nMAE is compared with block-wise masking.");
    expect(user).not.toContain("Numbered passage");
    expect(drafts(w)).toEqual([]);
  });
});
