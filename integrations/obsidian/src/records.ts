/**
 * The knowledge tree's path helpers, the first line of kgd's `lines` string,
 * and the case folding of kgd's ids. The plugin parses no record itself;
 * these mirror kgd's records.py so it never disagrees with `kgd check` or
 * `kgd index`.
 */

import caseFolds from "./case-folding.json";

/** The product's single knowledge tree at a base root. */
export const KNOWLEDGE_DIRECTORY = ".knowledge";
const ENTRIES_DIRECTORY = `${KNOWLEDGE_DIRECTORY}/entries`;

const ENTRY_PATH_RE = /^\.knowledge\/entries\/([^/]+)\.md$/;

/** The id of an accepted record's vault path, `.knowledge/entries/<id>.md`; undefined for any other path. */
export function entryId(path: string): string | undefined {
  return ENTRY_PATH_RE.exec(path)?.[1];
}

export const entryPath = (id: string): string => `${ENTRIES_DIRECTORY}/${id}.md`;

/** kgd's name key: the NFKC casefold's runs of letters and digits, joined by single spaces. */
export function nameKey(text: string): string {
  return (fold(text).match(/[\p{L}\p{N}]+/gu) ?? []).join(" ");
}

/**
 * What a same-named concept of another meaning adds to its label, as kgd's
 * `label_suffix`: its source file's name without the extension, or a web
 * page's last path segment, decoded, with underscores read as spaces.
 */
export function labelSuffix(source: string): string {
  if (/^https?:\/\//.test(source)) {
    const segment = source.split("?")[0]!.split("#")[0]!.replace(/\/+$/, "").split("/").pop() ?? "";
    let decoded = segment;
    try {
      decoded = decodeURIComponent(segment);
    } catch {
      decoded = segment;
    }
    return decoded.replace(/_/g, " ") || source;
  }
  const name = source.split("/").pop() ?? source;
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(0, dot) : name;
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
