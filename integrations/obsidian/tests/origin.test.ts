import { describe, expect, it } from "vitest";

import type { ChatReply, ChatRequest } from "../src/gateway";
import { acceptOrigin, judgeOrigin, originQuery, originUser, originWork, type OriginInput } from "../src/origin";

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
    await expect(originWork(async () => { throw new Error("export.arxiv.org answered 503"); }, accepted))
      .resolves.toEqual({ work: { title, url: DAE.url }, arxivError: "export.arxiv.org answered 503" });
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
        model: "m", finish_reason: "tool_calls", usage: { prompt_tokens: 1, completion_tokens: 1 },
        message: { content: null, tool_calls: [{ id: `c${requests.length}`, type: "function", function: { name: "origin", arguments: JSON.stringify(args) } }] },
      };
    };
    const fetch = async (): Promise<string> => FEED("2104.02057", "An Empirical Study of Training Self-Supervised Vision Transformers", "2021");
    await expect(judgeOrigin(complete, fetch, { ...input, term: "MoCo v3" })).resolves.toEqual({
      choice: "reference",
      reason: "MoCo v3 出自 [9]。",
      reference: "9",
      wikipedia: ARTICLES[0],
      work: { title: "An Empirical Study of Training Self-Supervised Vision Transformers", url: "https://arxiv.org/abs/2104.02057", arxiv: "2104.02057", year: "2021" },
    });
    expect(requests[0]!.messages[0]!.content).toContain("a term it only uses, studies or credits to earlier work is not its own");
    expect(requests[0]!.tool_choice).toEqual({ type: "function", function: { name: "origin" } });
    expect(requests.map((request) => request.reasoning_effort)).toEqual(["low", "low"]);
    expect(requests[1]!.messages.at(-1)).toMatchObject({ role: "tool", content: "error: number must be a cited reference: 2, 9" });
  });
});
