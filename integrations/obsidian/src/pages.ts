/** Read a web source as text using the browser's HTML parser and Obsidian's PDF.js. */
import { loadPdfJs, requestUrl } from "obsidian";

import type { ForeignPage } from "./foreign";
import { normalizeSpace } from "./evidence";
import { fetchError, USER_AGENT } from "./sources";

/** Capture reads a bounded excerpt, rather than sending an entire book to the model. */
export const PAGE_CHARS = 24000;
export const PDF_PAGES = 20;

export function htmlPage(html: string, url: string): ForeignPage {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const title = doc.querySelector('meta[name="citation_title"]')?.getAttribute("content")
    || doc.querySelector("h1")?.textContent || doc.title || url;
  for (const element of Array.from(doc.querySelectorAll("script, style, noscript, nav, footer, aside, form, svg"))) element.remove();
  for (const element of Array.from(doc.querySelectorAll("p, div, h1, h2, h3, h4, li, br, tr, section"))) {
    element.append(doc.createTextNode("\n"));
  }
  const text = normalizeSpace((doc.querySelector("main, article, [role=main]") ?? doc.body).textContent ?? "");
  if (!text) throw new Error("The page has no readable text.");
  const links: Array<{ title: string; url: string }> = [];
  for (const link of Array.from(doc.querySelectorAll("a[href]"))) {
    try {
      const target = new URL(link.getAttribute("href")!, url);
      if (["http:", "https:"].includes(target.protocol) && !target.username && !target.password && !links.some((other) => other.url === target.href)) {
        links.push({ title: normalizeSpace(link.textContent ?? "") || target.href, url: target.href });
      }
    } catch { /* A malformed link is not a source to follow. */ }
  }
  return { url, title: normalizeSpace(title), text: text.slice(0, PAGE_CHARS), ...(links.length ? { links } : {}) };
}

interface PdfDocument {
  numPages: number;
  getPage(number: number): Promise<{ getTextContent(): Promise<{ items: Array<{ str?: string; hasEOL?: boolean }> }> }>;
  destroy(): Promise<void>;
}

async function pdfPage(data: Uint8Array, url: string): Promise<ForeignPage> {
  const pdfjs = await loadPdfJs() as { getDocument(options: { data: Uint8Array; isEvalSupported: boolean }): { promise: Promise<PdfDocument> } };
  const document = await pdfjs.getDocument({ data, isEvalSupported: false }).promise;
  try {
    let text = "";
    for (let number = 1; number <= Math.min(document.numPages, PDF_PAGES) && text.length < PAGE_CHARS; number++) {
      const page = await document.getPage(number);
      const content = await page.getTextContent();
      text += content.items.map((item) => typeof item.str === "string" ? `${item.str}${item.hasEOL ? "\n" : " "}` : "").join("") + "\n";
    }
    if (!text.trim()) throw new Error("This PDF has no text layer; scanned pages need OCR before Capture can quote them.");
    return { url, title: url.split("/").pop() ?? url, text: normalizeSpace(text).slice(0, PAGE_CHARS) };
  } finally {
    await document.destroy();
  }
}

/** Follow the source link itself: publisher HTML, PDF, or other UTF-8 text. */
export async function readWebPage(url: string): Promise<ForeignPage> {
  const response = await requestUrl({ url, headers: { "User-Agent": USER_AGENT }, throw: false });
  if (response.status >= 400) throw fetchError(url, response.status, response.text);
  const data = new Uint8Array(response.arrayBuffer);
  const type = Object.entries(response.headers).find(([key]) => key.toLowerCase() === "content-type")?.[1]?.toLowerCase() ?? "";
  if (type.includes("application/pdf") || new TextDecoder().decode(data.slice(0, 5)) === "%PDF-") return pdfPage(data, url);
  const charset = /charset\s*=\s*["']?([^"'\s;]+)/i.exec(type)?.[1] ?? "utf-8";
  let text: string;
  try { text = new TextDecoder(charset, { fatal: true }).decode(data); }
  catch { throw new Error("The source is a binary document, not HTML, a PDF, or UTF-8 text."); }
  if (/\x00/.test(text) || text.startsWith("PK\u0003\u0004")) throw new Error("This binary document format is not readable by Capture.");
  if (type.includes("html") || /<\s*(?:!doctype\s+html|html|head|body|title|h1|p)\b/i.test(text)) return htmlPage(text, url);
  if (!text.trim()) throw new Error("The source has no readable text.");
  return { url, title: url.split("/").pop() ?? url, text: normalizeSpace(text).slice(0, PAGE_CHARS) };
}
