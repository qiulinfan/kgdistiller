import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ARXIV_INTERVAL_MS,
  arxivByTitle,
  arxivFeed,
  arxivId,
  citedReferences,
  fetchError,
  findWikipedia,
  normalizeTitle,
  wikipediaArticles,
  sourceOpening,
  wikiCitations,
  wikipediaEntry,
  wikipediaPage,
  wikipediaTitles,
  wikiPlain,
  wikiSection,
} from "../src/sources";

describe("the source being read", () => {
  it("is its first heading with the text up to the next heading", () => {
    const lines = ["# Masked Autoencoders Are Scalable Vision Learners", "", "Kaiming He、Xinlei Chen", "",
      "原文：[arXiv:2111.06377v3](https://arxiv.org/abs/2111.06377v3)。", "", "## 摘要", "本文表明"];
    expect(sourceOpening(lines, "/papers/MAE/mae_ch.md")).toEqual({
      title: "Masked Autoencoders Are Scalable Vision Learners",
      text: "# Masked Autoencoders Are Scalable Vision Learners Kaiming He、Xinlei Chen 原文：[arXiv:2111.06377v3](https://arxiv.org/abs/2111.06377v3)。",
    });
  });

  it("skips frontmatter, is named by its file without a heading, and is kept short", () => {
    const opening = sourceOpening(["---", "tags: [a]", "---", "", "Notes on autoencoders.", "x ".repeat(400)], "/notes/Autoencoders.md");
    expect(opening.title).toBe("Autoencoders");
    expect(opening.text.startsWith("Notes on autoencoders. x x")).toBe(true);
    expect(opening.text).toHaveLength(601);
  });
});

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
  it("are read from arXiv pages, mirrors, DOIs and citations, without versions", () => {
    for (const url of ["https://arxiv.org/abs/2111.06377v3", "http://arxiv.org/pdf/2111.06377", "https://arxiv.org/html/2111.06377v1",
      "https://ar5iv.labs.arxiv.org/html/2111.06377", "https://www.alphaxiv.org/abs/2111.06377", "https://doi.org/10.48550/arXiv.2111.06377",
      "Kaiming He et al. Masked autoencoders. arXiv: 2111.06377, 2021."]) {
      expect(arxivId(url)).toBe("2111.06377");
    }
    expect(arxivId("https://doi.org/10.1109/cvpr52688.2022.01553")).toBeUndefined();
    expect(arxivId(undefined)).toBeUndefined();
  });
});

describe("arXiv records", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  const FEED = `<feed><entry><id>http://arxiv.org/abs/2111.06377v3</id><published>2021-11-11T18:46:40Z</published><title>Masked Autoencoders Are Scalable
      Vision Learners</title></entry><entry><id>http://arxiv.org/abs/1312.6114v11</id><published>2013-12-20T20:58:10Z</published><title>Auto-Encoding Variational Bayes &amp; more</title></entry></feed>`;

  it("are read from an arXiv feed with their titles and years", () => {
    expect(arxivFeed(FEED)).toEqual([
      { arxiv: "2111.06377", title: "Masked Autoencoders Are Scalable Vision Learners", year: "2021" },
      { arxiv: "1312.6114", title: "Auto-Encoding Variational Bayes & more", year: "2013" },
    ]);
    expect(arxivFeed("<feed><entry><id>http://arxiv.org/api/errors#incorrect_id_format_for_x</id><title>Error</title></entry></feed>")).toEqual([]);
  });

  it("are searched by title and kept only when the title is the same", async () => {
    const urls: string[] = [];
    const fetch = async (url: string): Promise<string> => {
      urls.push(url);
      return FEED;
    };
    vi.useFakeTimers();
    const same = arxivByTitle(fetch, "Masked autoencoders are scalable vision learners");
    const other = arxivByTitle(fetch, "Masked autoencoders");
    await vi.advanceTimersByTimeAsync(ARXIV_INTERVAL_MS);
    await expect(same).resolves.toEqual({ arxiv: "2111.06377", title: "Masked Autoencoders Are Scalable Vision Learners", year: "2021" });
    await expect(other).resolves.toBeUndefined();
    expect(urls).toEqual([
      "https://export.arxiv.org/api/query?search_query=ti%3A%22Masked%20autoencoders%20are%20scalable%20vision%20learners%22&max_results=10",
      "https://export.arxiv.org/api/query?search_query=ti%3A%22Masked%20autoencoders%22&max_results=10",
    ]);
  });

  it("are asked at most once every three seconds through one fetcher", async () => {
    const times: number[] = [];
    const fetch = async (): Promise<string> => {
      times.push(Date.now());
      return "<feed></feed>";
    };
    let other = 0;
    vi.useFakeTimers();
    const asked = Promise.all([arxivByTitle(fetch, "A"), arxivByTitle(fetch, "B"),
      arxivByTitle(async () => { other++; return "<feed></feed>"; }, "B")]);
    await vi.advanceTimersByTimeAsync(ARXIV_INTERVAL_MS - 1);
    expect([times.length, other]).toEqual([1, 1]);
    await vi.advanceTimersByTimeAsync(1);
    await asked;
    expect(times[1]! - times[0]!).toBe(ARXIV_INTERVAL_MS);
  });

  it("compare titles without case, punctuation or plurals", () => {
    expect(normalizeTitle("Stacked Denoising Auto-Encoders: Learning")).toBe("stacked denoising autoencoder learning");
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

describe("finding Wikipedia's entry", () => {
  it("reads the article at the section a redirect points into", async () => {
    const urls: string[] = [];
    const fetch = async (url: string): Promise<string> => {
      urls.push(url);
      if (url.includes("redirects=1")) {
        return JSON.stringify({ query: {
          redirects: [{ from: "Masked autoencoder", to: "Vision transformer", tofragment: "Masked Autoencoder" }],
          pages: [{ title: "Vision transformer", extract: "A vision transformer (ViT) is a transformer.", revisions: [{ slots: { main: { content: "lead" } } }] }],
        } });
      }
      return JSON.stringify({ query: { pages: [{ title: "Vision transformer", revisions: [{ slots: { main: { content: VISION_TRANSFORMER } } }] }] } });
    };
    const entry = await findWikipedia("masked autoencoder", fetch);
    expect(entry?.url).toBe("https://en.wikipedia.org/wiki/Vision_transformer#Masked_Autoencoder");
    expect(entry?.cites?.arxiv).toBe("2111.06377");
    expect(urls[0]).toContain("titles=Masked%20autoencoder");
    expect(urls[1]).toContain("titles=Vision%20transformer");
    expect(urls[1]).not.toContain("rvsection");
  });

  it("adds the articles Wikipedia's search finds first, in rank order, without disambiguation pages or the named article again", async () => {
    const urls: string[] = [];
    const fetch = async (url: string): Promise<string> => {
      urls.push(url);
      if (url.includes("generator=search")) {
        return JSON.stringify({ query: { pages: [
          { title: "Latent space", index: 2, extract: "A latent space is an embedding.", langlinks: [{ lang: "zh", title: "潜空间 (机器学习)" }] },
          { title: "Latent (disambiguation)", index: 3, pageprops: { disambiguation: "" } },
          { title: "Latent diffusion model", index: 1, extract: "The latent diffusion model is a diffusion model.", revisions: [{ slots: { main: { content: "{{cite arXiv |title=High-Resolution Image Synthesis |eprint=2112.10752}}" } } }] },
          { title: "Latent representation", index: 4, extract: "Named again." },
        ] } });
      }
      return JSON.stringify({ query: { pages: [{ title: "Latent representation", extract: "Named.", revisions: [{ slots: { main: { content: "" } } }] }] } });
    };
    const articles = await wikipediaArticles("latent representation", fetch);
    expect(articles.map((article) => article.title)).toEqual(["Latent representation", "Latent diffusion model", "Latent space"]);
    expect(articles[1]?.cites).toEqual({ title: "High-Resolution Image Synthesis", arxiv: "2112.10752", url: "https://arxiv.org/abs/2112.10752" });
    expect(articles[2]?.zh?.title).toBe("潜空间 (机器学习)");
    expect(urls.find((url) => url.includes("generator=search"))).toContain("gsrsearch=latent%20representation&gsrlimit=3");
    await expect(wikipediaArticles("BEiT", async (url) => JSON.stringify({ query: { pages: url.includes("generator=search") ? [] : [{ title: "BEiT", missing: true }] } })))
      .resolves.toEqual([]);
  });

  it("is undefined without an article, and a service's error says why", async () => {
    await expect(findWikipedia("BEiT", async () => JSON.stringify({ query: { pages: [{ title: "BEiT", missing: true }] } }))).resolves.toBeUndefined();
    await expect(findWikipedia("BEiT", async (url) => { throw fetchError(url, 503, "<html>down</html>"); }))
      .rejects.toThrow("en.wikipedia.org answered 503");
    expect(fetchError("https://export.arxiv.org/api/query", 429, JSON.stringify({ error: "Rate exceeded" })).message)
      .toBe("export.arxiv.org answered 429: Rate exceeded");
  });
});
