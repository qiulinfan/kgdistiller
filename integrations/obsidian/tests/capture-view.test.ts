import { describe, expect, it, vi } from "vitest";

vi.mock("obsidian", () => ({ ItemView: class {}, Modal: class {}, SuggestModal: class {} }));

import { captureOutcome } from "../src/capture-view";

describe("a capture's outcome", () => {
  it("hands a written draft to the list, with the agent's report as its note", () => {
    expect(captureOutcome({ item: "i", decision: "superseding", uid: "papers:mae", label: "MAE", draft: true, replaces: true, report: "更一般。" }))
      .toEqual({ headline: "Drafted a replacement: MAE", report: "更一般。", draft: "papers:mae" });
  });

  it("names a replacement already waiting, and leaves that draft and its note alone", () => {
    expect(captureOutcome({ item: "i", decision: "conflicting", uid: "papers:mae", label: "MAE", waiting: true, report: "含义不同。" }))
      .toEqual({ headline: "A replacement already waits in Capture, not drafted: MAE", report: "含义不同。" });
  });
});
