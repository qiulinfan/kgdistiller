/**
 * Localization (docs/model.md, "Foreign knowledge and localization"): a
 * foreign record or draft cites the web page of an outside work; once the
 * owner holds that work as a registered source and selects the concept in it,
 * one model call checks that the passage around the selection states the same
 * knowledge point and copies the sentences that state it. `kgd relocate` then
 * moves the record's citation there: it finds those sentences in that source
 * and rewrites only the record's source, lines and Evidence, moving the record
 * into that source's base with every link to it rewritten when the source lies
 * in another. Nothing else of the record changes, and the plugin never writes
 * the record itself.
 */

import { runJob, type Complete, type Tool } from "./agent";
import { numberedPassage, passageRange, type CaptureKgd, type CaptureFiles, type Profile } from "./capture";
import type { Draft } from "./drafts";
import { KgdError } from "./kgd";
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
  /** The record's uid, a new one when it moved into the passage's base. */
  uid: string;
  label: string;
  report: string;
  /** The report of the index run `kgd relocate` ended with, when it ran one. */
  index?: Record<string, unknown>;
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
  const profile = (await kgd.command(["source", input.path])).report as unknown as Profile;
  const done = (localized: boolean, report: string): LocalizeResult => ({ localized, uid: target.uid, label: target.label, report });
  if (!profile.types.some((type) => target.kind in type.node_kinds || target.kind in type.relation_kinds)) {
    const names = profile.types.map((type) => type.type).join(", ");
    return done(false, `The kind ${target.kind} is not one of this source's document type${profile.types.length === 1 ? "" : "s"} ${names}.`);
  }
  const lines = (await files.read(input.path)).replace(/\r\n?/g, "\n").split("\n");
  const tool: Tool<LocalizeResult> = {
    spec: LOCALIZE_TOOL,
    run: async (args) => {
      const reason = typeof args.reason === "string" ? args.reason.trim() : "";
      if (args.same !== true) return { done: done(false, reason) };
      const passage = typeof args.passage === "string" ? args.passage.trim() : "";
      if (!passage) return "error: give passage, the sentences of the numbered passage that state the knowledge point";
      // A passage of another base moves the record there; "--" keeps a passage starting with "-" an argument.
      let moved: { report: Record<string, unknown> };
      try {
        moved = await kgd.command(["relocate", "--base", profile.base, "--source", profile.source, "--", target.ref, passage]);
      } catch (error) {
        // Only a passage kgd cannot find is the model's to correct; any other error ends the job.
        if (error instanceof KgdError) return { done: done(false, error.message) };
        throw error;
      }
      const refused = moved.report.refused as { message: string } | undefined;
      if (refused) return `error: ${refused.message}. Copy the passage from the numbered lines exactly and call localize again.`;
      const index = moved.report.index as Record<string, unknown> | undefined;
      return { done: { ...done(true, reason), uid: moved.report.relocated as string, ...(index ? { index } : {}) } };
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
