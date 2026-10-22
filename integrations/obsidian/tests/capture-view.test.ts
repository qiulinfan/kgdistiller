import { describe, expect, it, vi } from "vitest";

vi.mock("obsidian", () => ({ ItemView: class {}, Modal: class {}, SuggestModal: class {}, Notice: class {} }));

import { captureOutcome, KgdistillerCaptureView } from "../src/capture-view";
import { captureSelection } from "../src/capture";

vi.mock("../src/capture", () => ({ captureSelection: vi.fn() }));

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

describe("Capture completion", () => {
  const selection = { text: "autoencoders", context: "", source: { path: "/papers/mae.md", from: 1, to: 1 } };
  const fixture = () => new KgdistillerCaptureView(undefined as never, {
    originServices: () => ({}),
    readingPath: async () => undefined,
  } as never);

  it("reports a failed job to the sidebar even though Capture displays the error itself", async () => {
    vi.mocked(captureSelection).mockRejectedValueOnce(new Error("not a registered source"));
    await expect(fixture().startCapture(selection)).resolves.toBeUndefined();
  });

  it("reports a completed decision that does not write a draft", async () => {
    vi.mocked(captureSelection).mockResolvedValueOnce({ item: "autoencoders", decision: "unchanged", report: "Already recorded." });
    await expect(fixture().startCapture(selection)).resolves.toBeUndefined();
  });

  it("reports that a side selection could not start without a source open", async () => {
    await expect(fixture().startCapture({ text: "autoencoders", context: "", side: true })).resolves.toBeUndefined();
  });
});
