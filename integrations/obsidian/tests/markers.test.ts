import { DOMParser, Event as DomEvent } from "linkedom";
import { describe, expect, it, vi } from "vitest";

// Obsidian's Platform and its global createSpan, as the plugin calls them, on a linkedom page.
vi.mock("obsidian", () => ({ Platform: { isMacOS: false } }));
const page = new DOMParser().parseFromString("<html><body></body></html>", "text/html");
vi.stubGlobal("createSpan", (options: { cls?: string; text?: string; attr?: Record<string, string> } = {}) => {
  const span = page.createElement("span");
  if (options.cls) span.className = options.cls;
  if (options.text !== undefined) span.textContent = options.text;
  for (const [name, value] of Object.entries(options.attr ?? {})) span.setAttribute(name, value);
  return span;
});

import { chipRenderedMarkers } from "../src/marker-editor";
import {
  columnOffset,
  jumpTarget,
  MarkerCache,
  markerAt,
  markerFormat,
  markerLabel,
  recordFilePath,
  recordFileUid,
  resolveName,
  scanLine,
  touches,
  type MarkerReport,
} from "../src/markers";

const report = (name: string, extra: Partial<MarkerReport> = {}): MarkerReport => ({
  base: "notes", source: "math/measure.md", line: 3, column: 5, name, text: name, uid: null, candidates: [], path: null, ...extra,
});

describe("scanning markers as kgd does", () => {
  it("finds Markdown and plain-text markers with their names, texts and offsets", () => {
    const line = "See --[[Measure space]]-- and --[[transformer·Head ablation|heads]]--.";
    expect(scanLine(line, "md")).toEqual([
      { from: 4, to: 25, name: "Measure space", text: "Measure space" },
      { from: 30, to: 69, name: "transformer·Head ablation", text: "heads" },
    ]);
    // An empty NAME is no marker, a bare wikilink is none either, and an empty TEXT shows the NAME.
    expect(scanLine("--[[ ]]-- [[link]] --[[A|]]--", "md")).toEqual([{ from: 19, to: 29, name: "A", text: "A" }]);
    expect(markerFormat("notes/a.TXT")).toBe("md");
    expect(markerFormat("notes/a.md")).toBe("md");
    expect(markerFormat("notes/a.tex")).toBe("tex");
    expect(markerFormat("notes/a.typ")).toBeUndefined();
  });

  it("skips TeX markers in comments and keeps nested braces", () => {
    const nested = String.raw`\begin{definition}{\kn{由 $\mathit{n}$ 个 random variable 构成的 vector}}`;
    expect(scanLine(nested, "tex").map((marker) => marker.name)).toEqual([String.raw`由 $\mathit{n}$ 个 random variable 构成的 vector`]);
    expect(scanLine(String.raw`\kn[Measure space]{} text % \kn{commented}`, "tex"))
      .toEqual([{ from: 0, to: 20, name: "Measure space", text: "" }]);
    expect(scanLine(String.raw`50\% then \kn{Kept}`, "tex").map((marker) => marker.name)).toEqual(["Kept"]);
    // A TeX marker whose target is blank is named by its text; Markdown syntax means nothing in TeX.
    expect(scanLine(String.raw`\kn[ ]{Sigma algebra} --[[X]]--`, "tex").map((marker) => marker.name)).toEqual(["Sigma algebra"]);
    expect(scanLine(String.raw`\kn{}`, "tex")).toEqual([]);
  });

  it("shows a marker's text, or its name when the text is empty", () => {
    expect(markerLabel({ name: "Measure space", text: "" })).toBe("Measure space");
    expect(markerLabel({ name: "Measure space", text: "measure spaces" })).toBe("measure spaces");
  });
});

describe("the cursor and a marker", () => {
  const marker = { from: 10, to: 20 };

  it("touches a marker when a cursor lies inside it or at its edge, or a range overlaps it", () => {
    for (const at of [10, 15, 20]) expect(touches(marker, [{ from: at, to: at }])).toBe(true);
    expect(touches(marker, [{ from: 0, to: 12 }])).toBe(true);
    expect(touches(marker, [{ from: 0, to: 30 }])).toBe(true);
    expect(touches(marker, [{ from: 2, to: 2 }, { from: 18, to: 25 }])).toBe(true);
  });

  it("leaves a marker untouched when every range lies outside it", () => {
    expect(touches(marker, [{ from: 9, to: 9 }, { from: 21, to: 30 }])).toBe(false);
    expect(touches(marker, [])).toBe(false);
  });

  it("finds the marker a line offset is on", () => {
    const line = "a --[[A]]-- b --[[B]]--";
    expect(markerAt(line, "md", 2)?.name).toBe("A");
    expect(markerAt(line, "md", 11)?.name).toBe("A");
    expect(markerAt(line, "md", 12)).toBeUndefined();
    expect(markerAt(line, "md", 20)?.name).toBe("B");
  });
});

describe("resolving a clicked marker", () => {
  it("takes the record kgd resolved for any marker of the same name key in the file", () => {
    const path = "/vaults/notes/.knowledge/entries/measure-space.md";
    const markers = [report("Measure space", { uid: "notes:measure-space", candidates: ["notes:measure-space"], path })];
    expect(resolveName(markers, "measure-SPACE")).toEqual({ state: "resolved", uid: "notes:measure-space", path });
  });

  it("lists the candidates of an ambiguous name, and tells unresolved and unread names apart", () => {
    const markers = [report("Head", { candidates: ["a:head", "b:head"] }), report("Nothing")];
    expect(resolveName(markers, "Head")).toEqual({ state: "ambiguous", candidates: ["a:head", "b:head"] });
    expect(resolveName(markers, "Nothing")).toEqual({ state: "unresolved" });
    expect(resolveName(markers, "Written since")).toEqual({ state: "unknown" });
  });

  it("opens the record file inside its base and knows a record file of this vault", () => {
    expect(recordFilePath("/vaults/notes/.knowledge/drafts/measure-space.md")).toBe(".knowledge/drafts/measure-space.md");
    expect(recordFilePath("/vaults/notes/math/a.md")).toBeUndefined();
    expect(recordFileUid(".knowledge/entries/measure-space.md", "notes")).toBe("notes:measure-space");
    expect(recordFileUid(".knowledge/drafts/measure-space.md", "notes")).toBe("notes:measure-space");
    for (const other of ["math/a.md", ".knowledge/entries/sub/a.md", ".knowledge/other/a.md"]) expect(recordFileUid(other, "notes")).toBeUndefined();
  });
});

describe("the jump target", () => {
  it("is the first marker kgd lists, with its line and column", () => {
    const markers = [report("A", { base: "notes", source: "math/a.tex", line: 7, column: 3 }), report("A", { line: 9 })];
    expect(jumpTarget(markers, { base: "notes", source: "math/old.tex", lines: "2-4" }))
      .toEqual({ base: "notes", source: "math/a.tex", line: 7, column: 3 });
  });

  it("is the first cited line without a marker, the page of a foreign record, and nothing without a source", () => {
    expect(jumpTarget([], { base: "notes", source: "math/a.md", lines: "12-14, 30" })).toEqual({ base: "notes", source: "math/a.md", line: 12 });
    expect(jumpTarget([], { base: "notes", source: "math/a.md" })).toEqual({ base: "notes", source: "math/a.md" });
    expect(jumpTarget([], { base: "notes", source: "https://en.wikipedia.org/wiki/Measure_(mathematics)" }))
      .toEqual({ url: "https://en.wikipedia.org/wiki/Measure_(mathematics)" });
    expect(jumpTarget([], undefined)).toBeUndefined();
    expect(jumpTarget([], { base: "notes" })).toBeUndefined();
  });

  it("places kgd's character column on a line of UTF-16 text", () => {
    expect(columnOffset("𝔐 is --[[A]]--", 6)).toBe(6);
    expect(columnOffset("abc", 1)).toBe(0);
  });
});

describe("the marker cache", () => {
  it("asks kgd once per file until the file is forgotten, and again after a failure", async () => {
    const read = vi.fn(async (path: string) => {
      if (path === "/bad.md") throw new Error("not a source");
      return [report("A")];
    });
    const cache = new MarkerCache(read);
    await cache.get("/a.md");
    await cache.get("/a.md");
    expect(read).toHaveBeenCalledTimes(1);
    cache.forget("/a.md");
    await cache.get("/a.md");
    expect(read).toHaveBeenCalledTimes(2);
    await expect(cache.get("/bad.md")).rejects.toThrow("not a source");
    await expect(cache.get("/bad.md")).rejects.toThrow("not a source");
    expect(read).toHaveBeenCalledTimes(4);
    cache.clear();
    await cache.get("/a.md");
    expect(read).toHaveBeenCalledTimes(5);
  });
});

describe("markers in reading view", () => {
  const rendered = (html: string) => new DOMParser().parseFromString(`<html><body><p>${html}</p></body></html>`, "text/html").querySelector("p") as unknown as HTMLElement;

  it("turns -- an internal link -- into a chip that follows no link", () => {
    const element = rendered(`See --<a class="internal-link" data-href="Measure space" href="Measure space">measure spaces</a>-- and <a class="internal-link" data-href="Other" href="Other">Other</a>.`);
    const opened: string[] = [];
    chipRenderedMarkers(element, (name) => opened.push(name));
    expect(element.textContent).toBe("See measure spaces and Other.");
    expect(element.querySelectorAll("a.internal-link")).toHaveLength(1);
    const chip = element.querySelector(".kgd-marker-chip")!;
    expect(chip.textContent).toBe("measure spaces");
    expect(chip.getAttribute("data-kgd-marker")).toBe("Measure space");
    const click = (modifier: boolean) => {
      const event = Object.assign(new DomEvent("click", { cancelable: true, bubbles: true }), { ctrlKey: modifier, metaKey: modifier }) as unknown as MouseEvent;
      chip.dispatchEvent(event);
      return event;
    };
    expect(click(false).defaultPrevented).toBe(true);
    expect(opened).toEqual([]);
    click(true);
    expect(opened).toEqual(["Measure space"]);
  });
});
