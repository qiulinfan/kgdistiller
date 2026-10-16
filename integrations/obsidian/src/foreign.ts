/**
 * Foreign knowledge (docs/model.md, "Foreign knowledge and localization"): a
 * concept the text being read only uses or credits to other work is recorded
 * from the web page of the work that originates it, or of an article that
 * explains it, quoting that page. These are the pages a foreign draft may
 * cite, each with the text its Evidence quotes must be copied from: the
 * originating paper's arXiv abstract, then the Wikipedia article the agent
 * judged to be about the term.
 */

import { normalizeSpace } from "./evidence";
import type { Origin } from "./origin";
import { arxivPaper, wikipediaText, type Fetch } from "./sources";

export interface ForeignPage {
  url: string;
  title: string;
  /** The page's text as fetched; quotes must occur in it. */
  text: string;
}

/** The pages an origin offers, each fetched for its text; a page that cannot be read is left out and its failure returned. */
export async function foreignPages(fetch: Fetch, origin: Origin): Promise<{ pages: ForeignPage[]; failures: string[] }> {
  const failures: string[] = [];
  const read = async (what: string, load: () => Promise<ForeignPage | undefined>) => {
    try {
      return await load();
    } catch (error) {
      failures.push(`${what}: ${error instanceof Error ? error.message : String(error)}`);
      return undefined;
    }
  };
  const work = origin.work;
  const article = origin.wikipedia;
  const [paper, encyclopedia] = await Promise.all([
    work?.arxiv
      ? read("arXiv did not answer", async () => {
        const entry = await arxivPaper(fetch, work.arxiv!);
        return entry?.abstract ? { url: work.url ?? `https://arxiv.org/abs/${entry.arxiv}`, title: entry.title, text: `${entry.title}\n\n${entry.abstract}` } : undefined;
      })
      : undefined,
    article
      ? read("Wikipedia did not answer", async () => {
        const text = await wikipediaText(fetch, article);
        return text ? { url: article.url, title: article.section ? `${article.title} § ${article.section}` : article.title, text } : undefined;
      })
      : undefined,
  ]);
  return { pages: [paper, encyclopedia].filter((page): page is ForeignPage => page !== undefined), failures };
}

/** The pages as the capture model reads them, numbered from 1. */
export function pagesText(pages: readonly ForeignPage[]): string {
  return pages.map((page, index) => `[${index + 1}] ${page.title} — ${page.url}\n${page.text.trim()}`).join("\n\n");
}

/** Each quote as found in the page's text, whitespace collapsed; a string says which quote is not there. */
export function pageQuotes(quotes: readonly string[], page: ForeignPage): string[] | string {
  const text = normalizeSpace(page.text);
  const found: string[] = [];
  for (const [index, quote] of quotes.entries()) {
    const wanted = normalizeSpace(quote);
    if (!wanted || !text.includes(wanted)) return `quote ${index + 1} does not occur in page ${page.url}; copy it character for character from the page's text`;
    found.push(wanted);
  }
  return found;
}
