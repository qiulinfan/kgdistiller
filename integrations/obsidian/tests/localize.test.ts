import { describe, expect, it } from "vitest";

import type { CaptureFiles, CaptureKgd, Profile } from "../src/capture";
import type { Draft } from "../src/drafts";
import { KgdError } from "../src/kgd";
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
  types: [{
    type: "ai-research",
    node_kinds: { definition: [], algorithm: [] },
    relation_kinds: { property: ["subject", "uses"] },
    epistemic: ["empirical", "stated"],
    guidance: "",
  }],
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
  in: [], degree: 0,
};

const input: LocalizeInput = { path: LOCAL, from: 3, to: 3, selection: "Block-wise masking masks a block of patches" };
const PASSAGE = "Block-wise masking masks a block of patches at a time.";
const same = { same: true, passage: PASSAGE, reason: "同一概念。" };

interface World {
  kgd: CaptureKgd;
  /** Every kgd command run, as its arguments. */
  commands: string[][];
  files: CaptureFiles;
}

/**
 * kgd and the files for one localization; the first `refuse` relocations are
 * refused, and `fail`, when given, is the error every relocation then ends with.
 */
function world(refuse = 0, fail?: string, source: Profile = profile): World {
  const commands: string[][] = [];
  let refusals = refuse;
  const kgd: CaptureKgd = {
    async call<T>(tool: string): Promise<T> {
      throw new Error(`unexpected tool ${tool}`);
    },
    async command(args: string[]) {
      commands.push(args);
      if (args[0] === "source" && args[1] === LOCAL) return { report: source as unknown as Record<string, unknown>, failed: false };
      if (args[0] === "relocate") {
        if (refusals-- > 0) return { report: { refused: { message: "the passage does not occur in base papers", matches: [] } }, failed: true };
        if (fail) throw new KgdError(fail);
        const ref = args[args.indexOf("--") + 1]!;
        const relocated = ref.includes(":") ? `${profile.base}:${ref.split(":")[1]}` : `${profile.base}:block-wise-masking`;
        const index = ref.includes(":") ? { index: { unembedded: 1 } } : {};
        return { report: { relocated, to: { source: profile.source, lines: "3-3" }, evidence: "replaced", ...index }, failed: false };
      }
      throw new KgdError(`${args[1]} is not a registered source`);
    },
    async baseRoots() {
      return new Map([["papers", "/vaults/papers"], ["notes", "/vaults/notes"]]);
    },
  };
  return {
    kgd, commands,
    files: {
      read: async (path) => {
        if (path !== LOCAL) throw new Error(`no file ${path}`);
        return lines.join("\n");
      },
      ids: async () => new Set(),
      create: async () => { throw new Error("localization writes no file itself"); },
      remove: async () => { throw new Error("localization removes no file itself"); },
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
  label: "Block-wise masking", class: "node", kind: "algorithm", aliases: [], source: BEIT_URL, understanding: "yes", epistemic: "stated",
  body: record.body, search_terms: "", evidence: record.evidence, out: [], with: [], linked_by: [], problems: [],
};

describe("localizing foreign knowledge", () => {
  it("relocates an accepted record to the passage the model copies, in the selected source only", async () => {
    const w = world();
    const { complete, requests } = model(same);
    await expect(localizeRecord(complete, w.kgd, w.files, recordTarget(record), input)).resolves.toEqual({
      localized: true, uid: "papers:block-wise-masking", label: "Block-wise masking", report: "同一概念。", index: { unembedded: 1 },
    });
    expect(w.commands).toEqual([["source", LOCAL], ["relocate", "--base", "papers", "--source", "papers/beit/beit.md", "--", "papers:block-wise-masking", PASSAGE]]);
    expect(requests[0]).toMatchObject({ tool_choice: { type: "function", function: { name: "localize" } }, reasoning_effort: "low" });
    const user = requests[0]!.messages[1]!.content as string;
    expect(user).toContain(`Its evidence, quoted from ${BEIT_URL}:\n> Block-wise masking masks a block of image patches at a time.`);
    expect(user).toContain("Local source: papers · papers/beit/beit.md");
    expect(user).toContain("3| Block-wise masking masks a block of patches at a time.");
  });

  it("moves a record of another base into the selected source's base, under the uid kgd reports", async () => {
    const w = world();
    const other = recordTarget({ ...record, uid: "notes:block-wise-masking", base: "notes" });
    await expect(localizeRecord(model(same).complete, w.kgd, w.files, other, input)).resolves.toMatchObject({
      localized: true, uid: "papers:block-wise-masking",
    });
    expect(w.commands.at(-1)).toEqual(["relocate", "--base", "papers", "--source", "papers/beit/beit.md", "--", "notes:block-wise-masking", PASSAGE]);
  });

  it("relocates a draft by its file", async () => {
    const w = world();
    const result = await localizeRecord(model(same).complete, w.kgd, w.files, draftTarget(draft), input);
    expect(result).toMatchObject({ localized: true, uid: "papers:block-wise-masking" });
    expect(result).not.toHaveProperty("index");
    expect(w.commands.at(-1)).toEqual(["relocate", "--base", "papers", "--source", "papers/beit/beit.md", "--", DRAFT, PASSAGE]);
  });

  it("relocates nothing when the passage states something else", async () => {
    const w = world();
    await expect(localizeRecord(model({ same: false, reason: "不同。" }).complete, w.kgd, w.files, recordTarget(record), input))
      .resolves.toEqual({ localized: false, uid: "papers:block-wise-masking", label: "Block-wise masking", report: "不同。" });
    expect(w.commands).toEqual([["source", LOCAL]]);
  });

  it("returns kgd relocate's refusal and an empty passage to the model", async () => {
    const w = world(1);
    const { complete, requests } = model({ same: true, reason: "x" }, same, same);
    await expect(localizeRecord(complete, w.kgd, w.files, recordTarget(record), input)).resolves.toMatchObject({ localized: true });
    expect(requests.slice(1).map((request) => request.messages.at(-1)!.content)).toEqual([
      "error: give passage, the sentences of the numbered passage that state the knowledge point",
      "error: the passage does not occur in base papers. Copy the passage from the numbered lines exactly and call localize again.",
    ]);
  });

  it("ends at once with kgd's message when kgd relocate fails other than by not finding the passage", async () => {
    const w = world(0, "base papers already has a record or draft block-wise-masking; it cannot move there");
    const { complete, requests } = model(same, same);
    await expect(localizeRecord(complete, w.kgd, w.files, recordTarget({ ...record, uid: "notes:block-wise-masking" }), input)).resolves.toEqual({
      localized: false, uid: "notes:block-wise-masking", label: "Block-wise masking",
      report: "base papers already has a record or draft block-wise-masking; it cannot move there",
    });
    expect(requests).toHaveLength(1);
  });

  it("fails with kgd's message for a file that is not a registered source", async () => {
    const w = world();
    await expect(localizeRecord(model(same).complete, w.kgd, w.files, recordTarget(record), { ...input, path: "/vaults/papers/notes.md" }))
      .rejects.toThrow(new KgdError("/vaults/papers/notes.md is not a registered source"));
  });

  it("refuses a kind the source's type lacks, before asking the model", async () => {
    const never = async (): Promise<ChatReply> => { throw new Error("the model was asked"); };
    const w = world();
    await expect(localizeRecord(never, w.kgd, w.files, recordTarget({ ...record, kind: "theorem" }), input))
      .resolves.toMatchObject({ localized: false, report: "The kind theorem is not one of this source's document type ai-research." });
    expect(w.commands.filter((args) => args[0] === "relocate")).toEqual([]);
  });

  it("takes a kind any document type of the source's base declares", async () => {
    const notes = { type: "math-notes", node_kinds: { theorem: ["about"] }, relation_kinds: {}, epistemic: ["proved"], guidance: "" };
    const cs = { ...notes, type: "cs-notes", node_kinds: { method: [] } };
    const never = async (): Promise<ChatReply> => { throw new Error("the model was asked"); };
    const refused = world(0, undefined, { ...profile, types: [profile.types[0]!, cs] });
    await expect(localizeRecord(never, refused.kgd, refused.files, recordTarget({ ...record, kind: "theorem" }), input))
      .resolves.toMatchObject({ localized: false, report: "The kind theorem is not one of this source's document types ai-research, cs-notes." });
    const w = world(0, undefined, { ...profile, types: [profile.types[0]!, notes] });
    await expect(localizeRecord(model(same).complete, w.kgd, w.files, recordTarget({ ...record, kind: "theorem" }), input))
      .resolves.toMatchObject({ localized: true });
  });
});
