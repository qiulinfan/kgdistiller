/** Investigate a selected concept through searches, Wikipedia history and
 * references, and primary pages. Preserve observed leads and access failures;
 * a copied source passage verifies access, never historical priority by itself.
 */

import { AgentError, runJob, type Complete, type Tool } from "./agent";
import { arxivByTitle, arxivId, arxivUrl, normalizeTitle, wikipediaArticles, type Encyclopedia, type Fetch, type Reference, type SourceOpening } from "./sources";
import type { WebSource } from "./web";
import type { ForeignPage, ReadPage } from "./foreign";
import { normalizeSpace } from "./evidence";
import { rankSourceLinks, type RankedSource, type SourceCandidate } from "./origin-ranking";

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
  /** Rerank the observed sources into the sidebar's shortlist; Capture does not show it. */
  rank?: boolean;
  /** Owner-defined language and terminology guidance of the source being read. */
  guidance?: string;
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
  /** A passage copied from the chosen work, verified against the page read. */
  quote?: string;
  /** The chosen work as actually read, reused by Capture in this lookup. */
  page?: ForeignPage;
  /** Relevant earlier works or unresolved candidates, all with observed provenance. */
  leads?: Work[];
  /** The only source links displayed: at most ten, reranked and annotated. */
  ranked?: RankedSource[];
}

export const ORIGIN_SYSTEM = `Investigate where the exact selected concept comes from. The initial search is a starting point, not the end of the investigation. Use search for exact paper titles, authors, dates, earlier terminology and primary sources; use wikipedia to follow useful concepts and historical accounts; use read to inspect observed source links and their references. Wikipedia's history and ALL its cited works are clues, including papers, books, reports and dissertations outside arXiv. Follow those clues until you can support an origin or explain the unresolved gap. A title, a shared word, citation count, a web snippet or the first paper Wikipedia cites cannot establish an origin. Do not conflate the first use of a name, the original concept, a later formulation, and a popularization. The source being read may only use the concept. Earlier relevant work must not disappear merely because no single first inventor can be established.

Read the selected primary work before finishing when its link is available. If access fails, preserve it as a lead and explain the failure. For a verified work, quote a short verbatim passage from a page you read that supports its connection to this exact concept. Do not claim historical priority that the evidence does not establish. When uncertain, choose none and return relevant numbered leads. Work titles and links must come from observed candidates; never invent a reference.

Finish by calling origin with exactly one choice:
- source: the source being read itself introduces the term, even when a web result is the same work; a term it only uses, studies or credits to earlier work is not its own;
- reference: a cited reference that the text credits with the term (give its number);
- web: a web result that is the originating paper itself (give its number); a later paper that uses, extends or surveys the term is not;
- none: no candidate is the originating work, as for a general term with no single origin, or when the true origin is not among them.
Except for none, give the originating work's title, without authors, venue or year. The Wikipedia articles are numbered too: give article for the one that explains the term as the text uses it, even under another name, and leave it out when none does; an article about another thing of the same name does not. reason: a concise account in the language of the text the term was read in, separating supported facts, earlier related work, and unresolved access or historical uncertainty.`;

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
        quote: { type: "string", description: "A short passage copied verbatim from the selected primary page you read; omit when it could not be verified." },
        leads: { type: "array", items: { type: "integer" }, description: "Numbers of relevant earlier works or unresolved candidates among the web results, even when no unique origin is established." },
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
  const results = input.results
    .map((result, index) => `[${index + 1}] ${clip(result.title, TITLE_CHARS)} — ${result.url} — ${clip(result.snippet, SNIPPET_CHARS)}`).join("\n");
  const articles = input.articles
    .map((article, index) => `[${index + 1}] ${article.title}${article.section ? ` § ${article.section}` : ""} — ${clip(article.extract, EXTRACT_CHARS)}${article.context ? `\nContext and history: ${article.context}` : ""}${article.citations?.length ? `\nCited works (clues, not proof of origin):\n${article.citations.map((work) => `${work.title} — ${work.authors ?? ""} ${work.year ?? ""} — ${work.url ?? "no link; search the title"}`).join("\n")}` : ""}`).join("\n");
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
  const results = input.results;
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
 * 2021); only a candidate without any observed link is searched on arXiv by title.
 */
export async function originWork(fetch: Fetch, judged: Judged): Promise<Pick<Origin, "work" | "arxivError">> {
  const candidate = judged.candidate;
  if (!candidate) return {};
  const held = judged.title !== undefined && normalizeTitle(candidate.text).includes(normalizeTitle(judged.title));
  const title = held ? judged.title! : candidate.title;
  const id = candidate.arxiv;
  if (id) return { work: { title, url: arxivUrl(id), arxiv: id, year: `20${id.slice(0, 2)}` } };
  const own: Work = { title, ...(candidate.url ? { url: candidate.url } : {}) };
  if (candidate.url) return { work: own };
  try {
    const entry = await arxivByTitle(fetch, judged.title ?? candidate.title);
    return { work: entry ? { title: entry.title, url: arxivUrl(entry.arxiv), arxiv: entry.arxiv, year: entry.year } : own };
  } catch (error) {
    return { work: own, arxivError: error instanceof Error ? error.message : String(error) };
  }
}

/** Follow the observed clues before judging the origin; retain the primary page for Capture. */
export async function judgeOrigin(services: OriginServices, input: OriginInput, failures: string[]): Promise<Origin> {
  input = { ...input, results: [...input.results], articles: [...input.articles] };
  const readPages = new Map<string, ForeignPage>();
  const attempted = new Set<string>();
  let followed = 0;
  let quote: string | undefined;
  let leads: Work[] = [];
  const add = (results: WebSource[]): void => {
    for (const result of results) if (!input.results.some((other) => other.url === result.url)) input.results.push(result);
  };
  const addCitations = (): void => add(input.articles.flatMap((article) => (article.citations ?? []).flatMap((work) => work.url ? [{ title: work.title, url: work.url, snippet: `Cited by ${article.url}. ${work.authors ?? ""} ${work.year ?? ""}. A lead to investigate, not proof of origin.` }] : [])));
  addCitations();
  const present = async (origin: Origin): Promise<Origin> => {
    if (!input.rank) return origin;
    const candidates = new Map<string, SourceCandidate>();
    const addCandidate = (title: string, url: string | undefined, context: string): void => {
      if (!url) return;
      const known = candidates.get(url);
      candidates.set(url, { title: known?.title || title || url, url, context: known ? `${known.context}\n${context}` : context });
    };
    for (const result of input.results) addCandidate(result.title, result.url, result.snippet);
    for (const reference of input.references) addCandidate(reference.text, reference.url, `Cited by the selected passage: ${reference.text}`);
    for (const article of input.articles) {
      addCandidate(article.title, article.url, `Wikipedia background: ${article.extract}`);
      for (const citation of article.citations ?? []) addCandidate(citation.title, citation.url, `Wikipedia citation; not proof of relevance or priority. ${citation.authors ?? ""} ${citation.year ?? ""}`);
    }
    if (origin.work) addCandidate(origin.work.title, origin.work.url, `Investigation conclusion, subject to its uncertainty: ${origin.reason}`);
    for (const candidate of candidates.values()) {
      const page = readPages.get(candidate.url);
      candidate.context += page ? `\nPage actually read:\n${page.text.slice(0, 2000)}` : attempted.has(candidate.url) ? "\nPrimary-page read failed; only the observed clue is available." : "\nPrimary page not read; only the observed clue is available.";
    }
    try {
      return { ...origin, ranked: await rankSourceLinks(services.complete, { term: input.term, context: input.context, guidance: input.guidance, candidates: [...candidates.values()] }) };
    } catch (error) {
      failures.push(`Source ranking failed: ${error instanceof Error ? error.message : String(error)}`);
      return { ...origin, ranked: [] };
    }
  };
  const knownUrls = (): Set<string> => new Set([
    ...input.results.map((result) => result.url), ...input.references.flatMap((ref) => ref.url ? [ref.url] : []),
    ...input.articles.map((article) => article.url), ...[...readPages.values()].flatMap((page) => (page.links ?? []).map((link) => link.url)),
    ...(input.source && arxivId(input.source.text) ? [arxivUrl(arxivId(input.source.text)!)] : []),
  ]);
  const tool: Tool<Judged> = {
    spec: ORIGIN_TOOL,
    run: async (args) => {
      const judged = acceptOrigin(args, input);
      if (typeof judged === "string") return judged;
      const url = judged.candidate?.url ?? (judged.candidate?.arxiv ? arxivUrl(judged.candidate.arxiv) : undefined);
      if (url && !attempted.has(url)) return "error: read the selected primary work before finishing; if it cannot be read, preserve it as an unverified lead and explain why";
      if (judged.choice === "none" && followed === 0) return "error: investigate an earlier-work clue with search, wikipedia or read before concluding that the origin is unresolved";
      const proposedQuote = typeof args.quote === "string" ? normalizeSpace(args.quote) : "";
      const page = url ? readPages.get(url) : undefined;
      if (proposedQuote && (!page || !normalizeSpace(page.text).includes(proposedQuote))) return "error: quote must be copied verbatim from the selected page you read";
      const numbers = args.leads ?? [];
      if (!Array.isArray(numbers) || numbers.some((number) => !Number.isInteger(number) || number < 1 || number > input.results.length)) return "error: leads must be numbers of observed web results";
      leads = [...new Set(numbers as number[])].map((number) => ({ title: input.results[number - 1]!.title, url: input.results[number - 1]!.url }));
      quote = proposedQuote || undefined;
      return { done: judged };
    },
  };
  const search: Tool<Judged> = {
    spec: { type: "function", function: { name: "search", description: "Follow a citation or historical clue with a focused web search. Results are added to the numbered candidates.", parameters: { type: "object", properties: { query: { type: "string" } }, required: ["query"], additionalProperties: false } } },
    run: async (args) => {
      const query = typeof args.query === "string" ? args.query.trim() : "";
      if (!query) return "error: give a focused query";
      followed++;
      try { add((await services.webSearch(query)).slice(0, ORIGIN_RESULTS)); }
      catch (error) { const failure = `Follow-up search failed: ${error instanceof Error ? error.message : String(error)}`; failures.push(failure); return failure; }
      return originUser(input);
    },
  };
  const read: Tool<Judged> = {
    spec: { type: "function", function: { name: "read", description: "Read an observed source or reference link; inspect its actual text and links before judging origin.", parameters: { type: "object", properties: { url: { type: "string" } }, required: ["url"], additionalProperties: false } } },
    run: async (args) => {
      const url = typeof args.url === "string" ? args.url : "";
      if (!knownUrls().has(url)) return "error: read only a link observed in the results, Wikipedia references, source references, or a page already read";
      followed++;
      attempted.add(url);
      try {
        const page = readPages.get(url) ?? await services.readPage(url);
        readPages.set(url, page);
        const existing = input.results.find((result) => result.url === url);
        if (existing) existing.snippet = `${page.title} ${page.text.slice(0, 500)}`;
        else add([{ title: page.title, url, snippet: page.text.slice(0, SNIPPET_CHARS) }]);
        return `${page.title} — ${url}\n${page.text}\nLinks:\n${(page.links ?? []).map((link) => `${link.title} — ${link.url}`).join("\n")}\nNumbered candidates:\n${input.results.map((result, index) => `[${index + 1}] ${result.title} — ${result.url}`).join("\n")}`;
      } catch (error) {
        const failure = `Could not read ${url}: ${error instanceof Error ? error.message : String(error)}`;
        failures.push(failure);
        return failure;
      }
    },
  };
  const wikipedia: Tool<Judged> = {
    spec: { type: "function", function: { name: "wikipedia", description: "Investigate an alternative name or related concept through Wikipedia, including its history and references.", parameters: { type: "object", properties: { term: { type: "string" } }, required: ["term"], additionalProperties: false } } },
    run: async (args) => {
      const term = typeof args.term === "string" ? args.term.trim() : "";
      if (!term) return "error: give the term to investigate";
      followed++;
      try { for (const article of await wikipediaArticles(term, services.fetch)) if (!input.articles.some((other) => other.url === article.url)) input.articles.push(article); }
      catch (error) { const failure = `Wikipedia follow-up failed: ${error instanceof Error ? error.message : String(error)}`; failures.push(failure); return failure; }
      addCitations();
      return originUser(input);
    },
  };
  let judged: Judged;
  try {
    judged = await runJob(services.complete, {
      system: ORIGIN_SYSTEM,
      user: originUser(input),
      tools: [search, read, wikipedia, tool],
      turns: 10,
      last: "origin",
      effort: "low",
    });
  } catch (error) {
    failures.push(error instanceof Error ? error.message : String(error));
    return present({
      choice: "none", reason: error instanceof AgentError
        ? "The investigation did not establish an origin within its model-call limit; these are observed leads, not verified origins."
        : "The investigation could not finish; these are observed leads, not verified origins.",
      leads: [...input.results, ...input.articles].map((result) => ({ title: result.title, url: result.url })),
    });
  }
  const reference = judged.candidate?.reference;
  const work = await originWork(services.fetch, judged);
  const page = work.work?.url ? readPages.get(work.work.url) : undefined;
  return present({
    choice: judged.choice,
    reason: judged.reason,
    ...(reference ? { reference } : {}),
    ...(judged.wikipedia ? { wikipedia: judged.wikipedia } : {}),
    ...work,
    ...(page ? { page: { ...page, title: work.work!.title } } : {}),
    ...(quote ? { quote } : {}),
    ...(leads.length ? { leads } : {}),
  });
}

/** What finding a term's origin needs: the model, the fetcher for Wikipedia and arXiv, and the web search. */
export interface OriginServices {
  complete: Complete;
  fetch: Fetch;
  webSearch(query: string): Promise<WebSource[]>;
  readPage: ReadPage;
}

/**
 * Seed an investigation with web results and Wikipedia's history and references. Either
 * service failing leaves the other's candidates to judge; `failures` says which
 * failed.
 */
export async function findOrigin(services: OriginServices, input: Omit<OriginInput, "results" | "articles">):
  Promise<{ origin: Origin; failures: string[] }> {
  const failures: string[] = [];
  const [results, articles] = await Promise.all([
    services.webSearch(originQuery(input.term)).catch((error: unknown) => {
      failures.push(`The web search failed: ${error instanceof Error ? error.message : String(error)}`);
      return [];
    }),
    wikipediaArticles(input.term, services.fetch).catch((error: unknown) => {
      failures.push(`Wikipedia did not answer: ${error instanceof Error ? error.message : String(error)}`);
      return [];
    }),
  ]);
  const origin = await judgeOrigin(services, { ...input, results, articles }, failures);
  return { origin, failures };
}
