import { describe, expect, it } from "vitest";

import type { ChatReply, ChatRequest } from "../src/model";
import { acceptJudgment, candidateText, judgeSelection, judgeUser } from "../src/judge";
import type { RecordSummary } from "../src/lookup-model";

const record = (uid: string, label: string, kind = "architecture", cls: "node" | "relation" = "node"): RecordSummary =>
  ({ uid, label, kind, class: cls, base: uid.split(":")[0]!, source: "s.md", lines: "1", gloss: `${label} gloss` });

const mae = record("test-papers:masked-autoencoder", "Masked autoencoder");
const dae = record("test-notes:denoising-autoencoder", "Denoising autoencoder");
const claim = record("test-papers:mae-is-a-form-of-dae", "MAE is a form of denoising autoencoding", "property", "relation");

describe("the judge prompt", () => {
  it("numbers the candidates with kind, class, base and prerequisites", () => {
    const hit = { ...mae, requires: [{ label: "Autoencoder", uid: "test-notes:autoencoder" }, { label: "Mask token", uid: "test-papers:mask-token" }] };
    expect(candidateText([hit, claim])).toBe(
      "[1] Masked autoencoder · architecture node · test-papers · requires: Autoencoder, Mask token\n    Masked autoencoder gloss\n"
      + "[2] MAE is a form of denoising autoencoding · property relation · test-papers\n    MAE is a form of denoising autoencoding gloss",
    );
  });

  it("shows the text around the selection only when it adds something", () => {
    expect(judgeUser({ selection: "MAE", context: "MAE", candidates: [] })).toBe("Selection: MAE\n\nCandidates: none were found.");
    expect(judgeUser({ selection: "MAE", context: " The MAE is simple. ", candidates: [] }))
      .toBe("Selection: MAE\n\nText around it:\nThe MAE is simple.\n\nCandidates: none were found.");
  });
});

describe("accepting a judgment", () => {
  const candidates = [mae, dae, claim];

  it("maps candidate numbers to uids", () => {
    expect(acceptJudgment({ selection: "concept", verdict: "matched", candidates: [2], explanation: " same " }, candidates))
      .toEqual({ verdict: "matched", records: ["test-notes:denoising-autoencoder"], explanation: "same" });
    expect(acceptJudgment({ selection: "concept", verdict: "ambiguous", candidates: [1, 2, 1], explanation: "" }, candidates))
      .toEqual({ verdict: "ambiguous", records: ["test-papers:masked-autoencoder", "test-notes:denoising-autoencoder"], explanation: "" });
    expect(acceptJudgment({ selection: "statement", verdict: "matched", candidates: [3], explanation: "" }, candidates))
      .toEqual({ verdict: "matched", records: ["test-papers:mae-is-a-form-of-dae"], explanation: "" });
    expect(acceptJudgment({ selection: "concept", verdict: "unmatched", candidates: [], explanation: "none" }, candidates))
      .toEqual({ verdict: "unmatched", records: [], explanation: "none" });
  });

  it("refuses verdicts that do not fit their candidates", () => {
    expect(acceptJudgment({ verdict: "maybe", candidates: [] }, candidates)).toMatch(/^error: verdict/);
    expect(acceptJudgment({ verdict: "matched", candidates: [4] }, candidates)).toBe("error: candidates must be numbers from 1 to 3");
    expect(acceptJudgment({ verdict: "matched", candidates: [1, 2] }, candidates)).toBe("error: matched names exactly one candidate");
    expect(acceptJudgment({ verdict: "ambiguous", candidates: [1] }, candidates)).toMatch(/^error: ambiguous/);
    expect(acceptJudgment({ verdict: "unmatched", candidates: [1] }, candidates)).toBe("error: unmatched names no candidate");
    expect(acceptJudgment({ verdict: "unmatched", candidates: [] }, candidates)).toBe("error: selection must be concept or statement");
  });

  it("refuses to match a concept to a relation, and lets a statement match a node or a relation", () => {
    expect(acceptJudgment({ selection: "concept", verdict: "matched", candidates: [3] }, candidates)).toBe(
      "error: the selection is a concept, so it can only be a node; MAE is a form of denoising autoencoding is a relation. "
      + "Judge again: a record that only mentions it is related, not the same.");
    expect(acceptJudgment({ selection: "statement", verdict: "ambiguous", candidates: [3, 1] }, candidates)).toMatchObject(
      { verdict: "ambiguous", records: ["test-papers:mae-is-a-form-of-dae", "test-papers:masked-autoencoder"] });
  });
});

describe("judging a selection", () => {
  it("forces the judge tool and retries a refused answer once", async () => {
    const requests: ChatRequest[] = [];
    const answers = [
      { selection: "concept", verdict: "matched", candidates: [9], explanation: "" },
      { selection: "concept", verdict: "matched", candidates: [1], explanation: "It is MAE." },
    ];
    const complete = async (request: ChatRequest): Promise<ChatReply> => {
      requests.push(request);
      const args = answers.shift();
      return {
        message: { content: null, tool_calls: [{ id: `c${requests.length}`, type: "function", function: { name: "judge", arguments: JSON.stringify(args) } }] },
      };
    };
    await expect(judgeSelection(complete, { selection: "MAE", context: "", candidates: [mae, dae] }))
      .resolves.toEqual({ verdict: "matched", records: ["test-papers:masked-autoencoder"], explanation: "It is MAE." });
    expect(requests[0]!.tool_choice).toEqual({ type: "function", function: { name: "judge" } });
    expect(requests[1]!.tool_choice).toBe("required");
  });
});
