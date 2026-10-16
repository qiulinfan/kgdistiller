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
 * it. A concept the passage only uses becomes a foreign draft quoting the page
 * of the work that originates or explains it; a changed meaning of a recorded
 * item becomes a draft replacing it. Each refusal goes back to the model as
 * the tool's result. The job never accepts: the draft waits in the Capture
 * view for the owner.
 */

import { runJob, type Complete, type Tool } from "./agent";
import { mergeRanges, normalizeSpace, parseRanges, placeQuote, rangesText, type Range } from "./evidence";
import type { ToolSpec } from "./model";
import type { DraftsResult } from "./drafts";
import { foreignPages, pageQuotes, pagesText, type ForeignPage } from "./foreign";
import type { GetResult, RecordSummary, ResolveResult, SearchResult } from "./lookup-model";
import { findOrigin, type OriginServices } from "./origin";
import { fold } from "./records";
import { citedReferences, sourceOpening } from "./sources";

/** What `kgd source SOURCE` reports about a source: its base, document type and the records and drafts citing it. */
export interface Profile {
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
  /** Absolute path of the registered source being read. */
  path: string;
  /**
   * The selection's first and last line in that source, 1-based. Without them
   * the selection was made in a record or draft shown in a side pane, and only
   * a concept the knowledge base does not hold yet can be drafted, as foreign
   * knowledge, in the base and type of the source being read.
   */
  from?: number;
  to?: number;
  selection: string;
  /** The text around a selection made in a side pane. */
  context?: string;
}

export type CaptureDecision = "new" | "unchanged" | "superseding" | "conflicting" | "borrowed";

export interface CaptureResult {
  /** What the agent took the selection to name or state. */
  item: string;
  decision: CaptureDecision;
  /** The draft written, or for unchanged the record or draft this passage matches. */
  uid?: string;
  label?: string;
  /** The uid names a draft awaiting the owner, not an accepted record. */
  draft?: boolean;
  /** The draft cites a web page: foreign knowledge. */
  foreign?: boolean;
  /** The draft replaces the accepted record with its id when accepted. */
  replaces?: boolean;
  /** For a changed meaning of a record of another base: the record this passage proposes, as a file, not written. */
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
- Every value is a link "[[uid]]" to a record or draft shown in this conversation. A value names a concept that has its own record, such as a method, model, dataset, task or metric. A concept no record or draft shown holds is left out of the values; the statement may still name it. Configurations, quantities and conditions, such as "fine-tune one block" or "75% masking", are never values: they stay in the statement and the evidence.
- A draft awaits the owner's review and is not knowledge yet. A value may still link a draft shown to you of this source's base, and when the item is a draft's item, decide unchanged with that draft's uid.
- Record only what the cited text states. A connection you would infer across passages or sources is not knowledge of this source.

Decide by comparing meanings with the records shown; a shared name does not make the same concept:
- unchanged: a shown record or draft states the same knowledge point with the same meaning; each entails the other.
- superseding: a shown record holds this knowledge point with a different meaning, and this passage's version is strictly more general or corrects it.
- conflicting: a shown record holds it with a different meaning in any other way, a narrower one included.
- borrowed: the item is a concept this source only uses or credits to other work, or a sentence that only defines or describes such a concept; when the passage says other or earlier work proposed or used it, it is borrowed even if this source restates it. It is drafted as foreign knowledge: submit borrowed with the concept's name as label; you then get the pages of the work that originates it or of an article explaining it, and submit borrowed again with page and the record fields, quoting that page.
- new: none of the above.
For unchanged, superseding and conflicting, give that record's uid. For superseding and conflicting the submission is the record's replacement, written in full as this passage states it; the owner decides whether it replaces the record.

The submission, for new, superseding, conflicting and a borrowed concept's page:
- label: a short name in English, or for a relation a short English statement.
- aliases: a node's other names, including those the source uses in its own language. A relation has none.
- kind: one of the kinds below. A relation fills the roles of its kind; a node has none.
- requires: for a node, the direct prerequisites needed to understand it, as values. A relation's participants are its role values, so it rarely needs requires.
- statement: one or two sentences in the source's language that open with the knowledge itself, then its conditions.
- evidence: the selected text itself first, then other short quotes only when the item cannot be understood without them; each with its line or line range from the numbered passage, copied character for character, inline citation markup included. For a borrowed concept: one to three short quotes of the chosen page that define it, copied character for character, with empty lines.
- epistemic: how the cited passage itself supports it, from the list below; a result this source only reports from other work is stated.

Always give report: one short line for the owner, in the language of the passage, naming records by their labels, never by uid.`;

/** The instructions added when the selection was made in a side pane, not in the source. */
export const SIDE_SYSTEM = `This selection was made in a knowledge record or draft shown beside the source, not in the source: there is no numbered passage. Decide only unchanged, when a record or draft shown already holds the concept, or borrowed, to draft it as foreign knowledge in the base and document type of the source being read.`;

const LOOKUP_TOOL: ToolSpec = {
  type: "function",
  function: {
    name: "lookup",
    description: "Look names and phrases up in every base: the records and drafts named exactly so, and the records a search ranks for each phrase.",
    parameters: {
      type: "object",
      properties: {
        names: { type: "array", items: { type: "string" }, description: "The item's names in both languages and every concept you will link as a value." },
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
        page: { type: "integer", description: "For a borrowed concept, once its pages are shown: the number of the page the record cites." },
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

export function typeText(profile: Profile): string {
  const roles = Object.entries(profile.relation_kinds).map(([kind, names]) => `${kind} (${names.join(", ")})`).join("; ");
  return [
    `Document type ${profile.type}:`,
    `Node kinds: ${profile.node_kinds.join(", ")}`,
    `Relation kinds and their roles: ${roles}`,
    `Epistemic values: ${profile.epistemic.join(", ")}`,
    `Guidance:\n${profile.guidance.trim()}`,
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

export function captureUser(input: CaptureInput, profile: Profile, lines: readonly string[], found: readonly RecordSummary[],
  named: readonly string[] = []): string {
  const where = input.from !== undefined && input.to !== undefined
    ? [
      `Selected, lines ${rangesText([[input.from, input.to]])}:\n${input.selection.trim()}`,
      `Numbered passage:\n${numberedPassage(lines, passageRange(lines, input.from, input.to))}`,
    ]
    : [`Selected in a side pane:\n${input.selection.trim()}`, ...(input.context?.trim() ? [`Around it:\n${input.context.trim()}`] : [])];
  const parts = [
    `Source being read: ${profile.base} · ${profile.source}`,
    ...where,
    profile.records.length
      ? `Records already citing this source:\n${profile.records.map(summaryLine).join("\n")}`
      : "No record cites this source yet.",
    found.length ? `Records a search finds for the selection:\n${found.map(summaryLine).join("\n")}` : "A search for the selection finds no record.",
    ...(named.length ? [`Names in the selection, looked up in every base:\n${named.join("\n")}`] : []),
  ];
  return parts.join("\n\n");
}

export interface Draft {
  label: string;
  kind: string;
  aliases: string[];
  roles: Record<string, string[]>;
  requires: string[];
  statement: string;
  evidence: Array<{ lines: string; quote: string }>;
  epistemic?: string;
}

/**
 * The record file of a draft, every scalar written as a JSON string, which YAML
 * reads unchanged. A foreign draft cites its page and has no lines.
 */
export function recordFile(draft: Draft, source: string, lines: Range[] | undefined, quotes: string[], understanding?: string,
  searchTerms = ""): string {
  const front: string[] = [`label: ${JSON.stringify(draft.label)}`, `kind: ${JSON.stringify(draft.kind)}`];
  const list = (key: string, values: string[]) => {
    if (values.length) front.push(`${key}:`, ...values.map((value) => `  - ${JSON.stringify(value)}`));
  };
  list("aliases", draft.aliases);
  front.push(`source: ${JSON.stringify(source)}`);
  if (lines) front.push(`lines: ${JSON.stringify(rangesText(lines))}`);
  if (understanding === "no") front.push(`understanding: "no"`);
  if (draft.epistemic) front.push(`epistemic: ${JSON.stringify(draft.epistemic)}`);
  for (const [role, values] of Object.entries(draft.roles)) list(role, values);
  list("requires", draft.requires);
  const evidence = quotes.map((quote) => `> ${quote}`).join("\n\n");
  const search = searchTerms.trim() ? `## Search terms\n\n${searchTerms.trim()}\n\n` : "";
  return `---\n${front.join("\n")}\n---\n${draft.statement.trim()}\n\n${search}## Evidence\n\n${evidence}\n`;
}

/** The state of one capture job: what the model has seen and looked up. */
class CaptureState {
  /** Every record and draft shown to the model, by uid; only these may be linked. */
  readonly shown = new Map<string, { uid: string; label: string; kind: string; class?: string; base?: string; lines?: string; gloss?: string }>();
  /** The shown uids that are drafts. */
  readonly drafts = new Set<string>();
  /** Folded names already looked up. */
  readonly looked = new Set<string>();
  /** The pages a borrowed concept may be drafted from, once found. */
  pages: ForeignPage[] | undefined;

  constructor(readonly profile: Profile, readonly root: string, readonly lines: readonly string[], readonly input: CaptureInput) {}

  /** Whether the selection lies in the source, so it can be drafted from its lines. */
  get inSource(): boolean {
    return this.input.from !== undefined && this.input.to !== undefined;
  }

  show(records: Iterable<{ uid: string; label: string; kind: string; class?: string; base?: string; lines?: string; gloss?: string }>): void {
    for (const record of records) if (!this.shown.has(record.uid)) this.shown.set(record.uid, record);
  }
}

export async function captureSelection(complete: Complete, kgd: CaptureKgd, files: CaptureFiles, origin: OriginServices,
  input: CaptureInput): Promise<CaptureResult> {
  const described = await kgd.command(["source", input.path]);
  if (described.failed) throw new CaptureError(kgdMessage(described.report) ?? "kgd source refused the source");
  const profile = described.report as unknown as Profile;
  const root = (await kgd.baseRoots()).get(profile.base);
  if (!root) throw new CaptureError(`base ${profile.base} has no registered root`);
  const lines = (await files.read(input.path)).replace(/\r\n?/g, "\n").split("\n");
  const searched = await kgd.call<SearchResult>("kg_search", { query: input.selection.slice(0, 500), limit: SEARCH_LIMIT });
  const state = new CaptureState(profile, root, lines, input);
  state.show(profile.records.map((record) => ({ ...record, base: profile.base })));
  // The source's drafts, which no search finds, may be linked or be the item itself.
  const sourceDrafts = profile.drafts.map((draft) => ({ ...draft, uid: `${profile.base}:${draft.id}`, base: profile.base, gloss: DRAFT_GLOSS }));
  for (const draft of sourceDrafts) state.drafts.add(draft.uid);
  state.show(sourceDrafts);
  state.show(searched.results);
  const names = selectionNames(input.selection);
  const named = names.length
    ? [...resolvedText(state, await kgd.call<ResolveResult>("kg_resolve", { terms: names })), ...(await namedDrafts(kgd, state, names)).lines]
    : [];
  const lookup: Tool<CaptureResult> = { spec: LOOKUP_TOOL, run: (args) => lookUp(kgd, state, args) };
  const submit: Tool<CaptureResult> = { spec: SUBMIT_TOOL, run: (args) => file(kgd, files, origin, state, args) };
  return runJob(complete, {
    system: `${CAPTURE_SYSTEM}${state.inSource ? "" : `\n\n${SIDE_SYSTEM}`}\n\n${typeText(profile)}`,
    user: [
      captureUser(input, profile, lines, searched.results.filter((hit) => !profile.records.some((record) => record.uid === hit.uid)), named),
      ...(sourceDrafts.length ? [`Drafts of this source awaiting the owner's review:\n${sourceDrafts.map(summaryLine).join("\n")}`] : []),
    ].join("\n\n"),
    tools: [lookup, submit],
    turns: 6,
    // Low reasoning filed all eleven test captures right in two runs (median 6 s, slowest 20 s);
    // without it the median fell to 4 s, but a concept credited to earlier work was filed as new.
    effort: "low",
  });
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

/** Each resolved name's senses, marking them shown and the name looked up. */
function resolvedText(state: CaptureState, resolved: ResolveResult): string[] {
  const parts: string[] = [];
  for (const term of resolved.terms) {
    state.looked.add(fold(term.term));
    state.show(term.senses);
    const senses = term.senses.length ? term.senses.map((record) => `  - ${summaryLine(record)}`) : ["  - no record has this name"];
    parts.push(`"${term.term}":`, ...senses);
  }
  return parts;
}

async function file(kgd: CaptureKgd, files: CaptureFiles, origin: OriginServices, state: CaptureState, args: Record<string, unknown>):
  Promise<string | { done: CaptureResult }> {
  const decision = args.decision;
  if (decision !== "new" && decision !== "unchanged" && decision !== "superseding" && decision !== "conflicting" && decision !== "borrowed") {
    return "error: decision must be new, unchanged, superseding, conflicting or borrowed";
  }
  const report = typeof args.report === "string" ? args.report.trim() : "";
  const item = typeof args.item === "string" ? args.item.trim() : "";
  if (!state.inSource && decision !== "unchanged" && decision !== "borrowed") {
    return "error: a selection made in a side pane can only be unchanged or borrowed";
  }
  if (decision === "borrowed") return fileForeign(kgd, files, origin, state, args, item, report);
  const recorded = typeof args.record === "string" ? args.record.trim().replace(/^\[\[|\]\]$/g, "") : "";
  if (decision !== "new" && !state.shown.has(recorded)) return `error: record must be the uid of a record shown to you for ${decision}`;
  const draft = state.drafts.has(recorded);
  if (decision === "unchanged") {
    return { done: { item, decision, uid: recorded, label: state.shown.get(recorded)?.label, ...(draft ? { draft } : {}), report } };
  }
  if (draft) return `error: [[${recorded}]] is a draft awaiting the owner, not knowledge yet; decide unchanged when the item is that draft's item, or new`;
  const drafted = await checkedDraft(kgd, state, args, decision === "new");
  if (typeof drafted === "string") return drafted;
  const placed = placeEvidence(drafted, state.lines);
  if (typeof placed === "string") return placed;
  const { profile } = state;
  if (decision !== "new") {
    const [base, id] = splitUid(recorded);
    const text = (understanding?: string) => recordFile(drafted, profile.source, placed.ranges, placed.quotes, understanding);
    if (base !== profile.base) {
      // A replacement lives in its record's base; moving a record across bases is the compile Skill's relocation.
      return { done: { item, decision, uid: recorded, label: state.shown.get(recorded)?.label, proposal: text(), report } };
    }
    const path = `${state.root}/.knowledge/drafts/${id}.md`;
    if ((await kgd.call<DraftsResult>("kg_drafts", { base: [base] })).drafts.some((found) => found.uid === recorded)) {
      return { done: { item, decision, uid: recorded, label: state.shown.get(recorded)?.label, draft: true, replaces: true, report: `${report} A replacement of this record already waits for you.`.trim() } };
    }
    const current = await kgd.call<GetResult>("kg_get", { uids: [recorded] });
    return writeDraft(kgd, files, state, path, recorded, text(current.records[0]?.understanding), { item, decision, uid: recorded, label: drafted.label, draft: true, replaces: true, report });
  }
  const id = freeId(drafted.label, await files.ids(state.root));
  if (!id) return "error: the label gives an empty id; use a label with letters or digits";
  const uid = `${profile.base}:${id}`;
  return writeDraft(kgd, files, state, `${state.root}/.knowledge/drafts/${id}.md`, uid,
    recordFile(drafted, profile.source, placed.ranges, placed.quotes), { item, decision, uid, label: drafted.label, draft: true, report });
}

/**
 * A borrowed concept, drafted as foreign knowledge. The first borrowed
 * submission finds where the concept comes from and shows its pages; the next
 * one, naming a page, is checked against that page's text and written.
 */
async function fileForeign(kgd: CaptureKgd, files: CaptureFiles, origin: OriginServices, state: CaptureState,
  args: Record<string, unknown>, item: string, report: string): Promise<string | { done: CaptureResult }> {
  const { profile, input } = state;
  if (!state.pages) {
    const term = typeof args.label === "string" && args.label.trim() ? args.label.trim() : input.selection.trim();
    const context = state.inSource ? state.lines.slice(input.from! - 1, input.to).join("\n") : input.context ?? input.selection;
    const opening = state.lines.some((line) => line.trim()) ? sourceOpening(state.lines, input.path) : undefined;
    const references = state.inSource ? citedReferences(context, state.lines) : [];
    const found = await findOrigin(origin, { term, context, ...(opening ? { source: opening } : {}), references });
    const { pages, failures } = await foreignPages(origin.fetch, found.origin);
    state.pages = pages;
    if (pages.length === 0) {
      const why = [found.origin.reason, ...found.failures, ...failures].filter(Boolean).join(" ");
      return { done: { item, decision: "borrowed", label: term, report: `${report} No page that originates or explains it was found, so nothing was drafted.${why ? ` ${why}` : ""}`.trim() } };
    }
    return [
      `Where "${term}" comes from, by page:`,
      pagesText(pages),
      "Draft it as foreign knowledge: call submit with decision borrowed, page (the page that states the concept best, preferring the originating work), and the record fields, its kind one of the document type's, its evidence one to three quotes copied character for character from that page, with empty lines.",
    ].join("\n\n");
  }
  const number = typeof args.page === "number" ? args.page : Number(args.page);
  const page = Number.isInteger(number) ? state.pages[number - 1] : undefined;
  if (!page) return `error: page must be the number of a page shown to you, 1 to ${state.pages.length}`;
  const drafted = await checkedDraft(kgd, state, args, true);
  if (typeof drafted === "string") return drafted;
  const quotes = pageQuotes(drafted.evidence.map((quote) => quote.quote), page);
  if (typeof quotes === "string") return `error: ${quotes}`;
  const id = freeId(drafted.label, await files.ids(state.root));
  if (!id) return "error: the label gives an empty id; use a label with letters or digits";
  const uid = `${profile.base}:${id}`;
  return writeDraft(kgd, files, state, `${state.root}/.knowledge/drafts/${id}.md`, uid, recordFile(drafted, page.url, undefined, quotes),
    { item, decision: "borrowed", uid, label: drafted.label, draft: true, foreign: true, report });
}

/** Write a draft, keeping it only when `kg_drafts` finds nothing `kgd accept` would refuse. */
async function writeDraft(kgd: CaptureKgd, files: CaptureFiles, state: CaptureState, path: string, uid: string, text: string,
  result: CaptureResult): Promise<string | { done: CaptureResult }> {
  await files.create(path, text);
  const listed = await kgd.call<DraftsResult>("kg_drafts", { base: [state.profile.base] });
  const problems = listed.drafts.find((found) => found.uid === uid)?.problems ?? ["kg_drafts did not list the draft"];
  if (problems.length) {
    await files.remove(path);
    return `error: kgd accept would refuse the record: ${problems.join("; ")}. Correct it and call submit again.`;
  }
  return { done: result };
}

/**
 * The submission as a draft, after every name it would write has been looked
 * up (docs/model.md, "Look up before every draft"). Names that turn out to be
 * records or drafts the model had not seen go back to it first.
 */
async function checkedDraft(kgd: CaptureKgd, state: CaptureState, args: Record<string, unknown>, isNew: boolean): Promise<Draft | string> {
  const drafted = draftOf(state, args);
  if (typeof drafted === "string") return drafted;
  const unlooked = [...new Set([drafted.label, ...drafted.aliases].filter((name) => !state.looked.has(fold(name))))];
  if (!unlooked.length) return drafted;
  const before = new Set(state.shown.keys());
  const resolved = await kgd.call<ResolveResult>("kg_resolve", { terms: unlooked });
  const lines = resolvedText(state, resolved);
  const drafts = await namedDrafts(kgd, state, unlooked);
  const unseen = drafts.unseen || resolved.terms.some((term) => term.senses.some((found) => !before.has(found.uid)));
  if (!unseen) return drafted;
  return [
    "Not filed yet. These names belong to records or drafts you had not seen:",
    ...lines,
    ...drafts.lines,
    isNew
      ? "If the item itself is one of these records, decide unchanged, superseding or conflicting instead, and unchanged when it is one of these drafts; link a value as \"[[uid]]\" when it is the same concept. Then call submit again."
      : "Link a value as \"[[uid]]\" when it is the same concept. Then call submit again.",
  ].join("\n");
}

/** The submitted draft checked against the source's type, with link values rewritten to kgd's link form. */
function draftOf(state: CaptureState, args: Record<string, unknown>): Draft | string {
  const { profile } = state;
  const label = typeof args.label === "string" ? args.label.trim() : "";
  const kind = typeof args.kind === "string" ? args.kind.trim() : "";
  const statement = typeof args.statement === "string" ? args.statement.trim() : "";
  if (!label || /[\r\n]/.test(label)) return "error: label must be one non-empty line";
  if (!statement) return "error: statement is required";
  if (/^##\s/m.test(statement)) return "error: statement must not contain level-2 headings";
  const isNode = profile.node_kinds.includes(kind);
  const roleNames = profile.relation_kinds[kind];
  if (!isNode && !roleNames) return `error: kind must be one of ${[...profile.node_kinds, ...Object.keys(profile.relation_kinds)].join(", ")}`;
  const epistemic = typeof args.epistemic === "string" && args.epistemic.trim() ? args.epistemic.trim() : undefined;
  if (epistemic && !profile.epistemic.includes(epistemic)) return `error: epistemic must be one of ${profile.epistemic.join(", ")}`;
  const rawRoles = args.roles && typeof args.roles === "object" && !Array.isArray(args.roles) ? args.roles as Record<string, unknown> : {};
  const roles: Record<string, string[]> = {};
  const errors: string[] = [];
  const link = (value: string): string | undefined => {
    const match = /^\[\[([^\]|]+)(?:\|[^\]]*)?\]\]$/.exec(value);
    if (!match) {
      errors.push(`"${value}" is not a link "[[uid]]"; leave a concept no record or draft shown holds out of the values`);
      return undefined;
    }
    const uid = match[1]!.trim();
    if (!state.shown.has(uid)) {
      errors.push(`[[${uid}]] is not a record or draft shown to you; link only shown uids`);
      return undefined;
    }
    const [base, id] = splitUid(uid);
    if (state.drafts.has(uid) && base !== profile.base) {
      errors.push(`[[${uid}]] is a draft of another base, which only an accepted record can be linked across; leave it out`);
      return undefined;
    }
    return base === profile.base ? `[[${id}]]` : `[[${base}:${id}]]`;
  };
  const convert = (items: unknown): string[] => strings(items).map(link).filter((value): value is string => value !== undefined);
  for (const [role, items] of Object.entries(rawRoles)) {
    if (!roleNames?.includes(role)) {
      errors.push(isNode ? `a ${kind} node has no roles; drop "${role}"` : `${kind} has no role "${role}"; its roles are ${roleNames?.join(", ")}`);
      continue;
    }
    const converted = convert(items);
    if (converted.length) roles[role] = converted;
  }
  if (roleNames && Object.keys(roles).length === 0) errors.push(`a ${kind} relation needs at least one role value`);
  const requires = convert(args.requires);
  const evidence = Array.isArray(args.evidence)
    ? (args.evidence as Array<{ lines?: unknown; quote?: unknown }>).filter((item) => item && typeof item === "object")
      .map((item) => ({ lines: String(item.lines ?? ""), quote: String(item.quote ?? "") }))
    : [];
  if (evidence.length === 0) errors.push("evidence needs at least one quote");
  if (errors.length) return `error: ${errors.join("; ")}`;
  // A relation's label is a statement, so it takes no aliases.
  const aliases = isNode ? [...new Set(strings(args.aliases).filter((alias) => !/[\r\n]/.test(alias) && fold(alias) !== fold(label)))] : [];
  return { label, kind, aliases, roles, requires, statement, evidence, epistemic };
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
