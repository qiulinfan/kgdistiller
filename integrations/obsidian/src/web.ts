/** Direct Tavily Search API calls, executed as tools by the plugin's own agent. */
import type { PostJson } from "./api";
import { arxivId, arxivUrl } from "./sources";

export interface WebSource {
  title: string;
  url: string;
  snippet: string;
}

export interface SearchSettings {
  endpoint: string;
  apiKey: string;
}

export const WEB_SEARCH_TIMEOUT_MS = 60 * 1000;
const SNIPPET_CHARS = 400;

/** Normalize search results, keeping only web links and deduplicating arXiv mirrors. */
export function webSources(value: unknown): WebSource[] {
  const results = (value as { results?: unknown } | null)?.results;
  if (!Array.isArray(results)) throw new Error("The search API returned no results array.");
  const seen = new Set<string>();
  return results.flatMap((item: unknown) => {
    const source = item as { title?: unknown; url?: unknown; content?: unknown } | null;
    if (typeof source?.url !== "string") return [];
    let parsed: URL;
    try { parsed = new URL(source.url); } catch { return []; }
    if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) return [];
    const arxiv = arxivId(source.url);
    const url = arxiv ? arxivUrl(arxiv) : parsed.href;
    if (seen.has(url)) return [];
    seen.add(url);
    const snippet = typeof source.content === "string" ? source.content.split(/\s+/).filter(Boolean).join(" ") : "";
    return [{
      title: typeof source.title === "string" && source.title.trim() ? source.title.trim() : url,
      url,
      snippet: snippet.length > SNIPPET_CHARS ? `${snippet.slice(0, SNIPPET_CHARS)}…` : snippet,
    }];
  });
}

export async function webSearch(settings: SearchSettings, query: string, post: PostJson): Promise<WebSource[]> {
  const text = query.split(/\s+/).filter(Boolean).join(" ");
  if (!text) throw new Error("The search query is empty.");
  if (!settings.endpoint.trim() || !settings.apiKey.trim()) throw new Error("Set the search API endpoint and API key in the kgdistiller settings.");
  const result = await post(settings.endpoint, settings.apiKey, {
    query: text, search_depth: "basic", max_results: 8,
    include_answer: false, include_raw_content: false, auto_parameters: false,
  }, WEB_SEARCH_TIMEOUT_MS);
  return webSources(result);
}
