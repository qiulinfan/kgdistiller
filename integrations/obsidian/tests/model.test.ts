import { describe, expect, it, vi } from "vitest";
import { chatReply, ModelClient, type ModelSettings } from "../src/model";
import { ApiError, type PostJson } from "../src/api";
import { runJob } from "../src/agent";

const settings: ModelSettings = { endpoint: "https://example.com/v1/chat/completions", apiKey: "test-key", model: "test-model", protocol: "chat-completions" };
const answer = (content = "ok") => ({ choices: [{ message: { content }, finish_reason: "stop" }] });

describe("direct model requests", () => {
  it("sends the configured model, tools and effort directly in one request", async () => {
    const post = vi.fn<PostJson>().mockResolvedValue(answer());
    const model = new ModelClient(() => settings, post);
    const request = { messages: [{ role: "user" as const, content: "question" }], reasoning_effort: "none" as const, tool_choice: "required" as const };
    expect(model.configured()).toBe(true);
    await expect(model.complete(request)).resolves.toMatchObject({ message: { content: "ok" } });
    expect(post).toHaveBeenCalledExactlyOnceWith(settings.endpoint, "test-key", { ...request, model: "test-model", stream: false }, 120000);
  });

  it("uses fresh settings on every request and allows unauthenticated local endpoints", async () => {
    const current = { ...settings };
    const post = vi.fn<PostJson>().mockResolvedValue(answer());
    const model = new ModelClient(() => current, post);
    await model.complete({ messages: [] });
    Object.assign(current, { endpoint: "http://localhost:1234/v1/chat/completions", apiKey: "", model: "local" });
    await model.complete({ messages: [] });
    expect(post.mock.calls[1]).toEqual([current.endpoint, "", { messages: [], model: "local", stream: false }, 120000]);
  });

  it("refuses incomplete configuration before making a request", async () => {
    const post = vi.fn();
    for (const incomplete of [{ ...settings, endpoint: "" }, { ...settings, model: "" }]) {
      const model = new ModelClient(() => incomplete, post);
      expect(model.configured()).toBe(false);
      await expect(model.complete({ messages: [] })).rejects.toThrow("Add a model provider in the kgdistiller settings and choose a model");
    }
    expect(post).not.toHaveBeenCalled();
  });

  it("maps DeepSeek thinking controls and carries reasoning through a tool round", async () => {
    const post = vi.fn()
      .mockResolvedValueOnce({ choices: [{ message: { content: null, reasoning_content: "intermediate", tool_calls: [
        { id: "1", type: "function", function: { name: "read", arguments: "{}" } },
      ] } }] })
      .mockResolvedValueOnce({ choices: [{ message: { content: null, tool_calls: [
        { id: "2", type: "function", function: { name: "answer", arguments: "{}" } },
      ] } }] })
      .mockResolvedValueOnce(answer());
    const model = new ModelClient(() => ({ ...settings, protocol: "deepseek" }), post);
    const spec = (name: string) => ({ type: "function" as const, function: { name, description: "", parameters: {} } });
    await expect(runJob((request) => model.complete(request), {
      system: "test", user: "test", turns: 2, effort: "low", first: "read", last: "answer", tools: [
        { spec: spec("read"), run: async () => "read result" },
        { spec: spec("answer"), run: async () => ({ done: "done" }) },
      ],
    })).resolves.toMatchObject({ value: "done", turns: 2 });
    expect(post.mock.calls[0]![2]).toMatchObject({ thinking: { type: "enabled" }, reasoning_effort: "low" });
    expect(post.mock.calls[0]![2]).toMatchObject({ tool_choice: "auto", tools: [spec("read")] });
    expect(post.mock.calls[1]![2]).toMatchObject({ tool_choice: "auto", tools: [spec("answer")] });
    expect(post.mock.calls[1]![2].messages).toContainEqual(expect.objectContaining({ role: "assistant", reasoning_content: "intermediate" }));
    await model.complete({ messages: [], reasoning_effort: "none" });
    expect(post.mock.calls[2]![2]).toMatchObject({ thinking: { type: "disabled" } });
    expect(post.mock.calls[2]![2]).not.toHaveProperty("reasoning_effort");
  });
});

describe("a busy model service", () => {
  const busy = (status: number) => new ApiError(`API request failed (HTTP ${status}).`, status);

  it("is asked again after 1 s and 2 s while it answers 503 or 429", async () => {
    const post = vi.fn<PostJson>().mockRejectedValueOnce(busy(503)).mockRejectedValueOnce(busy(429)).mockResolvedValue(answer());
    const waits: number[] = [];
    const model = new ModelClient(() => settings, post, async (ms) => { waits.push(ms); });
    await expect(model.complete({ messages: [] })).resolves.toMatchObject({ message: { content: "ok" } });
    expect(post).toHaveBeenCalledTimes(3);
    expect(waits).toEqual([1000, 2000]);
  });

  it("is reported as busy when it still refuses after two retries", async () => {
    const post = vi.fn<PostJson>().mockRejectedValue(busy(503));
    const waits: number[] = [];
    const model = new ModelClient(() => settings, post, async (ms) => { waits.push(ms); });
    await expect(model.complete({ messages: [] })).rejects.toThrow(
      "The model service is busy (HTTP 503): it refused the request and 2 retries. Try again in a moment");
    expect(post).toHaveBeenCalledTimes(3);
    expect(waits).toEqual([1000, 2000]);
    await expect(new ModelClient(() => settings, vi.fn<PostJson>().mockRejectedValue(busy(429)), async () => undefined)
      .complete({ messages: [] })).rejects.toThrow("The model service is busy (HTTP 429)");
  });

  it("is reported in its own words when it gave them, since 429 also means an exhausted balance", async () => {
    const refusal = new ApiError("API request failed (HTTP 429): 余额不足或无可用资源包,请充值。", 429, "余额不足或无可用资源包,请充值。");
    await expect(new ModelClient(() => settings, vi.fn<PostJson>().mockRejectedValue(refusal), async () => undefined).complete({ messages: [] }))
      .rejects.toThrow("The model service refused the request and 2 retries (HTTP 429): 余额不足或无可用资源包,请充值。");
  });

  it("is not asked again after any other failure", async () => {
    for (const error of [busy(500), busy(401), new ApiError("API request timed out after 120 s."), new Error("The model API returned no message.")]) {
      const post = vi.fn<PostJson>().mockRejectedValue(error);
      const pause = vi.fn(async () => undefined);
      await expect(new ModelClient(() => settings, post, pause).complete({ messages: [] })).rejects.toBe(error);
      expect(post).toHaveBeenCalledOnce();
      expect(pause).not.toHaveBeenCalled();
    }
  });

  it("is asked again with the current settings", async () => {
    const current = { ...settings };
    const post = vi.fn<PostJson>()
      .mockImplementationOnce(async () => { Object.assign(current, { model: "other" }); throw busy(503); })
      .mockResolvedValue(answer());
    await new ModelClient(() => current, post, async () => undefined).complete({ messages: [] });
    expect(post.mock.calls.map((call) => (call[2] as { model: string }).model)).toEqual(["test-model", "other"]);
  });
});

describe("model reply validation", () => {
  it.each([null, {}, { choices: [] }, { choices: [{ message: { content: 9 } }] }, { choices: [{ message: { tool_calls: {} } }] },
    { choices: [{ message: { tool_calls: [{ id: "x", type: "function", function: { name: "read", arguments: {} } }] } }] },
  ])("refuses malformed replies before executing a tool: %j", (value) => {
    expect(() => chatReply(value)).toThrow();
  });
  it("refuses repeated tool-call IDs", () => {
    const call = { id: "x", type: "function", function: { name: "read", arguments: "{}" } };
    expect(() => chatReply({ choices: [{ message: { tool_calls: [call, call] } }] })).toThrow("invalid tool call");
  });
});
