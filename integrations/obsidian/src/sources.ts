/**
 * What Find sources reads without a model:
 * - the source being read: its title and opening lines, with its authors and
 *   links;
 * - the references the selected passage cites, from the source's own
 *   reference list (block ids `^reference-N`, as the owner's paper
 *   translations write them);
 * - Wikipedia's articles for the term: the one its name leads to, read at the
 *   section a redirect points into, and the first ones Wikipedia's search
 *   finds, each with the first paper its text cites, for the agent to judge
 *   which is about the term as the text uses it;
 * - arXiv's record of a paper found by its title, for a paper shown without
 *   its own arXiv link.
 * The web lookups run only when the owner asks, since they send the term to
 * these services. Nothing here is written to the knowledge base.
 */

export const WIKIPEDIA = "https://en.wikipedia.org/w/api.php";
export const ARXIV = "https://export.arxiv.org/api/query";
/** Wikimedia asks every client to name itself with a contact address. */
export const USER_AGENT = "kgdistiller-obsidian (https://github.com/qiulinfan/kgdistiller)";
/** arXiv's API terms ask for at most one request every three seconds. */
export const ARXIV_INTERVAL_MS = 3000;
const SNIPPET_SENTENCES = 2;
/** Articles Wikipedia's search adds to the one the term's name leads to. */
export const WIKIPEDIA_RESULTS = 3;
/** Characters of a source's opening lines kept for the model. */
const OPENING_CHARS = 600;

/** One GET returning the response body as text; it throws a FetchError for an error status. */
export type Fetch = (url: string) => Promise<string>;

export class FetchError extends Error {}

/** The error for an error status, with the service's own message from a JSON body. */
export function fetchError(url: string, status: number, body: string): FetchError {
  let detail = "";
  try {
    const parsed = JSON.parse(body) as { message?: unknown; error?: unknown };
    detail = typeof parsed.message === "string" ? parsed.message : typeof parsed.error === "string" ? parsed.error : "";
  } catch {
    detail = "";
  }
  const host = /^https?:\/\/([^/]+)/.exec(url)?.[1] ?? url;
  return new FetchError(`${host} answered ${status}${detail ? `: ${detail.slice(0, 300)}` : ""}`);
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

/** One entry of a source's reference list that a passage cites. */
export interface Reference {
  number: string;
  text: string;
  url?: string;
}

/** The source being read: its title and its opening lines up to the next heading. */
export interface SourceOpening {
  title: string;
  text: string;
}

/**
 * The opening of a source: its first heading as the title, or else the file's
 * name, and its text up to the next heading, after any frontmatter.
 */
export function sourceOpening(lines: readonly string[], path: string): SourceOpening {
  let start = 0;
  if (lines[0]?.trim() === "---") {
    const end = lines.findIndex((line, index) => index > 0 && line.trim() === "---");
    start = end < 0 ? 0 : end + 1;
  }
  while (start < lines.length && !lines[start]!.trim()) start++;
  const heading = /^#\s+(.+)$/.exec(lines[start] ?? "")?.[1]?.trim();
  let end = start + 1;
  while (end < lines.length && !/^#{1,6}\s/.test(lines[end]!)) end++;
  const text = lines.slice(start, end).join(" ").split(/\s+/).filter(Boolean).join(" ");
  const name = path.slice(path.lastIndexOf("/") + 1).replace(/\.md$/i, "");
  return { title: heading ?? name, text: text.length > OPENING_CHARS ? `${text.slice(0, OPENING_CHARS)}…` : text };
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

/**
 * The arXiv id in an arXiv URL (abs, pdf, html, ar5iv or alphaxiv), an arXiv
 * DOI or an `arXiv:` citation, without its version.
 */
export function arxivId(text: string | null | undefined): string | undefined {
  if (!text) return undefined;
  const match = /(?:arxiv\.org\/(?:abs|pdf|html)\/|ar5iv(?:\.labs)?\.arxiv\.org\/(?:abs|html)\/|ar5iv\.org\/(?:abs|html)\/|alphaxiv\.org\/(?:abs|overview)\/|10\.48550\/arxiv\.|\barxiv:\s*)(\d{4}\.\d{4,5})/i.exec(text);
  return match?.[1];
}

export const arxivUrl = (id: string): string => `https://arxiv.org/abs/${id}`;

function query(params: Record<string, string | number>): string {
  return Object.entries(params).map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`).join("&");
}

/** Lowercased words with plural s dropped, so "auto-encoders" and "autoencoder" compare equal. */
export function normalizeTitle(text: string): string {
  return text.toLowerCase().replace(/auto-encod/g, "autoencod").replace(/[^a-z0-9 ]+/g, " ").split(/\s+/).filter(Boolean)
    .map((word) => (word.length > 3 && word.endsWith("s") ? word.slice(0, -1) : word)).join(" ");
}

interface WikiPage {
  title?: string;
  /** A search result's rank. */
  index?: number;
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

async function json<T>(fetch: Fetch, url: string): Promise<T | undefined> {
  return JSON.parse(await fetch(url)) as T;
}

/** What every article query asks for: the lead's first sentences, disambiguation, the Chinese article and the lead's wikitext. */
const ARTICLE_PROPS = {
  prop: "extracts|pageprops|langlinks|revisions", exintro: 1, explaintext: 1, exsentences: SNIPPET_SENTENCES, exlimit: "max",
  ppprop: "disambiguation", lllang: "zh", rvprop: "content", rvslots: "main", rvsection: 0,
};

/** Wikipedia's entry for a term's name, or undefined when it has no article under that name. */
export async function findWikipedia(term: string, fetch: Fetch): Promise<Encyclopedia | undefined> {
  const located = wikipediaPage(await json<WikiReply>(fetch, `${WIKIPEDIA}?${query({
    action: "query", format: "json", formatversion: 2, origin: "*", titles: wikipediaTitles(term).join("|"), redirects: 1, ...ARTICLE_PROPS,
  })}`));
  if (!located) return undefined;
  // A redirect into a section needs the article's text, since the section, not the lead, is about the term.
  const article = located.section
    ? await json<WikiReply>(fetch, `${WIKIPEDIA}?${query({
      action: "query", format: "json", formatversion: 2, origin: "*", titles: located.page.title!, prop: "revisions", rvprop: "content", rvslots: "main",
    })}`)
    : undefined;
  return wikipediaEntry(located.page, located.section, article?.query?.pages?.[0]?.revisions?.[0]?.slots?.main?.content ?? "");
}

/**
 * Wikipedia's articles that may be about a term: the one its name leads to,
 * then the first ones Wikipedia's search finds, without disambiguation pages.
 * A name or a search may lead to another thing of the same name, so the agent
 * judges which, if any, is about the term as the text uses it.
 */
export async function wikipediaArticles(term: string, fetch: Fetch): Promise<Encyclopedia[]> {
  const [named, searched] = await Promise.all([
    findWikipedia(term, fetch),
    json<WikiReply>(fetch, `${WIKIPEDIA}?${query({
      action: "query", format: "json", formatversion: 2, origin: "*", generator: "search", gsrsearch: term, gsrlimit: WIKIPEDIA_RESULTS, ...ARTICLE_PROPS,
    })}`),
  ]);
  const found = (searched?.query?.pages ?? [])
    .filter((page) => page.title && !page.missing && page.pageprops?.disambiguation === undefined && page.title !== named?.title)
    .sort((a, b) => (a.index ?? 0) - (b.index ?? 0))
    .map((page) => wikipediaEntry(page, undefined, ""));
  return [...(named ? [named] : []), ...found];
}

/** A paper as arXiv records it. */
export interface ArxivEntry {
  arxiv: string;
  title: string;
  year: string;
  abstract: string;
}

function xmlText(text: string): string {
  return text.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, "\"").replace(/&apos;/g, "'").replace(/&amp;/g, "&")
    .split(/\s+/).filter(Boolean).join(" ");
}

/** The entries of an arXiv API Atom feed. */
export function arxivFeed(atom: string): ArxivEntry[] {
  const entries: ArxivEntry[] = [];
  for (const entry of atom.split("<entry>").slice(1)) {
    const arxiv = arxivId(/<id>([^<]+)<\/id>/.exec(entry)?.[1]);
    const title = /<title>([\s\S]*?)<\/title>/.exec(entry)?.[1];
    const abstract = /<summary>([\s\S]*?)<\/summary>/.exec(entry)?.[1] ?? "";
    if (arxiv && title) entries.push({ arxiv, title: xmlText(title), year: /<published>(\d{4})/.exec(entry)?.[1] ?? "", abstract: xmlText(abstract) });
  }
  return entries;
}

/** When each fetcher may next ask arXiv: one plugin has one fetcher, so one clock. */
const arxivSlots = new WeakMap<Fetch, number>();

/** One arXiv API request, spaced at least three seconds from the last one through the same fetcher. */
async function arxivQuery(fetch: Fetch, params: Record<string, string | number>): Promise<ArxivEntry[]> {
  const at = Math.max(Date.now(), (arxivSlots.get(fetch) ?? 0) + ARXIV_INTERVAL_MS);
  arxivSlots.set(fetch, at);
  if (at > Date.now()) await new Promise((resolve) => setTimeout(resolve, at - Date.now()));
  return arxivFeed(await fetch(`${ARXIV}?${query(params)}`));
}

/** arXiv's record of the paper with this title, from one title search. */
export async function arxivByTitle(fetch: Fetch, title: string): Promise<ArxivEntry | undefined> {
  const phrase = title.replace(/[^\p{L}\p{N}\s-]+/gu, " ").split(/\s+/).filter(Boolean).join(" ");
  const wanted = normalizeTitle(title);
  const entries = await arxivQuery(fetch, { search_query: `ti:"${phrase}"`, max_results: 10 });
  return entries.find((entry) => normalizeTitle(entry.title) === wanted);
}

/** arXiv's record of one paper, with its abstract. */
export async function arxivPaper(fetch: Fetch, id: string): Promise<ArxivEntry | undefined> {
  return (await arxivQuery(fetch, { id_list: id, max_results: 1 }))[0];
}

/** The plain text of an article about a term: the section a redirect points into, or else the article's lead. */
export async function wikipediaText(fetch: Fetch, article: Encyclopedia): Promise<string> {
  if (article.section) {
    const reply = await json<WikiReply>(fetch, `${WIKIPEDIA}?${query({
      action: "query", format: "json", formatversion: 2, origin: "*", titles: article.title, prop: "revisions", rvprop: "content", rvslots: "main",
    })}`);
    const wikitext = reply?.query?.pages?.[0]?.revisions?.[0]?.slots?.main?.content ?? "";
    return wikiPlain(wikiSection(wikitext, article.section) ?? "");
  }
  const reply = await json<WikiReply>(fetch, `${WIKIPEDIA}?${query({
    action: "query", format: "json", formatversion: 2, origin: "*", titles: article.title, prop: "extracts", exintro: 1, explaintext: 1,
  })}`);
  return (reply?.query?.pages?.[0]?.extract ?? "").trim();
}
