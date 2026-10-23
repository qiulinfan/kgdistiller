import { describe, expect, it, vi } from "vitest";

import type { ChatReply, ChatRequest } from "../src/model";
import { rankSourceLinks } from "../src/origin-ranking";
vi.mock("../src/origin-ranking", () => ({ rankSourceLinks: vi.fn(async () => []) }));
import { acceptOrigin, judgeOrigin, originQuery, originUser, originWork, type OriginInput, type OriginServices } from "../src/origin";

const BEIT = { number: "2", text: "Hangbo Bao, Li Dong, and Furu Wei. BEiT: BERT pre-training of image transformers. arXiv:2106.08254, 2021.", url: "https://arxiv.org/abs/2106.08254" };
const MOCO = { number: "9", text: "Xinlei Chen, Saining Xie, and Kaiming He. An empirical study of training self-supervised Vision Transformers. In ICCV, 2021." };
const DAE = { title: "Extracting and composing robust features with denoising autoencoders | Proceedings of the 25th ICML", url: "https://dl.acm.org/doi/10.1145/1390156.1390294", snippet: "We propose denoising autoencoders." };

const ARTICLES = [
  { title: "Vision transformer", url: "https://en.wikipedia.org/wiki/Vision_transformer#Masked_Autoencoder", extract: "The Masked Autoencoder took inspiration from denoising autoencoders.", section: "Masked Autoencoder" },
  { title: "Beit Shemesh", url: "https://en.wikipedia.org/wiki/Beit_Shemesh", extract: "Beit Shemesh is a city." },
];

const input: OriginInput = {
  term: "block-wise masking",
  context: "文献 [2](#^reference-2) 提出的 block-wise masking 倾向移除 large blocks。",
  source: { title: "Masked Autoencoders Are Scalable Vision Learners", text: "# Masked Autoencoders Are Scalable Vision Learners 原文：[arXiv:2111.06377v3](https://arxiv.org/abs/2111.06377v3)。" },
  references: [BEIT, MOCO],
  results: [DAE, { title: "SimMIM: a  Simple\nFramework", url: "https://arxiv.org/abs/2111.09886", snippet: "x".repeat(200) }],
  articles: ARTICLES,
};

const FEED = (id: string, title: string, year: string): string =>
  `<feed><entry><id>http://arxiv.org/abs/${id}v2</id><published>${year}-01-01T00:00:00Z</published><title>${title}</title></entry></feed>`;

describe("the origin prompt", () => {
  it("asks the web who introduced the term", () => {
    expect(originQuery("MoCo v3")).toBe("who first introduced MoCo v3? original paper");
  });

  it("shows the term, its text, the source, the cited references and the numbered web results", () => {
    expect(originUser(input)).toBe([
      "Term: block-wise masking",
      `Read in: ${input.context}`,
      `Source being read: ${input.source!.text}`,
      `References the text cites:\n[2] ${BEIT.text}\n[9] ${MOCO.text}`,
      `Web results:\n[1] ${DAE.title} — ${DAE.url} — ${DAE.snippet}\n[2] SimMIM: a Simple Framework — https://arxiv.org/abs/2111.09886 — ${"x".repeat(160)}…`,
      "Wikipedia articles:\n[1] Vision transformer § Masked Autoencoder — The Masked Autoencoder took inspiration from denoising autoencoders.\n[2] Beit Shemesh — Beit Shemesh is a city.",
    ].join("\n"));
    expect(originUser({ term: "MAE", context: " ", references: [], results: [], articles: [] }))
      .toBe("Term: MAE\nRead in: MAE\nSource being read: unknown\nReferences the text cites:\nnone\nWeb results:\nnone\nWikipedia articles:\nnone");
  });
});

describe("accepting an origin", () => {
  it("maps a choice to the candidate it names", () => {
    expect(acceptOrigin({ choice: "reference", number: 2, title: "BEiT", reason: " 文献 [2] 提出。 " }, input)).toEqual({
      choice: "reference", reason: "文献 [2] 提出。", title: "BEiT", candidate: { title: BEIT.text, text: BEIT.text, reference: "2", url: BEIT.url, arxiv: "2106.08254" },
    });
    expect(acceptOrigin({ choice: "web", number: 1, title: "Extracting and composing robust features with denoising autoencoders", reason: "" }, input))
      .toMatchObject({ choice: "web", candidate: { title: DAE.title, url: DAE.url } });
    expect(acceptOrigin({ choice: "source", title: "Masked Autoencoders Are Scalable Vision Learners", reason: "" }, input))
      .toMatchObject({ choice: "source", candidate: { title: "Masked Autoencoders Are Scalable Vision Learners", text: input.source!.text } });
    expect(acceptOrigin({ choice: "none", number: 3, reason: "A general term." }, input)).toEqual({ choice: "none", reason: "A general term." });
  });

  it("takes the Wikipedia article it names, if any", () => {
    expect(acceptOrigin({ choice: "none", article: 1, reason: "" }, input)).toEqual({ choice: "none", reason: "", wikipedia: ARTICLES[0] });
    expect(acceptOrigin({ choice: "none", article: null, reason: "" }, input)).toEqual({ choice: "none", reason: "" });
    expect(acceptOrigin({ choice: "reference", number: 2, title: "BEiT", article: 1, reason: "" }, input))
      .toMatchObject({ choice: "reference", wikipedia: ARTICLES[0], candidate: { reference: "2" } });
    expect(acceptOrigin({ choice: "none", article: 3, reason: "" }, input)).toBe("error: article must be a Wikipedia article from 1 to 2, or left out");
    expect(acceptOrigin({ choice: "none", article: 1, reason: "" }, { ...input, articles: [] })).toBe("error: there are no Wikipedia articles; leave article out");
  });

  it("refuses a choice that is not among the candidates", () => {
    expect(acceptOrigin({ choice: "paper", reason: "" }, input)).toBe("error: choice must be source, reference, web or none");
    expect(acceptOrigin({ choice: "web", number: 1, reason: "" }, input)).toBe("error: give the originating work's title");
    expect(acceptOrigin({ choice: "reference", number: 58, title: "t", reason: "" }, input)).toBe("error: number must be a cited reference: 2, 9");
    expect(acceptOrigin({ choice: "reference", number: 2, title: "t", reason: "" }, { ...input, references: [] }))
      .toBe("error: the text cites no references; choose another candidate or none");
    expect(acceptOrigin({ choice: "web", number: 3, title: "t", reason: "" }, input)).toBe("error: number must be a web result from 1 to 2");
    expect(acceptOrigin({ choice: "web", number: "1", title: "t", reason: "" }, input)).toBe("error: number must be a web result from 1 to 2");
    const { source: _source, ...unread } = input;
    expect(acceptOrigin({ choice: "source", title: "t", reason: "" }, unread)).toBe("error: no source is being read; choose another candidate or none");
  });
});

describe("the originating work", () => {
  it("links a candidate's own arXiv id without asking arXiv, showing the judged title the candidate holds", async () => {
    const asked: string[] = [];
    const fetch = async (url: string): Promise<string> => {
      asked.push(url);
      return "<feed></feed>";
    };
    const source = acceptOrigin({ choice: "source", title: "Masked Autoencoders Are Scalable Vision Learners", reason: "" }, input);
    await expect(originWork(fetch, source as Exclude<typeof source, string>)).resolves.toEqual({
      work: { title: "Masked Autoencoders Are Scalable Vision Learners", url: "https://arxiv.org/abs/2111.06377", arxiv: "2111.06377", year: "2021" },
    });
    const beit = acceptOrigin({ choice: "reference", number: 2, title: "BEiT: BERT Pre-Training of Image Transformers", reason: "" }, input);
    await expect(originWork(fetch, beit as Exclude<typeof beit, string>)).resolves.toEqual({
      work: { title: "BEiT: BERT Pre-Training of Image Transformers", url: "https://arxiv.org/abs/2106.08254", arxiv: "2106.08254", year: "2021" },
    });
    const named = acceptOrigin({ choice: "reference", number: 2, title: "BEiT, a vision BERT", reason: "" }, input);
    await expect(originWork(fetch, named as Exclude<typeof named, string>)).resolves.toMatchObject({ work: { title: BEIT.text, arxiv: "2106.08254" } });
    expect(asked).toEqual([]);
  });

  it("searches arXiv for the judged title, and keeps the candidate's own link when arXiv lacks it or fails", async () => {
    const title = "Extracting and composing robust features with denoising autoencoders";
    const judged = acceptOrigin({ choice: "web", number: 1, title, reason: "" }, input);
    const accepted = judged as Exclude<typeof judged, string>;
    await expect(originWork(async () => "<feed></feed>", accepted)).resolves.toEqual({ work: { title, url: DAE.url } });
    await expect(originWork(async () => { throw new Error("must not probe arXiv when the source has its own link"); }, accepted))
      .resolves.toEqual({ work: { title, url: DAE.url } });
    await expect(originWork(async () => "<feed></feed>", { ...accepted, title: "Denoising autoencoders: a survey" }))
      .resolves.toEqual({ work: { title: DAE.title, url: DAE.url } });
    await expect(originWork(async () => "<feed></feed>", { choice: "none", reason: "" })).resolves.toEqual({});
  });

  it("shows the judged title an untitled page's snippet holds, and takes a page's arXiv id from its link only", async () => {
    const pdf = { title: "https://arxiv.org/abs/1312.6114", url: "https://arxiv.org/abs/1312.6114", snippet: "Auto-Encoding Variational Bayes Diederik P. Kingma, Max Welling" };
    const survey = { title: "A survey of autoencoders", url: "https://example.org/survey", snippet: "Kingma and Welling (arXiv:1312.6114) introduced the VAE." };
    const pages = { ...input, results: [pdf, survey] };
    const judged = acceptOrigin({ choice: "web", number: 1, title: "Auto-Encoding Variational Bayes", reason: "" }, pages);
    await expect(originWork(async () => "<feed></feed>", judged as Exclude<typeof judged, string>)).resolves.toEqual({
      work: { title: "Auto-Encoding Variational Bayes", url: "https://arxiv.org/abs/1312.6114", arxiv: "1312.6114", year: "2013" },
    });
    const other = acceptOrigin({ choice: "web", number: 2, title: "A survey of autoencoders", reason: "" }, pages);
    expect(other).toMatchObject({ candidate: { url: "https://example.org/survey" } });
    expect((other as Exclude<typeof other, string>).candidate).not.toHaveProperty("arxiv");
  });
});

describe("judging an origin", () => {
  it("forces the origin tool at low reasoning, retries a refusal once, and finds the work on arXiv", async () => {
    const requests: ChatRequest[] = [];
    const answers = [
      { choice: "reference", number: 58, title: "x", reason: "" },
      { choice: "reference", number: 9, title: "An empirical study of training self-supervised Vision Transformers", article: 1, reason: "MoCo v3 出自 [9]。" },
    ];
    const complete = async (request: ChatRequest): Promise<ChatReply> => {
      requests.push(structuredClone(request));
      const args = answers.shift();
      return {
        message: { content: null, tool_calls: [{ id: `c${requests.length}`, type: "function", function: { name: "origin", arguments: JSON.stringify(args) } }] },
      };
    };
    const fetch = async (): Promise<string> => FEED("2104.02057", "An Empirical Study of Training Self-Supervised Vision Transformers", "2021");
    await expect(judgeOrigin({ complete, fetch, webSearch: async () => [], readPage: async (url) => ({ url, title: "Work", text: "Text" }) }, { ...input, term: "MoCo v3" }, [])).resolves.toEqual({
      choice: "reference",
      reason: "MoCo v3 出自 [9]。",
      reference: "9",
      wikipedia: ARTICLES[0],
      work: { title: "An Empirical Study of Training Self-Supervised Vision Transformers", url: "https://arxiv.org/abs/2104.02057", arxiv: "2104.02057", year: "2021" },
    });
    expect(requests[0]!.messages[0]!.content).toContain("The source being read may only use the concept");
    expect(requests[0]!.tool_choice).toBe("required");
    expect(requests.map((request) => request.reasoning_effort)).toEqual(["low", "low"]);
    expect(requests[1]!.messages.at(-1)).toMatchObject({ role: "tool", content: "error: number must be a cited reference: 2, 9" });
  });
});

describe("following origin clues", () => {
  const paper = { title: "Early coding networks", url: "https://publisher.example/early-coding", snippet: "A primary work." };
  const passage = "The coding network learns to reconstruct its input.";
  const article = { title: "Coding network", url: "https://en.wikipedia.org/wiki/Coding_network", extract: "A coding network is a neural network.", context: "The history discusses earlier coding methods.", citations: [{ title: paper.title, authors: "A. Reader", year: "1980", url: paper.url }] };
  const base: OriginInput = { term: "coding network", context: "A coding network.", references: [], results: [], articles: [article] };
  const reply = (name: string, args: Record<string, unknown>): ChatReply => ({ message: { content: null, tool_calls: [{ id: "call", type: "function", function: { name, arguments: JSON.stringify(args) } }] } });
  function services(answers: ChatReply[]) {
    const requests: ChatRequest[] = [];
    const complete = vi.fn(async (request: ChatRequest) => { requests.push(structuredClone(request)); const answer = answers.shift(); if (!answer) throw new Error("no more answers"); return answer; });
    const service: OriginServices = { complete, fetch: async () => "<feed></feed>", webSearch: vi.fn(async () => [paper]), readPage: vi.fn(async (url) => ({ url, title: paper.title, text: passage })) };
    return { service, requests };
  }

  it("follows a non-arXiv Wikipedia reference, verifies a quote and keeps the page for Capture", async () => {
    const { service, requests } = services([
      reply("read", { url: paper.url }),
      reply("origin", { choice: "web", number: 1, title: paper.title, article: 1, quote: passage, reason: "原文陈述该方法。" }),
    ]);
    const origin = await judgeOrigin(service, base, []);
    expect(origin).toMatchObject({ work: { title: paper.title, url: paper.url }, quote: passage, page: { text: passage }, wikipedia: article });
    expect(service.readPage).toHaveBeenCalledExactlyOnceWith(paper.url);
    expect(requests[0]!.messages[1]!.content).toContain("A. Reader 1980");
    expect(requests[0]!.messages[1]!.content).toContain("history discusses earlier coding methods");
  });

  it("searches an unlinked historical citation by title and then reads the discovered primary link", async () => {
    const { service } = services([
      reply("search", { query: '"Early coding networks" A. Reader 1980' }),
      reply("read", { url: paper.url }),
      reply("origin", { choice: "web", number: 1, title: paper.title, quote: passage, reason: "由引用追到原文。" }),
    ]);
    const origin = await judgeOrigin(service, { ...base, articles: [{ ...article, citations: [{ title: paper.title, authors: "A. Reader", year: "1980" }] }] }, []);
    expect(service.webSearch).toHaveBeenCalledExactlyOnceWith('"Early coding networks" A. Reader 1980');
    expect(origin.work?.url).toBe(paper.url);
  });

  it("refuses to stop at the initial search and preserves an unverified earlier-work lead", async () => {
    const { service, requests } = services([
      reply("origin", { choice: "none", reason: "没有。" }),
      reply("search", { query: "coding network early history" }),
      reply("origin", { choice: "none", leads: [1], reason: "找到了早期工作，尚不能确认唯一源头。" }),
    ]);
    const origin = await judgeOrigin(service, { ...base, articles: [] }, []);
    expect(requests[1]!.messages.at(-1)!.content).toContain("investigate an earlier-work clue");
    expect(origin).toMatchObject({ choice: "none", leads: [{ title: paper.title, url: paper.url }] });
    expect(origin).not.toHaveProperty("quote");
  });

  it("retains a primary-work lead and the access failure when it cannot be opened", async () => {
    const { service } = services([
      reply("read", { url: paper.url }),
      reply("origin", { choice: "web", number: 1, title: paper.title, reason: "找到论文，但原文访问被拒绝。" }),
    ]);
    vi.mocked(service.readPage).mockRejectedValueOnce(new Error("403"));
    const failures: string[] = [];
    const origin = await judgeOrigin(service, base, failures);
    expect(origin.work?.url).toBe(paper.url);
    expect(origin).not.toHaveProperty("quote");
    expect(failures[0]).toContain("403");
  });

  it("rejects an invented quote and an unobserved URL", async () => {
    const { service, requests } = services([
      reply("read", { url: "https://invented.example/paper" }),
      reply("read", { url: paper.url }),
      reply("origin", { choice: "web", number: 1, title: paper.title, quote: "An invented statement.", reason: "" }),
      reply("origin", { choice: "web", number: 1, title: paper.title, quote: passage, reason: "" }),
    ]);
    const origin = await judgeOrigin(service, base, []);
    expect(requests[1]!.messages.at(-1)!.content).toContain("read only a link observed");
    expect(requests[3]!.messages.at(-1)!.content).toContain("quote must be copied verbatim");
    expect(origin.quote).toBe(passage);
  });

  it("keeps observed leads if the investigation reaches its model-call limit", async () => {
    const { service } = services(Array.from({ length: 10 }, () => reply("search", { query: "earlier work" })));
    const failures: string[] = [];
    const origin = await judgeOrigin(service, base, failures);
    expect(origin.choice).toBe("none");
    expect(origin.leads).toContainEqual({ title: paper.title, url: paper.url });
    expect(failures[0]).toContain("did not finish");
  });

  it("retains Wikipedia's observed references when the model service fails", async () => {
    const { service } = services([]);
    service.complete = async () => { throw new Error("Model service unavailable"); };
    const failures: string[] = [];
    const origin = await judgeOrigin(service, base, failures);
    expect(origin.choice).toBe("none");
    expect(origin.leads).toContainEqual({ title: paper.title, url: paper.url });
    expect(failures).toContain("Model service unavailable");
    expect(origin).not.toHaveProperty("quote");
  });

  it("reranks the complete pool after a partial investigation without discarding the original evidence", async () => {
    const { service } = services(Array.from({ length: 10 }, () => reply("search", { query: "earlier work" })));
    const results = Array.from({ length: 24 }, (_, n) => ({ title: `Work ${n + 1}`, url: `https://publisher.example/work-${n + 1}`, snippet: "Observed search result." }));
    const selected = { title: results[23]!.title, url: results[23]!.url, annotation: "与所选概念直接相关；目前只核实到检索摘要。" };
    vi.mocked(rankSourceLinks).mockResolvedValueOnce([selected]);
    const origin = await judgeOrigin(service, { ...base, rank: true, results }, []);
    expect(origin.ranked).toEqual([selected]);
    const rankedInput = vi.mocked(rankSourceLinks).mock.calls.at(-1)![1];
    expect(rankedInput.candidates.some((candidate) => candidate.url === results[23]!.url)).toBe(true);
    expect(rankedInput.candidates.some((candidate) => candidate.url === article.url)).toBe(true);
    expect(rankedInput.candidates.some((candidate) => candidate.url === paper.url)).toBe(true);
    expect(new Set(rankedInput.candidates.map((candidate) => candidate.url)).size).toBe(rankedInput.candidates.length);
    expect(origin.leads!.length).toBeGreaterThan(10);
  });

  it("skips the ranking call when no shortlist is shown, as for Capture", async () => {
    const { service } = services([
      reply("read", { url: paper.url }),
      reply("origin", { choice: "web", number: 1, title: paper.title, quote: passage, reason: "原文陈述该方法。" }),
    ]);
    vi.mocked(rankSourceLinks).mockClear();
    const origin = await judgeOrigin(service, base, []);
    expect(rankSourceLinks).not.toHaveBeenCalled();
    expect(origin).not.toHaveProperty("ranked");
  });
});
