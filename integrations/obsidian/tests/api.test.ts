import { createServer, type IncomingMessage, type ServerResponse } from "http";
import { afterEach, describe, expect, it } from "vitest";
import { ApiClient, ApiError, apiUrl } from "../src/api";
import { ModelClient } from "../src/model";
import { EXA_MCP_URL, webSearch } from "../src/web";
import { searchKnowledge, type SearchKgd } from "../src/search";

const cleanup: Array<() => Promise<void> | void> = [];
afterEach(async () => { for (const close of cleanup.splice(0).reverse()) await close(); });
async function server(handler: (req: IncomingMessage, res: ServerResponse, body: Record<string, unknown>) => void): Promise<string> {
  const server = createServer((req, res) => {
    let data = "";
    req.setEncoding("utf8");
    req.on("data", (chunk) => { data += chunk; });
    req.on("end", () => handler(req, res, JSON.parse(data)));
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  cleanup.push(() => new Promise<void>((resolve, reject) => {
    server.close((error) => error ? reject(error) : resolve());
    server.closeAllConnections();
  }));
  return `http://127.0.0.1:${(server.address() as { port: number }).port}`;
}
function client(): ApiClient {
  const api = new ApiClient();
  cleanup.push(() => api.close());
  return api;
}

describe("the plugin's HTTP transport", () => {
  it("posts authenticated UTF-8 JSON and reuses the connection", async () => {
    const sockets: unknown[] = [];
    const url = await server((req, res, body) => {
      sockets.push(req.socket);
      expect(req.headers.authorization).toBe("Bearer synthetic-test-key");
      expect(req.headers["content-type"]).toBe("application/json");
      res.end(JSON.stringify({ received: body }));
    });
    const api = client();
    for (let i = 0; i < 2; i++) await expect(api.post(url, "synthetic-test-key", { query: "测度" }, 1000)).resolves.toEqual({ received: { query: "测度" } });
    expect(sockets[0]).toBe(sockets[1]);
  });

  it("does not follow redirects or echo API error bodies containing credentials", async () => {
    let requests = 0;
    const url = await server((_req, res) => { requests++; res.writeHead(302, { Location: "/elsewhere" }); res.end("synthetic-secret"); });
    await expect(client().post(url, "synthetic-secret", {}, 1000)).rejects.toThrow("HTTP 302");
    expect(requests).toBe(1);
  });

  it("reports invalid JSON and authentication errors", async () => {
    const url = await server((req, res) => {
      if (req.url === "/auth") { res.writeHead(401); res.end("secret"); }
      else res.end("<html>bad gateway</html>");
    });
    const api = client();
    await expect(api.post(url, "", {}, 1000)).rejects.toThrow("invalid JSON");
    await expect(api.post(`${url}/auth`, "", {}, 1000)).rejects.toThrow("HTTP 401");
  });

  it("times out a stalled request and cancels outstanding work on unload", async () => {
    const url = await server(() => undefined);
    const api = client();
    await expect(api.post(url, "", {}, 30)).rejects.toThrow("timed out");
    const pending = api.post(url, "", {}, 1000);
    const assertion = expect(pending).rejects.toThrow("cancelled");
    api.close();
    await assertion;
    await expect(api.post(url, "", {}, 1000)).rejects.toThrow("closed");
  });

  it("posts with the caller's headers, returns the text and keeps the HTTP status of a failure", async () => {
    const url = await server((req, res, body) => {
      if (req.url === "/limited") { res.writeHead(429); res.end("slow down"); return; }
      expect(req.headers["x-api-key"]).toBe("synthetic-test-key");
      expect(req.headers.authorization).toBeUndefined();
      expect(req.headers["content-type"]).toBe("application/json");
      res.setHeader("Content-Type", "text/event-stream");
      res.end(`data: ${JSON.stringify({ received: body })}\n\n`);
    });
    const api = client();
    await expect(api.postText(url, { "x-api-key": "synthetic-test-key" }, { query: "测度" }, 1000))
      .resolves.toBe(`data: ${JSON.stringify({ received: { query: "测度" } })}\n\n`);
    const failure = await api.postText(`${url}/limited`, {}, {}, 1000).catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(429);
  });

  it.each(["https://user:secret@example.org", "https://example.org?api_key=secret", "file:///tmp/x", "http://remote.example.org", "not a URL"])("rejects an invalid API address: %s", (url) => {
    expect(() => apiUrl(url)).toThrow();
  });
});

it("answers through the internal harness, direct model API and Exa's MCP search", async () => {
  let modelCalls = 0;
  let searches = 0;
  const url = await server((req, res, body) => {
    if (req.url === "/mcp") {
      searches++;
      expect(req.headers["x-api-key"]).toBe("exa-test-key");
      expect(req.headers.accept).toBe("application/json, text/event-stream");
      expect(body.method).toBe("tools/call");
      expect(body.params).toEqual({ name: "web_search_exa", arguments: { query: "masked autoencoder site:arxiv.org", numResults: 8 } });
      const text = "Title: MAE\nURL: https://arxiv.org/abs/2111.06377\nPublished: N/A\nHighlights:\nMAE reconstructs masked image patches.";
      res.setHeader("Content-Type", "text/event-stream");
      res.end(`event: message\ndata: ${JSON.stringify({ result: { content: [{ type: "text", text }] }, jsonrpc: "2.0", id: 1 })}\n\n`);
    } else {
      res.setHeader("Content-Type", "application/json");
      modelCalls++;
      expect(req.headers.authorization).toBe("Bearer model-test-key");
      if (modelCalls === 2) expect(JSON.stringify(body.messages)).toContain("MAE reconstructs masked image patches.");
      const call = modelCalls === 1
        ? { name: "paper_search", arguments: JSON.stringify({ query: "masked autoencoder" }) }
        : { name: "answer", arguments: JSON.stringify({ answer: "MAE reconstructs masked image patches. [W1]" }) };
      res.end(JSON.stringify({ choices: [{ message: { content: null, tool_calls: [{ id: String(modelCalls), type: "function", function: call }] }, finish_reason: "tool_calls" }] }));
    }
  });
  const api = client();
  const model = new ModelClient(() => ({ endpoint: `${url}/chat/completions`, apiKey: "model-test-key", model: "test", protocol: "chat-completions" }), api.post);
  const kgd: SearchKgd = { call: async <T>(tool: string): Promise<T> => (tool === "kg_search" ? { results: [] } : { terms: [] }) as T };
  const answer = await searchKnowledge((request) => model.complete(request), kgd,
    // The local server stands in for Exa's MCP server.
    (query) => webSearch("exa-test-key", query, (target, headers, body, timeout) => {
      expect(target).toBe(EXA_MCP_URL);
      return api.postText(`${url}/mcp`, headers, body, timeout);
    }), "What is MAE?",
    { reading: { base: "papers", source: "mae.md", type: "research" } });
  expect(answer.text).toContain("[W1]");
  expect(answer.web).toEqual([{ number: 1, title: "MAE", url: "https://arxiv.org/abs/2111.06377", snippet: "MAE reconstructs masked image patches." }]);
  expect(modelCalls).toBe(2);
  expect(searches).toBe(1);
});
