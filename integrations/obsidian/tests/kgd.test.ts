import { EventEmitter } from "events";
import { PassThrough } from "stream";
import { afterEach, describe, expect, it, vi } from "vitest";

import { IDLE_MS, KgdError, KgdServer, kgdEnvironment, runKgd, type RunKgd, type SpawnKgd } from "../src/kgd";

type Message = { jsonrpc: string; id?: number; method: string; params?: Record<string, unknown> };
type Responder = (message: Message) => Record<string, unknown> | undefined;

/** A `kgd mcp` stand-in: newline-delimited JSON-RPC on stdio, answered by `respond`. */
class FakeChild extends EventEmitter {
  readonly stdin = new PassThrough();
  readonly stdout = new PassThrough();
  readonly stderr = new PassThrough();
  readonly received: Message[] = [];
  readonly kill = vi.fn(() => {
    this.emit("exit", null, "SIGTERM");
    return true;
  });

  constructor(respond: Responder) {
    super();
    let buffer = "";
    this.stdin.setEncoding("utf8");
    this.stdin.on("data", (chunk: string) => {
      buffer += chunk;
      for (let newline = buffer.indexOf("\n"); newline >= 0; newline = buffer.indexOf("\n")) {
        const message = JSON.parse(buffer.slice(0, newline)) as Message;
        buffer = buffer.slice(newline + 1);
        this.received.push(message);
        const reply = respond(message);
        if (reply) this.stdout.write(`${JSON.stringify({ jsonrpc: "2.0", id: message.id, ...reply })}\n`);
      }
    });
  }
}

const tools: Responder = (message) => {
  if (message.method === "initialize") return { result: { protocolVersion: "2025-06-18", capabilities: {} } };
  if (message.method !== "tools/call") return undefined;
  const name = message.params?.name;
  if (name === "kg_fail") return { result: { isError: true, structuredContent: { error: { message: "unknown tool: kg_fail" } } } };
  return { result: { isError: false, structuredContent: { tool: name, arguments: message.params?.arguments } } };
};

function server(settings = { command: "/bin/kgd", home: "" }, respond: Responder = tools) {
  const children: FakeChild[] = [];
  const spawned: Array<{ command: string; args: string[]; environment: NodeJS.ProcessEnv }> = [];
  const spawn = vi.fn((command: string, args: string[], environment: NodeJS.ProcessEnv) => {
    const child = new FakeChild(respond);
    children.push(child);
    spawned.push({ command, args, environment });
    return child as never;
  }) satisfies SpawnKgd;
  const run = vi.fn(async (_command: string, args: string[], _environment: NodeJS.ProcessEnv, _timeout: number) => {
    if (args[0] === "base") return { stdout: JSON.stringify({ bases: [{ name: "notes", root: "/vaults/notes" }, { name: 3 }] }), failed: false };
    return { stdout: JSON.stringify({ bases: {}, embedded: 2 }), failed: true };
  }) satisfies RunKgd;
  return { kgd: new KgdServer(() => settings, spawn, run), children, spawned, spawn, run };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("the kgd environment", () => {
  it("keeps the inherited environment, works offline and sets the home only when configured", () => {
    expect(kgdEnvironment("", { PATH: "/usr/bin" })).toEqual({ PATH: "/usr/bin", HF_HUB_OFFLINE: "1" });
    expect(kgdEnvironment(" /tmp/home ", {})).toEqual({ HF_HUB_OFFLINE: "1", KGDISTILLER_HOME: "/tmp/home" });
    // Only the download of a model the owner chose runs online; an offline setting the owner made stays.
    expect(kgdEnvironment("/tmp/home", { PATH: "/usr/bin" }, true)).toEqual({ PATH: "/usr/bin", KGDISTILLER_HOME: "/tmp/home" });
    expect(kgdEnvironment("", { HF_HUB_OFFLINE: "1" }, true)).toEqual({ HF_HUB_OFFLINE: "1" });
  });
});

describe("the resident kgd mcp server", () => {
  it("starts kgd mcp once, completes the handshake and returns each tool's structured content", async () => {
    const { kgd, children, spawned } = server({ command: " /bin/kgd ", home: "/tmp/home" });
    await expect(kgd.call("kg_resolve", { terms: ["autoencoder"] }))
      .resolves.toEqual({ tool: "kg_resolve", arguments: { terms: ["autoencoder"] } });
    await kgd.call("kg_search", { query: "masked autoencoder" });
    expect(spawned).toHaveLength(1);
    expect(spawned[0]).toMatchObject({ command: "/bin/kgd", args: ["mcp"] });
    expect(spawned[0]!.environment).toMatchObject({ KGDISTILLER_HOME: "/tmp/home", HF_HUB_OFFLINE: "1" });
    expect(children[0]!.received.map((message) => message.method))
      .toEqual(["initialize", "notifications/initialized", "tools/call", "tools/call"]);
    kgd.stop();
  });

  it("reports a tool error as a KgdError and keeps the server", async () => {
    const { kgd, spawned } = server();
    await expect(kgd.call("kg_fail", {})).rejects.toThrow(new KgdError("unknown tool: kg_fail"));
    await kgd.call("kg_get", { uids: ["notes:x"] });
    expect(spawned).toHaveLength(1);
    kgd.stop();
  });

  it("refuses without spawning when no executable is configured", async () => {
    const { kgd, spawn } = server({ command: "  ", home: "" });
    expect(kgd.configured()).toBe(false);
    await expect(kgd.call("kg_search", { query: "x" })).rejects.toThrow("Set the kgd executable");
    expect(spawn).not.toHaveBeenCalled();
  });

  it("rejects waiting calls with kgd's stderr when the process dies, then starts afresh", async () => {
    let die = false;
    const { kgd, children, spawned } = server(undefined, (message) => (die && message.method === "tools/call" ? undefined : tools(message)));
    await kgd.call("kg_get", { uids: ["notes:x"] });
    die = true;
    const waiting = kgd.call("kg_search", { query: "x" });
    await vi.waitFor(() => expect(children[0]!.received).toHaveLength(4));
    children[0]!.stderr.write("Traceback: model failed\n");
    await new Promise((resolve) => setImmediate(resolve));
    children[0]!.emit("exit", 1, null);
    await expect(waiting).rejects.toThrow("kgd mcp exited (1): Traceback: model failed");
    die = false;
    await kgd.call("kg_get", { uids: ["notes:x"] });
    expect(spawned).toHaveLength(2);
    kgd.stop();
  });

  it("reports an executable that cannot start", async () => {
    const { kgd, children } = server(undefined, () => undefined);
    const call = kgd.call("kg_search", { query: "x" });
    await vi.waitFor(() => expect(children).toHaveLength(1));
    children[0]!.emit("error", new Error("spawn /bin/kgd ENOENT"));
    await expect(call).rejects.toThrow("cannot run /bin/kgd: spawn /bin/kgd ENOENT");
  });

  it("stops after IDLE_MS without calls and restarts on the next call", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const { kgd, children, spawned } = server();
    await kgd.call("kg_get", { uids: ["notes:x"] });
    vi.advanceTimersByTime(IDLE_MS - 1);
    expect(children[0]!.kill).not.toHaveBeenCalled();
    await kgd.call("kg_get", { uids: ["notes:x"] });
    vi.advanceTimersByTime(IDLE_MS - 1);
    expect(children[0]!.kill).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(children[0]!.kill).toHaveBeenCalledOnce();
    await kgd.call("kg_get", { uids: ["notes:x"] });
    expect(spawned).toHaveLength(2);
    kgd.stop();
  });

  it("stop rejects waiting calls and kills the process", async () => {
    const { kgd, children } = server(undefined, (message) => (message.method === "tools/call" ? undefined : tools(message)));
    const waiting = kgd.call("kg_search", { query: "x" });
    await vi.waitFor(() => expect(children[0]!.received).toHaveLength(3));
    kgd.stop();
    await expect(waiting).rejects.toThrow("kgd mcp was stopped");
    expect(children[0]!.kill).toHaveBeenCalledOnce();
  });
});

describe("short kgd commands", () => {
  it("reads the base roots once and keeps only well-formed entries", async () => {
    const { kgd, run } = server({ command: "/bin/kgd", home: "/tmp/home" });
    await expect(kgd.baseRoots()).resolves.toEqual(new Map([["notes", "/vaults/notes"]]));
    await kgd.baseRoots();
    expect(run).toHaveBeenCalledOnce();
    const [command, args, environment] = run.mock.calls[0]!;
    expect(command).toBe("/bin/kgd");
    expect(args).toEqual(["base", "list"]);
    expect(environment).toMatchObject({ HF_HUB_OFFLINE: "1", KGDISTILLER_HOME: "/tmp/home" });
    kgd.stop();
    await kgd.baseRoots();
    expect(run).toHaveBeenCalledTimes(2);
  });

  it("returns the index report together with kgd's failure flag", async () => {
    const { kgd } = server();
    await expect(kgd.index()).resolves.toEqual({ report: { bases: {}, embedded: 2 }, failed: true });
  });
});

describe("a short kgd command", () => {
  /** Run a Node script as kgd would run: the same exit codes and streams. */
  const script = (code: string) => runKgd(process.execPath, ["-e", code], process.env, 10_000);

  it("returns a refusing report printed with a nonzero exit", async () => {
    await expect(script(`process.stdout.write(JSON.stringify({ refused: { message: "no match" } })); process.exit(1);`))
      .resolves.toEqual({ stdout: "{\"refused\":{\"message\":\"no match\"}}", failed: true });
  });

  it("rejects with kgd's message when it prints only the error object on stderr", async () => {
    const error = JSON.stringify({ kind: "kgdistiller-error", code: "command-failed", message: "base notes already has a record mae" }, null, 2);
    await expect(script(`process.stderr.write(${JSON.stringify(error)}); process.exit(1);`))
      .rejects.toThrow(new KgdError("base notes already has a record mae"));
  });

  it("rejects with what a crashed kgd wrote", async () => {
    await expect(script(`process.stderr.write("usage: kgd\\n"); process.exit(2);`)).rejects.toThrow(/: usage: kgd$/);
  });
});
