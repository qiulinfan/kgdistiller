import { describe, expect, it, vi } from "vitest";
import { ApiError, type PostText } from "../src/api";
import { EXA_MCP_URL, mcpResult, webSearch, webSources } from "../src/web";

/** One `web_search_exa` result text, as Exa's MCP server writes it. */
const exaText = (blocks: Array<Record<string, string>>) => blocks.map((block) => Object.entries(block)
  .map(([name, value]) => (name === "Highlights" ? `Highlights:\n${value}` : `${name}: ${value}`)).join("\n")).join("\n\n---\n\n");
const sse = (message: unknown) => `event: message\ndata: ${JSON.stringify(message)}\n\n`;
const reply = (text: string) => sse({ result: { content: [{ type: "text", text }] }, jsonrpc: "2.0", id: 1 });

describe("Exa search", () => {
  it("calls web_search_exa on Exa's MCP server without a key or a model", async () => {
    const post = vi.fn<PostText>(async () => reply(exaText([
      { Title: "A", URL: "https://example.org/page", Published: "N/A", Author: "N/A", Highlights: "  source\n text " },
    ])));
    await expect(webSearch("", "  masked\n autoencoder ", post)).resolves.toEqual([{ title: "A", url: "https://example.org/page", snippet: "source text" }]);
    expect(post).toHaveBeenCalledExactlyOnceWith(EXA_MCP_URL, { Accept: "application/json, text/event-stream" }, {
      jsonrpc: "2.0", id: 1, method: "tools/call",
      params: { name: "web_search_exa", arguments: { query: "masked autoencoder", numResults: 8 } },
    }, 60000);
  });

  it("sends the owner's Exa key in the x-api-key header", async () => {
    const post = vi.fn<PostText>(async () => reply(""));
    await webSearch(" exa-test-key ", "query", post);
    expect(post.mock.calls[0]![1]).toEqual({ Accept: "application/json, text/event-stream", "x-api-key": "exa-test-key" });
  });

  it("refuses an empty query before any network request", async () => {
    const post = vi.fn<PostText>();
    await expect(webSearch("", " \n ", post)).rejects.toThrow("query is empty");
    expect(post).not.toHaveBeenCalled();
  });

  it("explains Exa's rate limit", async () => {
    const limited = vi.fn<PostText>(async () => { throw new ApiError("API request failed (HTTP 429).", 429); });
    await expect(webSearch("", "query", limited)).rejects.toThrow("Exa's free rate limit was reached");
    await expect(webSearch("key", "query", limited)).rejects.toThrow("rate limit for this key");
    const down = vi.fn<PostText>(async () => { throw new ApiError("API request failed (HTTP 503).", 503); });
    await expect(webSearch("", "query", down)).rejects.toThrow("HTTP 503");
  });
});

describe("Exa's replies", () => {
  it("read the JSON-RPC result from server-sent events or plain JSON", () => {
    expect(mcpResult(sse({ result: { content: [] }, jsonrpc: "2.0", id: 1 }))).toEqual({ content: [] });
    expect(mcpResult(JSON.stringify({ result: { content: [] }, jsonrpc: "2.0", id: 1 }))).toEqual({ content: [] });
    expect(() => mcpResult(sse({ error: { code: -32000, message: "Not Acceptable" }, jsonrpc: "2.0", id: null }))).toThrow("Exa search failed: Not Acceptable");
    expect(() => mcpResult("event: ping\n\n")).toThrow("no search result");
  });

  it("keep web links, a title or the URL, and bounded highlights", () => {
    expect(webSources({ content: [{ type: "text", text: exaText([
      { Title: "N/A", URL: "https://example.org/a", Highlights: "a".repeat(500) },
      { Title: "No link" },
      { Title: "Script", URL: "javascript:alert(1)" },
      { Title: "Credentials", URL: "https://user:pass@example.com" },
    ]) }] })).toEqual([{ title: "https://example.org/a", url: "https://example.org/a", snippet: `${"a".repeat(400)}…` }]);
  });

  it("deduplicate arXiv mirrors as abstract pages", () => {
    expect(webSources({ content: [{ type: "text", text: exaText([
      { Title: "BEiT", URL: "https://arxiv.org/pdf/2106.08254v2", Highlights: "a" },
      { Title: "BEiT again", URL: "https://arxiv.org/html/2106.08254v1" },
      { Title: "MAE", URL: "https://ar5iv.labs.arxiv.org/html/2111.06377" },
    ]) }] })).toEqual([
      { title: "BEiT", url: "https://arxiv.org/abs/2106.08254", snippet: "a" },
      { title: "MAE", url: "https://arxiv.org/abs/2111.06377", snippet: "" },
    ]);
  });

  it("report a tool error and an empty search apart", () => {
    expect(() => webSources({ content: [{ type: "text", text: "Search failed" }], isError: true })).toThrow("Exa search failed: Search failed");
    expect(() => webSources({ content: [] })).toThrow("no search result");
    expect(webSources({ content: [{ type: "text", text: "" }] })).toEqual([]);
  });
});
