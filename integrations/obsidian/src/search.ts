/**
 * The agent's search: answer the owner's question from the knowledge base
 * first, then from papers and the web for what it does not hold. The plugin
 * gives the model the first search results, the records named in the
 * question, and the document the owner is reading with its document type; the
 * model reads more of the knowledge base, searches arXiv for papers that may
 * answer the question, or searches the web, through read-only tools called
 * together where they are independent, and finishes with one answer. While the
 * owner reads a registered source, research material, the web is searched
 * for papers only, so the answer cites papers rather than general pages. Every
 * citation is checked against what the tools returned: `[[uid]]` for a
 * record, `[Wn]` for a paper or web page, so the answer keeps recorded
 * knowledge and the web apart and cites nothing it was not shown.
 */

import { runJob, type Complete, type Tool } from "./agent";
import { selectionNames } from "./capture";
import type { ToolSpec } from "./model";
import type { GetResult, NeighborsResult, RecordSummary, ResolveResult, SearchResult } from "./lookup-model";
import { arxivId } from "./sources";
import type { WebSource } from "./web";

/** The kgd reads a search needs; `KgdServer` provides them. */
export interface SearchKgd {
  call<T>(tool: string, args: Record<string, unknown>): Promise<T>;
}

export type SearchWeb = (query: string) => Promise<WebSource[]>;

/** The registered source the owner is reading, as `kgd source` reports it. */
export interface Reading {
  base: string;
  source: string;
  type: string;
}

export interface Citation {
  uid: string;
  label: string;
}

export interface SearchAnswer {
  /** Markdown with `[[uid]]` and `[Wn]` citations, each one checked. */
  text: string;
  /** The records the answer cites, in order of first citation. */
  records: Citation[];
  /** The web sources the answer cites, by their number. */
  web: Array<WebSource & { number: number }>;
}

export type SearchStep =
  | { tool: "kg_search" | "paper_search" | "web_search"; query: string }
  | { tool: "kg_get"; count: number }
  | { tool: "kg_neighbors"; count: number }
  | { tool: "answer" };

const FIRST_RESULTS = 8;
const SEARCH_LIMIT = 8;
const MAX_GET = 8;
const MAX_DEPTH = 3;
/** Characters of a record's prose or of one evidence quote shown to the model. */
const TEXT_CHARS = 700;

export const SEARCH_SYSTEM = `You answer the owner's question from their kgdistiller knowledge base, a research knowledge base of records extracted from papers and notes, and from papers and the web for what the knowledge base does not hold. Nodes are concepts; relations are statements binding records to roles. The first search results are given, with the document the owner is reading when it is a registered source. Read the records you rely on with kg_get unless their gloss already answers; use kg_search for other wordings and kg_neighbors for links and prerequisite chains. For what the records do not answer, search papers that may answer the question with paper_search; when web_search is offered, use it for what papers do not cover. Call independent tools together in one turn. Answer after one or two rounds of tools; take more only when the question needs them. Then call answer once.

Rules:
- A record supports only what its statement and evidence say; a shared name does not make two concepts the same.
- Cite every claim: [[uid]] for a record, [Wn] for a paper or web page, with uids and numbers exactly as the tools returned them. Cite a paper by its arXiv page when the results include one.
- Keep the sources apart: never present web content or your own knowledge as recorded knowledge. Leave out what you cannot cite.
- Say plainly when the knowledge base does not hold something, and what the web says instead.
- Answer in the language of the question, briefly and without headings: the direct answer first, then a few supporting points, about 150 words at most unless the question asks for detail.`;

const TOOLS: Record<string, ToolSpec> = {
  kg_search: {
    type: "function",
    function: {
      name: "kg_search",
      description: "Rank the knowledge base's records for a query, across every base.",
      parameters: { type: "object", properties: { query: { type: "string" } }, required: ["query"], additionalProperties: false },
    },
  },
  kg_get: {
    type: "function",
    function: {
      name: "kg_get",
      description: "Read whole records by uid: statement, evidence quotes, source and links.",
      parameters: {
        type: "object", properties: { uids: { type: "array", items: { type: "string" } } }, required: ["uids"], additionalProperties: false,
      },
    },
  },
  kg_neighbors: {
    type: "function",
    function: {
      name: "kg_neighbors",
      description: "Follow links from records: dir out follows their own links, in the records linking to them; role requires gives the prerequisite chain.",
      parameters: {
        type: "object",
        properties: {
          uids: { type: "array", items: { type: "string" } },
          dir: { type: "string", enum: ["out", "in", "both"] },
          role: { type: "string", description: "Follow only this role, such as requires." },
          depth: { type: "integer", minimum: 1, maximum: MAX_DEPTH },
        },
        required: ["uids", "dir"],
        additionalProperties: false,
      },
    },
  },
  paper_search: {
    type: "function",
    function: {
      name: "paper_search",
      description: "Search arXiv for papers that may answer a question; write the query in English. Papers are numbered with the web sources, W1, W2, …",
      parameters: { type: "object", properties: { query: { type: "string" } }, required: ["query"], additionalProperties: false },
    },
  },
  web_search: {
    type: "function",
    function: {
      name: "web_search",
      description: "Search the web; sources are numbered W1, W2, … across the whole search, papers included.",
      parameters: { type: "object", properties: { query: { type: "string" } }, required: ["query"], additionalProperties: false },
    },
  },
  answer: {
    type: "function",
    function: {
      name: "answer",
      description: "Give the answer, in Markdown, citing [[uid]] for records and [Wn] for web sources.",
      parameters: { type: "object", properties: { answer: { type: "string" } }, required: ["answer"], additionalProperties: false },
    },
  },
};

function summaryLine(record: Pick<RecordSummary, "uid" | "label" | "kind" | "class" | "base" | "gloss">): string {
  return `[${record.uid}] ${record.label} · ${record.kind} ${record.class} · ${record.base} — ${record.gloss}`;
}

function clip(text: string): string {
  const flat = text.split(/\s+/).filter(Boolean).join(" ");
  return flat.length > TEXT_CHARS ? `${flat.slice(0, TEXT_CHARS)}…` : flat;
}

/** What one search has seen: records by uid with their labels, and the web sources by number. */
class SearchState {
  readonly labels = new Map<string, string>();
  readonly web: WebSource[] = [];

  seeRecords(records: Iterable<{ uid: string; label?: string | null }>): void {
    for (const record of records) if (record.label) this.labels.set(record.uid, record.label);
  }
}

export function searchUser(question: string, first: SearchResult, named: ResolveResult | undefined, reading?: Reading): string {
  const parts = [`Question: ${question}`];
  if (reading) parts.push(`The owner is reading ${reading.source} in base ${reading.base}, a source of document type ${reading.type}.`);
  parts.push(first.results.length
    ? `Knowledge base search for the question:\n${first.results.map(summaryLine).join("\n")}`
    : "A knowledge base search for the question finds no record.");
  const terms = named?.terms ?? [];
  if (terms.length) {
    parts.push(`Names in the question, looked up in every base:\n${terms.map((term) => {
      const senses = term.senses.map((record) => `  - ${summaryLine(record)}`);
      return [`"${term.term}":`, ...(senses.length ? senses : ["  - no record has this name"])].join("\n");
    }).join("\n")}`);
  }
  return parts.join("\n\n");
}

export function recordText(records: GetResult["records"]): string {
  return records.map((record) => {
    const links = record.out.map((link) => `${link.role}: ${`[${link.uid}] ${link.label ?? ""}`.trim()}`);
    const inbound = record.in.slice(0, 12).map((link) => `${link.role} of [${link.uid}] ${link.label} (${link.kind})`);
    return [
      `[${record.uid}] ${record.label} · ${record.kind} ${record.class} · ${record.base} · ${record.source} L${record.lines}${record.epistemic ? ` · ${record.epistemic}` : ""}`,
      `Statement: ${clip(record.body)}`,
      ...record.evidence.map((quote) => `Evidence: ${clip(quote)}`),
      ...(links.length ? [`Links: ${links.join("; ")}`] : []),
      ...(inbound.length ? [`Linked from: ${inbound.join("; ")}${record.in.length > inbound.length ? `; and ${record.in.length - inbound.length} more` : ""}`] : []),
    ].join("\n");
  }).join("\n\n");
}

export function neighborText(result: NeighborsResult): string {
  if (result.edges.length === 0) return "No links.";
  const label = (uid: string) => `[${uid}] ${result.records[uid]?.label ?? ""}`.trim();
  return result.edges.map((edge) => `${label(edge.from)} -${edge.role}-> ${label(edge.to)} (depth ${edge.depth})`).join("\n");
}

const CITATION = /\[\[([^\]|]+)(?:\|[^\]]*)?\]\]|\[W(\d+)\]/g;

/** Check an answer's citations against what the search has seen; a string is the refusal. */
export function acceptAnswer(text: string, labels: ReadonlyMap<string, string>, web: readonly WebSource[]): SearchAnswer | string {
  const answer = text.trim();
  if (!answer) return "error: the answer is empty";
  const records: Citation[] = [];
  const cited = new Map<number, WebSource & { number: number }>();
  const unknown: string[] = [];
  for (const match of answer.matchAll(CITATION)) {
    if (match[1] !== undefined) {
      const uid = match[1].trim();
      const label = labels.get(uid);
      if (label === undefined) unknown.push(`[[${uid}]]`);
      else if (!records.some((record) => record.uid === uid)) records.push({ uid, label });
    } else {
      const number = Number(match[2]);
      const source = web[number - 1];
      if (!source) unknown.push(`[W${number}]`);
      else cited.set(number, { ...source, number });
    }
  }
  if (unknown.length) {
    return `error: ${unknown.join(", ")} ${unknown.length === 1 ? "is" : "are"} not among the records and web sources the tools returned. `
      + "Cite only those, or leave the claim out.";
  }
  return { text: answer, records, web: [...cited.values()].sort((a, b) => a.number - b.number) };
}

export interface SearchOptions {
  /** The registered source the owner is reading, if any. */
  reading?: Reading;
  /** Told of each step as it starts. */
  onStep?: (step: SearchStep) => void;
}

/** The query that keeps a web search to arXiv. */
export const paperQuery = (query: string): string => `${query} site:arxiv.org`;

export async function searchKnowledge(complete: Complete, kgd: SearchKgd, web: SearchWeb, question: string,
  { reading, onStep = () => undefined }: SearchOptions = {}): Promise<SearchAnswer> {
  const state = new SearchState();
  const names = selectionNames(question);
  const [first, named] = await Promise.all([
    kgd.call<SearchResult>("kg_search", { query: question.slice(0, 500), limit: FIRST_RESULTS }),
    names.length ? kgd.call<ResolveResult>("kg_resolve", { terms: names }) : Promise.resolve(undefined),
  ]);
  state.seeRecords(first.results);
  for (const term of named?.terms ?? []) state.seeRecords(term.senses);
  const tools: Tool<SearchAnswer>[] = [
    {
      spec: TOOLS.kg_search!,
      concurrent: true,
      run: async (args) => {
        const query = typeof args.query === "string" ? args.query.trim() : "";
        if (!query) return "error: query is required";
        onStep({ tool: "kg_search", query });
        const found = await kgd.call<SearchResult>("kg_search", { query: query.slice(0, 500), limit: SEARCH_LIMIT });
        state.seeRecords(found.results);
        return found.results.length ? found.results.map(summaryLine).join("\n") : "No record.";
      },
    },
    {
      spec: TOOLS.kg_get!,
      concurrent: true,
      run: async (args) => {
        const uids = uidsOf(args.uids).slice(0, MAX_GET);
        if (!uids.length) return "error: uids are required";
        onStep({ tool: "kg_get", count: uids.length });
        const got = await kgd.call<GetResult>("kg_get", { uids });
        state.seeRecords(got.records);
        for (const record of got.records) {
          state.seeRecords(record.out.flatMap((link) => (link.uid && link.exists ? [{ uid: link.uid, label: link.label }] : [])));
          state.seeRecords(record.in);
        }
        const missing = got.missing.length ? `\n\nNo record: ${got.missing.join(", ")}` : "";
        return `${recordText(got.records)}${missing}`.trim() || "No record.";
      },
    },
    {
      spec: TOOLS.kg_neighbors!,
      concurrent: true,
      run: async (args) => {
        const uids = uidsOf(args.uids).slice(0, MAX_GET);
        if (!uids.length) return "error: uids are required";
        const dir = args.dir === "in" || args.dir === "both" ? args.dir : "out";
        const depth = Math.min(MAX_DEPTH, Math.max(1, Number.isInteger(args.depth) ? (args.depth as number) : 1));
        onStep({ tool: "kg_neighbors", count: uids.length });
        const role = typeof args.role === "string" && args.role.trim() ? [args.role.trim()] : undefined;
        const result = await kgd.call<NeighborsResult>("kg_neighbors", { uids, dir, depth, ...(role ? { role } : {}) });
        state.seeRecords(Object.entries(result.records).filter(([, record]) => record.exists).map(([uid, record]) => ({ uid, label: record.label })));
        return neighborText(result);
      },
    },
    {
      spec: TOOLS.paper_search!,
      concurrent: true,
      run: async (args) => {
        const query = typeof args.query === "string" ? args.query.trim() : "";
        if (!query) return "error: query is required";
        onStep({ tool: "paper_search", query });
        const papers = (await web(paperQuery(query))).filter((source) => arxivId(source.url) !== undefined);
        if (!papers.length) return "The paper search found nothing.";
        return numbered(state, papers);
      },
    },
  ];
  // Reading research, the owner wants papers that may answer the question, not general pages.
  if (!reading) {
    tools.push({
      spec: TOOLS.web_search!,
      concurrent: true,
      run: async (args) => {
        const query = typeof args.query === "string" ? args.query.trim() : "";
        if (!query) return "error: query is required";
        onStep({ tool: "web_search", query });
        const sources = await web(query);
        if (!sources.length) return "The web search found nothing.";
        return numbered(state, sources);
      },
    });
  }
  const answer = async (text: string) => {
    const accepted = acceptAnswer(text, state.labels, state.web);
    if (typeof accepted === "string") return accepted;
    onStep({ tool: "answer" });
    return { done: accepted };
  };
  tools.push({ spec: TOOLS.answer!, run: (args) => answer(typeof args.answer === "string" ? args.answer : "") });
  return runJob(complete, {
    system: SEARCH_SYSTEM,
    user: searchUser(question, first, named, reading),
    tools,
    turns: 6,
    last: "answer",
    // A model that writes its answer as text instead of calling answer has still answered.
    onText: answer,
  });
}

/** Number new sources after those the search has already seen, and list them for the model. */
function numbered(state: SearchState, sources: readonly WebSource[]): string {
  const start = state.web.length;
  state.web.push(...sources);
  return sources.map((source, index) => {
    const id = arxivId(source.url);
    return `[W${start + index + 1}] ${source.title}${id ? ` — arXiv:${id}` : ""} — ${source.url}\n    ${source.snippet}`;
  }).join("\n");
}

function uidsOf(value: unknown): string[] {
  return Array.isArray(value)
    ? [...new Set(value.filter((item): item is string => typeof item === "string").map((item) => item.trim().replace(/^\[\[|\]\]$/g, "")).filter(Boolean))]
    : [];
}

/** What one search step is doing, for the progress list. */
export function stepText(step: SearchStep): string {
  switch (step.tool) {
    case "kg_search": return `Searching the knowledge base: ${step.query}`;
    case "paper_search": return `Searching papers: ${step.query}`;
    case "web_search": return `Searching the web: ${step.query}`;
    case "kg_get": return `Reading ${step.count} record${step.count === 1 ? "" : "s"}`;
    case "kg_neighbors": return `Following the links of ${step.count} record${step.count === 1 ? "" : "s"}`;
    case "answer": return "Answering";
  }
}

/**
 * The answer as Markdown with numbered citations: each cited record becomes
 * K1, K2, … by first citation, linked to `#kgd-record-N`, and each web source
 * keeps its number W1, W2, … linked to its page.
 */
export function answerMarkdown(answer: SearchAnswer): string {
  const numbers = new Map(answer.records.map((record, index) => [record.uid, index + 1]));
  const pages = new Map(answer.web.map((source) => [source.number, source.url]));
  return answer.text.replace(/\[\[([^\]|]+)(?:\|[^\]]*)?\]\]|\[W(\d+)\]/g, (whole, uid: string | undefined, web: string | undefined) => {
    if (uid !== undefined) {
      const number = numbers.get(uid.trim());
      return number === undefined ? whole : `[K${number}](#kgd-record-${number})`;
    }
    const url = pages.get(Number(web));
    return url === undefined ? whole : `[W${web}](${url})`;
  }).replace(/\)(?=\[[KW]\d+\]\()/g, ") ");
}
