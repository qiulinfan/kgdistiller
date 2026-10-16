/**
 * Drafts as `kg_drafts` lists them (docs/model.md, `kgd drafts`): proposed
 * records waiting in the Capture view until the owner accepts or discards
 * them. kgd reads and checks the files; the plugin only shows what it reports
 * and groups the drafts that must be accepted together. Revising an accepted
 * record starts by drawing it into a draft with its id, which replaces it when
 * accepted.
 */

import type { CaptureFiles } from "./capture";

export interface DraftValue {
  role: string;
  pos: number;
  uid: string;
  /** An accepted record, a draft of any base, or a record that does not exist. */
  state: "accepted" | "draft" | "missing";
  label?: string | null;
}

/** What a draft's kind, roles and epistemic label may be, from its source's document type. */
export interface Choices {
  /** Each node kind with its roles, which may be none. */
  node_kinds: Record<string, string[]>;
  relation_kinds: Record<string, string[]>;
  epistemic: string[];
}

export interface Draft {
  uid: string;
  base: string;
  id: string;
  /** The draft file. */
  path: string;
  /** The accepted record this draft replaces when accepted, or null for a new record. */
  replaces?: string | null;
  /** Whether its source is a web address: knowledge outside the owner's files. */
  foreign?: boolean;
  /** Null when the file cannot be read or parsed; `problems` then says why. */
  label: string | null;
  class?: "node" | "relation";
  kind?: string;
  aliases?: string[];
  source?: string;
  lines?: string;
  understanding?: string;
  epistemic?: string | null;
  body?: string;
  search_terms?: string;
  evidence?: string[];
  out?: DraftValue[];
  /** What its kind, roles and epistemic label may be; null when its source has no document type. */
  choices?: Choices | null;
  /** The drafts it links, directly or through other drafts, which are accepted with it. */
  with: string[];
  linked_by: string[];
  /** What `kgd accept` would refuse; empty when it would accept the draft with `with`. */
  problems: string[];
}

export interface DraftsResult {
  drafts: Draft[];
}

/** The drafts accepted together: the draft first, then those it links; undefined while one of them is not listed. */
export function acceptanceGroup(draft: Draft, drafts: readonly Draft[]): Draft[] | undefined {
  const byUid = new Map(drafts.map((item) => [item.uid, item]));
  const others = draft.with.map((uid) => byUid.get(uid));
  return others.every((item): item is Draft => item !== undefined) ? [draft, ...others] : undefined;
}

/** Why a group cannot be accepted: each member's problems, those of a linked draft prefixed with its label. */
export function groupProblems(group: readonly Draft[]): string[] {
  return group.flatMap((member, index) =>
    member.problems.map((problem) => (index === 0 ? problem : `${member.label ?? member.id}: ${problem}`)));
}

/** A draft's values by role in their order, roles first and requires last, as kgd lists them. */
export function valuesByRole(draft: Draft): Array<{ role: string; values: DraftValue[] }> {
  const groups: Array<{ role: string; values: DraftValue[] }> = [];
  for (const value of draft.out ?? []) {
    const last = groups.at(-1);
    if (last?.role === value.role) last.values.push(value);
    else groups.push({ role: value.role, values: [value] });
  }
  return groups;
}

/**
 * How a value reads on a draft's card: its name and what it is, with the base
 * of a record or draft of another base; uids and ids are never shown.
 */
export function valueText(value: DraftValue, base: string): { text: string; status: "accepted" | "draft" | "missing"; note: string } {
  if (value.state === "missing") return { text: "a record that does not exist", status: "missing", note: "missing" };
  const where = value.uid.startsWith(`${base}:`) ? "" : value.uid.slice(0, value.uid.indexOf(":"));
  if (value.state === "draft") return { text: value.label ?? "a draft", status: "draft", note: where ? `draft · ${where}` : "draft" };
  return { text: value.label ?? "a record", status: "accepted", note: where };
}

/**
 * Why `kgd accept` did not accept a group: the drafts it refused, admitting
 * none, or a target that appeared while it was moving the drafts, with what it
 * created and updated before it stopped. Undefined when it accepted them all.
 */
export function acceptProblem(report: Record<string, unknown>, group: readonly Draft[]): string | undefined {
  const refused = report.refused as Array<{ message: string }> | undefined;
  if (refused) return refused.map((item) => item.message).join("; ");
  const aborted = report.aborted as { message: string } | undefined;
  if (!aborted) return undefined;
  const names = (uids: string[]) => group.filter((member) => uids.includes(member.uid)).map((member) => member.label ?? member.id).join(", ");
  const created = report.created as string[];
  const updated = report.updated as string[];
  const done = [created.length ? `created ${names(created)}` : "", updated.length ? `updated ${names(updated)}` : ""].filter(Boolean);
  return `${aborted.message}. ${done.length ? `Before it stopped, kgd accept ${done.join(" and ")}.` : "Nothing was accepted."}`;
}

/** The drafts of one source, or of every foreign source, as the Capture view's source list shows them. */
export interface SourceGroup {
  key: string;
  /** The source's file name, with as many parent folders as tell it apart from the other sources' names. */
  name: string;
  /** The source's absolute path, or "foreign" for the drafts whose source is a web address. */
  title: string;
  drafts: Draft[];
}

export const FOREIGN_GROUP = "foreign";

/** A local draft's source as an absolute path: its base root, read from the draft's own path, joined with its source. */
export function absoluteSource(draft: Draft): string {
  const at = draft.path.lastIndexOf("/.knowledge/drafts/");
  const root = at >= 0 ? draft.path.slice(0, at) : "";
  return `${root}/${draft.source ?? ""}`;
}

/**
 * The drafts grouped by source, in the order kgd lists them, with every
 * foreign draft under one "foreign" group last. A source is named by its file
 * name; sources that share a name get one more parent folder each until the
 * names differ, so the absolute paths tell them apart.
 */
export function sourceGroups(drafts: readonly Draft[]): SourceGroup[] {
  const groups = new Map<string, SourceGroup>();
  const foreign: Draft[] = [];
  for (const draft of drafts) {
    if (draft.foreign) {
      foreign.push(draft);
      continue;
    }
    const title = draft.source === undefined ? draft.path : absoluteSource(draft);
    const group = groups.get(title) ?? { key: title, name: "", title, drafts: [] };
    group.drafts.push(draft);
    groups.set(title, group);
  }
  const local = [...groups.values()];
  const parts = local.map((group) => group.title.split("/").filter(Boolean));
  const depth = local.map(() => 1);
  for (;;) {
    const names = local.map((_, index) => parts[index]!.slice(-depth[index]!).join("/"));
    let grew = false;
    for (let index = 0; index < local.length; index++) {
      const clash = names.some((name, other) => other !== index && name === names[index]);
      if (clash && depth[index]! < parts[index]!.length) {
        depth[index]!++;
        grew = true;
      }
    }
    if (!grew) {
      names.forEach((name, index) => (local[index]!.name = name));
      break;
    }
  }
  return foreign.length ? [...local, { key: FOREIGN_GROUP, name: FOREIGN_GROUP, title: FOREIGN_GROUP, drafts: foreign }] : local;
}

/** What dragging a record from the lookup sidebar onto the Capture view carries: the record's uid. */
export const RECORD_DRAG_TYPE = "application/x-kgdistiller-record";

/**
 * Draw an accepted record into a draft with its id, a byte copy of its file,
 * which the owner then edits and accepts to replace it (docs/model.md,
 * "Drafts"). A draft already waiting under that id is kept and reported.
 */
export async function drawIntoDraft(roots: Map<string, string>, files: CaptureFiles, uid: string): Promise<{ path: string; created: boolean }> {
  const colon = uid.indexOf(":");
  const root = roots.get(uid.slice(0, colon));
  if (!root) throw new Error(`base ${uid.slice(0, colon)} has no registered root`);
  const id = uid.slice(colon + 1);
  const path = `${root}/.knowledge/drafts/${id}.md`;
  const waiting = await files.read(path).then(() => true, () => false);
  if (waiting) return { path, created: false };
  await files.create(path, await files.read(`${root}/.knowledge/entries/${id}.md`));
  return { path, created: true };
}

/** The value that links `uid` from a record of `base`: local `[[id]]`, or `[[base:id]]` across bases. */
export function linkFor(uid: string, base: string): string {
  const colon = uid.indexOf(":");
  return uid.slice(0, colon) === base ? `[[${uid.slice(colon + 1)}]]` : `[[${uid}]]`;
}

/** The links a role of a draft holds, in order, without the value at `drop` when given. */
export function roleLinks(draft: Draft, role: string, drop?: number): string[] {
  return (draft.out ?? []).filter((value) => value.role === role && value.pos !== drop).map((value) => linkFor(value.uid, draft.base));
}

/**
 * The value rows a draft's card offers: the roles its kind declares (or, for a
 * kind its type lacks, the roles it holds), in order, then `requires`.
 */
export function editableRoles(draft: Draft): string[] {
  const declared = draft.choices?.node_kinds[draft.kind ?? ""] ?? draft.choices?.relation_kinds[draft.kind ?? ""];
  const held = [...new Set((draft.out ?? []).map((value) => value.role))].filter((role) => role !== "requires");
  return [...new Set([...(declared ?? held), ...held]), "requires"];
}
