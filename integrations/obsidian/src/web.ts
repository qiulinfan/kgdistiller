/**
 * Web search through Exa's hosted MCP server, called directly as one JSON-RPC
 * `tools/call` of its `web_search_exa` tool. It needs no key at Exa's free
 * rate limits; the owner's optional Exa key moves searches to their plan.
 */
import { ApiError, type PostText } from "./api";
import { arxivId, arxivUrl } from "./sources";

export interface WebSource {
  title: string;
  url: string;
  snippet: string;
}

export const EXA_MCP_URL = "https://mcp.exa.ai/mcp";
export const WEB_SEARCH_TIMEOUT_MS = 60 * 1000;
const RESULTS = 8;
const SNIPPET_CHARS = 400;

/** The JSON-RPC result of an MCP reply, which arrives as JSON or as server-sent events. */
export function mcpResult(body: string): unknown {
  const messages = [body.trim(), ...body.split("\n").filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trim())];
  for (const message of messages) {
    if (!message.startsWith("{")) continue;
    let value: { result?: unknown; error?: { message?: unknown } };
    try { value = JSON.parse(message); } catch { continue; }
    if (value.error) throw new Error(`Exa search failed: ${typeof value.error.message === "string" ? value.error.message : "unknown error"}`);
    if ("result" in value) return value.result;
  }
  throw new Error("Exa returned no search result.");
}

/**
 * The sources of a `web_search_exa` result: text blocks of `Title:`, `URL:`
 * and other fields ending in `Highlights:` or `Text:`, separated by `---`.
 * Only web links are kept, a paper's mirror pages become its arXiv abstract
 * page, and a page that appears twice is kept once.
 */
export function webSources(result: unknown): WebSource[] {
  const value = result as { content?: Array<{ text?: unknown }>; isError?: unknown } | null;
  const text = value?.content?.find((item) => typeof item?.text === "string")?.text as string | undefined;
  if (value?.isError) throw new Error(`Exa search failed: ${(text ?? "unknown error").slice(0, 300)}`);
  if (text === undefined) throw new Error("Exa returned no search result.");
  const seen = new Set<string>();
  return text.split(/\n\n---\n\n/).flatMap((block) => {
    const field = (name: string) => new RegExp(`^${name}:[ \\t]*(.+)$`, "m").exec(block)?.[1]?.trim();
    const link = field("URL");
    if (!link) return [];
    let parsed: URL;
    try { parsed = new URL(link); } catch { return []; }
    if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) return [];
    const arxiv = arxivId(link);
    const url = arxiv ? arxivUrl(arxiv) : parsed.href;
    if (seen.has(url)) return [];
    seen.add(url);
    const title = field("Title");
    const content = /^(?:Highlights|Text):[ \t]*\n?([\s\S]*)$/m.exec(block)?.[1] ?? "";
    const snippet = content.split(/\s+/).filter(Boolean).join(" ");
    return [{
      title: title && title !== "N/A" ? title : url,
      url,
      snippet: snippet.length > SNIPPET_CHARS ? `${snippet.slice(0, SNIPPET_CHARS)}…` : snippet,
    }];
  });
}

/** Search the web with the query on one line; an empty key searches at Exa's free rate limits. */
export async function webSearch(apiKey: string, query: string, post: PostText): Promise<WebSource[]> {
  const text = query.split(/\s+/).filter(Boolean).join(" ");
  if (!text) throw new Error("The search query is empty.");
  const key = apiKey.trim();
  let body: string;
  try {
    body = await post(EXA_MCP_URL, { Accept: "application/json, text/event-stream", ...(key ? { "x-api-key": key } : {}) }, {
      jsonrpc: "2.0", id: 1, method: "tools/call",
      params: { name: "web_search_exa", arguments: { query: text, numResults: RESULTS } },
    }, WEB_SEARCH_TIMEOUT_MS);
  } catch (error) {
    if (error instanceof ApiError && error.status === 429) {
      throw new Error(key
        ? "Exa's rate limit for this key was reached; wait a moment and search again."
        : "Exa's free rate limit was reached; wait a moment, or add an Exa API key in the kgdistiller settings.");
    }
    throw error;
  }
  return webSources(mcpResult(body));
}
