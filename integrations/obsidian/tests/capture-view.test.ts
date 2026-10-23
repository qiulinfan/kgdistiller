import { describe, expect, it, vi } from "vitest";

const notices = vi.hoisted(() => [] as string[]);
vi.mock("obsidian", () => ({ ItemView: class {}, Modal: class {}, SuggestModal: class {}, Notice: class { constructor(message: string) { notices.push(message); } } }));

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
    kgd: { configured: () => false },
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

  it("keeps a draft whose source kgd mark could not mark, and says why", async () => {
    notices.length = 0;
    vi.mocked(captureSelection).mockResolvedValueOnce({
      item: "autoencoders", decision: "new", uid: "papers:autoencoder", label: "Autoencoder", draft: true, report: "已收录。",
      markError: "no line near mae.md:1 takes a marker; mark it by hand",
    });
    await fixture().startCapture(selection);
    expect(notices).toEqual([
      "kgdistiller drafted Autoencoder but could not mark it in its source: no line near mae.md:1 takes a marker; mark it by hand",
      "kgdistiller: Drafted: Autoencoder; it waits for you in Capture.",
    ]);
  });
});
