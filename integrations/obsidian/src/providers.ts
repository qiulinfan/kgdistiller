/**
 * The model providers the agent may call: each a Chat Completions service of
 * a known kind, with its key in Obsidian's SecretStorage. A kind gives the
 * base URL and request format of a hosted service; a local server or another
 * OpenAI-compatible service takes its own. A provider's models are detected
 * at `GET <base URL>/models`, and the sidebar lists them grouped by provider;
 * the reasoning levels a model accepts are detected by asking it at each.
 */
import { ApiError, type GetJson } from "./api";
import {
  isReasoningLevel,
  REASONING_LEVELS,
  sentLevel,
  type ChatReply,
  type ChatRequest,
  type ReasoningChoice,
  type ReasoningLevel,
  type ToolSpec,
} from "./model";

export const PROVIDER_FORMATS = ["chat-completions", "deepseek"] as const;
export type ProviderFormat = (typeof PROVIDER_FORMATS)[number];

export interface ProviderKind {
  id: string;
  label: string;
  /** The service's base URL; for a kind that takes its own, the default shown. */
  baseUrl?: string;
  format: ProviderFormat;
  /** The base URL is the provider's own, the kind's only its default. */
  ownUrl?: boolean;
  /** The request format is the provider's own. */
  ownFormat?: boolean;
}

/** The services a provider can be. Each hosted URL answered `/models` and `/chat/completions` (2026-10-15). */
export const PROVIDER_KINDS: readonly ProviderKind[] = [
  { id: "deepseek", label: "DeepSeek", baseUrl: "https://api.deepseek.com", format: "deepseek" },
  { id: "openai", label: "OpenAI", baseUrl: "https://api.openai.com/v1", format: "chat-completions" },
  { id: "openrouter", label: "OpenRouter", baseUrl: "https://openrouter.ai/api/v1", format: "chat-completions" },
  { id: "siliconflow", label: "SiliconFlow", baseUrl: "https://api.siliconflow.cn/v1", format: "chat-completions" },
  { id: "moonshot", label: "Moonshot (Kimi)", baseUrl: "https://api.moonshot.cn/v1", format: "chat-completions" },
  { id: "zhipu", label: "Zhipu (GLM)", baseUrl: "https://open.bigmodel.cn/api/paas/v4", format: "deepseek" },
  { id: "qwen", label: "Alibaba Qwen (DashScope)", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", format: "chat-completions" },
  { id: "doubao", label: "Volcengine Doubao (Ark)", baseUrl: "https://ark.cn-beijing.volces.com/api/v3", format: "chat-completions" },
  { id: "groq", label: "Groq", baseUrl: "https://api.groq.com/openai/v1", format: "chat-completions" },
  { id: "mistral", label: "Mistral", baseUrl: "https://api.mistral.ai/v1", format: "chat-completions" },
  { id: "xai", label: "xAI", baseUrl: "https://api.x.ai/v1", format: "chat-completions" },
  { id: "minimax", label: "MiniMax", baseUrl: "https://api.minimax.chat/v1", format: "chat-completions" },
  { id: "stepfun", label: "StepFun", baseUrl: "https://api.stepfun.com/v1", format: "chat-completions" },
  { id: "together", label: "Together AI", baseUrl: "https://api.together.xyz/v1", format: "chat-completions" },
  { id: "ollama", label: "Ollama (local)", baseUrl: "http://localhost:11434/v1", format: "chat-completions", ownUrl: true },
  { id: "lm-studio", label: "LM Studio (local)", baseUrl: "http://localhost:1234/v1", format: "chat-completions", ownUrl: true },
  { id: "custom", label: "Other OpenAI-compatible service", format: "chat-completions", ownUrl: true, ownFormat: true },
];

const CUSTOM = PROVIDER_KINDS.find((kind) => kind.id === "custom")!;

export function kindOf(provider: Pick<Provider, "kind">): ProviderKind {
  return PROVIDER_KINDS.find((kind) => kind.id === provider.kind) ?? CUSTOM;
}

export interface Provider {
  /** Unique among the vault's providers; the sidebar groups the models under it. */
  name: string;
  /** One of PROVIDER_KINDS. */
  kind: string;
  /** For a kind that takes its own base URL: that URL, empty for the kind's default. Ignored otherwise. */
  baseUrl: string;
  /** For a kind that takes its own format: that format. Ignored otherwise. */
  format: ProviderFormat;
  /** A SecretStorage identifier, never the key itself; empty for a local service without authentication. */
  apiKey: string;
}

/** The URL that `/chat/completions` and `/models` follow, such as https://api.deepseek.com. */
export function baseUrlOf(provider: Provider): string {
  const kind = kindOf(provider);
  return ((kind.ownUrl ? provider.baseUrl.trim() : "") || kind.baseUrl || "").replace(/\/+$/, "");
}

export function formatOf(provider: Provider): ProviderFormat {
  const kind = kindOf(provider);
  return kind.ownFormat ? provider.format : kind.format;
}

/** The model the agent uses: a provider's name and one of its model ids. */
export interface ModelChoice {
  provider: string;
  model: string;
}

/** A provider's detected models, or why they could not be listed. */
export type Detected = { models: string[] } | { error: string };

/** The reasoning levels a model accepts, or why they could not be detected. */
export type DetectedLevels = { levels: readonly ReasoningLevel[] } | { error: string };

/** The sidebar's reasoning choice: the level chosen, and what is known of the levels the model in use accepts. */
export interface ReasoningChoices {
  chosen: ReasoningChoice;
  /** Absent while no model is chosen, or when the levels could not be detected. */
  levels?: readonly ReasoningLevel[];
  error?: string;
}

export interface ModelGroup {
  provider: string;
  models: string[];
  error?: string;
}

export const MODELS_TIMEOUT_MS = 20 * 1000;

/** The provider's Chat Completions URL; empty while it has no base URL. */
export const chatUrl = (provider: Provider): string => (baseUrlOf(provider) ? `${baseUrlOf(provider)}/chat/completions` : "");

export const modelsUrl = (provider: Provider): string => `${baseUrlOf(provider)}/models`;

/** The model ids of a `/models` reply, from OpenAI's `data` list, a `models` list or a bare list: each once, sorted. */
export function modelIds(reply: unknown): string[] {
  const value = reply as { data?: unknown; models?: unknown } | null;
  const lists = [value?.data, value?.models, reply].filter((list): list is unknown[] => Array.isArray(list));
  if (lists.length === 0) throw new Error("The models reply holds no list of models.");
  const ids = new Set<string>();
  for (const list of lists) {
    for (const item of list) {
      const entry = item as { id?: unknown; name?: unknown } | null;
      const id = typeof item === "string" ? item : typeof entry?.id === "string" ? entry.id : entry?.name;
      if (typeof id === "string" && id.trim()) ids.add(id.trim());
    }
  }
  return [...ids].sort();
}

/** Ask a provider which models it serves. */
export async function listModels(provider: Provider, apiKey: string, get: GetJson): Promise<string[]> {
  if (!baseUrlOf(provider)) throw new Error("Set the provider's base URL.");
  return modelIds(await get(modelsUrl(provider), apiKey, MODELS_TIMEOUT_MS));
}

/** The valid providers of stored settings, each name once, and whether anything else was there. */
export function storedProviders(value: unknown): { providers: Provider[]; dropped: boolean } {
  if (!Array.isArray(value)) return { providers: [], dropped: value !== undefined };
  const providers: Provider[] = [];
  for (const item of value) {
    const entry = item as Partial<Provider> | null;
    if (!entry || typeof entry.name !== "string" || !entry.name.trim() || !PROVIDER_KINDS.some((kind) => kind.id === entry.kind)
      || typeof entry.baseUrl !== "string" || typeof entry.apiKey !== "string"
      || !PROVIDER_FORMATS.includes(entry.format as ProviderFormat) || providers.some((provider) => provider.name === entry.name)) continue;
    providers.push({ name: entry.name, kind: entry.kind!, baseUrl: entry.baseUrl, format: entry.format as ProviderFormat, apiKey: entry.apiKey });
  }
  return { providers, dropped: providers.length !== value.length };
}

/** A name no provider has yet, from `stem`: `stem`, then `stem-2`, `stem-3`, … */
export function newProviderName(providers: readonly Provider[], stem: string): string {
  const taken = new Set(providers.map((provider) => provider.name));
  if (!taken.has(stem)) return stem;
  let index = 2;
  while (taken.has(`${stem}-${index}`)) index++;
  return `${stem}-${index}`;
}

/** The reasoning levels one of a provider's models accepts, as detected. */
export interface ModelLevels {
  provider: string;
  model: string;
  levels: ReasoningLevel[];
}

/** The stored detected levels that name known levels, each provider's model once; anything else is detected again. */
export function storedLevels(value: unknown): ModelLevels[] {
  if (!Array.isArray(value)) return [];
  const stored: ModelLevels[] = [];
  for (const item of value) {
    const entry = item as Partial<ModelLevels> | null;
    if (!entry || typeof entry.provider !== "string" || typeof entry.model !== "string" || !Array.isArray(entry.levels)
      || !entry.levels.every(isReasoningLevel) || stored.some((other) => other.provider === entry.provider && other.model === entry.model)) continue;
    stored.push({ provider: entry.provider, model: entry.model, levels: [...entry.levels] });
  }
  return stored;
}

/** Levels asked at the same time: GLM's free models refused more than four requests at once with HTTP 429 (2026-10-15). */
const PROBES_AT_ONCE = 2;

const OK_TOOL: ToolSpec = { type: "function", function: { name: "ok", description: "Report that the connection works.", parameters: { type: "object", properties: {} } } };

/** A level the model accepted: how long its answer took, and whether it called the tool as the agent's jobs need. */
export interface LevelAnswer {
  level: ReasoningLevel;
  seconds: number;
  tool: boolean;
}

/** The levels a model accepted, in order, and the first refusal of one it did not. */
export interface LevelProbe {
  answers: LevelAnswer[];
  refusal: string;
}

/**
 * Ask the model at each reasoning level, two at a time, with one short
 * request of the kind the agent sends: a forced tool call. A level the service
 * refuses with HTTP 400 or 422 is one the model does not accept; any other
 * failure, such as a wrong key or an exhausted balance, ends the detection.
 */
export async function probeLevels(complete: (request: ChatRequest) => Promise<ChatReply>): Promise<LevelProbe> {
  const answers: Array<LevelAnswer | undefined> = [];
  let refusal = "";
  let failure: Error | undefined;
  let next = 0;
  const ask = async (): Promise<void> => {
    while (!failure && next < REASONING_LEVELS.length) {
      const index = next++;
      const level = REASONING_LEVELS[index]!;
      const started = performance.now();
      try {
        const reply = await complete({
          messages: [{ role: "user", content: "Call the ok tool." }],
          tools: [OK_TOOL],
          tool_choice: { type: "function", function: { name: "ok" } },
          reasoning_effort: level,
        });
        const tool = reply.message.tool_calls?.some((call) => call.function.name === "ok") ?? false;
        answers[index] = { level, seconds: (performance.now() - started) / 1000, tool };
      } catch (error) {
        if (error instanceof ApiError && (error.status === 400 || error.status === 422)) refusal ||= error.message;
        else failure ??= error instanceof Error ? error : new Error(String(error));
      }
    }
  };
  await Promise.all(Array.from({ length: PROBES_AT_ONCE }, ask));
  if (failure) throw failure;
  return { answers: answers.filter((answer): answer is LevelAnswer => answer !== undefined), refusal };
}

/** The connection test's result: the levels the model accepts, and its answer at the level that judgments use. */
export function connectionReport(model: string, probe: LevelProbe): string {
  const levels = probe.answers.map((answer) => answer.level);
  const fast = probe.answers.find((answer) => answer.level === sentLevel("none", levels));
  if (!fast) return `${model} refused the agent's kind of request at every reasoning level: ${probe.refusal}`;
  return `Connected: ${model} accepts reasoning ${levels.join(", ")}. At reasoning ${fast.level} it answered in ${fast.seconds.toFixed(1)} s `
    + `${fast.tool ? "with the tool call the agent needs" : "but without the tool call the agent needs"}.`;
}

/**
 * The sidebar's choices: every provider in settings order with its detected
 * models, and the chosen model kept in its group even when detection did not
 * list it.
 */
export function modelGroups(providers: readonly Provider[], detected: ReadonlyMap<string, Detected>, chosen: ModelChoice): ModelGroup[] {
  return providers.map((provider) => {
    const entry = detected.get(provider.name);
    const models = entry && "models" in entry ? [...entry.models] : [];
    if (chosen.provider === provider.name && chosen.model && !models.includes(chosen.model)) models.unshift(chosen.model);
    return { provider: provider.name, models, ...(entry && "error" in entry ? { error: entry.error } : {}) };
  });
}
