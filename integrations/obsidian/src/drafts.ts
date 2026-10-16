/**
 * Drafts as `kg_drafts` lists them (docs/model.md, `kgd drafts`): proposed
 * records waiting in the Capture view until the owner accepts or discards
 * them. kgd reads and checks the files; the plugin only shows what it reports
 * and groups the drafts that must be accepted together.
 */

export interface DraftValue {
  role: string;
  pos: number;
  uid: string;
  /** An accepted record, a draft of the same base, or a record that does not exist. */
  state: "accepted" | "draft" | "missing";
  label?: string | null;
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

/** How a value reads on a draft's card: its name and what it is; uids and ids are never shown. */
export function valueText(value: DraftValue, base: string): { text: string; status: "accepted" | "draft" | "missing"; note: string } {
  if (value.state === "draft") return { text: value.label ?? "a draft", status: "draft", note: "draft" };
  if (value.state === "accepted") {
    const where = value.uid.startsWith(`${base}:`) ? "" : value.uid.slice(0, value.uid.indexOf(":"));
    return { text: value.label ?? "a record", status: "accepted", note: where };
  }
  return { text: "a record that does not exist", status: "missing", note: "missing" };
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
