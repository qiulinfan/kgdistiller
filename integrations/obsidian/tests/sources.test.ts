import { describe, expect, it } from "vitest";

import { abstractText, citedReferences, findSources, normalizeTitle, rankPapers, wikipediaEntry, wikipediaTitles } from "../src/sources";

describe("cited references", () => {
  const lines = [
    "文献 \\[[2](#^reference-2)\\] 提出的 block-wise masking 倾向移除 large blocks \\[[2](#^reference-2), [9](#^reference-9)\\]。",
    "## 参考文献",
    "**[2]** Hangbo Bao, Li Dong, and Furu Wei. BEiT: BERT pre-training of image transformers. *arXiv:2106.08254*, 2021. [文献链接](https://arxiv.org/abs/2106.08254) ^reference-2",
    "**[9]** Xinlei Chen, Saining Xie, and Kaiming He. An empirical study of training self-supervised Vision Transformers. In *ICCV*, 2021. ^reference-9",
  ];

  it("reads each cited entry once, in citing order, with its link", () => {
    expect(citedReferences(lines[0]!, lines)).toEqual([
      { number: "2", text: "Hangbo Bao, Li Dong, and Furu Wei. BEiT: BERT pre-training of image transformers. arXiv:2106.08254, 2021.", url: "https://arxiv.org/abs/2106.08254" },
      { number: "9", text: "Xinlei Chen, Saining Xie, and Kaiming He. An empirical study of training self-supervised Vision Transformers. In ICCV, 2021." },
    ]);
  });

  it("skips citations whose entry the source lacks", () => {
    expect(citedReferences("see [5](#^reference-5)", lines)).toEqual([]);
  });
});

describe("ranking papers", () => {
  it("normalizes titles and rebuilds abstracts", () => {
    expect(normalizeTitle("Stacked Denoising Auto-Encoders: Learning")).toBe("stacked denoising autoencoder learning");
    expect(abstractText({ We: [0], propose: [1], BEiT: [2, 4], call: [3] })).toBe("We propose BEiT call BEiT");
    expect(abstractText(null)).toBe("");
  });

  it("prefers the co-cited paper that introduces the term over popular later papers", () => {
    const papers = rankPapers("variational autoencoder", [
      { id: "W1", display_name: "Auto-Encoding Variational Bayes", publication_date: "2013-12-20", cited_by_count: 20000, doi: "https://doi.org/10.48550/arxiv.1312.6114" },
      { id: "W2", display_name: "An Introduction to Variational Autoencoders", publication_date: "2019-01-01", cited_by_count: 3000 },
      { id: "W3", display_name: "Variational autoencoders for collaborative filtering", publication_date: "2018-01-01", cited_by_count: 2000 },
    ], new Map([["W1", 0.58], ["W3", 0.05]]), ["1312.6114"]);
    expect(papers.map((paper) => paper.title)).toEqual([
      "Auto-Encoding Variational Bayes", "Variational autoencoders for collaborative filtering", "An Introduction to Variational Autoencoders"]);
    expect(papers[0]).toEqual({ title: "Auto-Encoding Variational Bayes", year: "2013", url: "https://doi.org/10.48550/arxiv.1312.6114", citations: 20000, score: 3.24 });
  });

  it("rewards a title that starts with the term", () => {
    const [first] = rankPapers("BEiT", [
      { id: "W1", display_name: "Beit Shemesh water supply", cited_by_count: 5 },
      { id: "W2", display_name: "BEiT: BERT Pre-Training of Image Transformers", cited_by_count: 800 },
    ], new Map(), []);
    expect(first?.title).toBe("BEiT: BERT Pre-Training of Image Transformers");
  });
});

describe("the Wikipedia entry", () => {
  it("tries the singular of a plural term", () => {
    expect(wikipediaTitles("denoising autoencoders")).toEqual(["Denoising autoencoders", "Denoising autoencoder"]);
    expect(wikipediaTitles("BEiT")).toEqual(["BEiT"]);
  });

  it("follows a redirect into a section and links the Chinese article", () => {
    const reply = {
      query: {
        redirects: [{ from: "Denoising autoencoder", to: "Autoencoder", tofragment: "Denoising autoencoder (DAE)" }],
        pages: [{ title: "Denoising autoencoders", missing: true }, {
          title: "Autoencoder", extract: " An autoencoder is a neural network. ", langlinks: [{ lang: "zh", title: "自编码器" }],
          revisions: [{ slots: { main: { content: "{{cite arXiv |eprint=1312.6114 }}" } } }],
        }],
      },
    };
    expect(wikipediaEntry(reply)).toEqual({
      arxiv: ["1312.6114"],
      entry: {
        title: "Autoencoder",
        url: "https://en.wikipedia.org/wiki/Autoencoder#Denoising_autoencoder_(DAE)",
        extract: "An autoencoder is a neural network.",
        section: "Denoising autoencoder (DAE)",
        zh: { title: "自编码器", url: "https://zh.wikipedia.org/wiki/%E8%87%AA%E7%BC%96%E7%A0%81%E5%99%A8" },
      },
    });
  });

  it("has no entry for missing or disambiguation pages", () => {
    expect(wikipediaEntry({ query: { pages: [{ title: "BEiT", missing: true }] } })).toEqual({ arxiv: [] });
    expect(wikipediaEntry({ query: { pages: [{ title: "Beit", pageprops: { disambiguation: "" } }] } })).toEqual({ arxiv: [] });
    expect(wikipediaEntry(undefined)).toEqual({ arxiv: [] });
  });
});

describe("finding sources", () => {
  it("queries OpenAlex and Wikipedia in parallel and fetches the most co-cited works it lacks", async () => {
    const urls: string[] = [];
    const get = async (url: string): Promise<unknown> => {
      urls.push(url);
      if (url.includes("search.title=")) return { results: [{ id: "W9", display_name: "BEiT: BERT Pre-Training of Image Transformers", cited_by_count: 800 }] };
      if (url.includes("search.title_and_abstract=")) return { results: [{ id: "W5", referenced_works: ["W9", "W7"] }, { id: "W6", referenced_works: ["W9"] }] };
      if (url.includes("filter=openalex")) return { results: [{ id: "W7", display_name: "Attention is all you need", cited_by_count: 90000 }] };
      throw new Error("wikipedia is down");
    };
    const found = await findSources("BEiT", get);
    expect(found.papers.map((paper) => paper.title)).toEqual(["BEiT: BERT Pre-Training of Image Transformers", "Attention is all you need"]);
    expect(found.wikipedia).toBeUndefined();
    expect(urls[3]).toContain("filter=openalex%3AW7");
    expect(urls[0]).toContain("search.title=BEiT");
    expect(urls[2]).toContain("titles=BEiT");
  });
});
