/**
 * Where an unrecorded term comes from. First the references the selected
 * passage itself cites, read from the source's own reference list (block ids
 * `^reference-N`, as the owner's paper translations write them); then, from
 * the web, its likely originating paper, from OpenAlex, and its Wikipedia
 * article. The web lookup runs only when the owner asks, since it sends the
 * term to those services. The paper ranking follows the signals
 * that picked the right paper for BEiT, masked autoencoder, denoising
 * autoencoder and variational autoencoder: how many of the papers using the
 * term cite a candidate (co-citation share), a title that starts with the
 * term, an abstract that introduces it, and whether Wikipedia's article cites
 * it. Nothing here is written to the knowledge base.
 */

export const OPENALEX = "https://api.openalex.org/works";
export const WIKIPEDIA = "https://en.wikipedia.org/w/api.php";
/** Wikimedia asks every client to name itself with a contact address. */
export const USER_AGENT = "kgdistiller-obsidian (https://github.com/qiulinfan/kgdistiller)";
const TITLE_RESULTS = 15;
const COCITING_RESULTS = 100;
const COCITED_DETAILS = 4;
export const PAPERS_SHOWN = 3;

/** One GET returning parsed JSON; the plugin passes Obsidian's requestUrl. */
export type GetJson = (url: string) => Promise<unknown>;

export interface Paper {
  title: string;
  year: string;
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

interface Work {
  id: string;
  display_name?: string | null;
  publication_date?: string | null;
  cited_by_count?: number | null;
  doi?: string | null;
  abstract_inverted_index?: Record<string, number[]> | null;
  referenced_works?: string[] | null;
}

const WORK_FIELDS = "id,display_name,publication_date,cited_by_count,doi,abstract_inverted_index";
const INTRODUCES = /\b(we|this paper|this work)\s+(propose|introduce|present|call|name|develop|describe)\b/i;
const OVERVIEW = /\b(introduction to|tutorial|survey|review)\b/i;

function query(params: Record<string, string | number>): string {
  return Object.entries(params).map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`).join("&");
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

/** Rank candidate works for a term; `shares` maps work ids to their co-citation share. */
export function rankPapers(term: string, works: readonly Work[], shares: ReadonlyMap<string, number>, wikipediaArxiv: readonly string[]): Paper[] {
  const wanted = normalizeTitle(term);
  const most = Math.max(1, ...works.map((work) => work.cited_by_count ?? 0));
  const startsWith = new RegExp(`^\\s*${escapeRegExp(term)}\\s*[:\\-–]`, "i");
  const definesAcronym = new RegExp(`${escapeRegExp(term).replace(/\\ /g, "s?\\s+")}s?\\s*\\(([A-Z][A-Za-z0-9-]{1,8})\\)`, "i");
  const papers = works.map((work) => {
    const title = work.display_name ?? "";
    const summary = abstractText(work.abstract_inverted_index);
    const doi = (work.doi ?? "").toLowerCase();
    const score = 3 * (shares.get(work.id) ?? 0)
      + (normalizeTitle(title).includes(wanted) ? 1 : 0)
      + (startsWith.test(title) ? 2 : 0)
      + (INTRODUCES.test(summary) && normalizeTitle(summary).includes(wanted) ? 0.7 : 0)
      + (definesAcronym.test(summary) ? 0.7 : 0)
      + (wikipediaArxiv.some((id) => doi.includes(id)) ? 1 : 0)
      + 0.5 * ((work.cited_by_count ?? 0) / most)
      - (OVERVIEW.test(title) ? 1.5 : 0);
    return {
      title,
      year: (work.publication_date ?? "").slice(0, 4),
      url: work.doi ?? work.id,
      citations: work.cited_by_count ?? 0,
      score: Math.round(score * 100) / 100,
    };
  });
  return papers.filter((paper) => paper.title).sort((a, b) => b.score - a.score);
}

interface WikiPage {
  title?: string;
  missing?: boolean;
  extract?: string;
  pageprops?: { disambiguation?: string };
  langlinks?: Array<{ lang?: string; title?: string }>;
  revisions?: Array<{ slots?: { main?: { content?: string } } }>;
}

/** The titles to try on Wikipedia: the term with a capital first letter, and its singular when it reads as a plural. */
export function wikipediaTitles(term: string): string[] {
  const title = term.charAt(0).toUpperCase() + term.slice(1);
  return /[a-z]{3}s$/.test(title) ? [title, title.slice(0, -1)] : [title];
}

/** The first existing Wikipedia article among the titles asked for (redirects followed), and the arXiv ids its lead cites. */
export function wikipediaEntry(reply: unknown): { entry?: Encyclopedia; arxiv: string[] } {
  const result = (reply as {
    query?: { pages?: WikiPage[]; redirects?: Array<{ from?: string; to?: string; tofragment?: string }> };
  } | undefined)?.query;
  const page = result?.pages?.find((each) => each.title && !each.missing && each.pageprops?.disambiguation === undefined);
  if (!page?.title) return { arxiv: [] };
  const content = page.revisions?.[0]?.slots?.main?.content ?? "";
  const arxiv = [...content.matchAll(/(?:arxiv|eprint)\s*=\s*([0-9]{4}\.[0-9]{4,5})/gi)].map((match) => match[1]!);
  const section = result?.redirects?.find((redirect) => redirect.to === page.title)?.tofragment;
  const zh = page.langlinks?.find((link) => link.lang === "zh")?.title;
  const anchor = section ? `#${encodeURIComponent(section.replace(/ /g, "_"))}` : "";
  return {
    arxiv,
    entry: {
      title: page.title,
      url: `https://en.wikipedia.org/wiki/${encodeURIComponent(page.title.replace(/ /g, "_"))}${anchor}`,
      extract: (page.extract ?? "").trim(),
      ...(section ? { section } : {}),
      ...(zh ? { zh: { title: zh, url: `https://zh.wikipedia.org/wiki/${encodeURIComponent(zh.replace(/ /g, "_"))}` } } : {}),
    },
  };
}

export async function findSources(term: string, get: GetJson): Promise<Sources> {
  const phrase = term.includes(" ") ? `"${term}"` : term;
  const titled = `${OPENALEX}?${query({ "search.title": phrase, "per-page": TITLE_RESULTS, select: WORK_FIELDS })}`;
  const citing = `${OPENALEX}?${query({ "search.title_and_abstract": phrase, "per-page": COCITING_RESULTS, select: "id,referenced_works" })}`;
  const wiki = `${WIKIPEDIA}?${query({
    action: "query", format: "json", formatversion: 2, origin: "*", titles: wikipediaTitles(term).join("|"), redirects: 1,
    prop: "extracts|pageprops|langlinks|revisions", exintro: 1, explaintext: 1, exsentences: 2, ppprop: "disambiguation",
    lllang: "zh", rvprop: "content", rvslots: "main", rvsection: 0,
  })}`;
  const [titleReply, citingReply, wikiReply] = await Promise.all([get(titled), get(citing), get(wiki).catch(() => undefined)]);
  const counts = new Map<string, number>();
  let using = 0;
  for (const work of ((citingReply as { results?: Work[] })?.results ?? [])) {
    const references = new Set(work.referenced_works ?? []);
    if (references.size === 0) continue;
    using++;
    for (const reference of references) counts.set(reference, (counts.get(reference) ?? 0) + 1);
  }
  const shares = new Map([...counts].map(([id, count]) => [id, count / Math.max(using, 1)]));
  const works = new Map(((titleReply as { results?: Work[] })?.results ?? []).map((work) => [work.id, work]));
  const cocited = [...counts].sort((a, b) => b[1] - a[1]).slice(0, COCITED_DETAILS).map(([id]) => id).filter((id) => !works.has(id));
  if (cocited.length) {
    const ids = cocited.map((id) => id.slice(id.lastIndexOf("/") + 1)).join("|");
    const details = await get(`${OPENALEX}?${query({ filter: `openalex:${ids}`, select: WORK_FIELDS })}`);
    for (const work of ((details as { results?: Work[] })?.results ?? [])) works.set(work.id, work);
  }
  const { entry, arxiv } = wikipediaEntry(wikiReply);
  return { papers: rankPapers(term, [...works.values()], shares, arxiv).slice(0, PAPERS_SHOWN), ...(entry ? { wikipedia: entry } : {}) };
}
