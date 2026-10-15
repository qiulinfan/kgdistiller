/**
 * The agent's capture job: draft the passage the owner selected while reading
 * as one record, by the rules of docs/model.md that the capture-kgdistiller
 * Skill follows. The plugin prepares everything deterministic: the source's
 * document type, the numbered passage, the records and drafts already citing
 * the source and the records a search finds for the selection. The model then
 * decides in as few calls as it can: an optional batched lookup, then a
 * submission. The plugin checks the submission, looks up every name it would
 * write before writing it, places each evidence quote in the source, and
 * writes the draft only when `kg_drafts` reports that `kgd accept` would take
 * it. Each refusal goes back to the model as the tool's result. The job never
 * accepts: the draft waits in the Capture view for the owner.
 */

import { runJob, type Complete, type Tool } from "./agent";
import { mergeRanges, normalizeSpace, parseRanges, placeQuote, rangesText, type Range } from "./evidence";
import type { ToolSpec } from "./model";
import type { DraftsResult } from "./drafts";
import type { RecordSummary, ResolveResult, SearchResult } from "./lookup-model";
import { fold } from "./records";

/** What `kgd sheet SOURCE --json` reports about a source. */
export interface Sheet {
  base: string;
  source: string;
  type: string;
  node_kinds: string[];
  relation_kinds: Record<string, string[]>;
  epistemic: string[];
  guidance: string;
  line_count: number;
  records: Array<{ uid: string; label: string; kind: string; class: string; lines: string; gloss: string }>;
  drafts: Array<{ id: string; label: string; kind: string; class: string; lines: string }>;
}

export interface CaptureInput {
  /** Absolute path of the source file. */
  path: string;
  /** The selection's first and last line, 1-based. */
  from: number;
  to: number;
  selection: string;
}

export type CaptureDecision = "new" | "unchanged" | "superseding" | "conflicting" | "borrowed";

export interface CaptureResult {
  /** What the agent took the selection to name or state. */
  item: string;
  decision: CaptureDecision;
  /** For new, the draft written; otherwise the record or draft this passage matches, generalizes or contradicts. */
  uid?: string;
  label?: string;
  /** The uid names a draft awaiting the owner, not an accepted record. */
  draft?: boolean;
  /** For superseding and conflicting: the record this passage proposes, as a file. */
  proposal?: string;
  report: string;
}

/** The kgd operations a capture needs; `KgdServer` provides them. */
export interface CaptureKgd {
  call<T>(tool: string, args: Record<string, unknown>): Promise<T>;
  command(args: string[]): Promise<{ report: Record<string, unknown>; failed: boolean }>;
  baseRoots(): Promise<Map<string, string>>;
}

/** The file operations a capture needs. */
export interface CaptureFiles {
  read(path: string): Promise<string>;
  /** The ids of a base's `entries/` and `drafts/`, folded. */
  ids(root: string): Promise<Set<string>>;
  /** Create a file that must not exist yet, with its directories. */
  create(path: string, text: string): Promise<void>;
  remove(path: string): Promise<void>;
}

export class CaptureError extends Error {}

/** Lines of context shown before and after the selection's paragraphs. */
const CONTEXT_LINES = 2;
/** The passage shown to the model is cut at about this many characters. */
const PASSAGE_CHARS = 8000;
const SEARCH_LIMIT = 8;
const LOOKUP_SEARCH_LIMIT = 5;
const MAX_ID_LENGTH = 80;

export const CAPTURE_SYSTEM = `You file one knowledge item that the owner selected while reading into their kgdistiller knowledge base, by calling submit. The item is exactly what the selected text names or states; the rest of the passage is context for understanding it, never something else to file. When the selected text makes a statement, the item is that statement, a relation, even when it cites other work. When it names a concept, the item is that concept. File that one item alone: never merge it with neighbouring sentences into a broader record. The names in the selection have been looked up for you; when you will write another name or term that the records shown do not cover, call lookup first, once, with all of them.

The knowledge model:
- A node is a noun-like object this source defines or originates, such as a concept, method, architecture or objective, or a theorem it states precisely.
- Every other statement that can be true or false, such as a result, comparison, finding or limitation, is a relation: it binds its participants to the roles of its kind, and its evidence itself states it.
- Each concept has exactly one record, owned by the source that defines or originates it. A concept this source only uses, or credits to other work (a citation, "proposed by", "as in", "earlier work used"), is never a node of this source, even when the passage explains it.
- A value is a link "[[uid]]" to a record shown in this conversation, or else a plain term, which marks a concept no base records yet. A value names a concept that could have its own record, such as a method, model, dataset, task or metric. Configurations, quantities and conditions, such as "fine-tune one block" or "75% masking", are never values: they stay in the statement and the evidence.
- A draft awaits the owner's review and is not knowledge yet. A value may still link a draft shown to you of this source's base, and when the item is a draft's item, decide unchanged with that draft's uid.
- Record only what the cited text states. A connection you would infer across passages or sources is not knowledge of this source.

Decide by comparing meanings with the records shown; a shared name does not make the same concept:
- unchanged: a shown record states the same knowledge point with the same meaning; each entails the other.
- superseding: a shown record holds this knowledge point with a different meaning, and this passage's version is strictly more general or corrects it.
- conflicting: a shown record holds it with a different meaning in any other way, a narrower one included.
- borrowed: the item is a concept this source only uses or credits to other work, or a sentence that only defines or describes such a concept; when the passage says other or earlier work proposed or used it, it is borrowed even if this source restates it. Nothing is filed; the report names the concept and the work the passage credits.
- new: none of the above.
For unchanged, superseding and conflicting, give that record's uid.

The submission, for new, superseding and conflicting:
- label: a short name in English, or for a relation a short English statement.
- aliases: a node's other names, including those the source uses in its own language. A relation has none.
- kind: one of the kinds below. A relation fills the roles of its kind; a node has none.
- requires: for a node, the direct prerequisites needed to understand it, as values. A relation's participants are its role values, so it rarely needs requires.
- statement: one or two sentences in the source's language that open with the knowledge itself, then its conditions.
- evidence: the selected text itself first, then other short quotes only when the item cannot be understood without them; each with its line or line range from the numbered passage, copied character for character, inline citation markup included.
- epistemic: how the cited passage itself supports it, from the list below; a result this source only reports from other work is stated.

Always give report: one short line for the owner, in the language of the passage, naming records by their labels, never by uid.`;

const LOOKUP_TOOL: ToolSpec = {
  type: "function",
  function: {
    name: "lookup",
    description: "Look names and phrases up in every base: the records named exactly so, the records using them as pending terms, and the records a search ranks for each phrase.",
    parameters: {
      type: "object",
      properties: {
        names: { type: "array", items: { type: "string" }, description: "The item's names in both languages and every term you will write as a value." },
        phrases: { type: "array", items: { type: "string" }, description: "Defining phrases to search for, in both languages." },
      },
      required: ["names", "phrases"],
      additionalProperties: false,
    },
  },
};

const SUBMIT_TOOL: ToolSpec = {
  type: "function",
  function: {
    name: "submit",
    description: "File the item. A refusal explains what to correct.",
    parameters: {
      type: "object",
      properties: {
        item: { type: "string", description: "First, in one sentence: the concept or statement the selected text names or states, and who defines or states it." },
        decision: { type: "string", enum: ["new", "unchanged", "superseding", "conflicting", "borrowed"] },
        record: { type: "string", description: "The uid of the recorded item, for unchanged, superseding and conflicting." },
        label: { type: "string" },
        aliases: { type: "array", items: { type: "string" } },
        kind: { type: "string" },
        roles: { type: "object", additionalProperties: { type: "array", items: { type: "string" } }, description: "For a relation kind: each role's values." },
        requires: { type: "array", items: { type: "string" } },
        statement: { type: "string" },
        evidence: {
          type: "array",
          items: {
            type: "object",
            properties: { lines: { type: "string" }, quote: { type: "string" } },
            required: ["lines", "quote"],
            additionalProperties: false,
          },
        },
        epistemic: { type: "string" },
        report: { type: "string" },
      },
      required: ["item", "decision", "report"],
      additionalProperties: false,
    },
  },
};

/** The lines shown to the model: the selection's paragraphs, a little context, and the nearest heading above. */
export function passageRange(lines: readonly string[], from: number, to: number): Range {
  let start = Math.max(1, Math.min(from, lines.length));
  let end = Math.max(start, Math.min(to, lines.length));
  const blank = (number: number) => (lines[number - 1] ?? "").trim() === "";
  while (start > 1 && !blank(start - 1)) start--;
  while (end < lines.length && !blank(end + 1)) end++;
  for (let count = 0; count < CONTEXT_LINES && start > 1;) {
    start--;
    if (!blank(start)) count++;
  }
  for (let count = 0; count < CONTEXT_LINES && end < lines.length;) {
    end++;
    if (!blank(end)) count++;
  }
  return [start, end];
}

export function numberedPassage(lines: readonly string[], [start, end]: Range): string {
  const shown: string[] = [];
  for (let number = start - 1; number >= 1; number--) {
    const line = lines[number - 1] ?? "";
    if (/^#{1,6}\s/.test(line)) {
      shown.push(`${number}| ${line}`, "…");
      break;
    }
  }
  let size = 0;
  for (let number = start; number <= end; number++) {
    const line = lines[number - 1] ?? "";
    if (!line.trim()) continue;
    size += line.length;
    if (size > PASSAGE_CHARS && shown.length > 0) {
      shown.push("…");
      break;
    }
    shown.push(`${number}| ${line}`);
  }
  return shown.join("\n");
}

/** A Markdown line roughly as reading view shows it: links, escapes, emphasis, code marks and block ids removed. */
export function shownText(line: string): string {
  return line
    .replace(/\s\^[\w-]+\s*$/, "")
    .replace(/\\([[\]()*_`$#])/g, "$1")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[\[(?:[^\]|]*\|)?([^\]]*)\]\]/g, "$1")
    .replace(/\*\*|__|`/g, "")
    .replace(/(^|\s)[*_](?=\S)|(?<=\S)[*_](?=\s|$)/g, "$1")
    .replace(/^\s{0,3}(?:#{1,6}\s+|>\s?|[-+*]\s+|\d+[.)]\s+)/, "");
}

/** Characters of a reading-view block that locate it in the source. */
const BLOCK_PREFIX = 60;

/**
 * The source lines a selection made in reading view covers: its text found
 * in the lines as reading view shows them, whitespace collapsed. Text that
 * occurs more than once is looked for inside the block that holds the
 * selection, found by the start of the block's text. Undefined when neither
 * places it once.
 */
export function locateSelection(selection: string, lines: readonly string[], block = ""): Range | undefined {
  const wanted = normalizeSpace(selection);
  if (!wanted) return undefined;
  const starts: number[] = [];
  let flat = "";
  for (const line of lines) {
    starts.push(flat.length);
    flat += `${normalizeSpace(shownText(line))} `;
  }
  const lineOf = (position: number) => {
    let index = 0;
    while (index + 1 < starts.length && starts[index + 1]! <= position) index++;
    return index + 1;
  };
  const only = (text: string, from = 0): number | undefined => {
    const at = flat.indexOf(text, from);
    return at >= 0 && flat.indexOf(text, at + 1) < 0 ? at : undefined;
  };
  let at = only(wanted);
  const prefix = normalizeSpace(block).slice(0, BLOCK_PREFIX);
  if (at === undefined && prefix.length >= Math.min(BLOCK_PREFIX, wanted.length + 1)) {
    const opening = only(prefix);
    const inside = opening === undefined ? -1 : flat.indexOf(wanted, opening);
    if (opening !== undefined && inside >= 0 && inside < opening + normalizeSpace(block).length) at = inside;
  }
  return at === undefined ? undefined : [lineOf(at), lineOf(at + wanted.length - 1)];
}

/** `slug(label)` of the record format: folded, non-alphanumeric runs to `-`, cut at a `-` within 80 characters. */
export function slug(label: string): string {
  let value = fold(label).replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-+|-+$/g, "");
  if (value.length > MAX_ID_LENGTH) {
    const cut = value.lastIndexOf("-", MAX_ID_LENGTH);
    value = cut > 0 ? value.slice(0, cut) : value.slice(0, MAX_ID_LENGTH);
  }
  return value;
}

/** The first free id from `slug(label)`, appending -2, -3, … on a collision. */
export function freeId(label: string, taken: ReadonlySet<string>): string | undefined {
  const base = slug(label);
  if (!base) return undefined;
  if (!taken.has(fold(base))) return base;
  for (let n = 2; ; n++) {
    const id = `${base.slice(0, MAX_ID_LENGTH - String(n).length - 1)}-${n}`;
    if (!taken.has(fold(id))) return id;
  }
}

function summaryLine(record: { uid: string; label: string; kind: string; class?: string; base?: string; lines?: string; gloss?: string }): string {
  const where = [record.base, record.lines ? `L${record.lines}` : ""].filter(Boolean).join(" ");
  return `[${record.uid}] ${record.label} · ${record.kind}${record.class ? ` ${record.class}` : ""}${where ? ` · ${where}` : ""} — ${record.gloss ?? ""}`;
}

export function typeText(sheet: Sheet): string {
  const roles = Object.entries(sheet.relation_kinds).map(([kind, names]) => `${kind} (${names.join(", ")})`).join("; ");
  return [
    `Document type ${sheet.type}:`,
    `Node kinds: ${sheet.node_kinds.join(", ")}`,
    `Relation kinds and their roles: ${roles}`,
    `Epistemic values: ${sheet.epistemic.join(", ")}`,
    `Guidance:\n${sheet.guidance.trim()}`,
  ].join("\n");
}

/** The selection's Latin-script names, such as "MAE" or "block-wise masking", with singular forms, for one batched resolve. */
export function selectionNames(selection: string): string[] {
  const names = new Set<string>();
  for (const match of selection.matchAll(/[A-Za-z][A-Za-z0-9]*(?:[-\s][A-Za-z0-9]+)*/g)) {
    const name = match[0].trim();
    if (name.length < 2) continue;
    names.add(name);
    if (/[a-z]{3}s$/.test(name)) names.add(name.slice(0, -1));
  }
  return [...names].slice(0, 16);
}

export function captureUser(input: CaptureInput, sheet: Sheet, lines: readonly string[], found: readonly RecordSummary[],
  named: readonly string[] = []): string {
  const range = passageRange(lines, input.from, input.to);
  const parts = [
    `Source: ${sheet.base} · ${sheet.source}`,
    `Selected, lines ${rangesText([[input.from, input.to]])}:\n${input.selection.trim()}`,
    `Numbered passage:\n${numberedPassage(lines, range)}`,
    sheet.records.length
      ? `Records already citing this source:\n${sheet.records.map(summaryLine).join("\n")}`
      : "No record cites this source yet.",
    found.length ? `Records a search finds for the selection:\n${found.map(summaryLine).join("\n")}` : "A search for the selection finds no record.",
    ...(named.length ? [`Names in the selection, looked up in every base:\n${named.join("\n")}`] : []),
  ];
  return parts.join("\n\n");
}

interface Draft {
  label: string;
  kind: string;
  aliases: string[];
  roles: Record<string, string[]>;
  requires: string[];
  statement: string;
  evidence: Array<{ lines: string; quote: string }>;
  epistemic?: string;
}

/** The record file of a draft, every scalar written as a JSON string, which YAML reads unchanged. */
export function recordFile(draft: Draft, source: string, lines: Range[], quotes: string[]): string {
  const front: string[] = [`label: ${JSON.stringify(draft.label)}`, `kind: ${JSON.stringify(draft.kind)}`];
  const list = (key: string, values: string[]) => {
    if (values.length) front.push(`${key}:`, ...values.map((value) => `  - ${JSON.stringify(value)}`));
  };
  list("aliases", draft.aliases);
  front.push(`source: ${JSON.stringify(source)}`, `lines: ${JSON.stringify(rangesText(lines))}`);
  if (draft.epistemic) front.push(`epistemic: ${JSON.stringify(draft.epistemic)}`);
  for (const [role, values] of Object.entries(draft.roles)) list(role, values);
  list("requires", draft.requires);
  const evidence = quotes.map((quote) => `> ${quote}`).join("\n\n");
  return `---\n${front.join("\n")}\n---\n${draft.statement.trim()}\n\n## Evidence\n\n${evidence}\n`;
}

/** The state of one capture job: what the model has seen and looked up. */
class CaptureState {
  /** Every record and draft shown to the model, by uid; only these may be linked. */
  readonly shown = new Map<string, { uid: string; label: string; kind: string; class?: string; base?: string; lines?: string; gloss?: string }>();
  /** The shown uids that are drafts. */
  readonly drafts = new Set<string>();
  /** Folded names already looked up. */
  readonly looked = new Set<string>();

  constructor(readonly sheet: Sheet, readonly root: string, readonly lines: readonly string[]) {}

  show(records: Iterable<{ uid: string; label: string; kind: string; class?: string; base?: string; lines?: string; gloss?: string }>): void {
    for (const record of records) if (!this.shown.has(record.uid)) this.shown.set(record.uid, record);
  }
}

export async function captureSelection(complete: Complete, kgd: CaptureKgd, files: CaptureFiles, input: CaptureInput):
  Promise<CaptureResult & { turns: number }> {
  const sheeted = await kgd.command(["sheet", input.path, "--json"]);
  if (sheeted.failed) throw new CaptureError(kgdMessage(sheeted.report) ?? "kgd sheet refused the source");
  const sheet = sheeted.report as unknown as Sheet;
  const root = (await kgd.baseRoots()).get(sheet.base);
  if (!root) throw new CaptureError(`base ${sheet.base} has no registered root`);
  const lines = (await files.read(input.path)).replace(/\r\n?/g, "\n").split("\n");
  const searched = await kgd.call<SearchResult>("kg_search", { query: input.selection.slice(0, 500), limit: SEARCH_LIMIT });
  const state = new CaptureState(sheet, root, lines);
  state.show(sheet.records.map((record) => ({ ...record, base: sheet.base })));
  // The source's drafts, which no search finds, may be linked or be the item itself.
  const sourceDrafts = sheet.drafts.map((draft) => ({ ...draft, uid: `${sheet.base}:${draft.id}`, base: sheet.base, gloss: DRAFT_GLOSS }));
  for (const draft of sourceDrafts) state.drafts.add(draft.uid);
  state.show(sourceDrafts);
  state.show(searched.results);
  const names = selectionNames(input.selection);
  const named = names.length
    ? [...resolvedText(state, await kgd.call<ResolveResult>("kg_resolve", { terms: names })), ...(await namedDrafts(kgd, state, names)).lines]
    : [];
  const lookup: Tool<CaptureResult> = { spec: LOOKUP_TOOL, run: (args) => lookUp(kgd, state, args) };
  const submit: Tool<CaptureResult> = { spec: SUBMIT_TOOL, run: (args) => file(kgd, files, state, args) };
  const result = await runJob(complete, {
    system: `${CAPTURE_SYSTEM}\n\n${typeText(sheet)}`,
    user: [
      captureUser(input, sheet, lines, searched.results.filter((hit) => !sheet.records.some((record) => record.uid === hit.uid)), named),
      ...(sourceDrafts.length ? [`Drafts of this source awaiting the owner's review:\n${sourceDrafts.map(summaryLine).join("\n")}`] : []),
    ].join("\n\n"),
    tools: [lookup, submit],
    turns: 4,
    // Low reasoning filed all eleven test captures right in two runs (median 6 s, slowest 20 s);
    // without it the median fell to 4 s, but a concept credited to earlier work was filed as new.
    effort: "low",
  });
  return { ...result.value, turns: result.turns };
}

async function lookUp(kgd: CaptureKgd, state: CaptureState, args: Record<string, unknown>): Promise<string> {
  const names = strings(args.names).slice(0, 32);
  const phrases = strings(args.phrases).slice(0, 8);
  const parts: string[] = [];
  if (names.length) {
    const resolved = await kgd.call<ResolveResult>("kg_resolve", { terms: names });
    parts.push(...resolvedText(state, resolved), ...(await namedDrafts(kgd, state, names)).lines);
  }
  for (const phrase of phrases) {
    const searched = await kgd.call<SearchResult>("kg_search", { query: phrase, limit: LOOKUP_SEARCH_LIMIT });
    state.show(searched.results);
    parts.push(`Search "${phrase}":`, ...(searched.results.length ? searched.results.map((hit) => `- ${summaryLine(hit)}`) : ["- no record"]));
  }
  return parts.join("\n") || "Nothing to look up.";
}

const DRAFT_GLOSS = "a draft awaiting the owner's review";

/** The drafts named so in every base, marked shown; `unseen` says whether any was not shown before. */
async function namedDrafts(kgd: CaptureKgd, state: CaptureState, names: string[]): Promise<{ lines: string[]; unseen: boolean }> {
  const found = await kgd.call<DraftsResult>("kg_drafts", { names });
  const drafts = found.drafts.filter((draft) => draft.label !== null);
  const unseen = drafts.some((draft) => !state.shown.has(draft.uid));
  const shown = drafts.map((draft) => ({ uid: draft.uid, label: draft.label!, kind: draft.kind ?? "", class: draft.class, base: draft.base, lines: draft.lines, gloss: DRAFT_GLOSS }));
  for (const draft of shown) state.drafts.add(draft.uid);
  state.show(shown);
  return { lines: shown.length ? ["Drafts named so:", ...shown.map((draft) => `  - ${summaryLine(draft)}`)] : [], unseen };
}

/** Each resolved term's senses and pending uses, marking them shown and the term looked up. */
function resolvedText(state: CaptureState, resolved: ResolveResult): string[] {
  const parts: string[] = [];
  for (const term of resolved.terms) {
    state.looked.add(fold(term.term));
    state.show(term.senses);
    const senses = term.senses.length ? term.senses.map((record) => `  - ${summaryLine(record)}`) : ["  - no record has this name"];
    const pending = term.pending.length ? [`  - a pending term in ${term.pending.length} record(s)`] : [];
    parts.push(`"${term.term}":`, ...senses, ...pending);
  }
  return parts;
}

async function file(kgd: CaptureKgd, files: CaptureFiles, state: CaptureState, args: Record<string, unknown>):
  Promise<string | { done: CaptureResult }> {
  const decision = args.decision;
  if (decision !== "new" && decision !== "unchanged" && decision !== "superseding" && decision !== "conflicting" && decision !== "borrowed") {
    return "error: decision must be new, unchanged, superseding, conflicting or borrowed";
  }
  const report = typeof args.report === "string" ? args.report.trim() : "";
  const item = typeof args.item === "string" ? args.item.trim() : "";
  if (decision === "borrowed") return { done: { item, decision, report } };
  const recorded = typeof args.record === "string" ? args.record.trim().replace(/^\[\[|\]\]$/g, "") : "";
  if (decision !== "new" && !state.shown.has(recorded)) return `error: record must be the uid of a record shown to you for ${decision}`;
  const draft = state.drafts.has(recorded);
  if (decision === "unchanged") {
    return { done: { item, decision, uid: recorded, label: state.shown.get(recorded)?.label, ...(draft ? { draft } : {}), report } };
  }
  if (draft) return `error: [[${recorded}]] is a draft awaiting the owner, not knowledge yet; decide unchanged when the item is that draft's item, or new`;
  const drafted = draftOf(state, args);
  if (typeof drafted === "string") return drafted;
  const { draft: record, values } = drafted;
  // Look up every name the record would write before writing it (docs/model.md, "Look up before every write").
  const unlooked = [...new Set([record.label, ...record.aliases, ...values.filter((value) => !value.startsWith("[["))]
    .filter((name) => !state.looked.has(fold(name))))];
  if (unlooked.length) {
    const before = new Set(state.shown.keys());
    const resolved = await kgd.call<ResolveResult>("kg_resolve", { terms: unlooked });
    const lines = resolvedText(state, resolved);
    const drafts = await namedDrafts(kgd, state, unlooked);
    const unseen = drafts.unseen || resolved.terms.some((term) => term.senses.some((found) => !before.has(found.uid)));
    if (unseen) {
      return [
        "Not filed yet. These names belong to records or drafts you had not seen:",
        ...lines,
        ...drafts.lines,
        "Link a value as \"[[uid]]\" when it is the same concept and keep the plain term otherwise. If the item itself is one of these records, decide unchanged, superseding or conflicting instead of new, and unchanged when it is one of these drafts. Then call submit again.",
      ].join("\n");
    }
  }
  const placed = placeEvidence(record, state.lines);
  if (typeof placed === "string") return placed;
  if (decision !== "new") {
    const proposal = recordFile(record, state.sheet.source, placed.ranges, placed.quotes);
    return { done: { item, decision, uid: recorded, label: state.shown.get(recorded)?.label, proposal, report } };
  }
  const id = freeId(record.label, await files.ids(state.root));
  if (!id) return "error: the label gives an empty id; use a label with letters or digits";
  const path = `${state.root}/.knowledge/drafts/${id}.md`;
  const uid = `${state.sheet.base}:${id}`;
  await files.create(path, recordFile(record, state.sheet.source, placed.ranges, placed.quotes));
  // The draft stays only when kgd accept would take it, with the drafts it links.
  const listed = await kgd.call<DraftsResult>("kg_drafts", { base: [state.sheet.base], source: [state.sheet.source] });
  const problems = listed.drafts.find((found) => found.uid === uid)?.problems ?? ["kg_drafts did not list the draft"];
  if (problems.length) {
    await files.remove(path);
    return `error: kgd accept would refuse the record: ${problems.join("; ")}. Correct it and call submit again.`;
  }
  return { done: { item, decision, uid, label: record.label, draft: true, report } };
}

/** The submitted draft checked against the source's type, with link values rewritten to kgd's link form. */
function draftOf(state: CaptureState, args: Record<string, unknown>): { draft: Draft; values: string[] } | string {
  const { sheet } = state;
  const label = typeof args.label === "string" ? args.label.trim() : "";
  const kind = typeof args.kind === "string" ? args.kind.trim() : "";
  const statement = typeof args.statement === "string" ? args.statement.trim() : "";
  if (!label || /[\r\n]/.test(label)) return "error: label must be one non-empty line";
  if (!statement) return "error: statement is required";
  if (/^##\s/m.test(statement)) return "error: statement must not contain level-2 headings";
  const isNode = sheet.node_kinds.includes(kind);
  const roleNames = sheet.relation_kinds[kind];
  if (!isNode && !roleNames) return `error: kind must be one of ${[...sheet.node_kinds, ...Object.keys(sheet.relation_kinds)].join(", ")}`;
  const epistemic = typeof args.epistemic === "string" && args.epistemic.trim() ? args.epistemic.trim() : undefined;
  if (epistemic && !sheet.epistemic.includes(epistemic)) return `error: epistemic must be one of ${sheet.epistemic.join(", ")}`;
  const rawRoles = args.roles && typeof args.roles === "object" && !Array.isArray(args.roles) ? args.roles as Record<string, unknown> : {};
  const roles: Record<string, string[]> = {};
  const values: string[] = [];
  const errors: string[] = [];
  const link = (value: string): string | undefined => {
    const match = /^\[\[([^\]|]+)(?:\|[^\]]*)?\]\]$/.exec(value);
    if (!match) {
      if (value.includes("[[") || value.includes("]]") || /[\r\n]/.test(value)) {
        errors.push(`"${value}" is neither a link "[[uid]]" nor a plain term`);
        return undefined;
      }
      return value;
    }
    const uid = match[1]!.trim();
    if (!state.shown.has(uid)) {
      errors.push(`[[${uid}]] is not a record shown to you; link only shown uids, or write a plain term`);
      return undefined;
    }
    const [base, id] = splitUid(uid);
    if (state.drafts.has(uid) && base !== sheet.base) {
      errors.push(`[[${uid}]] is a draft of another base, which only an accepted record can be linked across; write a plain term`);
      return undefined;
    }
    return base === sheet.base ? `[[${id}]]` : `[[${base}:${id}]]`;
  };
  const convert = (items: unknown): string[] => strings(items).map(link).filter((value): value is string => value !== undefined);
  for (const [role, items] of Object.entries(rawRoles)) {
    if (!roleNames?.includes(role)) {
      errors.push(isNode ? `a ${kind} node has no roles; drop "${role}"` : `${kind} has no role "${role}"; its roles are ${roleNames?.join(", ")}`);
      continue;
    }
    const converted = convert(items);
    if (converted.length) roles[role] = converted;
    values.push(...converted);
  }
  if (roleNames && Object.keys(roles).length === 0) errors.push(`a ${kind} relation needs at least one role value`);
  const requires = convert(args.requires);
  values.push(...requires);
  const evidence = Array.isArray(args.evidence)
    ? (args.evidence as Array<{ lines?: unknown; quote?: unknown }>).filter((item) => item && typeof item === "object")
      .map((item) => ({ lines: String(item.lines ?? ""), quote: String(item.quote ?? "") }))
    : [];
  if (evidence.length === 0) errors.push("evidence needs at least one quote");
  if (errors.length) return `error: ${errors.join("; ")}`;
  // A relation's label is a statement, so it takes no aliases.
  const aliases = isNode ? [...new Set(strings(args.aliases).filter((alias) => !/[\r\n]/.test(alias) && fold(alias) !== fold(label)))] : [];
  return { draft: { label, kind, aliases, roles, requires, statement, evidence, epistemic }, values };
}

/** Each evidence quote placed in the source; a refusal shows the cited lines so the model can copy them. */
function placeEvidence(draft: Draft, lines: readonly string[]): { ranges: Range[]; quotes: string[] } | string {
  const ranges: Range[] = [];
  const quotes: string[] = [];
  const problems: string[] = [];
  for (const [index, item] of draft.evidence.entries()) {
    const parsed = parseRanges(item.lines);
    if (!parsed) {
      problems.push(`quote ${index + 1}: lines "${item.lines}" is not a line or a range such as 12 or 12-14`);
      continue;
    }
    const range: Range = [parsed[0]![0], parsed[parsed.length - 1]![1]];
    const placed = placeQuote(item.quote, range, lines);
    if (placed.status === "missing") {
      const shown = lines.slice(range[0] - 1, Math.min(range[1], range[0] + 2)).map((line, offset) => `${range[0] + offset}| ${line}`).join("\n");
      problems.push(`quote ${index + 1}: ${placed.reason}. Those lines read:\n${shown}`);
      continue;
    }
    ranges.push(placed.range);
    quotes.push(placed.quote);
  }
  if (problems.length) return `error: evidence must be copied verbatim from the source.\n${problems.join("\n")}`;
  return { ranges: mergeRanges(ranges), quotes };
}

function splitUid(uid: string): [string, string] {
  const colon = uid.indexOf(":");
  return [uid.slice(0, colon), uid.slice(colon + 1)];
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string").map((item) => item.trim()).filter(Boolean) : [];
}

function kgdMessage(report: Record<string, unknown>): string | undefined {
  const error = report.error as { message?: unknown } | string | undefined;
  if (typeof error === "string") return error;
  return typeof error?.message === "string" ? error.message : undefined;
}
