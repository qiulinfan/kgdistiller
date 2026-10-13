import { EventEmitter } from "events";
import { PassThrough } from "stream";
import { describe, expect, it, vi } from "vitest";

import { webSearch, webSearchArgs, webSources, WebSearchError, type SpawnWeb } from "../src/web";

/** An `omp -p --mode json` stand-in that prints the given event lines, then exits unless killed. */
class FakeOmp extends EventEmitter {
  readonly stdin = new PassThrough();
  readonly stdout = new PassThrough();
  readonly stderr = new PassThrough();
  killed = false;
  readonly kill = vi.fn(() => {
    this.killed = true;
    return true;
  });

  constructor(lines: string[], stderr = "", code = 0) {
    super();
    setTimeout(() => {
      for (const line of lines) this.stdout.write(`${line}\n`);
      if (stderr) this.stderr.write(stderr);
      setTimeout(() => { if (!this.killed) this.emit("exit", code, null); }, 5);
    }, 0);
  }
}

const toolEnd = (details: unknown) => JSON.stringify({ type: "tool_execution_end", toolName: "web_search", result: { content: [{ type: "text", text: "…" }], details } });

describe("web search events", () => {
  it("read the sources of web_search's result, flattening and cutting snippets", () => {
    const long = `${"word ".repeat(100)}end`;
    expect(webSources(JSON.parse(toolEnd({ response: { sources: [
      { title: " BEiT ", url: "https://arxiv.org/abs/2106.08254", snippet: "We  introduce\n BEiT" },
      { url: "https://example.org", snippet: long },
      { title: "no url" },
    ] } })))).toEqual([
      { title: "BEiT", url: "https://arxiv.org/abs/2106.08254", snippet: "We introduce BEiT" },
      { title: "https://example.org", url: "https://example.org", snippet: `${long.slice(0, 400)}…` },
    ]);
  });

  it("report the tool's error and ignore other events", () => {
    expect(webSources(JSON.parse(toolEnd({ error: "No search provider is available" })))).toEqual(new WebSearchError("No search provider is available"));
    expect(webSources({ type: "tool_execution_end", toolName: "web_search", result: { content: [{ text: "Error: offline" }] } }))
      .toEqual(new WebSearchError("Error: offline"));
    expect(webSources({ type: "message_update" })).toBeUndefined();
    expect(webSources({ type: "tool_execution_end", toolName: "read" })).toBeUndefined();
  });

  it("run omp with only the web_search tool and the given model", () => {
    const args = webSearchArgs("@smol", "masked autoencoder");
    expect(args).toContain("--tools=web_search");
    expect(args.slice(args.indexOf("--model"), args.indexOf("--model") + 2)).toEqual(["--model", "@smol"]);
    expect(args.at(-1)).toBe("masked autoencoder");
  });
});

describe("a web search", () => {
  it("resolves with the first web_search result and stops omp", async () => {
    let child: FakeOmp | undefined;
    const spawn = vi.fn((_command: string, _args: string[]) => {
      child = new FakeOmp(['{"type":"session"}', "not json", toolEnd({ response: { sources: [{ title: "A", url: "https://a.org", snippet: "a" }] } })]);
      return child as never;
    }) satisfies SpawnWeb;
    await expect(webSearch("/bin/omp", "@smol", "query", spawn)).resolves.toEqual([{ title: "A", url: "https://a.org", snippet: "a" }]);
    expect(spawn).toHaveBeenCalledWith("/bin/omp", webSearchArgs("@smol", "query"));
    expect(child?.kill).toHaveBeenCalled();
  });

  it("fails when omp exits without a result, with its error output", async () => {
    const spawn = vi.fn(() => new FakeOmp(['{"type":"agent_end"}'], "No available model matches", 1) as never) satisfies SpawnWeb;
    await expect(webSearch("/bin/omp", "x", "query", spawn)).rejects.toThrow("the web search ended without results (1): No available model matches");
  });
});
