/**
 * The kgdistiller record format as the plugin needs it: the fixed keys, the
 * class rule and the textual value grammar. Everything here mirrors kgd's
 * records.py so the plugin never disagrees with `kgd check` or `kgd index`.
 */

import caseFolds from "./case-folding.json";

/** The product's single knowledge tree at a base root. */
export const KNOWLEDGE_DIRECTORY = ".knowledge";
export const ENTRIES_DIRECTORY = `${KNOWLEDGE_DIRECTORY}/entries`;
export const DRAFTS_DIRECTORY = `${KNOWLEDGE_DIRECTORY}/drafts`;
export const SHEETS_DIRECTORY = `${KNOWLEDGE_DIRECTORY}/sheets`;

export const UNDERSTANDING_STATES = ["unknown", "not-yet-understood", "understood"] as const;
export type Understanding = (typeof UNDERSTANDING_STATES)[number];

/** The only keys the product knows; every other list-valued key is a role. */
export const FIXED_KEYS: ReadonlySet<string> = new Set([
  "label", "kind", "source", "lines", "aliases", "understanding",
  "epistemic", "requires", "tags", "cssclasses",
]);

export type RecordClass = "node" | "relation";

/** The shared class rule: a relation iff a non-fixed key holds a non-empty list. */
export function recordClass(frontmatter: Readonly<Record<string, unknown>>): RecordClass {
  return Object.entries(frontmatter).some(
    ([key, value]) => !FIXED_KEYS.has(key) && Array.isArray(value) && value.length > 0,
  ) ? "relation" : "node";
}

const RECORD_PATH_RE = /^\.knowledge\/(entries|drafts)\/([^/]+)\.md$/;

/** Where a vault path sits in the knowledge tree, when it is a record or a draft. */
export function recordLocation(path: string): { folder: "entries" | "drafts"; id: string } | undefined {
  const match = RECORD_PATH_RE.exec(path);
  return match ? { folder: match[1] as "entries" | "drafts", id: match[2]! } : undefined;
}

export const isRecordPath = (path: string): boolean => recordLocation(path) !== undefined;

export const entryPath = (id: string): string => `${ENTRIES_DIRECTORY}/${id}.md`;
export const draftPath = (id: string): string => `${DRAFTS_DIRECTORY}/${id}.md`;

/** The generated def/pending sheet of a source: `.knowledge/sheets/<source path>.md`. */
export const sheetPath = (source: string): string => `${SHEETS_DIRECTORY}/${source}.md`;

/** The source a sheet path belongs to, or undefined when the path is not a sheet. */
export function sourceOfSheet(path: string): string | undefined {
  const prefix = `${SHEETS_DIRECTORY}/`;
  if (!path.startsWith(prefix) || !path.endsWith(".md")) return undefined;
  const source = path.slice(prefix.length, -".md".length);
  return source === "" ? undefined : source;
}

const LINES_RE = /^(\d+)(?:-(\d+))?$/;

/** The 1-based first line of a `lines` value `a` or `a-b` with 1 <= a <= b; otherwise undefined. */
export function firstLine(lines: string | undefined): number | undefined {
  const match = LINES_RE.exec((lines ?? "").trim());
  if (!match) return undefined;
  const start = Number(match[1]);
  const end = match[2] === undefined ? start : Number(match[2]);
  return start >= 1 && start <= end ? start : undefined;
}

const CASE_FOLDS: ReadonlyMap<string, string> = new Map(Object.entries(caseFolds as Record<string, string>));

/**
 * NFKC, then Python's `str.casefold` character by character: `case-folding.json`
 * holds every character whose case folding differs from its lowercase mapping
 * (the Python suite checks it against `unicodedata`), and every other character
 * is lowercased on its own, so no final-sigma context applies.
 */
export function fold(text: string): string {
  let folded = "";
  for (const char of text.normalize("NFKC")) folded += CASE_FOLDS.get(char) ?? char.toLowerCase();
  return folded;
}

/**
 * The lookup key of a pending term, as kgd's `index.name_key`: the folded
 * text's runs of letters and digits joined by single spaces.
 */
export function nameKey(text: string): string {
  return (fold(text).match(/[\p{L}\p{N}]+/gu) ?? []).join(" ");
}

/** A record body split for display: the prose before the final `## Evidence` heading and the Evidence after it. */
export interface RecordBody {
  prose: string;
  evidence: string;
}

/**
 * Split a record file into prose and Evidence. The frontmatter follows the
 * record format: the file starts with a `---` line and the frontmatter ends at
 * the next line that is exactly `---`. Line endings are read as universal newlines.
 */
export function recordBody(text: string): RecordBody {
  let lines = text.replace(/\r\n?/g, "\n").split("\n");
  if (lines[0] === "---") {
    const end = lines.indexOf("---", 1);
    if (end > 0) lines = lines.slice(end + 1);
  }
  const heading = lines.lastIndexOf("## Evidence");
  if (heading < 0) return { prose: lines.join("\n").trim(), evidence: "" };
  return {
    prose: lines.slice(0, heading).join("\n").trim(),
    evidence: lines.slice(heading + 1).join("\n").trim(),
  };
}

export type ValueError =
  | "stray-brackets" | "fragment" | "path-form" | "own-base" | "foreign-form" | "empty" | "multiline";

/** One `requires` or role value: a local link, a foreign link, a pending term, or a grammar error. */
export type ParsedValue =
  | { kind: "local"; id: string }
  | { kind: "foreign"; base: string; id: string }
  | { kind: "term"; term: string }
  | { kind: "error"; error: ValueError };

const LINK_RE = /^\[\[([^[\]]*)\]\]$/;
const BASE_RE = /^[a-z0-9][a-z0-9-]*$/;
const ENTRY_PATH_PREFIX = `${ENTRIES_DIRECTORY}/`;

/**
 * Apply the value grammar. `base` is the record's own base name when known; the
 * plugin cannot read the home registry, so without it a `[[<own base>:id]]`
 * link is drawn as foreign instead of being reported (kgd check reports it).
 */
export function parseValue(raw: string, base?: string): ParsedValue {
  const text = raw.trim();
  if (text.includes("\n")) return { kind: "error", error: "multiline" };
  if (!text.includes("[[") && !text.includes("]]")) {
    return text ? { kind: "term", term: text } : { kind: "error", error: "empty" };
  }
  const match = LINK_RE.exec(text);
  if (!match) return { kind: "error", error: "stray-brackets" };
  const linked = match[1]!.split("|", 1)[0]!.trim();
  if (!linked) return { kind: "error", error: "empty" };
  if (linked.includes("#") || linked.includes("^")) return { kind: "error", error: "fragment" };
  let target = fold(linked);
  if (target.endsWith(".md")) target = target.slice(0, -3);
  if (target.includes("/")) {
    const id = target.startsWith(ENTRY_PATH_PREFIX) ? target.slice(ENTRY_PATH_PREFIX.length) : "";
    if (!id || id.includes("/") || id.includes(":")) return { kind: "error", error: "path-form" };
    return { kind: "local", id };
  }
  const colon = target.indexOf(":");
  if (colon >= 0) {
    const foreign = target.slice(0, colon);
    const id = target.slice(colon + 1);
    if (base !== undefined && foreign === base) return { kind: "error", error: "own-base" };
    if (!BASE_RE.test(foreign) || !id || id.includes(":")) return { kind: "error", error: "foreign-form" };
    return { kind: "foreign", base: foreign, id };
  }
  return { kind: "local", id: target };
}

/**
 * Textual resolution of a local link to a record path: an accepted record
 * resolves only to `entries/<id>.md`; a draft also to `drafts/<id>.md`.
 */
export function resolveLocal(id: string, fromDraft: boolean, exists: (path: string) => boolean): string | undefined {
  if (exists(entryPath(id))) return entryPath(id);
  if (fromDraft && exists(draftPath(id))) return draftPath(id);
  return undefined;
}

/** The understanding state as kgd normalizes it: empty or absent means `unknown`. */
export function understandingOf(value: unknown): Understanding | undefined {
  if (value === undefined || value === null || value === "") return "unknown";
  return (UNDERSTANDING_STATES as readonly unknown[]).includes(value) ? (value as Understanding) : undefined;
}
