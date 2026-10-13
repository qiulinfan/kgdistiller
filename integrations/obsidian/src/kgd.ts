/**
 * The plugin's connection to the local kgd command. Lookups go through one
 * resident `kgd mcp` process, kgd's read-only MCP server over stdio, which
 * keeps the embedding model loaded between queries; `kgd base list` and
 * `kgd index` run as short commands. kgd stays the only reader of the
 * kgdistiller home and its database: the plugin never opens either itself.
 */

import { execFile, spawn, type ChildProcessWithoutNullStreams } from "child_process";

export interface KgdSettings {
  /** Absolute path of the kgd executable; empty turns lookups off. */
  command: string;
  /** KGDISTILLER_HOME for kgd; empty keeps kgd's default home. */
  home: string;
}

export class KgdError extends Error {}

/** A stopped server is restarted by the next call; an idle one stops itself to release the model. */
export const IDLE_MS = 15 * 60 * 1000;
export const REQUEST_TIMEOUT_MS = 120 * 1000;
const INDEX_TIMEOUT_MS = 30 * 60 * 1000;
const MCP_PROTOCOL_VERSION = "2025-06-18";

/**
 * The environment of every kgd run: the inherited one, the configured home,
 * and an offline Hugging Face cache. Vectors exist only after a local
 * `kgd index` loaded the model, so the model is cached whenever dense search
 * can run.
 */
export function kgdEnvironment(home: string, inherited: NodeJS.ProcessEnv = process.env): NodeJS.ProcessEnv {
  const environment: NodeJS.ProcessEnv = { ...inherited, HF_HUB_OFFLINE: "1" };
  if (home.trim()) environment.KGDISTILLER_HOME = home.trim();
  return environment;
}

export type SpawnKgd = (command: string, args: string[], environment: NodeJS.ProcessEnv) => ChildProcessWithoutNullStreams;
export type RunKgd = (command: string, args: string[], environment: NodeJS.ProcessEnv, timeout: number) =>
  Promise<{ stdout: string; failed: boolean }>;

const spawnKgd: SpawnKgd = (command, args, environment) =>
  spawn(command, args, { env: environment, stdio: "pipe", windowsHide: true });

/** Run a short kgd command; a nonzero exit still returns kgd's JSON report on stdout. */
const runKgd: RunKgd = (command, args, environment, timeout) => new Promise((resolve, reject) => {
  execFile(command, args, { env: environment, timeout, maxBuffer: 64 * 1024 * 1024, windowsHide: true },
    (error, stdout, stderr) => {
      if (error && !stdout) reject(new KgdError(`${command} ${args.join(" ")}: ${stderr.trim() || error.message}`));
      else resolve({ stdout, failed: error !== null });
    });
});

interface ToolResult {
  structuredContent?: unknown;
  isError?: boolean;
}

interface Pending {
  resolve(result: unknown): void;
  reject(error: Error): void;
  timer: ReturnType<typeof setTimeout>;
}

export class KgdServer {
  private child: ChildProcessWithoutNullStreams | undefined;
  private started: Promise<void> | undefined;
  private buffer = "";
  private stderr = "";
  private nextId = 1;
  private readonly pending = new Map<number, Pending>();
  private idle: ReturnType<typeof setTimeout> | undefined;
  private bases: Promise<Map<string, string>> | undefined;

  constructor(
    private readonly settings: () => KgdSettings,
    private readonly spawnProcess: SpawnKgd = spawnKgd,
    private readonly runProcess: RunKgd = runKgd,
  ) {}

  /** Whether a kgd executable is configured. */
  configured(): boolean {
    return this.settings().command.trim() !== "";
  }

  /** Call one read-only tool of `kgd mcp` and return its JSON result. */
  async call<T>(tool: string, args: Record<string, unknown>): Promise<T> {
    this.clearIdle();
    try {
      await this.start();
      const result = await this.request("tools/call", { name: tool, arguments: args }) as ToolResult;
      if (result.isError) throw new KgdError(toolError(result.structuredContent) ?? `${tool} failed`);
      return result.structuredContent as T;
    } finally {
      this.armIdle();
    }
  }

  /** Every registered base's root by base name, from `kgd base list`; read once per server lifetime. */
  baseRoots(): Promise<Map<string, string>> {
    this.bases ??= this.run(["base", "list"], REQUEST_TIMEOUT_MS).then(({ stdout }) => {
      const listed = JSON.parse(stdout) as { bases?: Array<{ name?: unknown; root?: unknown }> };
      const roots = new Map<string, string>();
      for (const base of listed.bases ?? []) {
        if (typeof base.name === "string" && typeof base.root === "string") roots.set(base.name, base.root);
      }
      return roots;
    });
    this.bases.catch(() => { this.bases = undefined; });
    return this.bases;
  }

  /** Run `kgd index` and return its report; `failed` mirrors kgd's nonzero exit. */
  async index(): Promise<{ report: Record<string, unknown>; failed: boolean }> {
    const { stdout, failed } = await this.run(["index"], INDEX_TIMEOUT_MS);
    return { report: JSON.parse(stdout) as Record<string, unknown>, failed };
  }

  /** Stop the server and forget the base roots; the next call starts it with the current settings. */
  stop(): void {
    this.clearIdle();
    this.bases = undefined;
    const child = this.child;
    if (child) this.fail(child, "kgd mcp was stopped");
    child?.kill();
  }

  private run(args: string[], timeout: number): Promise<{ stdout: string; failed: boolean }> {
    const { command, home } = this.settings();
    if (!command.trim()) return Promise.reject(new KgdError("Set the kgd executable in the kgdistiller settings."));
    return this.runProcess(command.trim(), args, kgdEnvironment(home), timeout);
  }

  private start(): Promise<void> {
    if (this.started) return this.started;
    const { command, home } = this.settings();
    if (!command.trim()) return Promise.reject(new KgdError("Set the kgd executable in the kgdistiller settings."));
    let child: ChildProcessWithoutNullStreams;
    try {
      child = this.spawnProcess(command.trim(), ["mcp"], kgdEnvironment(home));
    } catch (error) {
      return Promise.reject(new KgdError(`cannot run ${command}: ${message(error)}`));
    }
    this.child = child;
    this.buffer = "";
    this.stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => this.receive(chunk));
    child.stderr.on("data", (chunk: string) => { this.stderr = (this.stderr + chunk).slice(-2000); });
    // A process that failed to start rejects writes; the "error" event below reports why.
    child.stdin.on("error", () => undefined);
    child.on("error", (error) => this.fail(child, `cannot run ${command}: ${error.message}`));
    child.on("exit", (code, signal) => this.fail(child, `kgd mcp exited (${signal ?? code ?? "unknown"})`));
    const started = this.request("initialize", {
      protocolVersion: MCP_PROTOCOL_VERSION,
      capabilities: {},
      clientInfo: { name: "kgdistiller-obsidian", version: "1" },
    }).then(
      () => this.send({ jsonrpc: "2.0", method: "notifications/initialized" }),
      (error: unknown) => {
        // A refused handshake leaves the process in an unknown state: drop it, so the next call starts afresh.
        if (this.child === child) {
          this.fail(child, `kgd mcp did not start: ${message(error)}`);
          child.kill();
        }
        throw error;
      },
    );
    this.started = started;
    return started;
  }

  private request(method: string, params: Record<string, unknown>): Promise<unknown> {
    const child = this.child;
    if (!child) return Promise.reject(new KgdError("kgd mcp is not running"));
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.fail(child, `kgd mcp did not answer ${method} within ${REQUEST_TIMEOUT_MS / 1000} s`);
        child.kill();
      }, REQUEST_TIMEOUT_MS);
      this.pending.set(id, { resolve, reject, timer });
      this.send({ jsonrpc: "2.0", id, method, params });
    });
  }

  private send(message: Record<string, unknown>): void {
    this.child?.stdin.write(`${JSON.stringify(message)}\n`);
  }

  private receive(chunk: string): void {
    this.buffer += chunk;
    let newline = this.buffer.indexOf("\n");
    while (newline >= 0) {
      const line = this.buffer.slice(0, newline).trim();
      this.buffer = this.buffer.slice(newline + 1);
      newline = this.buffer.indexOf("\n");
      if (!line) continue;
      let message: { id?: unknown; result?: unknown; error?: { message?: unknown } };
      try {
        message = JSON.parse(line) as typeof message;
      } catch {
        continue;
      }
      const pending = typeof message.id === "number" ? this.pending.get(message.id) : undefined;
      if (!pending) continue;
      this.pending.delete(message.id as number);
      clearTimeout(pending.timer);
      if (message.error) pending.reject(new KgdError(String(message.error.message ?? "kgd mcp error")));
      else pending.resolve(message.result);
    }
  }

  /** Reject every waiting request of this child and forget it, once. */
  private fail(child: ChildProcessWithoutNullStreams, reason: string): void {
    if (child !== this.child) return;
    this.child = undefined;
    this.started = undefined;
    const detail = this.stderr.trim();
    const error = new KgdError(detail ? `${reason}: ${detail}` : reason);
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }

  private armIdle(): void {
    this.clearIdle();
    if (this.child && this.pending.size === 0) this.idle = setTimeout(() => this.stop(), IDLE_MS);
  }

  private clearIdle(): void {
    if (this.idle !== undefined) clearTimeout(this.idle);
    this.idle = undefined;
  }
}

function toolError(content: unknown): string | undefined {
  const error = (content as { error?: { message?: unknown } } | undefined)?.error;
  return typeof error?.message === "string" ? error.message : undefined;
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
