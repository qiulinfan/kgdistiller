/**
 * Localization (docs/model.md, "Foreign knowledge and localization"): a
 * foreign record or draft cites the web page of an outside work; once the
 * owner holds that work as a registered source and selects the concept in it,
 * one model call checks that the passage around the selection states the same
 * knowledge point and copies the sentences that state it. `kgd relocate` then
 * moves the record's citation there: it finds those sentences in that source
 * and rewrites only the record's source, lines and Evidence. Nothing else of
 * the record changes, and the plugin never writes the record itself.
 */

import { runJob, type Complete, type Tool } from "./agent";
import { numberedPassage, passageRange, type CaptureKgd, type CaptureFiles, type Profile } from "./capture";
import type { Draft } from "./drafts";
import type { FullRecord } from "./lookup-model";

/** The foreign knowledge to localize, from `kg_get` (accepted) or `kg_drafts` (a draft). */
export interface LocalizeTarget {
  uid: string;
  label: string;
  kind: string;
  body: string;
  evidence: string[];
  source: string;
  /** What `kgd relocate` takes: the uid of an accepted record, or a draft's file. */
  ref: string;
}

export function recordTarget(record: FullRecord): LocalizeTarget {
  return { uid: record.uid, label: record.label, kind: record.kind, body: record.body, evidence: record.evidence, source: record.source, ref: record.uid };
}

export function draftTarget(draft: Draft): LocalizeTarget {
  return {
    uid: draft.uid, label: draft.label ?? draft.id, kind: draft.kind ?? "", body: draft.body ?? "",
    evidence: draft.evidence ?? [], source: draft.source ?? "", ref: draft.path,
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
  /** Whether the record now cites the local passage. */
  localized: boolean;
  uid: string;
  label: string;
  report: string;
}

export const LOCALIZE_SYSTEM = `A foreign record of the owner's knowledge base cites the web page of an outside work. The owner now holds a local text and selected a passage in it. Decide whether the passage states the same knowledge point with the same meaning as the record, and call localize once:
- same: true when it does; then passage: the sentence or sentences of the numbered passage that state it, copied character for character without line numbers, as one continuous stretch of the text.
- same: false when the passage is about something else or means it differently.
reason: one short sentence for the owner, in the language of the passage.`;

const LOCALIZE_TOOL = {
  type: "function" as const,
  function: {
    name: "localize",
    description: "Move the record's citation to the passage, or decline.",
    parameters: {
      type: "object",
      properties: {
        same: { type: "boolean" },
        passage: { type: "string" },
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

export async function localizeRecord(complete: Complete, kgd: CaptureKgd, files: CaptureFiles, target: LocalizeTarget,
  input: LocalizeInput): Promise<LocalizeResult> {
  const base = target.uid.slice(0, target.uid.indexOf(":"));
  const described = await kgd.command(["source", input.path]);
  if (described.failed) throw new Error("kgd source refused the file: it is not a registered source");
  const profile = described.report as unknown as Profile;
  const done = (localized: boolean, report: string): LocalizeResult => ({ localized, uid: target.uid, label: target.label, report });
  if (profile.base !== base) {
    return done(false, `This source belongs to base ${profile.base}, the record to base ${base}; moving a record across bases is the compile Skill's relocation.`);
  }
  if (!(target.kind in profile.node_kinds) && !(target.kind in profile.relation_kinds)) {
    return done(false, `The kind ${target.kind} is not one of this source's document type ${profile.type}.`);
  }
  const lines = (await files.read(input.path)).replace(/\r\n?/g, "\n").split("\n");
  const tool: Tool<LocalizeResult> = {
    spec: LOCALIZE_TOOL,
    run: async (args) => {
      const reason = typeof args.reason === "string" ? args.reason.trim() : "";
      if (args.same !== true) return { done: done(false, reason) };
      const passage = typeof args.passage === "string" ? args.passage.trim() : "";
      if (!passage) return "error: give passage, the sentences of the numbered passage that state the knowledge point";
      const moved = await kgd.command(["relocate", target.ref, passage, "--source", profile.source]);
      const refused = moved.report.refused as { message?: string } | undefined;
      if (refused || moved.failed) {
        const why = refused?.message ?? (moved.report.error as { message?: string } | undefined)?.message ?? "kgd relocate refused it";
        return `error: ${why}. Copy the passage from the numbered lines exactly and call localize again.`;
      }
      return { done: done(true, reason) };
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
