/**
 * Where an unrecorded term comes from, located three ways:
 * - the references the selected passage itself cites, from the source's own
 *   reference list (block ids `^reference-N`, as the owner's paper
 *   translations write them);
 * - Wikipedia's article on the term, read at the section a redirect points
 *   into, with the first paper that text cites;
 * - OpenAlex's citation graph: the papers that the papers using the term cite
 *   most, ranked with a title that starts with the term, an abstract that
 *   introduces it and Wikipedia's citation. These signals picked the right
 *   paper for BEiT, masked autoencoder, denoising autoencoder and variational
 *   autoencoder.
 * Every paper is shown as its arXiv page: from OpenAlex's arXiv location or
 * DOI, from a same-titled arXiv record of OpenAlex, or from one arXiv title
 * search. A paper arXiv does not hold is shown as such, with its DOI. The web
 * lookup runs only when the owner asks, since it sends the term to these
 * services. Nothing here is written to the knowledge base.
 */

export const OPENALEX = "https://api.openalex.org/works";
export const WIKIPEDIA = "https://en.wikipedia.org/w/api.php";
export const ARXIV = "https://export.arxiv.org/api/query";
/** Wikimedia asks every client to name itself with a contact address. */
export const USER_AGENT = "kgdistiller-obsidian (https://github.com/qiulinfan/kgdistiller)";
const TITLE_RESULTS = 15;
const COCITING_RESULTS = 100;
const COCITED_DETAILS = 4;
export const PAPERS_SHOWN = 3;
/**
 * The least score of a paper shown as a likely origin. A title that merely
 * contains the term and its citation count stay below it; a paper reaches it
 * only with an origin signal: a share of the papers using the term citing it,
 * a title that starts with the term, or Wikipedia's citation.
 */
export const ORIGIN_SCORE = 2;
/** arXiv's API terms ask for at most one request every three seconds. */
export const ARXIV_INTERVAL_MS = 3000;
const SNIPPET_SENTENCES = 2;

/** One GET returning the response body as text; the plugin passes Obsidian's requestUrl. */
export type Fetch = (url: string) => Promise<string>;

export interface Paper {
  title: string;
  year: string;
  /** The arXiv id, when arXiv holds the paper. */
  arxiv?: string;
  /** The arXiv abstract page, otherwise the paper's DOI or landing page. */
  url: string;
  citations: number;
  score: number;
}

export interface Encyclopedia {
  title: string;
  url: string;
  extract: string;
  /** The section a redirect points into, such as "Denoising autoencoder (DAE)". */
  section?: string;
  zh?: { title: string; url: string };
  /** The first paper on arXiv that the article's text about the term cites. */
  cites?: { title: string; arxiv: string; url: string };
}

export interface Sources {
  papers: Paper[];
  wikipedia?: Encyclopedia;
}

/** One entry of a source's reference list that a passage cites. */
export interface Reference {
  number: string;
  text: string;
  url?: string;
}

/**
 * The references `text` cites through links to `#^reference-N`, in citing
 * order, each read from the line of `lines` that ends with that block id.
 */
export function citedReferences(text: string, lines: readonly string[]): Reference[] {
  const numbers = [...new Set([...text.matchAll(/#\^reference-(\d+)/g)].map((match) => match[1]!))];
  const references: Reference[] = [];
  for (const number of numbers) {
    const line = lines.find((each) => new RegExp(`\\^reference-${number}\\s*$`).test(each));
    if (!line) continue;
    const url = /\]\((https?:\/\/[^)\s]+)\)/.exec(line)?.[1];
    const body = line.replace(new RegExp(`\\s*\\^reference-${number}\\s*$`), "").replace(/^\s*\*\*\[\d+\]\*\*\s*/, "")
      .replace(/\s*\[[^\]]*\]\(https?:\/\/[^)]*\)\s*$/, "").replace(/\*/g, "").trim();
    references.push({ number, text: body, ...(url ? { url } : {}) });
  }
  return references;
}

/** The arXiv id in an arXiv URL (abs, pdf, html, ar5iv or alphaxiv) or an arXiv DOI, without its version. */
export function arxivId(text: string | null | undefined): string | undefined {
  if (!text) return undefined;
  const match = /(?:arxiv\.org\/(?:abs|pdf|html)\/|ar5iv(?:\.labs)?\.arxiv\.org\/(?:abs|html)\/|ar5iv\.org\/(?:abs|html)\/|alphaxiv\.org\/(?:abs|overview)\/|10\.48550\/arxiv\.)(\d{4}\.\d{4,5})/i.exec(text);
  return match?.[1];
}

export const arxivUrl = (id: string): string => `https://arxiv.org/abs/${id}`;

interface Work {
  id: string;
  display_name?: string | null;
  publication_date?: string | null;
  cited_by_count?: number | null;
  doi?: string | null;
  abstract_inverted_index?: Record<string, number[]> | null;
  referenced_works?: string[] | null;
  locations?: Array<{ landing_page_url?: string | null; pdf_url?: string | null }> | null;
}

const WORK_FIELDS = "id,display_name,publication_date,cited_by_count,doi,abstract_inverted_index,locations";
const INTRODUCES = /\b(we|this paper|this work)\s+(propose|introduce|present|call|name|develop|describe)\b/i;
const OVERVIEW = /\b(introduction to|tutorial|survey|review)\b/i;

function query(params: Record<string, string | number>): string {
  return Object.entries(params).map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`).join("&");
}

/** A work's arXiv id, from its DOI or any of its locations. */
export function workArxiv(work: Work): string | undefined {
  return arxivId(work.doi) ?? (work.locations ?? []).map((location) => arxivId(location.landing_page_url) ?? arxivId(location.pdf_url)).find(Boolean);
}

/** Lowercased words with plural s dropped, so "auto-encoders" and "autoencoder" compare equal. */
export function normalizeTitle(text: string): string {
  return text.toLowerCase().replace(/auto-encod/g, "autoencod").replace(/[^a-z0-9 ]+/g, " ").split(/\s+/).filter(Boolean)
    .map((word) => (word.length > 3 && word.endsWith("s") ? word.slice(0, -1) : word)).join(" ");
}

export function abstractText(index: Work["abstract_inverted_index"]): string {
  if (!index) return "";
  const placed: Array<[number, string]> = [];
  for (const [word, positions] of Object.entries(index)) for (const position of positions) placed.push([position, word]);
  return placed.sort((a, b) => a[0] - b[0]).map(([, word]) => word).join(" ");
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Rank candidate works for a term; `shares` maps work ids to their co-citation
 * share and `wikipediaArxiv` holds the arXiv ids Wikipedia's text cites. A work
 * without its own arXiv id takes that of a same-titled work, its arXiv record.
 */
export function rankPapers(term: string, works: readonly Work[], shares: ReadonlyMap<string, number>, wikipediaArxiv: readonly string[]): Paper[] {
  const wanted = normalizeTitle(term);
  const most = Math.max(1, ...works.map((work) => work.cited_by_count ?? 0));
  const startsWith = new RegExp(`^\\s*${escapeRegExp(term)}\\s*[:\\-–]`, "i");
  const definesAcronym = new RegExp(`${escapeRegExp(term).replace(/\\ /g, "s?\\s+")}s?\\s*\\(([A-Z][A-Za-z0-9-]{1,8})\\)`, "i");
  const twins = new Map<string, string>();
  for (const work of works) {
    const id = workArxiv(work);
    if (id && work.display_name) twins.set(normalizeTitle(work.display_name), id);
  }
  const papers = new Map<string, Paper>();
  for (const work of works) {
    const title = work.display_name ?? "";
    if (!title) continue;
    const arxiv = workArxiv(work) ?? twins.get(normalizeTitle(title));
    const summary = abstractText(work.abstract_inverted_index);
    const score = 3 * (shares.get(work.id) ?? 0)
      + (normalizeTitle(title).includes(wanted) ? 1 : 0)
      + (startsWith.test(title) ? 2 : 0)
      + (INTRODUCES.test(summary) && normalizeTitle(summary).includes(wanted) ? 0.7 : 0)
      + (definesAcronym.test(summary) ? 0.7 : 0)
      + (arxiv && wikipediaArxiv.includes(arxiv) ? 1 : 0)
      + 0.5 * ((work.cited_by_count ?? 0) / most)
      - (OVERVIEW.test(title) ? 1.5 : 0);
    const paper: Paper = {
      title,
      year: (work.publication_date ?? "").slice(0, 4),
      ...(arxiv ? { arxiv } : {}),
      url: arxiv ? arxivUrl(arxiv) : work.doi ?? work.id,
      citations: work.cited_by_count ?? 0,
      score: Math.round(score * 100) / 100,
    };
    // A published version and its arXiv record are one paper: keep the better-scored entry, citations summed.
    const key = arxiv ?? `title:${normalizeTitle(title)}`;
    const known = papers.get(key);
    if (!known) papers.set(key, paper);
    else papers.set(key, { ...(known.score >= paper.score ? known : paper), citations: known.citations + paper.citations, year: [known.year, paper.year].filter(Boolean).sort()[0] ?? "" });
  }
  return [...papers.values()].sort((a, b) => b.score - a.score);
}

interface WikiPage {
  title?: string;
  missing?: boolean;
  extract?: string;
  pageprops?: { disambiguation?: string };
  langlinks?: Array<{ lang?: string; title?: string }>;
  revisions?: Array<{ slots?: { main?: { content?: string } } }>;
}

type WikiReply = { query?: { pages?: WikiPage[]; redirects?: Array<{ from?: string; to?: string; tofragment?: string }> } } | undefined;

/** The titles to try on Wikipedia: the term with a capital first letter, and its singular when it reads as a plural. */
export function wikipediaTitles(term: string): string[] {
  const title = term.charAt(0).toUpperCase() + term.slice(1);
  return /[a-z]{3}s$/.test(title) ? [title, title.slice(0, -1)] : [title];
}

/** The wikitext of one section, from its heading to the next heading of the same or a higher level. */
export function wikiSection(wikitext: string, heading: string): string | undefined {
  const lines = wikitext.split("\n");
  const wanted = heading.trim().toLowerCase();
  const start = lines.findIndex((line) => {
    const match = /^(=+)\s*(.*?)\s*\1\s*$/.exec(line);
    return match !== null && match[2]!.toLowerCase() === wanted;
  });
  if (start < 0) return undefined;
  const level = /^(=+)/.exec(lines[start]!)![1]!.length;
  let end = start + 1;
  while (end < lines.length) {
    const match = /^(=+)[^=].*?\1\s*$/.exec(lines[end]!);
    if (match && match[1]!.length <= level) break;
    end++;
  }
  return lines.slice(start + 1, end).join("\n");
}

/** The papers wikitext cites on arXiv, in order: each citation template's title and its eprint, arxiv or arXiv DOI. */
export function wikiCitations(wikitext: string): Array<{ title: string; arxiv: string }> {
  const cited: Array<{ title: string; arxiv: string }> = [];
  for (const match of wikitext.matchAll(/\{\{\s*cite[^{}]*\}\}/gi)) {
    const template = match[0];
    const id = /\|\s*(?:eprint|arxiv)\s*=\s*(\d{4}\.\d{4,5})/i.exec(template)?.[1] ?? arxivId(template);
    if (!id || cited.some((each) => each.arxiv === id)) continue;
    const title = /\|\s*title\s*=\s*([^|}]+)/i.exec(template)?.[1]?.replace(/\[\[(?:[^\]|]*\|)?([^\]]*)\]\]/g, "$1").trim() ?? "";
    cited.push({ title, arxiv: id });
  }
  return cited;
}

/** Wikitext without its embedded files, whose captions may hold links of their own. */
function withoutFiles(wikitext: string): string {
  let text = wikitext;
  for (let start = text.search(/\[\[(?:File|Image):/i); start >= 0; start = text.search(/\[\[(?:File|Image):/i)) {
    let depth = 0;
    let end = start;
    while (end < text.length) {
      if (text.startsWith("[[", end)) {
        depth++;
        end += 2;
      } else if (text.startsWith("]]", end)) {
        depth--;
        end += 2;
        if (depth === 0) break;
      } else end++;
    }
    text = text.slice(0, start) + text.slice(end);
  }
  return text;
}

/** Wikitext roughly as read: references, templates, files and markup removed, links kept as their text. */
export function wikiPlain(wikitext: string): string {
  let text = wikitext.replace(/<ref[^>]*\/>/gi, "").replace(/<ref[^>]*>[\s\S]*?<\/ref>/gi, "").replace(/<!--[\s\S]*?-->/g, "");
  for (let previous = ""; previous !== text;) {
    previous = text;
    text = text.replace(/\{\{[^{}]*\}\}/g, "");
  }
  return withoutFiles(text)
    .replace(/\[\[(?:[^\]|]*\|)?([^\]]*)\]\]/g, "$1")
    .replace(/'{2,}/g, "")
    .replace(/^=+.*=+\s*$/gm, "")
    .replace(/<[^>]+>/g, "")
    .split(/\s+/).filter(Boolean).join(" ");
}

function firstSentences(text: string): string {
  const sentences = text.match(/[^.。!?！？]+[.。!?！？]+(?=\s|$)/g) ?? [text];
  return sentences.slice(0, SNIPPET_SENTENCES).map((sentence) => sentence.trim()).join(" ");
}

/** The first existing Wikipedia article among the titles asked for, with redirects followed. */
export function wikipediaPage(reply: WikiReply): { page: WikiPage; section?: string } | undefined {
  const result = reply?.query;
  const page = result?.pages?.find((each) => each.title && !each.missing && each.pageprops?.disambiguation === undefined);
  if (!page?.title) return undefined;
  const section = result?.redirects?.find((redirect) => redirect.to === page.title)?.tofragment;
  return { page, ...(section ? { section } : {}) };
}

/** The entry for an article, read from the section about the term when a redirect points into one. */
export function wikipediaEntry(page: WikiPage, section: string | undefined, wikitext: string): Encyclopedia {
  const title = page.title!;
  const text = section ? wikiSection(wikitext, section) : undefined;
  const lead = page.revisions?.[0]?.slots?.main?.content ?? "";
  const cites = wikiCitations(text ?? lead)[0];
  const zh = page.langlinks?.find((link) => link.lang === "zh")?.title;
  const anchor = section ? `#${encodeURIComponent(section.replace(/ /g, "_"))}` : "";
  return {
    title,
    url: `https://en.wikipedia.org/wiki/${encodeURIComponent(title.replace(/ /g, "_"))}${anchor}`,
    extract: text !== undefined ? firstSentences(wikiPlain(text)) : (page.extract ?? "").trim(),
    ...(section ? { section } : {}),
    ...(zh ? { zh: { title: zh, url: `https://zh.wikipedia.org/wiki/${encodeURIComponent(zh.replace(/ /g, "_"))}` } } : {}),
    ...(cites ? { cites: { ...cites, url: arxivUrl(cites.arxiv) } } : {}),
  };
}

/** arXiv ids by normalized title, from an arXiv API Atom feed. */
export function arxivFeed(atom: string): Map<string, string> {
  const found = new Map<string, string>();
  for (const entry of atom.split("<entry>").slice(1)) {
    const id = arxivId(/<id>([^<]+)<\/id>/.exec(entry)?.[1]);
    const title = /<title>([\s\S]*?)<\/title>/.exec(entry)?.[1];
    if (id && title) found.set(normalizeTitle(title.replace(/&amp;/g, "&")), id);
  }
  return found;
}

let lastArxivRequest = 0;

/** One arXiv title search for papers without an arXiv id, spaced as arXiv's terms ask. */
async function arxivByTitle(papers: Paper[], fetch: Fetch): Promise<Paper[]> {
  const missing = papers.filter((paper) => !paper.arxiv);
  if (missing.length === 0) return papers;
  const phrases = missing.map((paper) => `ti:"${paper.title.replace(/[^\p{L}\p{N}\s-]+/gu, " ").split(/\s+/).filter(Boolean).join(" ")}"`);
  const wait = lastArxivRequest + ARXIV_INTERVAL_MS - Date.now();
  if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait));
  lastArxivRequest = Date.now();
  let found: Map<string, string>;
  try {
    found = arxivFeed(await fetch(`${ARXIV}?${query({ search_query: phrases.join(" OR "), max_results: 10 })}`));
  } catch {
    return papers;
  }
  return papers.map((paper) => {
    const id = paper.arxiv ?? found.get(normalizeTitle(paper.title));
    return id && !paper.arxiv ? { ...paper, arxiv: id, url: arxivUrl(id) } : paper;
  });
}

async function json<T>(fetch: Fetch, url: string): Promise<T | undefined> {
  return JSON.parse(await fetch(url)) as T;
}

export async function findSources(term: string, fetch: Fetch): Promise<Sources> {
  const phrase = term.includes(" ") ? `"${term}"` : term;
  const titled = `${OPENALEX}?${query({ "search.title": phrase, "per-page": TITLE_RESULTS, select: WORK_FIELDS })}`;
  const citing = `${OPENALEX}?${query({ "search.title_and_abstract": phrase, "per-page": COCITING_RESULTS, select: "id,referenced_works" })}`;
  const wiki = `${WIKIPEDIA}?${query({
    action: "query", format: "json", formatversion: 2, origin: "*", titles: wikipediaTitles(term).join("|"), redirects: 1,
    prop: "extracts|pageprops|langlinks|revisions", exintro: 1, explaintext: 1, exsentences: SNIPPET_SENTENCES, ppprop: "disambiguation",
    lllang: "zh", rvprop: "content", rvslots: "main", rvsection: 0,
  })}`;
  const [titleReply, citingReply, wikiReply] = await Promise.all([
    json<{ results?: Work[] }>(fetch, titled),
    json<{ results?: Work[] }>(fetch, citing),
    json<WikiReply>(fetch, wiki).catch(() => undefined),
  ]);
  const counts = new Map<string, number>();
  let using = 0;
  for (const work of citingReply?.results ?? []) {
    const references = new Set(work.referenced_works ?? []);
    if (references.size === 0) continue;
    using++;
    for (const reference of references) counts.set(reference, (counts.get(reference) ?? 0) + 1);
  }
  const shares = new Map([...counts].map(([id, count]) => [id, count / Math.max(using, 1)]));
  const works = new Map((titleReply?.results ?? []).map((work) => [work.id, work]));
  const cocited = [...counts].sort((a, b) => b[1] - a[1]).slice(0, COCITED_DETAILS).map(([id]) => id).filter((id) => !works.has(id));
  const located = wikipediaPage(wikiReply);
  // A redirect into a section needs the article's text, since the section, not the lead, is about the term.
  const [details, article] = await Promise.all([
    cocited.length
      ? json<{ results?: Work[] }>(fetch, `${OPENALEX}?${query({ filter: `openalex:${cocited.map((id) => id.slice(id.lastIndexOf("/") + 1)).join("|")}`, select: WORK_FIELDS })}`)
      : Promise.resolve(undefined),
    located?.section
      ? json<WikiReply>(fetch, `${WIKIPEDIA}?${query({
        action: "query", format: "json", formatversion: 2, origin: "*", titles: located.page.title!, prop: "revisions", rvprop: "content", rvslots: "main",
      })}`).catch(() => undefined)
      : Promise.resolve(undefined),
  ]);
  for (const work of details?.results ?? []) works.set(work.id, work);
  const wikitext = article?.query?.pages?.[0]?.revisions?.[0]?.slots?.main?.content ?? "";
  const wikipedia = located ? wikipediaEntry(located.page, located.section, wikitext) : undefined;
  const cited = wikipedia ? wikiCitations(located!.section ? wikiSection(wikitext, located!.section) ?? "" : located!.page.revisions?.[0]?.slots?.main?.content ?? "")
    .map((each) => each.arxiv) : [];
  const ranked = rankPapers(term, [...works.values()], shares, cited).filter((paper) => paper.score >= ORIGIN_SCORE);
  const papers = await arxivByTitle(ranked.slice(0, PAPERS_SHOWN), fetch);
  return { papers, ...(wikipedia ? { wikipedia } : {}) };
}
