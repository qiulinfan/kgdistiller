import { describe, expect, it, vi } from "vitest";

import type { GetJson } from "../src/api";
import type { ChatReply, ChatRequest } from "../src/model";
import {
  baseUrlOf,
  chatUrl,
  connectionTest,
  formatOf,
  kindOf,
  listModels,
  modelGroups,
  modelIds,
  modelsUrl,
  newProviderName,
  PROVIDER_KINDS,
  storedProviders,
  type Detected,
  type Provider,
} from "../src/providers";

const deepseek: Provider = { name: "deepseek", kind: "deepseek", baseUrl: "", format: "chat-completions", apiKey: "deepseek-key" };
const local: Provider = { name: "local", kind: "custom", baseUrl: "http://localhost:8080/v1/", format: "chat-completions", apiKey: "" };

describe("a provider's addresses", () => {
  it("come from a hosted service's kind, whatever the provider stores", () => {
    expect(chatUrl(deepseek)).toBe("https://api.deepseek.com/chat/completions");
    expect(modelsUrl(deepseek)).toBe("https://api.deepseek.com/models");
    expect(formatOf(deepseek)).toBe("deepseek");
    expect(baseUrlOf({ ...deepseek, baseUrl: "https://elsewhere.example" })).toBe("https://api.deepseek.com");
    expect(chatUrl({ ...deepseek, kind: "openrouter" })).toBe("https://openrouter.ai/api/v1/chat/completions");
  });

  it("are a local server's or another service's own, with a local server's default", () => {
    expect(chatUrl(local)).toBe("http://localhost:8080/v1/chat/completions");
    expect(chatUrl({ ...local, baseUrl: "  " })).toBe("");
    expect(chatUrl({ ...local, kind: "ollama", baseUrl: "" })).toBe("http://localhost:11434/v1/chat/completions");
    expect(formatOf({ ...local, format: "deepseek" })).toBe("deepseek");
    expect(formatOf({ ...local, kind: "ollama", format: "deepseek" })).toBe("chat-completions");
  });

  it("name every kind once, with a base URL for every hosted one", () => {
    expect(new Set(PROVIDER_KINDS.map((kind) => kind.id)).size).toBe(PROVIDER_KINDS.length);
    for (const kind of PROVIDER_KINDS) if (!kind.ownUrl) expect(kind.baseUrl).toMatch(/^https:\/\//);
    expect(kindOf({ kind: "unknown" }).id).toBe("custom");
  });
});

describe("model detection", () => {
  it("reads OpenAI's data list, a models list or a bare list, each id once and sorted", () => {
    expect(modelIds({ object: "list", data: [{ id: "deepseek-v4-pro" }, { id: "deepseek-flash" }, { id: "deepseek-flash" }] }))
      .toEqual(["deepseek-flash", "deepseek-v4-pro"]);
    expect(modelIds({ models: [{ name: "qwen3:8b" }, { id: " llama3 " }, { other: 1 }, null] })).toEqual(["llama3", "qwen3:8b"]);
    expect(modelIds(["b", "a", ""])).toEqual(["a", "b"]);
    expect(modelIds({ data: [] })).toEqual([]);
    expect(() => modelIds({ error: "nope" })).toThrow("no list of models");
  });

  it("asks GET <base URL>/models with the provider's key", async () => {
    const get = vi.fn<GetJson>().mockResolvedValue({ data: [{ id: "deepseek-flash" }] });
    await expect(listModels(deepseek, "secret-value", get)).resolves.toEqual(["deepseek-flash"]);
    expect(get).toHaveBeenCalledExactlyOnceWith("https://api.deepseek.com/models", "secret-value", 20000);
  });

  it("asks nothing before the provider has a base URL", async () => {
    const get = vi.fn<GetJson>();
    await expect(listModels({ ...local, baseUrl: "" }, "", get)).rejects.toThrow("Set the provider's base URL.");
    expect(get).not.toHaveBeenCalled();
  });
});

describe("stored providers", () => {
  it("keep the complete ones, each name once, and say when anything else was there", () => {
    expect(storedProviders([deepseek, local])).toEqual({ providers: [deepseek, local], dropped: false });
    expect(storedProviders([deepseek, { ...deepseek, baseUrl: "x" }, { ...local, format: "anthropic" }, { ...local, name: " " },
      { ...local, name: "other", kind: "gemini" }, { name: "old", baseUrl: "https://api.deepseek.com", format: "deepseek", apiKey: "" }, null, local]))
      .toEqual({ providers: [deepseek, local], dropped: true });
    expect(storedProviders(undefined)).toEqual({ providers: [], dropped: false });
    expect(storedProviders({ name: "deepseek" })).toEqual({ providers: [], dropped: true });
  });

  it("are suggested after their kind, then kind-2 and on", () => {
    expect(newProviderName([], "deepseek")).toBe("deepseek");
    expect(newProviderName([deepseek, { ...deepseek, name: "deepseek-2" }], "deepseek")).toBe("deepseek-3");
  });
});

describe("the sidebar's model choices", () => {
  it("group each provider's models in settings order, with why a provider listed none", () => {
    const detected = new Map<string, Detected>([["local", { error: "API connection failed (ECONNREFUSED)." }], ["deepseek", { models: ["deepseek-flash", "deepseek-v4-pro"] }]]);
    expect(modelGroups([deepseek, local], detected, { provider: "", model: "" })).toEqual([
      { provider: "deepseek", models: ["deepseek-flash", "deepseek-v4-pro"] },
      { provider: "local", models: [], error: "API connection failed (ECONNREFUSED)." },
    ]);
  });

  it("keep the model in use in its group when detection did not list it", () => {
    const detected = new Map<string, Detected>([["deepseek", { error: "API request failed (HTTP 503)." }]]);
    expect(modelGroups([deepseek], detected, { provider: "deepseek", model: "deepseek-flash" }))
      .toEqual([{ provider: "deepseek", models: ["deepseek-flash"], error: "API request failed (HTTP 503)." }]);
    expect(modelGroups([deepseek], new Map(), { provider: "gone", model: "x" })).toEqual([{ provider: "deepseek", models: [] }]);
  });
});

describe("the connection test", () => {
  const reply = (calls: string[]): ChatReply => ({
    model: "m", finish_reason: "tool_calls", usage: { prompt_tokens: 1, completion_tokens: 1 },
    message: { content: null, tool_calls: calls.map((name, index) => ({ id: String(index), type: "function", function: { name, arguments: "{}" } })) },
  });

  it("sends the agent's kind of request: a forced tool call with reasoning off", async () => {
    const requests: ChatRequest[] = [];
    const result = await connectionTest(async (request) => { requests.push(request); return reply(["ok"]); }, "deepseek-flash");
    expect(result).toMatch(/^Connected: deepseek-flash answered in \d+\.\d s with a tool call, as the agent needs\.$/);
    expect(requests[0]).toMatchObject({ tool_choice: { type: "function", function: { name: "ok" } }, reasoning_effort: "none" });
    expect(requests[0]!.tools!.map((tool) => tool.function.name)).toEqual(["ok"]);
  });

  it("says when the model answered without the tool call, or did not answer", async () => {
    await expect(connectionTest(async () => reply([]), "m")).resolves.toContain("but without the tool call the agent needs");
    await expect(connectionTest(async () => { throw new Error("API request failed (HTTP 401). Check the API key and access permissions."); }, "m"))
      .resolves.toBe("m did not answer: API request failed (HTTP 401). Check the API key and access permissions.");
  });
});
