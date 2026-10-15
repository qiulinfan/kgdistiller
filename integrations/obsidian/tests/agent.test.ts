import { describe, expect, it, vi } from "vitest";

import { AgentError, runJob, type Complete, type Tool } from "../src/agent";
import type { ChatReply, ChatRequest, ToolCall } from "../src/model";

function call(name: string, args: unknown, id = name): ToolCall {
  return { id, type: "function", function: { name, arguments: typeof args === "string" ? args : JSON.stringify(args) } };
}

function reply(calls: ToolCall[], content: string | null = null): ChatReply {
  return { message: { content, tool_calls: calls } };
}

/** A model that answers with the scripted replies in order and records each request. */
function scripted(...replies: ChatReply[]): { complete: Complete; requests: ChatRequest[] } {
  const requests: ChatRequest[] = [];
  const complete = vi.fn(async (request: ChatRequest) => {
    requests.push(structuredClone(request));
    const next = replies.shift();
    if (!next) throw new Error("no more replies");
    return next;
  });
  return { complete, requests };
}

const lookup: Tool<string> = {
  spec: { type: "function", function: { name: "lookup", description: "", parameters: { type: "object" } } },
  run: async (args) => `found ${String(args.name)}`,
};
const submit: Tool<string> = {
  spec: { type: "function", function: { name: "submit", description: "", parameters: { type: "object" } } },
  run: async (args) => (args.answer === "good" ? { done: "filed" } : "error: answer must be good"),
};

describe("the agent loop", () => {
  it("returns the final tool's value and the tool results the model saw", async () => {
    const { complete, requests } = scripted(reply([call("lookup", { name: "MAE" })]), reply([call("submit", { answer: "good" })]));
    await expect(runJob(complete, { system: "S", user: "U", tools: [lookup, submit], turns: 3 })).resolves.toBe("filed");
    expect(requests).toHaveLength(2);
    expect(requests[0]).toMatchObject({ tool_choice: "required", reasoning_effort: "none" });
    expect(requests[0]!.messages).toEqual([{ role: "system", content: "S" }, { role: "user", content: "U" }]);
    expect(requests[1]!.messages.slice(2)).toEqual([
      { role: "assistant", content: null, tool_calls: [call("lookup", { name: "MAE" })] },
      { role: "tool", tool_call_id: "lookup", content: "found MAE" },
    ]);
  });

  it("forces the first tool and passes a reasoning effort", async () => {
    const { complete, requests } = scripted(reply([call("submit", { answer: "good" })]));
    await runJob(complete, { system: "S", user: "U", tools: [lookup, submit], turns: 1, first: "submit", effort: "low" });
    expect(requests[0]).toMatchObject({ tool_choice: { type: "function", function: { name: "submit" } }, reasoning_effort: "low" });
  });

  it("returns refusals, unknown tools and malformed arguments to the model", async () => {
    const { complete, requests } = scripted(
      reply([call("submit", { answer: "bad" }, "a"), call("missing", {}, "b"), call("submit", "{not json", "c")]),
      reply([call("submit", { answer: "good" })]),
    );
    await expect(runJob(complete, { system: "S", user: "U", tools: [submit], turns: 2 })).resolves.toBe("filed");
    expect(requests[1]!.messages.slice(3)).toEqual([
      { role: "tool", tool_call_id: "a", content: "error: answer must be good" },
      { role: "tool", tool_call_id: "b", content: "error: there is no tool named missing" },
      { role: "tool", tool_call_id: "c", content: "error: the arguments are not valid JSON" },
    ]);
  });

  it("reports a thrown tool error as the tool's result", async () => {
    const failing: Tool<string> = { spec: lookup.spec, run: async () => { throw new Error("kgd mcp exited"); } };
    const { complete, requests } = scripted(reply([call("lookup", {})]), reply([call("submit", { answer: "good" })]));
    await runJob(complete, { system: "S", user: "U", tools: [failing, submit], turns: 2 });
    expect(requests[1]!.messages.at(-1)).toEqual({ role: "tool", tool_call_id: "lookup", content: "error: kgd mcp exited" });
  });

  it("reminds a model that answers in text to use the tools", async () => {
    const { complete, requests } = scripted(reply([], "I think it is new."), reply([call("submit", { answer: "good" })]));
    await runJob(complete, { system: "S", user: "U", tools: [submit], turns: 2 });
    expect(requests[1]!.messages.slice(2)).toEqual([
      { role: "assistant", content: "I think it is new." },
      { role: "user", content: "Answer only by calling one of the tools." },
    ]);
  });

  it("runs a turn's read-only calls together, and the rest in order", async () => {
    const order: string[] = [];
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const slow: Tool<string> = {
      spec: { type: "function", function: { name: "slow", description: "", parameters: { type: "object" } } },
      concurrent: true,
      run: async () => { order.push("slow start"); await gate; order.push("slow end"); return "slow"; },
    };
    const fast: Tool<string> = {
      spec: { type: "function", function: { name: "fast", description: "", parameters: { type: "object" } } },
      concurrent: true,
      run: async () => { order.push("fast"); release(); return "fast"; },
    };
    const { complete, requests } = scripted(reply([call("slow", {}, "a"), call("fast", {}, "b")]), reply([call("submit", { answer: "good" })]));
    await runJob(complete, { system: "S", user: "U", tools: [slow, fast, submit], turns: 2 });
    expect(order).toEqual(["slow start", "fast", "slow end"]);
    expect(requests[1]!.messages.slice(3)).toEqual([
      { role: "tool", tool_call_id: "a", content: "slow" },
      { role: "tool", tool_call_id: "b", content: "fast" },
    ]);
  });

  it("fails after its turn limit", async () => {
    const { complete } = scripted(reply([call("submit", { answer: "bad" })]), reply([call("submit", { answer: "bad" })]));
    const job = runJob(complete, { system: "S", user: "U", tools: [submit], turns: 2 });
    await expect(job).rejects.toBeInstanceOf(AgentError);
    await expect(job).rejects.toThrow("the agent did not finish within 2 model calls");
  });
});
