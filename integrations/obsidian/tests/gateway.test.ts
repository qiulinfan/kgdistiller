import { EventEmitter } from "events";
import { PassThrough } from "stream";
import { afterEach, describe, expect, it, vi } from "vitest";

import { chatReply, GATEWAY_IDLE_MS, GatewayError, ModelGateway, type SpawnGateway } from "../src/gateway";

type Request = { id: number; path: string; body: Record<string, unknown> };

/** An `omp auth-gateway stdio` stand-in: a ready line, then one reply line per request, from `respond`. */
class FakeGateway extends EventEmitter {
  readonly stdin = new PassThrough();
  readonly stdout = new PassThrough();
  readonly stderr = new PassThrough();
  readonly received: Request[] = [];
  readonly kill = vi.fn(() => {
    this.emit("exit", null, "SIGTERM");
    return true;
  });

  constructor(respond: (request: Request, child: FakeGateway) => void, ready = true) {
    super();
    if (ready) setTimeout(() => this.stdout.write(`${JSON.stringify({ ready: true, version: "18.8.7" })}\n`), 0);
    let buffer = "";
    this.stdin.setEncoding("utf8");
    this.stdin.on("data", (chunk: string) => {
      buffer += chunk;
      for (let newline = buffer.indexOf("\n"); newline >= 0; newline = buffer.indexOf("\n")) {
        const request = JSON.parse(buffer.slice(0, newline)) as Request;
        buffer = buffer.slice(newline + 1);
        this.received.push(request);
        respond(request, this);
      }
    });
  }

  reply(id: number, status: number, body: unknown): void {
    this.stdout.write(`${JSON.stringify({ id, status, body })}\n`);
  }
}

const completion = (content: string) => ({
  model: "deepseek/deepseek-v4-flash",
  choices: [{ index: 0, message: { role: "assistant", content }, finish_reason: "stop" }],
  usage: { prompt_tokens: 11, completion_tokens: 1 },
});

function gateway(respond: (request: Request, child: FakeGateway) => void, settings = { command: " /bin/omp ", model: "@smol" }) {
  const children: FakeGateway[] = [];
  const spawn = vi.fn((command: string, args: string[]) => {
    const child = new FakeGateway(respond);
    children.push(child);
    void command;
    void args;
    return child as never;
  }) satisfies SpawnGateway;
  return { model: new ModelGateway(() => settings, spawn), children, spawn };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("the model gateway", () => {
  it("starts omp auth-gateway stdio once and sends chat completions with the configured model", async () => {
    const { model, children, spawn } = gateway((request, child) => child.reply(request.id, 200, completion("ok")));
    await expect(model.complete({ messages: [{ role: "user", content: "hi" }] })).resolves.toEqual({
      model: "deepseek/deepseek-v4-flash",
      message: { content: "ok", tool_calls: undefined },
      finish_reason: "stop",
      usage: { prompt_tokens: 11, completion_tokens: 1 },
    });
    await model.complete({ messages: [{ role: "user", content: "again" }], reasoning_effort: "none" });
    expect(spawn).toHaveBeenCalledTimes(1);
    expect(spawn).toHaveBeenCalledWith("/bin/omp", ["auth-gateway", "stdio"]);
    expect(children[0]!.received[1]).toEqual({
      id: 2, path: "/v1/chat/completions",
      body: { model: "@smol", messages: [{ role: "user", content: "again" }], reasoning_effort: "none" },
    });
    model.stop();
  });

  it("answers concurrent requests by id, in any order", async () => {
    const held: Request[] = [];
    const { model, children } = gateway((request, child) => {
      held.push(request);
      if (held.length === 2) for (const each of [...held].reverse()) child.reply(each.id, 200, completion(`answer ${each.id}`));
    });
    const [first, second] = await Promise.all([
      model.complete({ messages: [{ role: "user", content: "1" }] }),
      model.complete({ messages: [{ role: "user", content: "2" }] }),
    ]);
    expect(first.message.content).toBe("answer 1");
    expect(second.message.content).toBe("answer 2");
    expect(children).toHaveLength(1);
    model.stop();
  });

  it("reports a refused request with the gateway's message", async () => {
    const { model } = gateway((request, child) =>
      child.reply(request.id, 404, { error: { message: "No available model matches \"x\"", type: "invalid_request_error" } }));
    await expect(model.complete({ messages: [] })).rejects.toThrow("the model gateway refused the request: No available model matches \"x\"");
    model.stop();
  });

  it("rejects waiting requests when the gateway exits, and restarts on the next request", async () => {
    let calls = 0;
    const { model, children } = gateway((request, child) => {
      calls++;
      if (calls === 1) {
        child.stderr.write("provider error\n");
        setTimeout(() => child.emit("exit", 1, null), 5);
      } else child.reply(request.id, 200, completion("back"));
    });
    await expect(model.complete({ messages: [] })).rejects.toThrow("the model gateway exited (1): provider error");
    await expect(model.complete({ messages: [] })).resolves.toMatchObject({ message: { content: "back" } });
    expect(children).toHaveLength(2);
    model.stop();
  });

  it("refuses to start without an omp executable", async () => {
    const model = new ModelGateway(() => ({ command: " ", model: "@smol" }), vi.fn());
    expect(model.configured()).toBe(false);
    await expect(model.complete({ messages: [] })).rejects.toBeInstanceOf(GatewayError);
  });

  it("stops itself after an idle period", async () => {
    vi.useFakeTimers();
    const { model, children } = gateway((request, child) => child.reply(request.id, 200, completion("ok")));
    const answered = model.complete({ messages: [] });
    await vi.advanceTimersByTimeAsync(1);
    await answered;
    expect(children[0]!.kill).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(GATEWAY_IDLE_MS);
    expect(children[0]!.kill).toHaveBeenCalledTimes(1);
  });
});

describe("gateway replies", () => {
  it("read tool calls and fill missing usage with zeros", () => {
    const calls = [{ id: "c", type: "function", function: { name: "judge", arguments: "{}" } }];
    expect(chatReply({ id: 1, status: 200, body: { model: "m", choices: [{ message: { content: null, tool_calls: calls }, finish_reason: "tool_calls" }] } }))
      .toEqual({ model: "m", message: { content: null, tool_calls: calls }, finish_reason: "tool_calls", usage: { prompt_tokens: 0, completion_tokens: 0 } });
  });

  it("turn errors and empty choices into gateway errors", () => {
    expect(chatReply({ id: 1, status: 404, body: { error: "No route: POST /v1/x" } })).toEqual(new GatewayError("the model gateway refused the request: No route: POST /v1/x"));
    expect(chatReply({ id: 1, status: 500 })).toEqual(new GatewayError("the model gateway refused the request: status 500"));
    expect(chatReply({ id: 1, status: 200, body: { choices: [] } })).toEqual(new GatewayError("the model gateway returned no choice"));
  });
});
