import { describe, expect, it, vi } from "vitest";

import { foreignPages, pageQuotes, pagesText, type ForeignPage } from "../src/foreign";
import type { Origin } from "../src/origin";

const page: ForeignPage = {
  url: "https://arxiv.org/abs/2106.08254",
  title: "BEiT: BERT Pre-Training of Image Transformers",
  text: "BEiT: BERT Pre-Training of Image Transformers\n\nWe introduce BEiT.\nBlock-wise masking masks a block of\nimage patches at a time.",
};

describe("a foreign draft's quotes", () => {
  it("are found in the page's text with whitespace collapsed, and returned so", () => {
    expect(pageQuotes(["Block-wise masking masks a block of image patches", "  We introduce\tBEiT. "], page))
      .toEqual(["Block-wise masking masks a block of image patches", "We introduce BEiT."]);
  });

  it("are refused, naming the first quote the page lacks", () => {
    expect(pageQuotes(["We introduce BEiT.", "MAE introduced block-wise masking."], page))
      .toBe("quote 2 does not occur in page https://arxiv.org/abs/2106.08254; copy it character for character from the page's text");
    expect(pageQuotes(["  "], page)).toBe("quote 1 does not occur in page https://arxiv.org/abs/2106.08254; copy it character for character from the page's text");
  });
});

describe("the pages of an origin", () => {
  const unusedPage = async (): Promise<ForeignPage> => { throw new Error("unexpected page read"); };
  const origin: Origin = {
    choice: "web", reason: "",
    work: { title: "BEiT", url: "https://arxiv.org/abs/2106.08254", arxiv: "2106.08254", year: "2021" },
    wikipedia: { title: "Vision transformer", url: "https://en.wikipedia.org/wiki/Vision_transformer#BEiT", extract: "", section: "BEiT" },
  };
  const feed = "<feed><entry><id>http://arxiv.org/abs/2106.08254v2</id><published>2021-06-15T00:00:00Z</published>"
    + "<title>BEiT: BERT Pre-Training of Image Transformers</title><summary>We introduce BEiT.</summary></entry></feed>";
  const article = JSON.stringify({ query: { pages: [{ title: "Vision transformer", revisions: [{ slots: { main: { content: "== BEiT ==\nBEiT masks [[patch]]es." } } }] }] } });

  it("are the originating paper's abstract, then the Wikipedia article's text, numbered for the model", async () => {
    const { pages, failures } = await foreignPages(async (url) => (url.includes("arxiv.org") ? feed : article), origin, unusedPage);
    expect(failures).toEqual([]);
    expect(pages).toEqual([
      { url: "https://arxiv.org/abs/2106.08254", title: "BEiT: BERT Pre-Training of Image Transformers", text: "BEiT: BERT Pre-Training of Image Transformers\n\nWe introduce BEiT." },
      { url: "https://en.wikipedia.org/wiki/Vision_transformer#BEiT", title: "Vision transformer § BEiT", text: "BEiT masks patches." },
    ]);
    expect(pagesText(pages)).toBe([
      "[1] BEiT: BERT Pre-Training of Image Transformers — https://arxiv.org/abs/2106.08254\nBEiT: BERT Pre-Training of Image Transformers\n\nWe introduce BEiT.",
      "[2] Vision transformer § BEiT — https://en.wikipedia.org/wiki/Vision_transformer#BEiT\nBEiT masks patches.",
    ].join("\n\n"));
  });

  it("leave out a page that cannot be read, saying why, and a paper without an abstract", async () => {
    const failing = await foreignPages(async (url) => {
      if (url.includes("arxiv.org")) return feed;
      throw new Error("en.wikipedia.org answered 503");
    }, origin, unusedPage);
    expect(failing.pages.map((each) => each.title)).toEqual(["BEiT: BERT Pre-Training of Image Transformers"]);
    expect(failing.failures).toEqual(["Wikipedia did not answer: en.wikipedia.org answered 503"]);
    const bare = await foreignPages(async () => "<feed><entry><id>http://arxiv.org/abs/2106.08254v2</id><title>BEiT</title></entry></feed>",
      { ...origin, wikipedia: undefined }, unusedPage);
    expect(bare).toEqual({ pages: [], failures: [] });
    await expect(foreignPages(async () => { throw new Error("unexpected"); }, { choice: "none", reason: "" }, unusedPage)).resolves.toEqual({ pages: [], failures: [] });
  });

  it("reads a non-arXiv work's own URL and presents it before Wikipedia", async () => {
    const work = { title: "Original paper", url: "https://publisher.example/paper.pdf" };
    const readPage = vi.fn(async (url: string) => ({ url, title: "paper.pdf", text: "A coding network reconstructs its input." }));
    const { pages, failures } = await foreignPages(async () => article, { ...origin, work }, readPage);
    expect(readPage).toHaveBeenCalledExactlyOnceWith(work.url);
    expect(pages[0]).toEqual({ ...work, text: "A coding network reconstructs its input." });
    expect(pages[1]!.url).toContain("wikipedia.org");
    expect(failures).toEqual([]);
  });

  it("reports a non-arXiv source failure even when another page is readable", async () => {
    const work = { title: "Original paper", url: "https://publisher.example/paper" };
    const { pages, failures } = await foreignPages(async () => article, { ...origin, work }, async () => { throw new Error("HTTP 403"); });
    expect(pages).toHaveLength(1);
    expect(pages[0]!.url).toContain("wikipedia.org");
    expect(failures).toEqual(["The originating work could not be read: HTTP 403"]);
  });
});
