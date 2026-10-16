/**
 * Localization (docs/model.md, "Foreign knowledge and localization"): a
 * foreign record or draft cites the web page of an outside work; once the
 * owner holds that work as a registered source and selects the concept in it,
 * the agent checks that the selected passage states the same knowledge point
 * and moves the record's evidence there. The result is a draft with the same
 * id: the replacement of an accepted record, or the rewritten draft. Every
 * other field is kept; the owner accepts it like any draft.
 */

import { runJob, type Complete, type Tool } from "./agent";
import { numberedPassage, passageRange, recordFile, type CaptureFiles, type CaptureKgd, type Profile } from "./capture";
import type { Draft, DraftsResult } from "./drafts";
import { mergeRanges, parseRanges, placeQuote, type Range } from "./evidence";
import type { FullRecord } from "./lookup-model";

/** The foreign knowledge to localize, from `kg_get` (accepted) or `kg_drafts` (a draft). */
export interface LocalizeTarget {
  uid: string;
  label: string;
  kind: string;
  aliases: string[];
  /** Each value in the record's own link form, by role in order, `requires` last. */
  values: Array<{ role: string; uid: string }>;
  understanding: string;
  epistemic: string | null;
  body: string;
  searchTerms: string;
  evidence: string[];
  source: string;
  /** The draft file, when the target is a draft rather than an accepted record. */
  draftPath?: string;
}

export function recordTarget(record: FullRecord & { search_terms?: string }): LocalizeTarget {
  return {
    uid: record.uid, label: record.label, kind: record.kind, aliases: record.aliases,
    values: record.out.map((link) => ({ role: link.role, uid: link.uid })),
    understanding: record.understanding, epistemic: record.epistemic, body: record.body,
    searchTerms: record.search_terms ?? "", evidence: record.evidence, source: record.source,
  };
}

export function draftTarget(draft: Draft): LocalizeTarget {
  return {
    uid: draft.uid, label: draft.label ?? draft.id, kind: draft.kind ?? "", aliases: draft.aliases ?? [],
    values: (draft.out ?? []).map((value) => ({ role: value.role, uid: value.uid })),
    understanding: draft.understanding ?? "yes", epistemic: draft.epistemic ?? null, body: draft.body ?? "",
    searchTerms: draft.search_terms ?? "", evidence: draft.evidence ?? [], source: draft.source ?? "", draftPath: draft.path,
  };
}

export interface LocalizeInput {
  /** The registered source the owner selected the concept in, and the selection's lines. */
  path: string;
  from: number;
  to: number;
  selection: string;
}

export interface LocalizeResult {
  /** Whether the passage states the same knowledge point and the draft was written. */
  localized: boolean;
  uid: string;
  label: string;
  /** The draft replaces an accepted record (otherwise it rewrote a draft). */
  replaces?: boolean;
  report: string;
}

export const LOCALIZE_SYSTEM = `A foreign record of the owner's knowledge base cites the web page of an outside work. The owner now holds a local text and selected a passage in it. Decide whether the passage states the same knowledge point with the same meaning as the record, and call localize once:
- same: true when it does; then evidence: the short quotes of the numbered passage that state it, the selected text first, each with its line or line range, copied character for character; and statement only when the record's statement should follow the passage's wording, in the passage's language.
- same: false when the passage is about something else or means it differently.
reason: one short sentence for the owner, in the language of the passage.`;

const LOCALIZE_TOOL = {
  type: "function" as const,
  function: {
    name: "localize",
    description: "Move the record's evidence to the passage, or decline.",
    parameters: {
      type: "object",
      properties: {
        same: { type: "boolean" },
        evidence: {
          type: "array",
          items: { type: "object", properties: { lines: { type: "string" }, quote: { type: "string" } }, required: ["lines", "quote"], additionalProperties: false },
        },
        statement: { type: "string" },
        reason: { type: "string" },
      },
      required: ["same", "reason"],
      additionalProperties: false,
    },
  },
};

export function localizeUser(target: LocalizeTarget, profile: Profile, lines: readonly string[], input: LocalizeInput): string {
  return [
    `Record: ${target.label} · ${target.kind}`,
    `Statement:\n${target.body.trim()}`,
    `Its evidence, quoted from ${target.source}:\n${target.evidence.map((quote) => `> ${quote}`).join("\n")}`,
    `Local source: ${profile.base} · ${profile.source}`,
    `Selected, lines ${input.from}-${input.to}:\n${input.selection.trim()}`,
    `Numbered passage:\n${numberedPassage(lines, passageRange(lines, input.from, input.to))}`,
  ].join("\n\n");
}

/** The target's values in link form for a record of `base`. */
function links(target: LocalizeTarget, base: string): { roles: Record<string, string[]>; requires: string[] } {
  const roles: Record<string, string[]> = {};
  const requires: string[] = [];
  for (const value of target.values) {
    const colon = value.uid.indexOf(":");
    const link = value.uid.slice(0, colon) === base ? `[[${value.uid.slice(colon + 1)}]]` : `[[${value.uid}]]`;
    if (value.role === "requires") requires.push(link);
    else (roles[value.role] ??= []).push(link);
  }
  return { roles, requires };
}

export async function localizeRecord(complete: Complete, kgd: CaptureKgd, files: CaptureFiles, target: LocalizeTarget,
  input: LocalizeInput): Promise<LocalizeResult> {
  const [base, id] = [target.uid.slice(0, target.uid.indexOf(":")), target.uid.slice(target.uid.indexOf(":") + 1)];
  const described = await kgd.command(["source", input.path]);
  if (described.failed) throw new Error("kgd source refused the file: it is not a registered source");
  const profile = described.report as unknown as Profile;
  const done = (localized: boolean, report: string, replaces?: boolean): LocalizeResult =>
    ({ localized, uid: target.uid, label: target.label, ...(replaces ? { replaces } : {}), report });
  if (profile.base !== base) {
    return done(false, `This source belongs to base ${profile.base}, the record to base ${base}; moving a record across bases is the compile Skill's relocation.`);
  }
  if (!profile.node_kinds.includes(target.kind) && !(target.kind in profile.relation_kinds)) {
    return done(false, `The kind ${target.kind} is not one of this source's document type ${profile.type}.`);
  }
  const root = (await kgd.baseRoots()).get(base);
  if (!root) throw new Error(`base ${base} has no registered root`);
  const lines = (await files.read(input.path)).replace(/\r\n?/g, "\n").split("\n");
  const tool: Tool<LocalizeResult> = {
    spec: LOCALIZE_TOOL,
    run: async (args) => {
      const reason = typeof args.reason === "string" ? args.reason.trim() : "";
      if (args.same !== true) return { done: done(false, reason) };
      const placed: Range[] = [];
      const quotes: string[] = [];
      const items = Array.isArray(args.evidence) ? args.evidence as Array<{ lines?: unknown; quote?: unknown }> : [];
      if (items.length === 0) return "error: evidence needs at least one quote of the passage";
      for (const [index, item] of items.entries()) {
        const parsed = parseRanges(String(item?.lines ?? ""));
        if (!parsed) return `error: quote ${index + 1}: lines must be a line or a range such as 12 or 12-14`;
        const found = placeQuote(String(item?.quote ?? ""), [parsed[0]![0], parsed[parsed.length - 1]![1]], lines);
        if (found.status === "missing") return `error: quote ${index + 1}: ${found.reason}; copy it from the numbered passage`;
        placed.push(found.range);
        quotes.push(found.quote);
      }
      const statement = typeof args.statement === "string" && args.statement.trim() ? args.statement.trim() : target.body;
      const epistemic = target.epistemic && profile.epistemic.includes(target.epistemic) ? target.epistemic : undefined;
      const text = recordFile(
        { label: target.label, kind: target.kind, aliases: target.aliases, ...links(target, base), statement, evidence: [], epistemic },
        profile.source, mergeRanges(placed), quotes, target.understanding, target.searchTerms,
      );
      const path = target.draftPath ?? `${root}/.knowledge/drafts/${id}.md`;
      const previous = target.draftPath ? await files.read(path) : undefined;
      if (previous !== undefined) await files.remove(path);
      try {
        await files.create(path, text);
      } catch (error) {
        if (previous !== undefined) await files.create(path, previous);
        return { done: done(false, `${reason} A draft with this id already waits for you, so nothing was written (${error instanceof Error ? error.message : String(error)}).`.trim()) };
      }
      const listed = await kgd.call<DraftsResult>("kg_drafts", { base: [base] });
      const problems = listed.drafts.find((draft) => draft.uid === target.uid)?.problems ?? ["kg_drafts did not list the draft"];
      if (problems.length) {
        await files.remove(path);
        if (previous !== undefined) await files.create(path, previous);
        return `error: kgd accept would refuse the localized record: ${problems.join("; ")}. Correct the evidence and call localize again.`;
      }
      return { done: done(true, reason, previous === undefined) };
    },
  };
  return runJob(complete, {
    system: LOCALIZE_SYSTEM,
    user: localizeUser(target, profile, lines, input),
    tools: [tool],
    turns: 3,
    first: "localize",
    effort: "low",
  });
}
