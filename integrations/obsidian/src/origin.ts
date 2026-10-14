/**
 * Where an unrecorded term comes from, judged by the agent. One web search asks
 * who first introduced the term; one model call at low reasoning then picks
 * the originating work among the source being read, the references the
 * passage cites and the web results, or none, as for a general term with no
 * single origin, and the Wikipedia article that explains the term as the text
 * uses it, if any. The chosen work is shown as its arXiv page when arXiv holds it: from
 * the candidate's own arXiv link, or else from one arXiv title search, so the
 * rate-limited arXiv API is asked only for a work without a link. Nothing is
 * written to the knowledge base.
 */

import { runJob, type Complete, type Tool } from "./agent";
import { arxivByTitle, arxivId, arxivUrl, normalizeTitle, type Encyclopedia, type Fetch, type Reference, type SourceOpening } from "./sources";
import type { WebSource } from "./web";

/** Web results beyond this many are not shown to the model. */
export const ORIGIN_RESULTS = 8;
const TITLE_CHARS = 120;
const SNIPPET_CHARS = 160;
const EXTRACT_CHARS = 240;

/** The web search for a term's originating work. */
export const originQuery = (term: string): string => `who first introduced ${term}? original paper`;

export interface OriginInput {
  term: string;
  /** The text the term was read in. */
  context: string;
  /** The source being read, when the selection lies in a file. */
  source?: SourceOpening;
  /** The references the text cites, from the source's reference list. */
  references: Reference[];
  /** The web results of `originQuery`. */
  results: WebSource[];
  /** Wikipedia's articles that may be about the term. */
  articles: Encyclopedia[];
}

export type OriginChoice = "source" | "reference" | "web" | "none";

/** The originating work as shown: its arXiv page, title and year when arXiv holds it. */
export interface Work {
  title: string;
  url?: string;
  year?: string;
  arxiv?: string;
}

export interface Origin {
  choice: OriginChoice;
  reason: string;
  /** The cited reference's number, when a reference introduced the term. */
  reference?: string;
  work?: Work;
  /** Why arXiv could not be asked about the work. */
  arxivError?: string;
  /** The Wikipedia article that explains the term as the text uses it. */
  wikipedia?: Encyclopedia;
}

export const ORIGIN_SYSTEM = `You find the work that originated a term: the one that proposed or introduced it. You see the term, the text it was read in, the source being read, the references that text cites, and numbered web results. Answer by calling origin with exactly one choice:
- source: the source being read itself introduces the term, even when a web result is the same work; a term it only uses, studies or credits to earlier work is not its own;
- reference: a cited reference that the text credits with the term (give its number);
- web: a web result that is the originating paper itself (give its number); a later paper that uses, extends or surveys the term is not;
- none: no candidate is the originating work, as for a general term with no single origin, or when the true origin is not among them.
Except for none, give the originating work's title, without authors, venue or year. The Wikipedia articles are numbered too: give article for the one that explains the term as the text uses it, even under another name, and leave it out when none does; an article about another thing of the same name does not. reason: one short sentence in the language of the text the term was read in.`;

const ORIGIN_TOOL = {
  type: "function" as const,
  function: {
    name: "origin",
    description: "Record where the term originates.",
    parameters: {
      type: "object",
      properties: {
        choice: { type: "string", enum: ["source", "reference", "web", "none"] },
        number: { type: "integer", description: "reference: the reference's number; web: the web result's number." },
        title: { type: "string", description: "The originating work's title." },
        article: { type: "integer", description: "The Wikipedia article that explains the term as the text uses it; left out when none does." },
        reason: { type: "string" },
      },
      required: ["choice", "reason"],
      additionalProperties: false,
    },
  },
};

function clip(text: string, chars: number): string {
  const plain = text.split(/\s+/).filter(Boolean).join(" ");
  return plain.length > chars ? `${plain.slice(0, chars)}…` : plain;
}

export function originUser(input: OriginInput): string {
  const references = input.references.map((reference) => `[${reference.number}] ${reference.text}`).join("\n");
  const results = input.results.slice(0, ORIGIN_RESULTS)
    .map((result, index) => `[${index + 1}] ${clip(result.title, TITLE_CHARS)} — ${result.url} — ${clip(result.snippet, SNIPPET_CHARS)}`).join("\n");
  const articles = input.articles
    .map((article, index) => `[${index + 1}] ${article.title}${article.section ? ` § ${article.section}` : ""} — ${clip(article.extract, EXTRACT_CHARS)}`).join("\n");
  return [
    `Term: ${input.term}`,
    `Read in: ${input.context.trim() || input.term}`,
    `Source being read: ${input.source?.text ?? "unknown"}`,
    `References the text cites:\n${references || "none"}`,
    `Web results:\n${results || "none"}`,
    `Wikipedia articles:\n${articles || "none"}`,
  ].join("\n");
}

/** What a candidate itself shows: its title, link and arXiv id, and the text a judged title must appear in to be shown. */
interface Candidate {
  title: string;
  url?: string;
  arxiv?: string;
  text: string;
  reference?: string;
}

/** A judged origin before arXiv is asked: the model's title of the work and the candidate it chose. */
export interface Judged {
  choice: OriginChoice;
  reason: string;
  /** The model's title of the work: searched on arXiv, and shown only when the candidate's text holds it. */
  title?: string;
  candidate?: Candidate;
  wikipedia?: Encyclopedia;
}

/** Check one origin call against the candidates; a string is the refusal the model sees. */
export function acceptOrigin(args: Record<string, unknown>, input: OriginInput): Judged | string {
  const article = acceptArticle(args.article, input.articles);
  if (typeof article === "string") return article;
  const judged = acceptChoice(args, input);
  return typeof judged === "string" || !article ? judged : { ...judged, wikipedia: article };
}

/** The Wikipedia article an origin call names, if any; a string is the refusal. */
function acceptArticle(article: unknown, articles: Encyclopedia[]): Encyclopedia | undefined | string {
  if (article === undefined || article === null) return undefined;
  if (typeof article !== "number" || !Number.isInteger(article) || article < 1 || article > articles.length) {
    return articles.length ? `error: article must be a Wikipedia article from 1 to ${articles.length}, or left out` : "error: there are no Wikipedia articles; leave article out";
  }
  return articles[article - 1];
}

function acceptChoice(args: Record<string, unknown>, input: OriginInput): Judged | string {
  const choice = args.choice;
  const reason = typeof args.reason === "string" ? args.reason.trim() : "";
  if (choice === "none") return { choice, reason };
  if (choice !== "source" && choice !== "reference" && choice !== "web") return "error: choice must be source, reference, web or none";
  const title = typeof args.title === "string" ? args.title.trim() : "";
  if (!title) return "error: give the originating work's title";
  if (choice === "source") {
    if (!input.source) return "error: no source is being read; choose another candidate or none";
    const { title: name, text } = input.source;
    const arxiv = arxivId(text);
    return { choice, reason, title, candidate: { title: name, text, ...(arxiv ? { arxiv } : {}) } };
  }
  if (choice === "reference") {
    const reference = input.references.find((each) => each.number === String(args.number));
    if (!reference) {
      return input.references.length
        ? `error: number must be a cited reference: ${input.references.map((each) => each.number).join(", ")}`
        : "error: the text cites no references; choose another candidate or none";
    }
    const { number, text, url } = reference;
    const arxiv = arxivId(url) ?? arxivId(text);
    return { choice, reason, title, candidate: { title: text, text, reference: number, ...(url ? { url } : {}), ...(arxiv ? { arxiv } : {}) } };
  }
  const results = input.results.slice(0, ORIGIN_RESULTS);
  const number = args.number;
  if (typeof number !== "number" || !Number.isInteger(number) || number < 1 || number > results.length) {
    return results.length ? `error: number must be a web result from 1 to ${results.length}` : "error: there are no web results; choose another candidate or none";
  }
  const result = results[number - 1]!;
  // A page's snippet may name the paper an untitled PDF holds; its arXiv id comes from its own link only.
  const arxiv = arxivId(result.url);
  return {
    choice, reason, title,
    candidate: { title: clip(result.title, TITLE_CHARS), url: result.url, text: `${result.title} ${result.snippet}`, ...(arxiv ? { arxiv } : {}) },
  };
}

/**
 * The work as shown. A reference or page names its work among authors, venue
 * and site, so the judged title is shown when the candidate holds it. A
 * candidate's own arXiv id gives the link and the year (2106.08254 is from
 * 2021); only a candidate without one is searched on arXiv by the judged title.
 */
export async function originWork(fetch: Fetch, judged: Judged): Promise<Pick<Origin, "work" | "arxivError">> {
  const candidate = judged.candidate;
  if (!candidate) return {};
  const held = judged.title !== undefined && normalizeTitle(candidate.text).includes(normalizeTitle(judged.title));
  const title = held ? judged.title! : candidate.title;
  const id = candidate.arxiv;
  if (id) return { work: { title, url: arxivUrl(id), arxiv: id, year: `20${id.slice(0, 2)}` } };
  const own: Work = { title, ...(candidate.url ? { url: candidate.url } : {}) };
  try {
    const entry = await arxivByTitle(fetch, judged.title ?? candidate.title);
    return { work: entry ? { title: entry.title, url: arxivUrl(entry.arxiv), arxiv: entry.arxiv, year: entry.year } : own };
  } catch (error) {
    return { work: own, arxivError: error instanceof Error ? error.message : String(error) };
  }
}

/** Judge where a term comes from among the candidates, then find the chosen work on arXiv. */
export async function judgeOrigin(complete: Complete, fetch: Fetch, input: OriginInput): Promise<Origin> {
  const tool: Tool<Judged> = {
    spec: ORIGIN_TOOL,
    run: async (args) => {
      const judged = acceptOrigin(args, input);
      return typeof judged === "string" ? judged : { done: judged };
    },
  };
  const { value: judged } = await runJob(complete, {
    system: ORIGIN_SYSTEM,
    user: originUser(input),
    tools: [tool],
    turns: 2,
    first: "origin",
    effort: "low",
  });
  const reference = judged.candidate?.reference;
  return {
    choice: judged.choice,
    reason: judged.reason,
    ...(reference ? { reference } : {}),
    ...(judged.wikipedia ? { wikipedia: judged.wikipedia } : {}),
    ...(await originWork(fetch, judged)),
  };
}
