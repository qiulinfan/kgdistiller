import { describe, expect, it } from "vitest";

import {
  abstractText,
  arxivFeed,
  arxivId,
  citedReferences,
  findSources,
  normalizeTitle,
  rankPapers,
  wikiCitations,
  wikipediaEntry,
  wikipediaPage,
  wikipediaTitles,
  wikiPlain,
  wikiSection,
  workArxiv,
} from "../src/sources";

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

describe("arXiv ids", () => {
  it("are read from arXiv pages, mirrors and DOIs, without versions", () => {
    for (const url of ["https://arxiv.org/abs/2111.06377v3", "http://arxiv.org/pdf/2111.06377", "https://arxiv.org/html/2111.06377v1",
      "https://ar5iv.labs.arxiv.org/html/2111.06377", "https://www.alphaxiv.org/abs/2111.06377", "https://doi.org/10.48550/arXiv.2111.06377"]) {
      expect(arxivId(url)).toBe("2111.06377");
    }
    expect(arxivId("https://doi.org/10.1109/cvpr52688.2022.01553")).toBeUndefined();
    expect(arxivId(undefined)).toBeUndefined();
  });

  it("come from a work's DOI or any location", () => {
    expect(workArxiv({ id: "W1", doi: "https://doi.org/10.48550/arxiv.1312.6114" })).toBe("1312.6114");
    expect(workArxiv({ id: "W2", doi: "https://doi.org/10.1/x", locations: [{ landing_page_url: "https://dare.uva.nl/x" }, { pdf_url: "https://arxiv.org/pdf/1312.6114" }] }))
      .toBe("1312.6114");
    expect(workArxiv({ id: "W3", doi: "https://doi.org/10.1145/1390156.1390294" })).toBeUndefined();
  });

  it("are found by title in an arXiv feed", () => {
    const atom = `<feed><entry><id>http://arxiv.org/abs/2111.06377v3</id><title>Masked Autoencoders Are Scalable
      Vision Learners</title></entry><entry><id>http://arxiv.org/abs/1312.6114v11</id><title>Auto-Encoding Variational Bayes</title></entry></feed>`;
    expect(arxivFeed(atom)).toEqual(new Map([
      [normalizeTitle("Masked Autoencoders Are Scalable Vision Learners"), "2111.06377"],
      [normalizeTitle("Auto-Encoding Variational Bayes"), "1312.6114"],
    ]));
  });
});

describe("ranking papers", () => {
  it("normalizes titles and rebuilds abstracts", () => {
    expect(normalizeTitle("Stacked Denoising Auto-Encoders: Learning")).toBe("stacked denoising autoencoder learning");
    expect(abstractText({ We: [0], propose: [1], BEiT: [2, 4], call: [3] })).toBe("We propose BEiT call BEiT");
    expect(abstractText(null)).toBe("");
  });

  it("prefers the co-cited paper that introduces the term over popular later papers, linking arXiv", () => {
    const papers = rankPapers("variational autoencoder", [
      { id: "W1", display_name: "Auto-Encoding Variational Bayes", publication_date: "2013-12-20", cited_by_count: 20000, doi: "https://doi.org/10.48550/arxiv.1312.6114" },
      { id: "W2", display_name: "An Introduction to Variational Autoencoders", publication_date: "2019-01-01", cited_by_count: 3000 },
      { id: "W3", display_name: "Variational autoencoders for collaborative filtering", publication_date: "2018-01-01", cited_by_count: 2000, doi: "https://doi.org/10.1145/3178876.3186150" },
    ], new Map([["W1", 0.58], ["W3", 0.05]]), ["1312.6114"]);
    expect(papers.map((paper) => paper.title)).toEqual([
      "Auto-Encoding Variational Bayes", "Variational autoencoders for collaborative filtering", "An Introduction to Variational Autoencoders"]);
    expect(papers[0]).toEqual({
      title: "Auto-Encoding Variational Bayes", year: "2013", arxiv: "1312.6114", url: "https://arxiv.org/abs/1312.6114", citations: 20000, score: 3.24,
    });
    expect(papers[1]).toMatchObject({ url: "https://doi.org/10.1145/3178876.3186150" });
    expect(papers[1]).not.toHaveProperty("arxiv");
  });

  it("joins a published paper with its same-titled arXiv record", () => {
    const papers = rankPapers("masked autoencoder", [
      { id: "W1", display_name: "Masked Autoencoders Are Scalable Vision Learners", publication_date: "2022-06-01", cited_by_count: 5000, doi: "https://doi.org/10.1109/cvpr52688.2022.01553" },
      { id: "W2", display_name: "Masked autoencoders are scalable vision learners", publication_date: "2021-11-11", cited_by_count: 900, doi: "https://doi.org/10.48550/arxiv.2111.06377" },
    ], new Map([["W1", 0.89]]), []);
    expect(papers).toEqual([{
      title: "Masked Autoencoders Are Scalable Vision Learners", year: "2021", arxiv: "2111.06377", url: "https://arxiv.org/abs/2111.06377", citations: 5900, score: 4.17,
    }]);
  });

  it("rewards a title that starts with the term", () => {
    const [first] = rankPapers("BEiT", [
      { id: "W1", display_name: "Beit Shemesh water supply", cited_by_count: 5 },
      { id: "W2", display_name: "BEiT: BERT Pre-Training of Image Transformers", cited_by_count: 800 },
    ], new Map(), []);
    expect(first?.title).toBe("BEiT: BERT Pre-Training of Image Transformers");
  });
});

const VISION_TRANSFORMER = [
  "A '''vision transformer''' is a [[transformer (deep learning)|transformer]] for vision.<ref>{{cite arXiv |title=An Image is Worth 16x16 Words |eprint=2010.11929}}</ref>",
  "== History ==",
  "Early work.",
  "=== Masked Autoencoder ===",
  "[[File:Masked Autoencoder.svg|thumb|The [[MAE]] architecture]]",
  "The '''Masked Autoencoder''' took inspiration from [[denoising autoencoder]]s.<ref name=\"mae\">{{Cite arXiv |last=He |title=Masked Autoencoders Are Scalable Vision Learners |eprint=2111.06377}}</ref> It has two ViTs put end-to-end. Later text.",
  "{{cite journal |title=Context Encoders |arxiv=1604.07379}}",
  "=== Next ===",
  "Other.",
  "== Later ==",
].join("\n");

describe("Wikipedia", () => {
  it("tries the singular of a plural term", () => {
    expect(wikipediaTitles("denoising autoencoders")).toEqual(["Denoising autoencoders", "Denoising autoencoder"]);
    expect(wikipediaTitles("BEiT")).toEqual(["BEiT"]);
  });

  it("reads one section, its citations in order, and its plain text", () => {
    const section = wikiSection(VISION_TRANSFORMER, "masked autoencoder");
    expect(section?.split("\n")).toHaveLength(3);
    expect(wikiCitations(section!)).toEqual([
      { title: "Masked Autoencoders Are Scalable Vision Learners", arxiv: "2111.06377" },
      { title: "Context Encoders", arxiv: "1604.07379" },
    ]);
    expect(wikiPlain(section!)).toBe("The Masked Autoencoder took inspiration from denoising autoencoders. It has two ViTs put end-to-end. Later text.");
    expect(wikiSection(VISION_TRANSFORMER, "History")?.split("\n")).toHaveLength(7);
    expect(wikiSection(VISION_TRANSFORMER, "Missing")).toBeUndefined();
  });

  it("is read at the section a redirect points into, with the paper that section cites first", () => {
    const reply = {
      query: {
        redirects: [{ from: "Masked autoencoder", to: "Vision transformer", tofragment: "Masked Autoencoder" }],
        pages: [{ title: "Masked autoencoders", missing: true }, {
          title: "Vision transformer", extract: "A vision transformer (ViT) is a transformer.", langlinks: [],
          revisions: [{ slots: { main: { content: VISION_TRANSFORMER.split("\n")[0] } } }],
        }],
      },
    };
    const located = wikipediaPage(reply);
    expect(located?.section).toBe("Masked Autoencoder");
    expect(wikipediaEntry(located!.page, located!.section, VISION_TRANSFORMER)).toEqual({
      title: "Vision transformer",
      url: "https://en.wikipedia.org/wiki/Vision_transformer#Masked_Autoencoder",
      extract: "The Masked Autoencoder took inspiration from denoising autoencoders. It has two ViTs put end-to-end.",
      section: "Masked Autoencoder",
      cites: { title: "Masked Autoencoders Are Scalable Vision Learners", arxiv: "2111.06377", url: "https://arxiv.org/abs/2111.06377" },
    });
  });

  it("uses the lead for an article about the term, and skips missing and disambiguation pages", () => {
    const page = {
      title: "Variational autoencoder", extract: " A VAE is a model. ", langlinks: [{ lang: "zh", title: "变分自编码器" }],
      revisions: [{ slots: { main: { content: "{{cite arXiv |title=Auto-Encoding Variational Bayes |eprint=1312.6114 }}" } } }],
    };
    expect(wikipediaEntry(page, undefined, "")).toEqual({
      title: "Variational autoencoder",
      url: "https://en.wikipedia.org/wiki/Variational_autoencoder",
      extract: "A VAE is a model.",
      zh: { title: "变分自编码器", url: "https://zh.wikipedia.org/wiki/%E5%8F%98%E5%88%86%E8%87%AA%E7%BC%96%E7%A0%81%E5%99%A8" },
      cites: { title: "Auto-Encoding Variational Bayes", arxiv: "1312.6114", url: "https://arxiv.org/abs/1312.6114" },
    });
    expect(wikipediaPage({ query: { pages: [{ title: "BEiT", missing: true }] } })).toBeUndefined();
    expect(wikipediaPage({ query: { pages: [{ title: "Beit", pageprops: { disambiguation: "" } }] } })).toBeUndefined();
    expect(wikipediaPage(undefined)).toBeUndefined();
  });
});

describe("finding sources", () => {
  it("shows no paper when only the term's appearance in titles speaks for it", async () => {
    const fetch = async (url: string): Promise<string> => {
      if (url.includes("search.title=")) {
        return JSON.stringify({ results: [{ id: "W1", display_name: "POI2Vec: Geographical Latent Representation", cited_by_count: 223, doi: "https://doi.org/10.1/x" }] });
      }
      if (url.includes("search.title_and_abstract=")) return JSON.stringify({ results: [{ id: "W5", referenced_works: ["W8"] }, { id: "W6", referenced_works: ["W9"] }] });
      if (url.includes("filter=openalex")) return JSON.stringify({ results: [] });
      throw new Error("not asked");
    };
    await expect(findSources("latent representation", fetch)).resolves.toEqual({ papers: [] });
  });

  it("asks OpenAlex and Wikipedia together, then the co-cited works, then arXiv for papers without an arXiv id", async () => {
    const urls: string[] = [];
    const fetch = async (url: string): Promise<string> => {
      urls.push(url);
      if (url.includes("search.title=")) {
        return JSON.stringify({ results: [{ id: "W9", display_name: "BEiT: BERT Pre-Training of Image Transformers", cited_by_count: 800, doi: "https://doi.org/10.48550/arxiv.2106.08254" }] });
      }
      if (url.includes("search.title_and_abstract=")) return JSON.stringify({ results: [{ id: "W5", referenced_works: ["W9", "W7"] }, { id: "W6", referenced_works: ["W9"] }] });
      if (url.includes("filter=openalex")) return JSON.stringify({ results: [{ id: "W7", display_name: "Attention is all you need", cited_by_count: 90000, doi: "https://doi.org/10.5555/x" }] });
      if (url.startsWith("https://export.arxiv.org")) {
        return "<feed><entry><id>http://arxiv.org/abs/1706.03762v7</id><title>Attention Is All You Need</title></entry></feed>";
      }
      throw new Error("wikipedia is down");
    };
    const found = await findSources("BEiT", fetch);
    expect(found.papers.map((paper) => [paper.title, paper.url])).toEqual([
      ["BEiT: BERT Pre-Training of Image Transformers", "https://arxiv.org/abs/2106.08254"],
      ["Attention is all you need", "https://arxiv.org/abs/1706.03762"],
    ]);
    expect(found.papers.every((paper) => paper.score >= 2)).toBe(true);
    expect(found.wikipedia).toBeUndefined();
    expect(urls[0]).toContain("search.title=BEiT");
    expect(urls[2]).toContain("titles=BEiT");
    expect(urls[3]).toContain("filter=openalex%3AW7");
    expect(urls[4]).toContain("search_query=ti%3A%22Attention%20is%20all%20you%20need%22");
  });
});
