import { describe, expect, it, vi } from "vitest";
import { webSearch, webSources } from "../src/web";

describe("direct search", () => {
  it("passes the query to the Search API without invoking a model", async () => {
    const post = vi.fn(async () => ({ results: [{ title: "A", url: "https://example.org/page", content: "  source\n text " }] }));
    await expect(webSearch({ endpoint: "https://api.tavily.com/search", apiKey: "test-key" }, "  masked\n autoencoder ", post))
      .resolves.toEqual([{ title: "A", url: "https://example.org/page", snippet: "source text" }]);
    expect(post).toHaveBeenCalledExactlyOnceWith("https://api.tavily.com/search", "test-key", {
      query: "masked autoencoder", search_depth: "basic", max_results: 8,
      include_answer: false, include_raw_content: false, auto_parameters: false,
    }, 60000);
  });

  it("refuses missing credentials and empty queries before any network request", async () => {
    const post = vi.fn();
    await expect(webSearch({ endpoint: "https://example.com", apiKey: "" }, "query", post)).rejects.toThrow("Set the search API");
    await expect(webSearch({ endpoint: "https://example.com", apiKey: "key" }, " \n ", post)).rejects.toThrow("query is empty");
    expect(post).not.toHaveBeenCalled();
  });

  it("deduplicates arXiv mirrors and rejects non-web URLs", () => {
    expect(webSources({ results: [
      { title: "BEiT", url: "https://arxiv.org/pdf/2106.08254v2", content: "a" },
      { title: "BEiT again", url: "https://arxiv.org/html/2106.08254v1" },
      { title: "MAE", url: "https://ar5iv.labs.arxiv.org/html/2111.06377" },
      null, { url: "javascript:alert(1)" }, { url: "file:///etc/passwd" }, { url: "https://user:pass@example.com" }, { url: "invalid" },
    ] })).toEqual([
      { title: "BEiT", url: "https://arxiv.org/abs/2106.08254", snippet: "a" },
      { title: "MAE", url: "https://arxiv.org/abs/2111.06377", snippet: "" },
    ]);
  });

  it("bounds excerpts and distinguishes no matches from invalid responses", () => {
    expect(webSources({ results: [{ url: "https://example.org", content: "a".repeat(500) }] })[0]?.snippet).toBe(`${"a".repeat(400)}…`);
    expect(webSources({ results: [] })).toEqual([]);
    expect(() => webSources({ error: "offline" })).toThrow("no results array");
  });
});
