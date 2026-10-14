import { describe, expect, it } from "vitest";

import type { ChatReply, ChatRequest } from "../src/gateway";
import type { GetResult, NeighborsResult, RecordSummary } from "../src/lookup-model";
import {
  acceptAnswer,
  answerMarkdown,
  neighborText,
  recordText,
  searchKnowledge,
  paperQuery,
  searchUser,
  stepText,
  type SearchKgd,
  type SearchStep,
} from "../src/search";
import type { WebSource } from "../src/web";

const summary = (uid: string, label: string, cls: "node" | "relation" = "node"): RecordSummary =>
  ({ uid, label, kind: cls === "node" ? "architecture" : "property", class: cls, base: uid.split(":")[0]!, source: "s.md", lines: "1", gloss: `${label}.` });

const mae = summary("papers:masked-autoencoder", "Masked autoencoder");
const noAug = summary("papers:mae-works-without-data-augmentation", "MAE works without data augmentation", "relation");
const web: WebSource[] = [{ title: "BEiT", url: "https://arxiv.org/abs/2106.08254", snippet: "We introduce BEiT." }];

describe("citations", () => {
  const labels = new Map([[mae.uid, mae.label], [noAug.uid, noAug.label]]);

  it("are kept in order of first citation, records and web sources apart", () => {
    expect(acceptAnswer(` MAE needs no augmentation [[${noAug.uid}]] and is an autoencoder [[${mae.uid}|MAE]]; BEiT differs [W1] [[${noAug.uid}]]. `, labels, web))
      .toEqual({
        text: `MAE needs no augmentation [[${noAug.uid}]] and is an autoencoder [[${mae.uid}|MAE]]; BEiT differs [W1] [[${noAug.uid}]].`,
        records: [{ uid: noAug.uid, label: noAug.label }, { uid: mae.uid, label: mae.label }],
        web: [{ ...web[0]!, number: 1 }],
      });
  });

  it("refuse uids and web numbers the tools never returned", () => {
    expect(acceptAnswer("x [[papers:invented]] [W2]", labels, web)).toBe(
      "error: [[papers:invented]], [W2] are not among the records and web sources the tools returned. Cite only those, or leave the claim out.");
    expect(acceptAnswer("  ", labels, web)).toBe("error: the answer is empty");
  });

  it("become numbered links: K for records, W for web pages", () => {
    const accepted = acceptAnswer(`A [[${noAug.uid}]], B [[${mae.uid}]], C [W1], again [[${noAug.uid}]][[${mae.uid}]][W1].`, labels, web);
    expect(typeof accepted).toBe("object");
    expect(answerMarkdown(accepted as Exclude<typeof accepted, string>)).toBe(
      "A [K1](#kgd-record-1), B [K2](#kgd-record-2), C [W1](https://arxiv.org/abs/2106.08254), "
      + "again [K1](#kgd-record-1) [K2](#kgd-record-2) [W1](https://arxiv.org/abs/2106.08254).");
  });
});

describe("the search prompt", () => {
  it("gives the question, the first results and the looked-up names", () => {
    const hit = { ...mae, understanding: "unknown", epistemic: null, ranks: {} };
    expect(searchUser("What is BEiT?", { query: "", lanes: [], results: [hit], lag: {} as never },
      { terms: [{ term: "BEiT", key: "beit", senses: [], mentions: [], pending: [] }], lag: {} as never })).toBe([
      "Question: What is BEiT?",
      "Knowledge base search for the question:\n[papers:masked-autoencoder] Masked autoencoder · architecture node · papers — Masked autoencoder.",
      "Names in the question, looked up in every base:\n\"BEiT\":\n  - no record has this name",
    ].join("\n\n"));
  });

  it("shows whole records and links compactly", () => {
    const records: GetResult["records"] = [{
      ...noAug, id: "x", aliases: [], understanding: "unknown", epistemic: "empirical", body: "MAE works\nwell.",
      evidence: ["即使没有 data augmentation，MAE 也有不错表现。"],
      out: [{ role: "subject", pos: 0, uid: mae.uid, label: mae.label, exists: true }, { role: "uses", pos: 0, term: "data augmentation" }],
      in: [{ uid: "papers:y", label: "Y", kind: "compares", role: "baseline" }],
    }];
    expect(recordText(records)).toBe([
      `[${noAug.uid}] MAE works without data augmentation · property relation · papers · s.md L1 · empirical`,
      "Statement: MAE works well.",
      "Evidence: 即使没有 data augmentation，MAE 也有不错表现。",
      `Links: subject: [${mae.uid}] Masked autoencoder; uses: data augmentation (pending)`,
      "Linked from: baseline of [papers:y] Y (compares)",
    ].join("\n"));
    const around: NeighborsResult = {
      edges: [{ from: mae.uid, role: "requires", to: "notes:autoencoder", depth: 1 }, { from: mae.uid, role: "requires", term: "mask token", depth: 1 }],
      records: { [mae.uid]: { label: mae.label, kind: "architecture", class: "node", exists: true }, "notes:autoencoder": { label: "Autoencoder", kind: "architecture", class: "node", exists: true } },
      missing: [], lag: {} as never,
    };
    expect(neighborText(around)).toBe([
      `[${mae.uid}] Masked autoencoder -requires-> [notes:autoencoder] Autoencoder (depth 1)`,
      `[${mae.uid}] Masked autoencoder -requires-> mask token (pending) (depth 1)`,
    ].join("\n"));
  });

  it("names each step for the progress list", () => {
    expect([
      { tool: "kg_search", query: "BEiT" }, { tool: "web_search", query: "BEiT paper" }, { tool: "kg_get", count: 1 }, { tool: "kg_neighbors", count: 2 },
    ].map((step) => stepText(step as SearchStep))).toEqual([
      "Searching the knowledge base: BEiT", "Searching the web: BEiT paper", "Reading 1 record", "Following the links of 2 records"]);
  });
});

describe("searching", () => {
  const kgd: SearchKgd = {
    async call<T>(tool: string, args: Record<string, unknown>): Promise<T> {
      if (tool === "kg_search") return { query: args.query, lanes: ["lexical"], results: [mae], lag: {} } as T;
      if (tool === "kg_resolve") return { terms: (args.terms as string[]).map((term) => ({ term, key: term, senses: [], mentions: [], pending: [] })), lag: {} } as T;
      if (tool === "kg_get") return { records: [], missing: args.uids, lag: {} } as T;
      throw new Error(`unexpected ${tool}`);
    },
  };

  /** A model that replies with each scripted reply in turn: tool calls by name and arguments, or text. */
  function model(...replies: Array<Array<[string, Record<string, unknown>]> | string>) {
    const requests: ChatRequest[] = [];
    const complete = async (request: ChatRequest): Promise<ChatReply> => {
      requests.push(structuredClone(request));
      const next = replies.shift();
      if (next === undefined) throw new Error("no more replies");
      const calls = typeof next === "string" ? [] : next.map(([name, args], index) =>
        ({ id: `c${requests.length}-${index}`, type: "function" as const, function: { name, arguments: JSON.stringify(args) } }));
      return { model: "m", finish_reason: "stop", usage: { prompt_tokens: 1, completion_tokens: 1 }, message: { content: typeof next === "string" ? next : null, tool_calls: calls } };
    };
    return { complete, requests };
  }

  it("searches the web when asked, then accepts an answer citing what it was shown", async () => {
    const steps: SearchStep[] = [];
    const queries: string[] = [];
    const { complete, requests } = model(
      [["kg_search", { query: "BEiT" }], ["web_search", { query: "BEiT paper" }]],
      [["answer", { answer: `BEiT predicts visual tokens [W1]; MAE reconstructs pixels [[${mae.uid}]].` }]],
    );
    const answer = await searchKnowledge(complete, kgd, async (query) => { queries.push(query); return web; }, "What is BEiT?",
      { onStep: (step) => steps.push(step) });
    expect(answer).toMatchObject({ turns: 2, records: [{ uid: mae.uid, label: mae.label }], web: [{ number: 1, url: web[0]!.url }] });
    expect(queries).toEqual(["BEiT paper"]);
    expect(steps).toEqual([{ tool: "kg_search", query: "BEiT" }, { tool: "web_search", query: "BEiT paper" }, { tool: "answer" }]);
    expect(requests[1]!.messages.at(-1)!.content).toBe(`[W1] BEiT — arXiv:2106.08254 — ${web[0]!.url}\n    We introduce BEiT.`);
  });

  it("searches arXiv for papers, keeps only arXiv pages, and numbers them with the web sources", async () => {
    const queries: string[] = [];
    const found: WebSource[] = [
      { title: "Masked Autoencoders are Robust Data Augmentors", url: "https://arxiv.org/abs/2206.04846", snippet: "MAE as augmentation." },
      { title: "A blog post", url: "https://example.org/mae", snippet: "Not a paper." },
    ];
    const { complete, requests } = model(
      [["web_search", { query: "BEiT" }], ["paper_search", { query: "why MAE needs no data augmentation" }]],
      [["answer", { answer: "Masking acts as augmentation [W1]." }]],
    );
    const reading = { base: "papers", source: "papers/mae/mae_ch.md", type: "ai-research" };
    const steps: SearchStep[] = [];
    const answer = await searchKnowledge(complete, kgd, async (query) => { queries.push(query); return query.endsWith("site:arxiv.org") ? found : web; },
      "为什么 MAE 不需要 data augmentation？", { reading, onStep: (step) => steps.push(step) });
    expect(queries).toEqual([paperQuery("why MAE needs no data augmentation")]);
    expect(answer.web.map((source) => [source.number, source.url])).toEqual([[1, found[0]!.url]]);
    expect(requests[0]!.tools!.map((tool) => tool.function.name)).toEqual(["kg_search", "kg_get", "kg_neighbors", "paper_search", "answer"]);
    expect(requests[1]!.messages.slice(-2)).toEqual([
      { role: "tool", tool_call_id: "c1-0", content: "error: there is no tool named web_search" },
      { role: "tool", tool_call_id: "c1-1",
        content: "[W1] Masked Autoencoders are Robust Data Augmentors — arXiv:2206.04846 — https://arxiv.org/abs/2206.04846\n    MAE as augmentation." },
    ]);
    expect(requests[0]!.messages[1]!.content).toContain(
      "The owner is reading papers/mae/mae_ch.md in base papers, a source of document type ai-research.");
    expect(steps[0]).toEqual({ tool: "paper_search", query: "why MAE needs no data augmentation" });
    expect(stepText(steps[0]!)).toBe("Searching papers: why MAE needs no data augmentation");
  });

  it("offers the general web search only when the owner reads no registered source", async () => {
    const { complete, requests } = model([["answer", { answer: "Nothing found." }]]);
    await searchKnowledge(complete, kgd, async () => [], "Q?");
    expect(requests[0]!.tools!.map((tool) => tool.function.name)).toEqual(["kg_search", "kg_get", "kg_neighbors", "paper_search", "web_search", "answer"]);
  });

  it("takes a text reply as the answer and returns a refused one as a message", async () => {
    const { complete, requests } = model(`Wrong [[papers:invented]].`, `MAE is recorded [[${mae.uid}]].`);
    const answer = await searchKnowledge(complete, kgd, async () => [], "What is MAE?");
    expect(answer).toMatchObject({ turns: 2, text: `MAE is recorded [[${mae.uid}]].` });
    expect(requests[1]!.messages.at(-1)).toEqual({
      role: "user", content: "error: [[papers:invented]] is not among the records and web sources the tools returned. Cite only those, or leave the claim out.",
    });
  });

  it("forces the answer on its last turn", async () => {
    const lookups = Array.from({ length: 5 }, () => [["kg_get", { uids: [mae.uid] }]] as Array<[string, Record<string, unknown>]>);
    const { complete, requests } = model(...lookups, [["answer", { answer: "Nothing recorded answers this." }]]);
    await expect(searchKnowledge(complete, kgd, async () => [], "Q?")).resolves.toMatchObject({ turns: 6, records: [], web: [] });
    expect(requests[0]!.tool_choice).toBe("required");
    expect(requests[5]!.tool_choice).toEqual({ type: "function", function: { name: "answer" } });
  });
});
