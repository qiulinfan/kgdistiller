/** The plugin's direct Chat Completions client. No external agent runtime is involved. */
import { ApiError, apiUrl, type PostJson } from "./api";

/**
 * The reasoning levels a request can name, from none to the most. Each
 * service kind declares the ones its vendor documents (providers.ts).
 */
export const REASONING_LEVELS = ["none", "minimal", "low", "medium", "high", "xhigh", "max"] as const;
export type ReasoningLevel = (typeof REASONING_LEVELS)[number];
/** A level for every request, or empty for each request's own. */
export type ReasoningChoice = ReasoningLevel | "";

export const isReasoningLevel = (value: unknown): value is ReasoningLevel => REASONING_LEVELS.includes(value as ReasoningLevel);

/**
 * The level a request goes at when the service documents `levels`: the wanted
 * one, else the next one up, else the highest; none at all when it documents
 * no level.
 */
export function sentLevel(wanted: ReasoningLevel | undefined, levels: readonly ReasoningLevel[]): ReasoningLevel | undefined {
  if (!wanted || levels.includes(wanted)) return wanted;
  const rank = REASONING_LEVELS.indexOf(wanted);
  return levels.find((level) => REASONING_LEVELS.indexOf(level) > rank) ?? levels.at(-1);
}

export interface ModelSettings {
  endpoint: string;
  apiKey: string;
  model: string;
  /** "deepseek" turns thinking on and off as DeepSeek and Zhipu do; "chat-completions" sends reasoning_effort alone. */
  protocol: "chat-completions" | "deepseek";
  /** The owner's level for every request; absent or empty leaves each request its own. */
  reasoning?: ReasoningChoice;
  /** The levels the service documents; a request's own level is moved to one of them. */
  levels: readonly ReasoningLevel[];
}

export const MODEL_TIMEOUT_MS = 120 * 1000;
/** The waits before asking again a service that answered 503 (busy) or 429 (too many requests). */
export const BUSY_RETRY_DELAYS_MS: readonly number[] = [1000, 2000];

export type Wait = (ms: number) => Promise<void>;
const wait: Wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export interface ToolCall {
  id: string;
  type: "function";
  function: { name: string; arguments: string };
}

export type ChatMessage =
  | { role: "system" | "user"; content: string }
  | { role: "assistant"; content: string | null; tool_calls?: ToolCall[]; reasoning_content?: string }
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
  /** "none" keeps the model's reasoning off; another level turns it on. Absent, the service decides. */
  reasoning_effort?: ReasoningLevel;
}

export interface Usage {
  prompt_tokens: number;
  completion_tokens: number;
}

export interface ChatReply {
  model: string;
  message: { content: string | null; tool_calls?: ToolCall[]; reasoning_content?: string };
  finish_reason: string;
  usage: Usage;
}


export class ModelClient {
  constructor(private readonly settings: () => ModelSettings, private readonly post: PostJson, private readonly pause: Wait = wait) {}

  configured(): boolean {
    const settings = this.settings();
    return settings.endpoint.trim() !== "" && settings.model.trim() !== "";
  }

  /**
   * One model call. A service that answers 503 or 429 is asked again after
   * 1 s and then 2 s, each time with the current settings; any other failure,
   * a timeout included, is reported at once.
   */
  async complete(request: ChatRequest): Promise<ChatReply> {
    for (let attempt = 0; ; attempt++) {
      try {
        return await this.send(request);
      } catch (error) {
        if (!(error instanceof ApiError) || (error.status !== 503 && error.status !== 429)) throw error;
        const delay = BUSY_RETRY_DELAYS_MS[attempt];
        if (delay === undefined) {
          // 429 also means an exhausted balance or plan, which only the service's own words tell apart.
          throw new Error(error.detail
            ? `The model service refused the request and ${BUSY_RETRY_DELAYS_MS.length} retries (HTTP ${error.status}): ${error.detail}`
            : `The model service is busy (HTTP ${error.status}): it refused the request and ${BUSY_RETRY_DELAYS_MS.length} retries. `
              + "Try again in a moment, or choose another model in the sidebar.");
        }
        await this.pause(delay);
      }
    }
  }

  private async send(request: ChatRequest): Promise<ChatReply> {
    const settings = this.settings();
    if (!this.configured()) throw new Error("Add a model provider in the kgdistiller settings and choose a model in the sidebar.");
    apiUrl(settings.endpoint);
    const { reasoning_effort: wanted, ...chat } = request;
    const effort = settings.reasoning || sentLevel(wanted, settings.levels);
    const body: Record<string, unknown> = { ...chat, model: settings.model.trim(), stream: false };
    if (effort && settings.protocol === "deepseek") {
      body.thinking = { type: effort !== "none" ? "enabled" : "disabled" };
      if (effort !== "none") {
        body.reasoning_effort = effort;
        // DeepSeek thinking mode rejects required/named tool_choice. Offer only
        // the named tool when forced; the harness checks it and retries plain text.
        if (request.tool_choice && request.tool_choice !== "auto") {
          body.tool_choice = "auto";
          if (typeof request.tool_choice === "object") {
            const name = request.tool_choice.function.name;
            body.tools = request.tools?.filter((tool) => tool.function.name === name);
          }
        }
      }
    } else if (effort) {
      body.reasoning_effort = effort;
    }
    return chatReply(await this.post(settings.endpoint, settings.apiKey, body, MODEL_TIMEOUT_MS));
  }
}

/** Validate the tool-call envelope before the internal harness executes anything. */
export function chatReply(value: unknown): ChatReply {
  const body = value as { model?: unknown; choices?: Array<{ message?: unknown; finish_reason?: unknown }>; usage?: Partial<Usage> } | null;
  const choice = Array.isArray(body?.choices) ? body.choices[0] : undefined;
  const message = choice?.message as { content?: unknown; tool_calls?: unknown; reasoning_content?: unknown } | undefined;
  if (!message || typeof message !== "object") throw new Error("The model API returned no message.");
  if (message.content != null && typeof message.content !== "string") throw new Error("The model API returned invalid content.");
  let calls: ToolCall[] | undefined;
  if (message.tool_calls !== undefined) {
    if (!Array.isArray(message.tool_calls)) throw new Error("The model API returned invalid tool calls.");
    const ids = new Set<string>();
    calls = message.tool_calls.map((call: unknown) => {
      const item = call as Partial<ToolCall> | null;
      if (!item || typeof item.id !== "string" || !item.id || ids.has(item.id) || item.type !== "function"
        || typeof item.function?.name !== "string" || !item.function.name || typeof item.function.arguments !== "string") {
        throw new Error("The model API returned an invalid tool call.");
      }
      ids.add(item.id);
      return { id: item.id, type: "function", function: { name: item.function.name, arguments: item.function.arguments } };
    });
  }
  const count = (n: unknown): number => typeof n === "number" && Number.isFinite(n) && n >= 0 ? n : 0;
  return {
    model: typeof body?.model === "string" ? body.model : "",
    message: {
      content: typeof message.content === "string" ? message.content : null,
      ...(calls ? { tool_calls: calls } : {}),
      ...(typeof message.reasoning_content === "string" ? { reasoning_content: message.reasoning_content } : {}),
    },
    finish_reason: typeof choice?.finish_reason === "string" ? choice.finish_reason : "",
    usage: { prompt_tokens: count(body?.usage?.prompt_tokens), completion_tokens: count(body?.usage?.completion_tokens) },
  };
}
