import { describe, expect, it } from "vitest";

import { acceptanceGroup, groupProblems, valuesByRole, valueText, type Draft } from "../src/drafts";

const draft = (uid: string, extra: Partial<Draft> = {}): Draft => ({
  uid, base: uid.split(":")[0]!, id: uid.split(":")[1]!, path: `/v/.knowledge/drafts/${uid.split(":")[1]}.md`,
  label: uid.split(":")[1]!, with: [], linked_by: [], problems: [], ...extra,
});

describe("drafts accepted together", () => {
  it("are the draft and every draft it links, or none while one is not listed", () => {
    const best = draft("papers:best", { with: ["papers:mae", "papers:sampling"] });
    const listed = [best, draft("papers:sampling"), draft("papers:mae")];
    expect(acceptanceGroup(best, listed)?.map((item) => item.uid)).toEqual(["papers:best", "papers:mae", "papers:sampling"]);
    expect(acceptanceGroup(best, [best, draft("papers:mae")])).toBeUndefined();
  });

  it("report each member's problems, naming the linked draft a problem belongs to", () => {
    const group = [draft("papers:best", { problems: ["evidence is stale"] }), draft("papers:mae", { label: "Masked autoencoder", problems: ["kind x is not a kind"] })];
    expect(groupProblems(group)).toEqual(["evidence is stale", "Masked autoencoder: kind x is not a kind"]);
  });
});

describe("a draft's values on its card", () => {
  it("are grouped by role in kgd's order", () => {
    const values = [
      { role: "baseline", pos: 0, term: "block-wise masking" }, { role: "baseline", pos: 1, term: "grid-wise sampling" },
      { role: "candidate", pos: 0, uid: "papers:sampling", state: "draft" as const, label: "Random sampling" },
      { role: "requires", pos: 0, term: "patches" },
    ];
    expect(valuesByRole(draft("papers:best", { out: values })).map(({ role, values: items }) => [role, items.length]))
      .toEqual([["baseline", 2], ["candidate", 1], ["requires", 1]]);
  });

  it("read as names with what they are, the base only when it is another, and never an id", () => {
    expect(valueText({ role: "requires", pos: 0, term: "mask token" }, "papers")).toEqual({ text: "mask token", status: "pending", note: "not recorded" });
    expect(valueText({ role: "requires", pos: 0, uid: "notes:autoencoder", state: "accepted", label: "Autoencoder" }, "papers"))
      .toEqual({ text: "Autoencoder", status: "accepted", note: "notes" });
    expect(valueText({ role: "requires", pos: 0, uid: "papers:encoder", state: "accepted", label: "Encoder" }, "papers"))
      .toEqual({ text: "Encoder", status: "accepted", note: "" });
    expect(valueText({ role: "setting", pos: 0, uid: "papers:mae", state: "draft", label: "Masked autoencoder" }, "papers"))
      .toEqual({ text: "Masked autoencoder", status: "draft", note: "draft" });
    expect(valueText({ role: "setting", pos: 0, uid: "papers:gone", state: "missing", label: null }, "papers"))
      .toEqual({ text: "a record that does not exist", status: "missing", note: "missing" });
  });
});
