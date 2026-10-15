/**
 * The kgdistiller agent's loop. The model answers only through tools: the
 * plugin runs each call and returns its result, and a job ends when its final
 * tool accepts an answer. A refused answer goes back to the model as the tool's
 * result, so the model can correct it within the job's turn limit.
 */

import type { ChatMessage, ChatReply, ChatRequest, ToolSpec } from "./model";

export class AgentError extends Error {}

/** What a tool returns: text for the model, or the job's finished value. */
export type ToolOutcome<R> = string | { done: R };

export interface Tool<R> {
  spec: ToolSpec;
  /** A read-only tool: its calls in one turn run at the same time, before the others. */
  concurrent?: boolean;
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
  /** The tool the last allowed turn must call, so the job ends with an answer. */
  last?: string;
  /**
   * What a reply in plain text means. Absent, the model is reminded to use the
   * tools; present, the text is taken as this answer, and a refusal goes back
   * to the model as a message.
   */
  onText?: (text: string) => Promise<ToolOutcome<R>>;
  /** The model's reasoning effort; absent means none. */
  effort?: ChatRequest["reasoning_effort"];
}

export type Complete = (request: ChatRequest) => Promise<ChatReply>;

/** Run a job to its finished value. */
export async function runJob<R>(complete: Complete, job: Job<R>): Promise<R> {
  const tools = new Map(job.tools.map((tool) => [tool.spec.function.name, tool]));
  const specs = job.tools.map((tool) => tool.spec);
  const messages: ChatMessage[] = [{ role: "system", content: job.system }, { role: "user", content: job.user }];
  for (let turn = 1; turn <= job.turns; turn++) {
    const forced = turn === 1 && job.first ? job.first : turn === job.turns && job.last ? job.last : undefined;
    const reply = await complete({
      messages,
      tools: specs,
      tool_choice: forced ? { type: "function", function: { name: forced } } : "required",
      reasoning_effort: job.effort ?? "none",
    });
    const calls = reply.message.tool_calls ?? [];
    messages.push({ role: "assistant", content: reply.message.content,
      ...(calls.length ? { tool_calls: calls } : {}),
      ...(reply.message.reasoning_content !== undefined ? { reasoning_content: reply.message.reasoning_content } : {}),
    });
    if (calls.length === 0) {
      const text = reply.message.content?.trim() ?? "";
      const outcome = job.onText && text ? await job.onText(text) : "Answer only by calling one of the tools.";
      if (typeof outcome !== "string") return outcome.done;
      messages.push({ role: "user", content: outcome });
      continue;
    }
    const execute = async (call: (typeof calls)[number]): Promise<ToolOutcome<R>> => {
      if (forced && call.function.name !== forced) return `error: call ${forced} on this turn`;
      const tool = tools.get(call.function.name);
      if (!tool) return `error: there is no tool named ${call.function.name}`;
      try {
        return await tool.run(parseArguments(call.function.arguments));
      } catch (error) {
        return `error: ${error instanceof Error ? error.message : String(error)}`;
      }
    };
    // Read-only calls run together; the others run afterwards in order, and the first finished value ends the job.
    const early = await Promise.all(calls.map((call) => (tools.get(call.function.name)?.concurrent ? execute(call) : undefined)));
    for (const [index, call] of calls.entries()) {
      const outcome = early[index] ?? await execute(call);
      if (typeof outcome !== "string") return outcome.done;
      messages.push({ role: "tool", tool_call_id: call.id, content: outcome });
    }
  }
  throw new AgentError(`the agent did not finish within ${job.turns} model calls`);
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
