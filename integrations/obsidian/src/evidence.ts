/**
 * Placing a model's evidence quote in the source, the way `kgd check` reads
 * it (docs/model.md): a quote is fresh when its whitespace-collapsed text lies
 * inside one cited line range. The model names lines and copies a quote; the
 * plugin keeps the quote when it is verbatim there, moves the range when the
 * quote occurs exactly once elsewhere in the source, and otherwise snaps a
 * near-verbatim quote to the source's own text inside the cited lines. Only
 * the source's text is ever written as evidence.
 */

/** A 1-based inclusive line range. */
export type Range = [number, number];

/** The least similarity a near-verbatim quote needs to be replaced by the source's text. */
export const SNAP_SIMILARITY = 0.9;
/** Lines around the cited range searched when snapping. */
const SNAP_MARGIN = 1;

export function normalizeSpace(text: string): string {
  return text.split(/\s+/).filter(Boolean).join(" ");
}

/** Parse `a` or `a-b`, comma-separated, as kgd's `lines`; undefined when malformed. */
export function parseRanges(text: string): Range[] | undefined {
  const ranges: Range[] = [];
  for (const part of text.split(",")) {
    const match = /^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$/.exec(part);
    if (!match) return undefined;
    const start = Number(match[1]);
    const end = match[2] === undefined ? start : Number(match[2]);
    if (start < 1 || end < start) return undefined;
    ranges.push([start, end]);
  }
  return ranges.length ? ranges : undefined;
}

/** Ascending, disjoint ranges with overlapping and adjacent ones joined. */
export function mergeRanges(ranges: readonly Range[]): Range[] {
  const sorted = [...ranges].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const merged: Range[] = [];
  for (const [start, end] of sorted) {
    const last = merged[merged.length - 1];
    if (last && start <= last[1] + 1) last[1] = Math.max(last[1], end);
    else merged.push([start, end]);
  }
  return merged;
}

export function rangesText(ranges: readonly Range[]): string {
  return ranges.map(([start, end]) => (start === end ? `${start}` : `${start}-${end}`)).join(", ");
}

export type Placement =
  | { status: "verbatim" | "moved" | "snapped"; quote: string; range: Range }
  | { status: "missing"; reason: string };

/** The normalized text of lines start..end (1-based, clamped to the source). */
function cited(lines: readonly string[], [start, end]: Range): string {
  return normalizeSpace(lines.slice(Math.max(start, 1) - 1, Math.min(end, lines.length)).join("\n"));
}

/** Place one quote the model cited at `range`; the result's quote is what the record may hold. */
export function placeQuote(quote: string, range: Range, lines: readonly string[]): Placement {
  const wanted = normalizeSpace(quote);
  if (!wanted) return { status: "missing", reason: "the quote is empty" };
  if (range[1] > lines.length) return { status: "missing", reason: `the source has only ${lines.length} lines` };
  if (cited(lines, range).includes(wanted)) return { status: "verbatim", quote: wanted, range };
  const elsewhere = uniqueOccurrence(wanted, lines);
  if (elsewhere) return { status: "moved", quote: wanted, range: elsewhere };
  const window: Range = [Math.max(1, range[0] - SNAP_MARGIN), Math.min(lines.length, range[1] + SNAP_MARGIN)];
  const snapped = closestSpan(wanted, lines, window);
  if (snapped && snapped.similarity >= SNAP_SIMILARITY) return { status: "snapped", quote: snapped.text, range: snapped.range };
  return { status: "missing", reason: `it is not verbatim in lines ${rangesText([range])}` };
}

/** The lines of the quote's only occurrence in the whole source, as `kgd check` relocates evidence. */
function uniqueOccurrence(quote: string, lines: readonly string[]): Range | undefined {
  const { flat, offsets, numbers } = flatten(lines, [1, lines.length]);
  const first = flat.indexOf(quote);
  if (first < 0 || flat.indexOf(quote, first + 1) >= 0) return undefined;
  return [lineAt(offsets, numbers, first), lineAt(offsets, numbers, first + quote.length - 1)];
}

/** Lines joined as `kgd` does: each non-blank line's words joined by single spaces, lines by one space. */
function flatten(lines: readonly string[], [start, end]: Range): { flat: string; offsets: number[]; numbers: number[] } {
  const parts: string[] = [];
  const offsets: number[] = [];
  const numbers: number[] = [];
  let position = 0;
  for (let number = start; number <= end; number++) {
    const chunk = normalizeSpace(lines[number - 1] ?? "");
    if (!chunk) continue;
    if (parts.length) position += 1;
    offsets.push(position);
    numbers.push(number);
    parts.push(chunk);
    position += chunk.length;
  }
  return { flat: parts.join(" "), offsets, numbers };
}

function lineAt(offsets: readonly number[], numbers: readonly number[], position: number): number {
  let index = 0;
  while (index + 1 < offsets.length && offsets[index + 1]! <= position) index++;
  return numbers[index]!;
}

/**
 * The substring of the window's text closest to the quote, by semi-global edit
 * distance (free start and end in the text), with its similarity
 * 1 − distance / quote length and its line range.
 */
export function closestSpan(quote: string, lines: readonly string[], window: Range):
  { text: string; similarity: number; range: Range } | undefined {
  const { flat, offsets, numbers } = flatten(lines, window);
  const q = [...quote];
  const t = [...flat];
  if (!q.length || !t.length) return undefined;
  // distance[j] and start[j]: the best alignment of the quote's first i characters ending at text position j.
  let distance = new Array<number>(t.length + 1).fill(0);
  let start = Array.from({ length: t.length + 1 }, (_, j) => j);
  for (let i = 1; i <= q.length; i++) {
    const nextDistance = new Array<number>(t.length + 1);
    const nextStart = new Array<number>(t.length + 1);
    nextDistance[0] = i;
    nextStart[0] = 0;
    for (let j = 1; j <= t.length; j++) {
      const substitute = distance[j - 1]! + (q[i - 1] === t[j - 1] ? 0 : 1);
      const skipQuote = distance[j]! + 1;
      const skipText = nextDistance[j - 1]! + 1;
      if (substitute <= skipQuote && substitute <= skipText) {
        nextDistance[j] = substitute;
        nextStart[j] = start[j - 1]!;
      } else if (skipQuote <= skipText) {
        nextDistance[j] = skipQuote;
        nextStart[j] = start[j]!;
      } else {
        nextDistance[j] = skipText;
        nextStart[j] = nextStart[j - 1]!;
      }
    }
    distance = nextDistance;
    start = nextStart;
  }
  let end = 1;
  for (let j = 2; j <= t.length; j++) if (distance[j]! < distance[end]!) end = j;
  const from = start[end]!;
  const text = t.slice(from, end).join("").trim();
  if (!text) return undefined;
  // Character offsets into `flat` count UTF-16 units; recount them for the chosen span.
  const prefix = t.slice(0, from).join("").length;
  const length = t.slice(from, end).join("").length;
  return {
    text,
    similarity: 1 - distance[end]! / q.length,
    range: [lineAt(offsets, numbers, prefix), lineAt(offsets, numbers, prefix + Math.max(length - 1, 0))],
  };
}
