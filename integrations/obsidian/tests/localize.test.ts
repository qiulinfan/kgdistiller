import { describe, expect, it } from "vitest";

import type { CaptureFiles, CaptureKgd, Profile } from "../src/capture";
import type { Draft } from "../src/drafts";
import { draftTarget, localizeRecord, recordTarget, type LocalizeInput } from "../src/localize";
import type { FullRecord } from "../src/lookup-model";
import type { ChatReply, ChatRequest } from "../src/model";

const BEIT_URL = "https://arxiv.org/abs/2106.08254";
const LOCAL = "/vaults/papers/papers/beit/beit.md";
const DRAFT = "/vaults/papers/.knowledge/drafts/block-wise-masking.md";
const lines = [
  "# BEiT",
  "",
  "Block-wise masking masks a block of patches at a time.",
  "It is used in pre-training.",
];

const profile: Profile = {
  base: "papers",
  source: "papers/beit/beit.md",
  type: "ai-research",
  node_kinds: ["definition", "algorithm"],
  relation_kinds: { property: ["subject", "uses"] },
  epistemic: ["empirical", "stated"],
  guidance: "",
  line_count: lines.length,
  records: [],
  drafts: [],
};

const record: FullRecord = {
  uid: "papers:block-wise-masking", id: "block-wise-masking", label: "Block-wise masking", kind: "algorithm", class: "node",
  base: "papers", source: BEIT_URL, lines: "", foreign: true, gloss: "", aliases: ["分块掩码"], understanding: "no", epistemic: "stated",
  body: "Block-wise masking masks a block of image patches at a time.", search_terms: "block masking",
  evidence: ["Block-wise masking masks a block of image patches at a time."],
  out: [
    { role: "requires", pos: 0, uid: "papers:masked-autoencoder", label: "Masked autoencoder", exists: true },
    { role: "requires", pos: 1, uid: "notes:autoencoder", label: "Autoencoder", exists: true },
  ],
  in: [],
};

const input: LocalizeInput = { path: LOCAL, from: 3, to: 3, selection: "Block-wise masking masks a block of patches" };
const same = { same: true, evidence: [{ lines: "3", quote: "Block-wise masking masks a block of patches at a time." }], reason: "同一概念。" };

/** The localized file of `record`, citing line 3 of the local source. */
const localized = (understanding = "no", search = "block masking") => [
  "---", "label: \"Block-wise masking\"", "kind: \"algorithm\"", "aliases:", "  - \"分块掩码\"", "source: \"papers/beit/beit.md\"", "lines: \"3\"",
  ...(understanding === "no" ? ["understanding: \"no\""] : []), "epistemic: \"stated\"",
  "requires:", "  - \"[[masked-autoencoder]]\"", "  - \"[[notes:autoencoder]]\"", "---", "Block-wise masking masks a block of image patches at a time.", "",
  ...(search ? ["## Search terms", "", search, ""] : []), "## Evidence", "", "> Block-wise masking masks a block of patches at a time.", "",
].join("\n");

interface World {
  kgd: CaptureKgd;
  files: CaptureFiles;
  written: Map<string, string>;
  /** Each change to the drafts folder, in order. */
  changes: string[];
}

/** kgd and the files for one localization; the first `refuse` listings report a problem with the target's draft. */
function world(refuse = 0, files: Record<string, string> = {}): World {
  const written = new Map<string, string>([[LOCAL, lines.join("\n")], ...Object.entries(files)]);
  const changes: string[] = [];
  let refusals = refuse;
  const kgd: CaptureKgd = {
    async call<T>(tool: string): Promise<T> {
      if (tool !== "kg_drafts") throw new Error(`unexpected tool ${tool}`);
      const drafts = [...written.keys()].filter((path) => path.includes("/drafts/")).map((path) => ({
        uid: `papers:${path.split("/").pop()!.replace(/\.md$/, "")}`, path, problems: refusals-- > 0 ? ["evidence: quote 1 is not in lines 3"] : [],
      }));
      return { drafts } as T;
    },
    async command(args: string[]) {
      if (args[0] === "source" && args[1] === LOCAL) return { report: profile as unknown as Record<string, unknown>, failed: false };
      return { report: { error: "not a registered source" }, failed: true };
    },
    async baseRoots() {
      return new Map([["papers", "/vaults/papers"], ["notes", "/vaults/notes"]]);
    },
  };
  return {
    written, changes, kgd,
    files: {
      read: async (path) => {
        const text = written.get(path);
        if (text === undefined) throw new Error(`no file ${path}`);
        return text;
      },
      ids: async () => new Set(),
      create: async (path, text) => {
        if (written.has(path)) throw new Error("exists");
        changes.push(`create ${path}`);
        written.set(path, text);
      },
      remove: async (path) => {
        changes.push(`remove ${path}`);
        written.delete(path);
      },
    },
  };
}

/** A model that calls localize with each argument object in turn. */
function model(...answers: Array<Record<string, unknown>>): { complete: (request: ChatRequest) => Promise<ChatReply>; requests: ChatRequest[] } {
  const requests: ChatRequest[] = [];
  return {
    requests,
    complete: async (request) => {
      requests.push(structuredClone(request));
      const args = answers.shift();
      if (!args) throw new Error("no more answers");
      return { message: { content: null, tool_calls: [{ id: `c${requests.length}`, type: "function", function: { name: "localize", arguments: JSON.stringify(args) } }] } };
    },
  };
}

const draft: Draft = {
  uid: "papers:block-wise-masking", base: "papers", id: "block-wise-masking", path: DRAFT, replaces: null, foreign: true,
  label: "Block-wise masking", class: "node", kind: "algorithm", aliases: ["分块掩码"], source: BEIT_URL, understanding: "yes", epistemic: "stated",
  body: record.body, search_terms: "", evidence: record.evidence,
  out: [
    { role: "requires", pos: 0, uid: "papers:masked-autoencoder", state: "accepted", label: "Masked autoencoder" },
    { role: "requires", pos: 1, uid: "notes:autoencoder", state: "accepted", label: "Autoencoder" },
  ],
  with: [], linked_by: [], problems: [],
};
const FOREIGN_TEXT = "---\nlabel: \"Block-wise masking\"\nsource: \"https://arxiv.org/abs/2106.08254\"\n---\nthe foreign draft\n";

describe("localizing an accepted foreign record", () => {
  it("writes a draft with the record's id citing the local passage, keeping its values, understanding and search terms", async () => {
    const w = world();
    const { complete, requests } = model(same);
    await expect(localizeRecord(complete, w.kgd, w.files, recordTarget(record), input)).resolves.toEqual({
      localized: true, uid: "papers:block-wise-masking", label: "Block-wise masking", replaces: true, report: "同一概念。",
    });
    expect(w.written.get(DRAFT)).toBe(localized());
    expect(requests[0]).toMatchObject({ tool_choice: { type: "function", function: { name: "localize" } }, reasoning_effort: "low" });
    const user = requests[0]!.messages[1]!.content as string;
    expect(user).toContain(`Its evidence, quoted from ${BEIT_URL}:\n> Block-wise masking masks a block of image patches at a time.`);
    expect(user).toContain("Local source: papers · papers/beit/beit.md");
    expect(user).toContain("3| Block-wise masking masks a block of patches at a time.");
  });

  it("writes nothing when the passage states something else, or a draft with that id already waits", async () => {
    const w = world();
    await expect(localizeRecord(model({ same: false, reason: "不同。" }).complete, w.kgd, w.files, recordTarget(record), input))
      .resolves.toEqual({ localized: false, uid: "papers:block-wise-masking", label: "Block-wise masking", report: "不同。" });
    expect(w.changes).toEqual([]);
    const waiting = world(0, { [DRAFT]: "an earlier replacement" });
    const result = await localizeRecord(model(same).complete, waiting.kgd, waiting.files, recordTarget(record), input);
    expect(result).toMatchObject({ localized: false, report: expect.stringContaining("A draft with this id already waits for you, so nothing was written") });
    expect(waiting.written.get(DRAFT)).toBe("an earlier replacement");
  });

  it("refuses a source of another base and a kind the source's type lacks, before asking the model", async () => {
    const never = async (): Promise<ChatReply> => { throw new Error("the model was asked"); };
    const w = world();
    const other = recordTarget({ ...record, uid: "notes:block-wise-masking", base: "notes" });
    await expect(localizeRecord(never, w.kgd, w.files, other, input)).resolves.toEqual({
      localized: false, uid: "notes:block-wise-masking", label: "Block-wise masking",
      report: "This source belongs to base papers, the record to base notes; moving a record across bases is the compile Skill's relocation.",
    });
    await expect(localizeRecord(never, w.kgd, w.files, recordTarget({ ...record, kind: "theorem" }), input))
      .resolves.toMatchObject({ localized: false, report: "The kind theorem is not one of this source's document type ai-research." });
    expect(w.changes).toEqual([]);
  });
});

describe("localizing a foreign draft", () => {
  it("rewrites the draft file in place", async () => {
    const w = world(0, { [DRAFT]: FOREIGN_TEXT });
    await expect(localizeRecord(model(same).complete, w.kgd, w.files, draftTarget(draft), input)).resolves.toEqual({
      localized: true, uid: "papers:block-wise-masking", label: "Block-wise masking", report: "同一概念。",
    });
    expect(w.written.get(DRAFT)).toBe(localized("yes", ""));
    expect(w.changes).toEqual([`remove ${DRAFT}`, `create ${DRAFT}`]);
  });

  it("restores the old draft when kgd accept would refuse the localized one", async () => {
    const w = world(1, { [DRAFT]: FOREIGN_TEXT });
    const { complete, requests } = model(same, { same: false, reason: "放弃。" });
    await expect(localizeRecord(complete, w.kgd, w.files, draftTarget(draft), input)).resolves.toMatchObject({ localized: false, report: "放弃。" });
    expect(requests[1]!.messages.at(-1)!.content).toBe(
      "error: kgd accept would refuse the localized record: evidence: quote 1 is not in lines 3. Correct the evidence and call localize again.");
    expect(w.written.get(DRAFT)).toBe(FOREIGN_TEXT);
    expect(w.changes).toEqual([`remove ${DRAFT}`, `create ${DRAFT}`, `remove ${DRAFT}`, `create ${DRAFT}`]);
  });

  it("refuses evidence the passage does not hold", async () => {
    const w = world(0, { [DRAFT]: FOREIGN_TEXT });
    const { complete, requests } = model({ ...same, evidence: [{ lines: "4", quote: "MAE invented block-wise masking" }] }, { ...same, evidence: [] }, same);
    await expect(localizeRecord(complete, w.kgd, w.files, draftTarget(draft), input)).resolves.toMatchObject({ localized: true });
    expect(requests.slice(1).map((request) => request.messages.at(-1)!.content)).toEqual([
      expect.stringMatching(/^error: quote 1: .*; copy it from the numbered passage$/),
      "error: evidence needs at least one quote of the passage",
    ]);
    expect(w.written.get(DRAFT)).toBe(localized("yes", ""));
  });
});
