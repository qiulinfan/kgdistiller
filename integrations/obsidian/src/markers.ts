/**
 * Knowledge markers: a source names one kgdistiller record at the place it
 * states it, in the source's own syntax. kgd finds and resolves them
 * (`kgd markers`); the plugin scans a line only to draw a marker and to know
 * which one the cursor or a click is on, with kgd's own patterns:
 *
 * - Markdown and plain text: `--[[NAME]]--`, or `--[[NAME|TEXT]]--`;
 * - LaTeX: `\kn{NAME}`, or `\kn[NAME]{TEXT}`; a marker after an unescaped `%`
 *   is a comment.
 *
 * Which record a NAME means depends only on its name key and the file it lies
 * in, so a click is resolved by any marker of the same name in that file.
 */

import { firstLine, nameKey } from "./records";

/** How a file writes its markers: `md` for Markdown and plain text, `tex` for LaTeX. */
export type MarkerFormat = "md" | "tex";

const MD_MARKER = /--\[\[([^[\]|\n]+?)(?:\|([^[\]\n]*))?\]\]--/g;
const TEX_MARKER = /\\kn(?:\[([^\]\n]*)\])?\{((?:[^{}\n]|\{(?:[^{}\n]|\{[^{}\n]*\})*\})*)\}/g;
const TEX_COMMENT = /(?<!\\)%/;

/** The marker format of a file by its extension, as kgd's `source_format`; undefined for any other file. */
export function markerFormat(path: string): MarkerFormat | undefined {
  const name = path.split("/").pop() ?? "";
  const dot = name.lastIndexOf(".");
  const extension = dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
  return extension === "md" || extension === "txt" ? "md" : extension === "tex" ? "tex" : undefined;
}

/** One marker of a line: `from` and `to` are offsets in the line, `to` exclusive. */
export interface LineMarker {
  from: number;
  to: number;
  /** The NAME that names the record. */
  name: string;
  /** What the marker shows: its TEXT, which may be empty in LaTeX. */
  text: string;
}

/** Every marker of one line, as kgd's `scan` finds them. */
export function scanLine(line: string, format: MarkerFormat): LineMarker[] {
  const found: LineMarker[] = [];
  if (format === "tex") {
    const comment = TEX_COMMENT.exec(line);
    const code = comment ? line.slice(0, comment.index) : line;
    for (const match of code.matchAll(TEX_MARKER)) {
      const target = match[1];
      const shown = match[2] ?? "";
      const name = (target && target.trim() ? target : shown).trim();
      if (name) found.push({ from: match.index!, to: match.index! + match[0].length, name, text: shown });
    }
    return found;
  }
  for (const match of line.matchAll(MD_MARKER)) {
    const name = match[1]!.trim();
    if (name) found.push({ from: match.index!, to: match.index! + match[0].length, name, text: match[2] || name });
  }
  return found;
}

/** What a marker reads as: its TEXT, or its NAME when the TEXT is empty. */
export function markerLabel(marker: Pick<LineMarker, "name" | "text">): string {
  return marker.text.trim() ? marker.text : marker.name;
}

/** The marker of a line that `offset` lies in or at the edge of. */
export function markerAt(line: string, format: MarkerFormat, offset: number): LineMarker | undefined {
  return scanLine(line, format).find((marker) => marker.from <= offset && offset <= marker.to);
}

/**
 * Whether a selection touches a marker: a range that overlaps it, or a cursor
 * inside it or at its edge. A touched marker shows its raw text for editing.
 */
export function touches(marker: { from: number; to: number }, ranges: Iterable<{ from: number; to: number }>): boolean {
  for (const range of ranges) if (range.from <= marker.to && range.to >= marker.from) return true;
  return false;
}

/** One marker as `kgd markers` reports it; `column` is 1-based, in characters. */
export interface MarkerReport {
  base: string;
  source: string;
  line: number;
  column: number;
  name: string;
  text: string;
  /** The record it names, `base:id`, or null when it names none or several. */
  uid: string | null;
  candidates: string[];
  /** The record's file, absolute, or null. */
  path: string | null;
}

export interface MarkersResult {
  markers: MarkerReport[];
  total: number;
}

export type Resolution =
  | { state: "resolved"; uid: string; path: string | null }
  | { state: "ambiguous"; candidates: string[] }
  | { state: "unresolved" }
  /** kgd has not read a marker of this name in the file: the file has not been saved since it was written. */
  | { state: "unknown" };

/** The record a NAME means in a file whose markers kgd reported. */
export function resolveName(markers: readonly MarkerReport[], name: string): Resolution {
  const key = nameKey(name);
  const found = markers.find((marker) => nameKey(marker.name) === key);
  if (!found) return { state: "unknown" };
  if (found.uid) return { state: "resolved", uid: found.uid, path: found.path };
  return found.candidates.length > 1 ? { state: "ambiguous", candidates: found.candidates } : { state: "unresolved" };
}

/** A record file's path inside its base, from its absolute path: `.knowledge/entries/<id>.md` or `.knowledge/drafts/<id>.md`. */
export function recordFilePath(path: string): string | undefined {
  const at = path.lastIndexOf("/.knowledge/");
  return at >= 0 ? path.slice(at + 1) : undefined;
}

/** The uid of a record file of this vault, accepted or a draft; undefined for any other path. */
export function recordFileUid(path: string, base: string): string | undefined {
  const match = /^\.knowledge\/(?:entries|drafts)\/([^/]+)\.md$/.exec(path);
  return match ? `${base}:${match[1]}` : undefined;
}

export type JumpTarget =
  | { base: string; source: string; line?: number; column?: number }
  /** A foreign record cites a web page, where no marker lies. */
  | { url: string };

/**
 * Where a record jumps to: its first marker, which kgd lists from its own
 * source first, then its base, then the other bases; without one, the first
 * line it cites, or the page a foreign record cites.
 */
export function jumpTarget(markers: readonly MarkerReport[],
  cited?: { base: string; source?: string; lines?: string }): JumpTarget | undefined {
  const marker = markers[0];
  if (marker) return { base: marker.base, source: marker.source, line: marker.line, column: marker.column };
  if (!cited?.source) return undefined;
  if (/^https?:\/\//.test(cited.source)) return { url: cited.source };
  const line = firstLine(cited.lines);
  return { base: cited.base, source: cited.source, ...(line === undefined ? {} : { line }) };
}

/** The UTF-16 offset in `line` of kgd's 1-based character column. */
export function columnOffset(line: string, column: number): number {
  return Array.from(line).slice(0, Math.max(0, column - 1)).join("").length;
}

/**
 * `kgd markers FILE` per file, kept until the file or any record changes.
 * A failed read is not kept, so the next click asks kgd again.
 */
export class MarkerCache {
  private readonly files = new Map<string, Promise<MarkerReport[]>>();

  constructor(private readonly read: (path: string) => Promise<MarkerReport[]>) {}

  get(path: string): Promise<MarkerReport[]> {
    let found = this.files.get(path);
    if (!found) {
      found = this.read(path);
      this.files.set(path, found);
      found.catch(() => { if (this.files.get(path) === found) this.files.delete(path); });
    }
    return found;
  }

  forget(path: string): void {
    this.files.delete(path);
  }

  clear(): void {
    this.files.clear();
  }
}
