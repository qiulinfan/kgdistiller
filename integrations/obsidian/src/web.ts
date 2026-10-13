/**
 * Web search for the agent through OMP's own search chain. The gateway has no
 * search route, so each search is one short `omp -p --mode json` run that may
 * only call OMP's web_search tool: the plugin reads that tool's structured
 * result from the event stream and stops the run. OMP holds the search
 * providers' credentials, so the plugin stores none.
 */

import { spawn, type ChildProcessWithoutNullStreams } from "child_process";
import { tmpdir } from "os";

import { arxivId, arxivUrl } from "./sources";

export interface WebSource {
  title: string;
  url: string;
  snippet: string;
}

export class WebSearchError extends Error {}

export const WEB_SEARCH_TIMEOUT_MS = 60 * 1000;
/** Characters of a source's snippet kept for the model and the sidebar. */
const SNIPPET_CHARS = 400;
const SEARCH_PROMPT = "Call web_search exactly once with the user's text as the query.";

export type SpawnWeb = (command: string, args: string[]) => ChildProcessWithoutNullStreams;

/** Runs from the temporary directory, so no project's context files join OMP's prompt. */
const spawnWeb: SpawnWeb = (command, args) => spawn(command, args, { cwd: tmpdir(), stdio: "pipe", windowsHide: true });

export function webSearchArgs(model: string, query: string): string[] {
  return [
    "-p", "--mode", "json", "--no-session", "--no-title", "--no-skills", "--no-rules", "--no-extensions",
    "--tools=web_search", "--model", model, "--thinking", "off", "--system-prompt", SEARCH_PROMPT, query,
  ];
}

/**
 * The sources of one `tool_execution_end` event of web_search, or the error it
 * reports. A paper's PDF, HTML or mirror page becomes its arXiv abstract page,
 * and a page that appears twice is kept once.
 */
export function webSources(event: unknown): WebSource[] | WebSearchError | undefined {
  const value = event as {
    type?: unknown;
    toolName?: unknown;
    result?: { content?: Array<{ text?: unknown }>; details?: { error?: unknown; response?: { sources?: unknown } } };
  };
  if (value?.type !== "tool_execution_end" || value.toolName !== "web_search") return undefined;
  const details = value.result?.details;
  if (typeof details?.error === "string") return new WebSearchError(details.error);
  const sources = details?.response?.sources;
  if (!Array.isArray(sources)) {
    const text = value.result?.content?.[0]?.text;
    return new WebSearchError(typeof text === "string" ? text.slice(0, 300) : "the web search returned no sources");
  }
  const seen = new Set<string>();
  return sources.flatMap((source: { title?: unknown; url?: unknown; snippet?: unknown }) => {
    if (typeof source?.url !== "string") return [];
    const arxiv = arxivId(source.url);
    const url = arxiv ? arxivUrl(arxiv) : source.url;
    if (seen.has(url)) return [];
    seen.add(url);
    const snippet = typeof source.snippet === "string" ? source.snippet.split(/\s+/).filter(Boolean).join(" ") : "";
    return [{
      title: typeof source.title === "string" && source.title.trim() ? source.title.trim() : url,
      url,
      snippet: snippet.length > SNIPPET_CHARS ? `${snippet.slice(0, SNIPPET_CHARS)}…` : snippet,
    }];
  });
}

/** Search the web with OMP's configured providers, as the given model starts the search. */
export function webSearch(command: string, model: string, query: string, spawnProcess: SpawnWeb = spawnWeb): Promise<WebSource[]> {
  return new Promise((resolve, reject) => {
    let child: ChildProcessWithoutNullStreams;
    try {
      child = spawnProcess(command, webSearchArgs(model, query));
    } catch (error) {
      reject(new WebSearchError(`cannot run ${command}: ${error instanceof Error ? error.message : String(error)}`));
      return;
    }
    let settled = false;
    let buffer = "";
    let stderr = "";
    const finish = (outcome: WebSource[] | Error): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      child.kill();
      if (outcome instanceof Error) reject(outcome);
      else resolve(outcome);
    };
    const timer = setTimeout(() => finish(new WebSearchError(`the web search did not finish within ${WEB_SEARCH_TIMEOUT_MS / 1000} s`)), WEB_SEARCH_TIMEOUT_MS);
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdin.on("error", () => undefined);
    child.stdin.end();
    child.stderr.on("data", (chunk: string) => { stderr = (stderr + chunk).slice(-1000); });
    child.stdout.on("data", (chunk: string) => {
      buffer += chunk;
      for (let newline = buffer.indexOf("\n"); newline >= 0; newline = buffer.indexOf("\n")) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        if (!line) continue;
        let event: unknown;
        try {
          event = JSON.parse(line);
        } catch {
          continue;
        }
        const sources = webSources(event);
        if (sources !== undefined) finish(sources);
      }
    });
    child.on("error", (error) => finish(new WebSearchError(`cannot run ${command}: ${error.message}`)));
    child.on("exit", (code) => finish(new WebSearchError(
      `the web search ended without results (${code ?? "signal"})${stderr.trim() ? `: ${stderr.trim()}` : ""}`)));
  });
}
