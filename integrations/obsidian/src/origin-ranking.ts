/** Rank observed source links by meaning before showing the owner's shortlist. */
import { runJob, type Complete, type Tool } from "./agent";

export const MAX_ORIGIN_LINKS = 10;

export interface SourceCandidate {
  title: string;
  url: string;
  context: string;
}

export interface RankedSource {
  title: string;
  url: string;
  annotation: string;
}

export interface RankingInput {
  term: string;
  context: string;
  guidance?: string;
  candidates: SourceCandidate[];
}

export const RANKING_SYSTEM = `Rerank observed source links for the exact concept the owner selected while reading. Return at most ten relevant sources in descending semantic relevance, using rank. Compare the meanings and scope of the sources with the selected concept: a shared word is not relevance. Prefer a primary work that defines the exact concept and useful earlier work; include a background explanation when it helps. Exclude unrelated applications and narrower or broader methods unless their relationship to this exact concept is useful and explained. Deduplicate versions and mirrors of the same work. Do not fill a quota: fewer than ten, or none, is fine.

Every chosen link needs one concise annotation explaining its specific relationship to the selected concept and what supports that assessment. Distinguish a definition, early formulation, later development, or background explanation when the evidence supports it. Clearly qualify a Wikipedia citation, search snippet, or failed read as an unverified lead. Reading a passage does not establish historical priority. Never invent a contribution or claim to have read unavailable text. Follow the registered document type's writing-language and terminology guidance when provided; otherwise use the language of the reading context, preserving English technical terms. A source's language must not override the owner's reading language. Return candidate numbers, never new titles or URLs.`;

/** Validate the ranking without silently turning a bibliography into its first ten entries. */
export function acceptRanking(args: Record<string, unknown>, candidates: SourceCandidate[]): RankedSource[] | string {
  if (!Array.isArray(args.sources) || args.sources.length > MAX_ORIGIN_LINKS) return `error: sources must contain at most ${MAX_ORIGIN_LINKS} ranked entries`;
  const result: RankedSource[] = [];
  const seen = new Set<string>();
  for (const entry of args.sources as Array<{ number?: unknown; annotation?: unknown } | null>) {
    if (!entry || !Number.isInteger(entry.number) || Number(entry.number) < 1 || Number(entry.number) > candidates.length) return "error: each source must name an observed candidate number";
    const candidate = candidates[Number(entry.number) - 1]!;
    const annotation = typeof entry.annotation === "string" ? entry.annotation.trim() : "";
    if (!annotation) return "error: every source needs a specific relationship and evidence-status annotation";
    if (seen.has(candidate.url)) return "error: rank each source link only once";
    seen.add(candidate.url);
    result.push({ title: candidate.title, url: candidate.url, annotation });
  }
  return result;
}

export async function rankSourceLinks(complete: Complete, input: RankingInput): Promise<RankedSource[]> {
  if (!input.candidates.length) return [];
  const tool: Tool<RankedSource[]> = {
    spec: { type: "function", function: {
      name: "rank", description: "Choose, order and annotate up to ten relevant source links.",
      parameters: { type: "object", properties: { sources: { type: "array", maxItems: MAX_ORIGIN_LINKS, items: {
        type: "object", properties: { number: { type: "integer" }, annotation: { type: "string" } }, required: ["number", "annotation"], additionalProperties: false,
      } } }, required: ["sources"], additionalProperties: false },
    } },
    run: async (args) => {
      const ranked = acceptRanking(args, input.candidates);
      return typeof ranked === "string" ? ranked : { done: ranked };
    },
  };
  return runJob(complete, {
    system: RANKING_SYSTEM,
    user: `Selected concept: ${input.term}\nReading context: ${input.context}${input.guidance ? `\nWriting guidance:\n${input.guidance}` : ""}\nObserved candidates:\n${input.candidates.map((candidate, index) => `[${index + 1}] ${candidate.title} — ${candidate.url}\n${candidate.context}`).join("\n\n")}`,
    tools: [tool], first: "rank", turns: 2, effort: "low",
  });
}
