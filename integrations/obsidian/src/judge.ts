/**
 * The agent's lookup judgment: whether the selected passage names knowledge a
 * base already records. The deterministic lookup has already retrieved the
 * candidates by name and search; one model call compares their meanings with
 * the passage and explains how the selection connects to them. The verdict
 * only guides reading: nothing is written.
 */

import { runJob, type Complete, type Tool } from "./agent";
import type { SearchHit, RecordSummary } from "./lookup-model";

/** Candidates beyond this many are not shown to the model. */
export const JUDGE_CANDIDATES = 8;

export interface JudgeInput {
  selection: string;
  /** The line or paragraph around the selection, when the editor knows it. */
  context: string;
  candidates: Array<RecordSummary | SearchHit>;
}

export type Verdict = "matched" | "ambiguous" | "unmatched";

export interface Judgment {
  verdict: Verdict;
  /** The uid of the matched record, or the uids an ambiguous selection may be. */
  records: string[];
  explanation: string;
}

export const JUDGE_SYSTEM = `You judge whether a passage the owner selected while reading names knowledge already recorded in their kgdistiller knowledge base. You see the selection, the text around it, and numbered candidate records found by name and by search. Nodes are concepts and the results stated about them, such as theorems and findings; relations are connections stated between two or more records. Answer by calling judge.

First decide what the selection means in the text around it: a concept, or a statement. Context disambiguates the selected text; it must not silently add or remove qualifiers. Selecting a general concept inside the name or description of a particular method does not select that method. Singular and plural names and translations may still name the same concept.
- matched: one candidate is that same concept or statement. Judge by meaning: compare definitions and conditions. A shared name does not make the same concept: a general term used in its general sense is not a specific record that happens to share the word, and a broader or narrower concept is not a match. A concept is a node that defines it, never a statement or relation that merely mentions it; a statement matches only a record that states it.
- ambiguous: two or more candidates are each that same concept or statement under some reading of the text.
- unmatched: no candidate is it.

explanation: one or two short sentences in the language of the text around the selection. For matched, say what the record states and what it builds on. Otherwise say which candidates are related and how, or that nothing related is recorded. Name records by their labels, never by number or id. Use only what the candidates' text supports; never invent knowledge or records.`;

const JUDGE_TOOL = {
  type: "function" as const,
  function: {
    name: "judge",
    description: "Record the judgment.",
    parameters: {
      type: "object",
      properties: {
        selection: { type: "string", enum: ["concept", "statement"], description: "What the selection is in the text around it." },
        verdict: { type: "string", enum: ["matched", "ambiguous", "unmatched"] },
        candidates: {
          type: "array",
          items: { type: "integer" },
          description: "matched: the one candidate number; ambiguous: every candidate it may be; unmatched: empty.",
        },
        explanation: { type: "string" },
      },
      required: ["selection", "verdict", "candidates", "explanation"],
      additionalProperties: false,
    },
  },
};

/** The numbered candidates, one block each: label, kind, base and prerequisites, then the gloss. */
export function candidateText(candidates: JudgeInput["candidates"]): string {
  return candidates.map((record, index) => {
    const requires = "requires" in record && Array.isArray(record.requires)
      ? (record.requires as Array<{ label?: string | null }>).map((value) => value.label).filter(Boolean)
      : [];
    const head = `[${index + 1}] ${record.label} · ${record.kind} ${record.class} · ${record.base}`;
    return `${head}${requires.length ? ` · requires: ${requires.join(", ")}` : ""}\n    ${record.gloss}`;
  }).join("\n");
}

export function judgeUser(input: JudgeInput): string {
  const parts = [`Selection: ${input.selection}`];
  if (input.context.trim() && input.context.trim() !== input.selection.trim()) parts.push(`Text around it:\n${input.context.trim()}`);
  parts.push(input.candidates.length ? `Candidates:\n${candidateText(input.candidates)}` : "Candidates: none were found.");
  return parts.join("\n\n");
}

/** Check one judge call against the candidates; a string is the refusal the model sees. */
export function acceptJudgment(args: Record<string, unknown>, candidates: JudgeInput["candidates"]): Judgment | string {
  const verdict = args.verdict;
  if (verdict !== "matched" && verdict !== "ambiguous" && verdict !== "unmatched") return "error: verdict must be matched, ambiguous or unmatched";
  const numbers = Array.isArray(args.candidates) ? args.candidates : [];
  if (!numbers.every((value) => Number.isInteger(value) && value >= 1 && value <= candidates.length)) {
    return `error: candidates must be numbers from 1 to ${candidates.length}`;
  }
  const chosen = [...new Set(numbers as number[])].map((value) => candidates[value - 1]!);
  const records = chosen.map((record) => record.uid);
  if (verdict === "matched" && records.length !== 1) return "error: matched names exactly one candidate";
  if (verdict === "ambiguous" && records.length < 2) return "error: ambiguous names at least two candidates; with one, decide matched or unmatched";
  if (verdict === "unmatched" && records.length > 0) return "error: unmatched names no candidate";
  // A concept is a node (docs/model.md), so a concept matches no relation; a statement may be either.
  if (args.selection !== "concept" && args.selection !== "statement") return "error: selection must be concept or statement";
  const relation = args.selection === "concept" ? chosen.find((record) => record.class === "relation") : undefined;
  if (relation) {
    return `error: the selection is a concept, so it can only be a node; ${relation.label} is a relation. `
      + "Judge again: a record that only mentions it is related, not the same.";
  }
  const explanation = typeof args.explanation === "string" ? args.explanation.trim() : "";
  return { verdict, records, explanation };
}

export async function judgeSelection(complete: Complete, input: JudgeInput): Promise<Judgment> {
  const candidates = input.candidates.slice(0, JUDGE_CANDIDATES);
  const tool: Tool<Judgment> = {
    spec: JUDGE_TOOL,
    run: async (args) => {
      const judged = acceptJudgment(args, candidates);
      return typeof judged === "string" ? judged : { done: judged };
    },
  };
  return runJob(complete, {
    system: JUDGE_SYSTEM,
    user: judgeUser({ ...input, candidates }),
    tools: [tool],
    turns: 2,
    first: "judge",
  });
}
