/**
 * The plugin's model channel: one resident `omp auth-gateway stdio`, OMP's
 * OpenAI-compatible gateway speaking JSON lines on stdio. OMP holds the
 * provider credentials and maps role aliases such as `@smol` to its configured
 * models, so the plugin stores no API key and never talks to a provider
 * itself. Requests are answered by id, so several may be in flight at once.
 */

import { spawn, type ChildProcessWithoutNullStreams } from "child_process";

export interface GatewaySettings {
  /** Absolute path of the omp executable; empty turns the agent off. */
  command: string;
  /** A model OMP knows, or one of its role aliases such as `@smol`. */
  model: string;
}

export class GatewayError extends Error {}

export const GATEWAY_IDLE_MS = 15 * 60 * 1000;
export const GATEWAY_TIMEOUT_MS = 120 * 1000;

export interface ToolCall {
  id: string;
  type: "function";
  function: { name: string; arguments: string };
}

export type ChatMessage =
  | { role: "system" | "user"; content: string }
  | { role: "assistant"; content: string | null; tool_calls?: ToolCall[] }
  | { role: "tool"; tool_call_id: string; content: string };

export interface ToolSpec {
  type: "function";
  function: { name: string; description: string; parameters: Record<string, unknown> };
}

export interface ChatRequest {
  messages: ChatMessage[];
  tools?: ToolSpec[];
  /** "required" makes the model call some tool; a named tool forces that one. */
  tool_choice?: "auto" | "required" | { type: "function"; function: { name: string } };
  /** "none" keeps the model's reasoning off; a level turns it on. */
  reasoning_effort?: "none" | "low" | "medium" | "high";
  max_tokens?: number;
}

export interface Usage {
  prompt_tokens: number;
  completion_tokens: number;
}

export interface ChatReply {
  model: string;
  message: { content: string | null; tool_calls?: ToolCall[] };
  finish_reason: string;
  usage: Usage;
}

export type SpawnGateway = (command: string, args: string[]) => ChildProcessWithoutNullStreams;

const spawnGateway: SpawnGateway = (command, args) => spawn(command, args, { stdio: "pipe", windowsHide: true });

interface Pending<T> {
  resolve(value: T): void;
  reject(error: Error): void;
  timer: ReturnType<typeof setTimeout>;
}

interface GatewayLine {
  ready?: boolean;
  id?: unknown;
  status?: unknown;
  body?: unknown;
}

export class ModelGateway {
  private child: ChildProcessWithoutNullStreams | undefined;
  private started: Promise<void> | undefined;
  private startup: Pending<void> | undefined;
  private buffer = "";
  private stderr = "";
  private nextId = 1;
  private readonly pending = new Map<number, Pending<ChatReply>>();
  private idle: ReturnType<typeof setTimeout> | undefined;

  constructor(
    private readonly settings: () => GatewaySettings,
    private readonly spawnProcess: SpawnGateway = spawnGateway,
  ) {}

  /** Whether an omp executable is configured. */
  configured(): boolean {
    return this.settings().command.trim() !== "";
  }

  /** One chat completion with the configured model. */
  async complete(request: ChatRequest): Promise<ChatReply> {
    this.clearIdle();
    try {
      await this.start();
      return await this.request(request);
    } finally {
      this.armIdle();
    }
  }

  /** Stop the gateway; the next request starts it with the current settings. */
  stop(): void {
    this.clearIdle();
    const child = this.child;
    if (child) this.fail(child, "the model gateway was stopped");
    child?.kill();
  }

  private start(): Promise<void> {
    if (this.started) return this.started;
    const command = this.settings().command.trim();
    if (!command) return Promise.reject(new GatewayError("Set the omp executable in the kgdistiller settings."));
    let child: ChildProcessWithoutNullStreams;
    try {
      child = this.spawnProcess(command, ["auth-gateway", "stdio"]);
    } catch (error) {
      return Promise.reject(new GatewayError(`cannot run ${command}: ${message(error)}`));
    }
    this.child = child;
    this.buffer = "";
    this.stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => this.receive(chunk));
    child.stderr.on("data", (chunk: string) => { this.stderr = (this.stderr + chunk).slice(-2000); });
    child.stdin.on("error", () => undefined);
    child.on("error", (error) => this.fail(child, `cannot run ${command}: ${error.message}`));
    child.on("exit", (code, signal) => this.fail(child, `the model gateway exited (${signal ?? code ?? "unknown"})`));
    // The gateway announces itself with a ready line before it reads requests.
    this.started = new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.fail(child, `the model gateway did not start within ${GATEWAY_TIMEOUT_MS / 1000} s`);
        child.kill();
      }, GATEWAY_TIMEOUT_MS);
      this.startup = { resolve, reject, timer };
    });
    return this.started;
  }

  private request(request: ChatRequest): Promise<ChatReply> {
    const child = this.child;
    if (!child) return Promise.reject(new GatewayError("the model gateway is not running"));
    const id = this.nextId++;
    const body = { model: this.settings().model.trim(), ...request };
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new GatewayError(`the model did not answer within ${GATEWAY_TIMEOUT_MS / 1000} s`));
      }, GATEWAY_TIMEOUT_MS);
      this.pending.set(id, { resolve, reject, timer });
      child.stdin.write(`${JSON.stringify({ id, path: "/v1/chat/completions", body })}\n`);
    });
  }

  private receive(chunk: string): void {
    this.buffer += chunk;
    let newline = this.buffer.indexOf("\n");
    while (newline >= 0) {
      const line = this.buffer.slice(0, newline).trim();
      this.buffer = this.buffer.slice(newline + 1);
      newline = this.buffer.indexOf("\n");
      if (!line) continue;
      let parsed: GatewayLine;
      try {
        parsed = JSON.parse(line) as GatewayLine;
      } catch {
        continue;
      }
      if (parsed.ready === true) {
        const startup = this.startup;
        this.startup = undefined;
        if (startup) {
          clearTimeout(startup.timer);
          startup.resolve();
        }
        continue;
      }
      const pending = typeof parsed.id === "number" ? this.pending.get(parsed.id) : undefined;
      if (!pending) continue;
      this.pending.delete(parsed.id as number);
      clearTimeout(pending.timer);
      const reply = chatReply(parsed);
      if (reply instanceof GatewayError) pending.reject(reply);
      else pending.resolve(reply);
    }
  }

  /** Reject every waiting request of this child and forget it, once. */
  private fail(child: ChildProcessWithoutNullStreams, reason: string): void {
    if (child !== this.child) return;
    this.child = undefined;
    this.started = undefined;
    const detail = this.stderr.trim();
    const error = new GatewayError(detail ? `${reason}: ${detail}` : reason);
    const startup = this.startup;
    this.startup = undefined;
    for (const pending of [...(startup ? [startup] : []), ...this.pending.values()]) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }

  private armIdle(): void {
    this.clearIdle();
    if (this.child && this.pending.size === 0) this.idle = setTimeout(() => this.stop(), GATEWAY_IDLE_MS);
  }

  private clearIdle(): void {
    if (this.idle !== undefined) clearTimeout(this.idle);
    this.idle = undefined;
  }
}

/** The reply of one gateway line, or the error it reports. */
export function chatReply(line: GatewayLine): ChatReply | GatewayError {
  const body = line.body as {
    error?: unknown;
    model?: unknown;
    choices?: Array<{ message?: ChatReply["message"]; finish_reason?: unknown }>;
    usage?: Partial<Usage>;
  } | undefined;
  if (line.status !== 200 || !body || typeof body !== "object") {
    const error = body && typeof body === "object" ? body.error : body;
    const text = typeof error === "string" ? error
      : typeof (error as { message?: unknown } | undefined)?.message === "string" ? (error as { message: string }).message
        : `status ${String(line.status)}`;
    return new GatewayError(`the model gateway refused the request: ${text}`);
  }
  const choice = body.choices?.[0];
  if (!choice?.message) return new GatewayError("the model gateway returned no choice");
  return {
    model: typeof body.model === "string" ? body.model : "",
    message: { content: choice.message.content ?? null, tool_calls: choice.message.tool_calls },
    finish_reason: typeof choice.finish_reason === "string" ? choice.finish_reason : "",
    usage: { prompt_tokens: body.usage?.prompt_tokens ?? 0, completion_tokens: body.usage?.completion_tokens ?? 0 },
  };
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
