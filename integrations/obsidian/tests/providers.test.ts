import { describe, expect, it, vi } from "vitest";

import { ApiError, type GetJson } from "../src/api";
import { REASONING_LEVELS, type ChatReply, type ChatRequest } from "../src/model";
import {
  baseUrlOf,
  chatUrl,
  connectionReport,
  formatOf,
  kindOf,
  listModels,
  modelGroups,
  modelIds,
  modelsUrl,
  newProviderName,
  probeLevels,
  PROVIDER_KINDS,
  storedLevels,
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
    // Zhipu turns thinking off as DeepSeek does: GLM-4.5 Flash ignores reasoning_effort none.
    expect(formatOf({ ...deepseek, kind: "zhipu" })).toBe("deepseek");
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

describe("reasoning level detection", () => {
  const reply = (calls: string[]): ChatReply => ({
    model: "m", finish_reason: "tool_calls", usage: { prompt_tokens: 1, completion_tokens: 1 },
    message: { content: null, tool_calls: calls.map((name, index) => ({ id: String(index), type: "function", function: { name, arguments: "{}" } })) },
  });
  const refusal = (status: number, level: string) => new ApiError(`API request failed (HTTP ${status}): ${level} is not supported.`, status);

  it("asks the agent's kind of request at each level, two at a time, and keeps the levels the service did not refuse", async () => {
    const requests: ChatRequest[] = [];
    let running = 0;
    let most = 0;
    const probe = await probeLevels(async (request) => {
      requests.push(request);
      most = Math.max(most, ++running);
      await new Promise((resolve) => setTimeout(resolve, 1));
      running--;
      const level = request.reasoning_effort!;
      if (level === "minimal") throw refusal(422, level);
      if (!["low", "high", "max"].includes(level)) throw refusal(400, level);
      return reply(level === "max" ? [] : ["ok"]);
    });
    expect(requests.map((request) => request.reasoning_effort)).toEqual([...REASONING_LEVELS]);
    expect(requests[0]).toMatchObject({ tool_choice: { type: "function", function: { name: "ok" } } });
    expect(requests[0]!.tools!.map((tool) => tool.function.name)).toEqual(["ok"]);
    expect(most).toBe(2);
    expect(probe.answers.map(({ level, tool }) => ({ level, tool })))
      .toEqual([{ level: "low", tool: true }, { level: "high", tool: true }, { level: "max", tool: false }]);
    expect(probe.refusal).toBe("API request failed (HTTP 400): none is not supported.");
  });

  it("stops at the first failure that is not a refusal", async () => {
    const denied = new ApiError("API request failed (HTTP 401). Check the API key and access permissions.", 401);
    const complete = vi.fn(async (): Promise<ChatReply> => { throw denied; });
    await expect(probeLevels(complete)).rejects.toBe(denied);
    expect(complete).toHaveBeenCalledTimes(2);
  });

  it("is reported with the accepted levels and the answer at the level judgments use", () => {
    expect(connectionReport("glm-5.3-flash", { answers: [{ level: "low", seconds: 1.04, tool: true }, { level: "high", seconds: 2, tool: true }], refusal: "" }))
      .toBe("Connected: glm-5.3-flash accepts reasoning low, high. At reasoning low it answered in 1.0 s with the tool call the agent needs.");
    expect(connectionReport("m", { answers: [{ level: "none", seconds: 0.5, tool: false }], refusal: "" }))
      .toBe("Connected: m accepts reasoning none. At reasoning none it answered in 0.5 s but without the tool call the agent needs.");
    expect(connectionReport("m", { answers: [], refusal: "API request failed (HTTP 400): model not found." }))
      .toBe("m refused the agent's kind of request at every reasoning level: API request failed (HTTP 400): model not found.");
  });

  it("is stored for each provider's model once, with known levels only", () => {
    const glm = { provider: "glm", model: "glm-5.3-flash", levels: ["low", "high", "max"] };
    const none = { provider: "glm", model: "glm-4.5-flash", levels: [] };
    expect(storedLevels([glm, { ...glm, levels: ["low"] }, { ...glm, model: "x", levels: ["turbo"] }, { provider: 1, model: "y", levels: [] }, null, none]))
      .toEqual([glm, none]);
    expect(storedLevels({ glm })).toEqual([]);
  });
});
