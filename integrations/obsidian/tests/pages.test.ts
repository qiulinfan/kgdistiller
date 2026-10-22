import { DOMParser } from "linkedom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadPdfJs, requestUrl } from "obsidian";

vi.mock("obsidian", () => ({ loadPdfJs: vi.fn(), requestUrl: vi.fn() }));

import { htmlPage, PAGE_CHARS, PDF_PAGES, readWebPage } from "../src/pages";

const url = "https://publisher.example/paper";
const html = '<!doctype html><html><head><title>Paper</title></head><body><nav>Navigation</nav><main><h1>A coding network</h1><p>A network uses an encoder &amp; a decoder.</p><p>It reconstructs its input.</p><script>untrusted()</script></main><footer>Links</footer></body></html>';
function response(text: string, type: string, status = 200): void {
  vi.mocked(requestUrl).mockResolvedValue({ status, headers: { "Content-Type": type }, arrayBuffer: new TextEncoder().encode(text).buffer, text } as never);
}

beforeEach(() => { vi.clearAllMocks(); vi.stubGlobal("DOMParser", DOMParser); });
afterEach(() => vi.unstubAllGlobals());

describe("source page reading", () => {
  it("extracts text and title from a publisher page without running scripts or including navigation", async () => {
    response(html, "text/html");
    expect(await readWebPage(url)).toEqual({ url, title: "A coding network", text: "A coding network A network uses an encoder & a decoder. It reconstructs its input." });
    expect(loadPdfJs).not.toHaveBeenCalled();
  });

  it("reads plain UTF-8 sources, independently of file extension", async () => {
    response("这是一个 encoder。\n它生成 code。", "text/plain; charset=utf-8");
    expect((await readWebPage(`${url}.tex`)).text).toBe("这是一个 encoder。 它生成 code。");
  });

  it("bounds HTML excerpts without converting a source's text into a model summary", () => {
    expect(htmlPage(html.replace("It reconstructs its input.", "x".repeat(PAGE_CHARS)), url).text).toHaveLength(PAGE_CHARS);
  });

  it("reports access failures and unreadable binary formats", async () => {
    response("Forbidden", "text/html", 403);
    await expect(readWebPage(url)).rejects.toThrow("403");
    response("PK\u0003\u0004archive", "application/vnd.openxmlformats-officedocument.wordprocessingml.document");
    await expect(readWebPage(url)).rejects.toThrow("binary document format");
  });

  it("recognizes a PDF by its bytes and releases PDF.js after extracting its text", async () => {
    response("%PDF-fixture", "application/octet-stream");
    const document = {
      numPages: 2,
      getPage: vi.fn(async (number: number) => ({ getTextContent: async () => ({ items: [{ str: number === 1 ? "An encoder produces a code." : "A decoder reconstructs the input.", hasEOL: true }] }) })),
      destroy: vi.fn(async () => {}),
    };
    const getDocument = vi.fn(() => ({ promise: Promise.resolve(document) }));
    vi.mocked(loadPdfJs).mockResolvedValue({ getDocument });
    expect((await readWebPage(url)).text).toBe("An encoder produces a code. A decoder reconstructs the input.");
    expect(getDocument).toHaveBeenCalledWith({ data: expect.any(Uint8Array), isEvalSupported: false });
    expect(document.destroy).toHaveBeenCalledOnce();
  });

  it("reports a scanned PDF instead of inventing quotations", async () => {
    response("%PDF-fixture", "application/pdf");
    const document = { numPages: 1, getPage: async () => ({ getTextContent: async () => ({ items: [] }) }), destroy: vi.fn(async () => {}) };
    vi.mocked(loadPdfJs).mockResolvedValue({ getDocument: () => ({ promise: Promise.resolve(document) }) });
    await expect(readWebPage(url)).rejects.toThrow("no text layer");
    expect(document.destroy).toHaveBeenCalledOnce();
  });

  it("bounds PDF reading and releases the document when extraction fails", async () => {
    response("%PDF-fixture", "application/pdf");
    const document = { numPages: 100, getPage: vi.fn(async () => ({ getTextContent: async () => ({ items: [{ str: "text" }] }) })), destroy: vi.fn(async () => {}) };
    vi.mocked(loadPdfJs).mockResolvedValue({ getDocument: () => ({ promise: Promise.resolve(document) }) });
    await readWebPage(url);
    expect(document.getPage).toHaveBeenCalledTimes(PDF_PAGES);
    document.getPage.mockRejectedValueOnce(new Error("corrupt page"));
    await expect(readWebPage(url)).rejects.toThrow("corrupt page");
    expect(document.destroy).toHaveBeenCalledTimes(2);
  });
});
