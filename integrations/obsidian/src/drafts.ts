/**
 * Drafts as `kg_drafts` lists them (docs/model.md, `kgd drafts`): proposed
 * records waiting in the Capture view until the owner accepts or discards
 * them. kgd reads and checks the files; the plugin only shows what it reports
 * and groups the drafts that must be accepted together.
 */

export interface DraftValue {
  role: string;
  pos: number;
  /** A pending term: a concept no base records yet. */
  term?: string;
  uid?: string;
  /** For a link: an accepted record, a draft of the same base, or a record that does not exist. */
  state?: "accepted" | "draft" | "missing";
  label?: string | null;
  understanding?: string;
}

export interface Draft {
  uid: string;
  base: string;
  id: string;
  /** The draft file. */
  path: string;
  /** Null when the file cannot be read or parsed; `problems` then says why. */
  label: string | null;
  class?: "node" | "relation";
  kind?: string;
  aliases?: string[];
  source?: string;
  lines?: string;
  understanding?: string;
  epistemic?: string | null;
  gloss?: string;
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
  unavailable_bases: string[];
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
export function valueText(value: DraftValue, base: string): { text: string; status: "accepted" | "draft" | "missing" | "pending"; note: string } {
  if (value.term !== undefined) return { text: value.term, status: "pending", note: "not recorded" };
  if (value.state === "draft") return { text: value.label ?? "a draft", status: "draft", note: "draft" };
  if (value.state === "accepted") {
    const where = value.uid && !value.uid.startsWith(`${base}:`) ? value.uid.slice(0, value.uid.indexOf(":")) : "";
    return { text: value.label ?? "a record", status: "accepted", note: where };
  }
  return { text: "a record that does not exist", status: "missing", note: "missing" };
}
