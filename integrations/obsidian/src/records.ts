/**
 * The knowledge tree's paths and the record fields the plugin reads itself,
 * and the case folding of kgd's ids. Everything here mirrors kgd's
 * records.py so the plugin never disagrees with `kgd check` or `kgd index`.
 */

import caseFolds from "./case-folding.json";

/** The product's single knowledge tree at a base root. */
export const KNOWLEDGE_DIRECTORY = ".knowledge";
const ENTRIES_DIRECTORY = `${KNOWLEDGE_DIRECTORY}/entries`;
const SHEETS_DIRECTORY = `${KNOWLEDGE_DIRECTORY}/sheets`;

const ENTRY_PATH_RE = /^\.knowledge\/entries\/([^/]+)\.md$/;

/** The id of an accepted record's vault path, `.knowledge/entries/<id>.md`; undefined for any other path. */
export function entryId(path: string): string | undefined {
  return ENTRY_PATH_RE.exec(path)?.[1];
}

export const entryPath = (id: string): string => `${ENTRIES_DIRECTORY}/${id}.md`;

/** The source a sheet path belongs to, or undefined when the path is not a sheet. */
export function sourceOfSheet(path: string): string | undefined {
  const prefix = `${SHEETS_DIRECTORY}/`;
  if (!path.startsWith(prefix) || !path.endsWith(".md")) return undefined;
  const source = path.slice(prefix.length, -".md".length);
  return source === "" ? undefined : source;
}

const RANGE_RE = /^(\d+)(?:-(\d+))?$/;

/**
 * The 1-based first line of a `lines` value: ranges `a` or `a-b` with 1 <= a <= b,
 * comma-separated, ascending and disjoint. Otherwise undefined.
 */
export function firstLine(lines: string | undefined): number | undefined {
  let first: number | undefined;
  let previous = 0;
  for (const part of (lines ?? "").split(",")) {
    const match = RANGE_RE.exec(part.trim());
    if (!match) return undefined;
    const start = Number(match[1]);
    const end = match[2] === undefined ? start : Number(match[2]);
    if (start < 1 || start > end || start <= previous) return undefined;
    if (first === undefined) first = start;
    previous = end;
  }
  return first;
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
