/**
 * The kgdistiller agent's loop. The model answers only through tools: the
 * plugin runs each call and returns its result, and a job ends when its final
 * tool accepts an answer. A refused answer goes back to the model as the tool's
 * result, so the model can correct it within the job's turn limit.
 */

import type { ChatMessage, ChatReply, ChatRequest, ToolSpec, Usage } from "./gateway";

export class AgentError extends Error {
  constructor(message: string, readonly steps: readonly Step[] = []) {
    super(message);
  }
}

/** What a tool returns: text for the model, or the job's finished value. */
export type ToolOutcome<R> = string | { done: R };

export interface Tool<R> {
  spec: ToolSpec;
  /** Run one call; a thrown error is reported to the model as the result. */
  run(args: Record<string, unknown>): Promise<ToolOutcome<R>>;
}

export interface Job<R> {
  system: string;
  user: string;
  tools: Tool<R>[];
  /** Model calls allowed before the job fails. */
  turns: number;
  /** The tool the first turn must call; later turns may call any tool. */
  first?: string;
  /** The model's reasoning effort; absent means none. */
  effort?: ChatRequest["reasoning_effort"];
}

export interface Step {
  tool: string;
  arguments: string;
  result: string;
}

export interface JobResult<R> {
  value: R;
  turns: number;
  usage: Usage;
  steps: Step[];
}

export type Complete = (request: ChatRequest) => Promise<ChatReply>;

export async function runJob<R>(complete: Complete, job: Job<R>): Promise<JobResult<R>> {
  const tools = new Map(job.tools.map((tool) => [tool.spec.function.name, tool]));
  const specs = job.tools.map((tool) => tool.spec);
  const messages: ChatMessage[] = [{ role: "system", content: job.system }, { role: "user", content: job.user }];
  const usage: Usage = { prompt_tokens: 0, completion_tokens: 0 };
  const steps: Step[] = [];
  for (let turn = 1; turn <= job.turns; turn++) {
    const forced = turn === 1 && job.first ? job.first : undefined;
    const reply = await complete({
      messages,
      tools: specs,
      tool_choice: forced ? { type: "function", function: { name: forced } } : "required",
      reasoning_effort: job.effort ?? "none",
    });
    usage.prompt_tokens += reply.usage.prompt_tokens;
    usage.completion_tokens += reply.usage.completion_tokens;
    const calls = reply.message.tool_calls ?? [];
    messages.push({ role: "assistant", content: reply.message.content, ...(calls.length ? { tool_calls: calls } : {}) });
    if (calls.length === 0) {
      messages.push({ role: "user", content: "Answer only by calling one of the tools." });
      continue;
    }
    for (const call of calls) {
      const tool = tools.get(call.function.name);
      let outcome: ToolOutcome<R>;
      if (!tool) {
        outcome = `error: there is no tool named ${call.function.name}`;
      } else {
        try {
          outcome = await tool.run(parseArguments(call.function.arguments));
        } catch (error) {
          outcome = `error: ${error instanceof Error ? error.message : String(error)}`;
        }
      }
      if (typeof outcome !== "string") {
        steps.push({ tool: call.function.name, arguments: call.function.arguments, result: "done" });
        return { value: outcome.done, turns: turn, usage, steps };
      }
      steps.push({ tool: call.function.name, arguments: call.function.arguments, result: outcome });
      messages.push({ role: "tool", tool_call_id: call.id, content: outcome });
    }
  }
  throw new AgentError(`the agent did not finish within ${job.turns} model calls`, steps);
}

function parseArguments(text: string): Record<string, unknown> {
  let value: unknown;
  try {
    value = JSON.parse(text || "{}");
  } catch {
    throw new AgentError("the arguments are not valid JSON");
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new AgentError("the arguments must be a JSON object");
  return value as Record<string, unknown>;
}
