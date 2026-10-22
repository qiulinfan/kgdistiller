import { describe, expect, it, vi } from "vitest";

import { AgentPane, type AgentHost, type Selection } from "../src/agent-pane";
import { findOrigin } from "../src/origin";

vi.mock("../src/origin", () => ({ findOrigin: vi.fn() }));

/** The small part of Obsidian's DOM extensions used by the selection actions. */
class Element {
  children: Element[] = [];
  disabled = false;
  text = "";
  cls = "";
  addClass(): void {}
  empty(): void { this.children = []; }
  createDiv(options: { cls?: string; text?: string } = {}): Element { return this.createEl("div", options); }
  createSpan(options: { cls?: string; text?: string } = {}): Element { return this.createEl("span", options); }
  get childElementCount(): number { return this.children.length; }
  remove(): void {}
  createEl(_tag: string, options: { cls?: string; text?: string } = {}): Element {
    const element = new Element();
    element.text = options.text ?? "";
    element.cls = options.cls ?? "";
    this.children.push(element);
    return element;
  }
  setText(text: string): void { this.text = text; }
  addEventListener(): void {}
  querySelector(): Element | undefined { return this.children.find((child) => child.cls === "mod-cta"); }
}

const selection: Selection = { text: "autoencoders", context: "An autoencoder reconstructs its input.", source: { path: "/papers/mae.md", from: 1, to: 1 } };

function fixture() {
  const container = new Element();
  const capture = vi.fn<AgentHost["capture"]>();
  const host = { model: { configured: () => true }, captureFiles: { read: async () => "" }, originServices: () => ({}), capture } as unknown as AgentHost;
  const pane = new AgentPane(container as unknown as HTMLElement, host, () => undefined, () => {}, async () => {});
  pane.reset(selection);
  const button = () => container.children[2]!.querySelector()!;
  return { pane, capture, button, container };
}

describe("Capture action lifecycle", () => {
  it("blocks duplicate submissions while running, then permits retry after a failed job", async () => {
    const { pane, capture, button } = fixture();
    let finish!: () => void;
    capture.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    const first = pane.startCapture();
    expect(button().text).toBe("Drafting in Capture");
    expect(button().disabled).toBe(true);
    await pane.startCapture();
    expect(capture).toHaveBeenCalledOnce();
    finish();
    await first;
    expect(button().text).toBe("Capture");
    expect(button().disabled).toBe(false);
    capture.mockResolvedValueOnce();
    await pane.startCapture();
    expect(capture).toHaveBeenCalledTimes(2);
    expect(button().text).toBe("Capture");
    expect(button().disabled).toBe(false);
    capture.mockResolvedValueOnce();
    await pane.startCapture();
    expect(capture).toHaveBeenCalledTimes(3);
  });

  it("restores the action and shows an error if opening Capture rejects", async () => {
    const { pane, capture, button, container } = fixture();
    capture.mockRejectedValueOnce(new Error("Could not open Capture"));
    await pane.startCapture();
    expect(button().disabled).toBe(false);
    expect(button().text).toBe("Capture");
    expect(container.children[2]!.children.at(-1)!.text).toContain("Could not open Capture");
  });

  it("leaves a newer selection's Capture action alone when an older attempt finishes", async () => {
    const { pane, capture, button } = fixture();
    let finish!: () => void;
    capture.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    const first = pane.startCapture();
    pane.reset({ ...selection, text: "mask token" });
    finish();
    await first;
    expect(button().text).toBe("Capture");
    expect(button().disabled).toBe(false);
  });

  it("passes the origin displayed for this selection to Capture and forgets it on a new lookup", async () => {
    const { pane, capture } = fixture();
    const origin = { choice: "none" as const, reason: "General concept." };
    vi.mocked(findOrigin).mockResolvedValueOnce({ origin, failures: [] });
    await pane.sourcesFor(selection);
    capture.mockResolvedValue();
    await pane.startCapture();
    expect(capture).toHaveBeenLastCalledWith(selection, origin);
    const other = { ...selection, text: "mask token" };
    pane.reset(other);
    await pane.startCapture();
    expect(capture).toHaveBeenLastCalledWith(other, undefined);
  });
});
