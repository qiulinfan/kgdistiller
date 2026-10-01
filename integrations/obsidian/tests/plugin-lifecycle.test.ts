import { describe, expect, it, vi } from "vitest";

const controls = vi.hoisted(() => ({ headings: [] as string[] }));
vi.mock("obsidian", () => {
  class Plugin { app: unknown; onunload(): void {} }
  class PluginSettingTab { containerEl: unknown; constructor(_app: unknown, _host: unknown) {} }
  class Setting {
    private name = "";
    constructor(_container: unknown) {}
    setName(name: string): this { this.name = name; return this; }
    setHeading(): this { controls.headings.push(this.name); return this; }
    setDesc(_description: string): this { return this; }
    addText(_callback: unknown): this { return this; }
    addToggle(_callback: unknown): this { return this; }
  }
  return { Plugin, PluginSettingTab, Setting, Notice: class {}, normalizePath: (path: string) => path };
});
vi.mock("../src/view", () => ({ KGDISTILLER_ICON: "network", VIEW_TYPE_KGDISTILLER_GRAPH: "kgdistiller-graph", KgdistillerGraphView: class {} }));

import KgdistillerPlugin from "../src/main";
import { DEFAULT_SETTINGS, KgdistillerSettingTab } from "../src/settings";

describe("community lifecycle conventions", () => {
  it("unloading keeps an existing graph leaf's chosen workspace location", () => {
    const plugin = new KgdistillerPlugin({} as never, {} as never);
    const leaf = { location: "main split chosen by user" };
    const detach = vi.fn(() => { leaf.location = "default sidebar"; });
    plugin.app = { workspace: { detachLeavesOfType: detach } } as unknown as typeof plugin.app;
    plugin.onunload();
    expect(detach).not.toHaveBeenCalled();
    expect(leaf.location).toBe("main split chosen by user");
  });
  it("settings use an Obsidian heading without writing an HTML heading", () => {
    controls.headings.length = 0;
    const tab = new KgdistillerSettingTab({} as never, { settings: DEFAULT_SETTINGS } as never);
    tab.containerEl = { empty: vi.fn(), createEl: vi.fn(() => { throw new Error("direct HTML heading"); }) } as unknown as typeof tab.containerEl;
    tab.display();
    expect(controls.headings).toEqual(["kgdistiller graph"]);
  });
});
