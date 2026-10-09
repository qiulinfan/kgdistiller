import { beforeEach, describe, expect, it, vi } from "vitest";

const controls = vi.hoisted(() => ({
  headings: [] as string[], mobile: false, stored: null as Record<string, unknown> | null,
  configure: vi.fn(), rescan: vi.fn(), dispose: vi.fn(), saveData: vi.fn(), registerView: vi.fn(),
  commands: [] as Array<{ id: string; callback: () => void }>,
  settings: [] as Array<{
    name: string; description?: string; disabled?: boolean;
    textChange?: (value: string) => void | Promise<void>;
    toggleChange?: (value: boolean) => void | Promise<void>;
    buttonClick?: () => void | Promise<void>;
  }>,
}));
vi.mock("obsidian", () => {
  class Plugin {
    constructor(public app: unknown, _manifest: unknown) {}
    loadData(): Promise<unknown> { return Promise.resolve(controls.stored); }
    saveData(data: unknown): Promise<void> { controls.saveData(data); return Promise.resolve(); }
    registerView(...args: unknown[]): void { controls.registerView(...args); }
    addRibbonIcon(): void {}
    addCommand(command: { id: string; callback: () => void }): void { controls.commands.push(command); }
    addSettingTab(): void {}
    registerEvent(): void {}
  }
  class PluginSettingTab { containerEl: unknown; constructor(_app: unknown, _host: unknown) {} }
  class Setting {
    private record: (typeof controls.settings)[number] = { name: "" };
    constructor(_container: unknown) { controls.settings.push(this.record); }
    setName(name: string): this { this.record.name = name; return this; }
    setHeading(): this { controls.headings.push(this.record.name); return this; }
    setDesc(description: string): this { this.record.description = description; return this; }
    addText(callback: (text: unknown) => void): this {
      const control = {
        setPlaceholder: () => control, setValue: () => control,
        setDisabled: (value: boolean) => { this.record.disabled = value; return control; },
        onChange: (fn: (value: string) => void | Promise<void>) => { this.record.textChange = fn; return control; },
      };
      callback(control); return this;
    }
    addToggle(callback: (toggle: unknown) => void): this {
      const control = {
        setValue: () => control,
        setDisabled: (value: boolean) => { this.record.disabled = value; return control; },
        onChange: (fn: (value: boolean) => void | Promise<void>) => { this.record.toggleChange = fn; return control; },
      };
      callback(control); return this;
    }
    addButton(callback: (button: unknown) => void): this {
      const control = {
        setButtonText: () => control,
        setDisabled: (value: boolean) => { this.record.disabled = value; return control; },
        onClick: (fn: () => void | Promise<void>) => { this.record.buttonClick = fn; return control; },
      };
      callback(control); return this;
    }
  }
  return { Plugin, PluginSettingTab, Setting, Notice: class {}, normalizePath: (path: string) => path,
    Platform: { get isMobile() { return controls.mobile; } } };
});
vi.mock("../src/hidden-knowledge", () => ({ HiddenKnowledgeIndexer: class {
  configure(enabled: boolean, root: string): unknown { return controls.configure(enabled, root); }
  rescan(): unknown { return controls.rescan(); }
  dispose(): void { controls.dispose(); }
} }));
vi.mock("../src/view", () => ({ KGDISTILLER_ICON: "network", VIEW_TYPE_KGDISTILLER_GRAPH: "kgdistiller-graph", KgdistillerGraphView: class {} }));

import KgdistillerPlugin from "../src/main";
import { DEFAULT_SETTINGS, KgdistillerSettingTab } from "../src/settings";

function pluginFixture(): KgdistillerPlugin {
  return new KgdistillerPlugin({
    workspace: { getLeavesOfType: vi.fn(() => []), detachLeavesOfType: vi.fn() }, vault: { on: vi.fn() },
    plugins: { enabledPlugins: new Set<string>(), plugins: {} },
  } as never, {} as never);
}
function showSettings(plugin: KgdistillerPlugin): KgdistillerSettingTab {
  const tab = new KgdistillerSettingTab(plugin.app, plugin);
  tab.containerEl = { empty: vi.fn(), createEl: vi.fn(() => { throw new Error("direct HTML heading"); }) } as unknown as typeof tab.containerEl;
  tab.display(); return tab;
}
beforeEach(() => {
  controls.headings.length = 0; controls.commands.length = 0; controls.settings.length = 0;
  controls.mobile = false; controls.stored = null;
  vi.clearAllMocks();
  controls.configure.mockImplementation(async (enabled: boolean, root: string) => ({ state: enabled ? "enabled" : "disabled", root }));
  controls.rescan.mockResolvedValue({ state: "enabled", root: ".knowledge" });
});

describe("community lifecycle conventions", () => {
  it("unloading stops indexing and keeps the graph leaf's workspace location", async () => {
    const plugin = pluginFixture(); await plugin.onload(); plugin.onunload();
    expect(controls.dispose).toHaveBeenCalledOnce();
    expect(plugin.app.workspace.detachLeavesOfType).not.toHaveBeenCalled();
  });
  it("settings use Obsidian headings without writing HTML headings", () => {
    showSettings(pluginFixture());
    expect(controls.headings).toEqual(["Graph data", "Hidden knowledge folder"]);
  });
});

describe("hidden knowledge lifecycle", () => {
  it("keeps existing installs disabled and preserves the graph path", async () => {
    controls.stored = { showReferences: false };
    const plugin = pluginFixture(); await plugin.onload();
    expect(controls.configure).toHaveBeenCalledWith(false, ".knowledge");
    expect(plugin.settings.graphPath).toBe(DEFAULT_SETTINGS.graphPath);
    expect(plugin.settings.showReferences).toBe(false);
  });
  it("awaits initial indexing before completing plugin load for workspace restoration", async () => {
    controls.stored = { hiddenKnowledgeEnabled: true };
    let finish!: (status: unknown) => void;
    controls.configure.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const plugin = pluginFixture(); const loading = plugin.onload();
    await vi.waitFor(() => expect(controls.configure).toHaveBeenCalledWith(true, ".knowledge"));
    expect(controls.registerView).not.toHaveBeenCalled();
    finish({ state: "enabled", root: ".knowledge" }); await loading;
    expect(plugin.hiddenKnowledgeStatus.state).toBe("enabled");
    expect(controls.registerView).toHaveBeenCalledOnce();
    expect(controls.commands.map((command) => command.id)).toContain("rescan-hidden-knowledge-folder");
  });
  it("does not finish UI registration after unloading while indexing", async () => {
    let finish!: (status: unknown) => void;
    controls.configure.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const plugin = pluginFixture(); const loading = plugin.onload();
    await vi.waitFor(() => expect(controls.configure).toHaveBeenCalledOnce());
    plugin.onunload(); finish({ state: "enabled", root: ".knowledge" }); await loading;
    expect(controls.registerView).not.toHaveBeenCalled(); expect(controls.dispose).toHaveBeenCalledOnce();
  });
  it("does not rescan when graph settings change", async () => {
    controls.stored = { hiddenKnowledgeEnabled: true };
    const plugin = pluginFixture(); await plugin.onload(); controls.configure.mockClear();
    plugin.settings.showReferences = false; await plugin.savePluginSettings();
    plugin.settings.graphPath = "custom/graph.json"; await plugin.savePluginSettings();
    expect(controls.configure).not.toHaveBeenCalled(); expect(controls.rescan).not.toHaveBeenCalled();
    plugin.settings.hiddenKnowledgeFolder = ".research"; await plugin.savePluginSettings();
    expect(controls.configure).toHaveBeenCalledExactlyOnceWith(true, ".research");
  });
  it("rescans an enabled indexer on explicit request", async () => {
    controls.stored = { hiddenKnowledgeEnabled: true };
    const plugin = pluginFixture(); await plugin.onload(); controls.configure.mockClear();
    await plugin.rescanHiddenKnowledge();
    expect(controls.rescan).toHaveBeenCalledOnce(); expect(controls.configure).not.toHaveBeenCalled();
  });
  it("does not display a stale indexing result after a newer configuration", async () => {
    const plugin = pluginFixture(); await plugin.onload();
    let finish!: (status: unknown) => void;
    controls.configure.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    plugin.settings.hiddenKnowledgeEnabled = true;
    const firstSave = plugin.savePluginSettings();
    await vi.waitFor(() => expect(controls.configure).toHaveBeenCalledTimes(2));
    plugin.settings.hiddenKnowledgeFolder = ".research";
    await plugin.savePluginSettings();
    finish({ state: "error", root: ".knowledge", message: "stale failure" }); await firstSave;
    expect(plugin.hiddenKnowledgeStatus).toEqual({ state: "enabled", root: ".research" });
  });
  it("leaves the graph available on mobile without enabling hidden indexing", async () => {
    controls.mobile = true; controls.stored = { hiddenKnowledgeEnabled: true };
    const plugin = pluginFixture(); await plugin.onload();
    expect(controls.configure).toHaveBeenCalledExactlyOnceWith(false, ".knowledge");
    expect(plugin.hiddenKnowledgeStatus).toMatchObject({ state: "unsupported", message: expect.stringContaining("desktop only") });
    expect(controls.registerView).toHaveBeenCalledOnce(); showSettings(plugin);
    expect(controls.settings.find((setting) => setting.name === "Index hidden knowledge folder")?.disabled).toBe(true);
  });
  it.each(["enabled", "loaded"])("rejects the external hidden-folder indexer when %s", async (state) => {
    controls.stored = { hiddenKnowledgeEnabled: true };
    const plugin = pluginFixture();
    const plugins = (plugin.app as unknown as { plugins: { enabledPlugins: Set<string>; plugins: Record<string, unknown> } }).plugins;
    if (state === "enabled") plugins.enabledPlugins.add("hidden-folders-access");
    else plugins.plugins["hidden-folders-access"] = {};
    await plugin.onload();
    expect(controls.configure).toHaveBeenCalledExactlyOnceWith(false, ".knowledge");
    expect(plugin.hiddenKnowledgeStatus).toMatchObject({ state: "unsupported", message: expect.stringContaining("Hidden Folders Access") });
    plugins.enabledPlugins.clear(); plugins.plugins = {}; await plugin.rescanHiddenKnowledge();
    expect(controls.configure).toHaveBeenLastCalledWith(true, ".knowledge");
    expect(plugin.hiddenKnowledgeStatus.state).toBe("enabled");
  });
  it("reports a missing root and clears that status after a successful rescan", async () => {
    controls.stored = { hiddenKnowledgeEnabled: true };
    controls.configure.mockResolvedValueOnce({ state: "missing", root: ".knowledge" });
    const plugin = pluginFixture(); await plugin.onload(); const tab = showSettings(plugin);
    const status = controls.settings.find((setting) => setting.name === "Indexing status");
    expect(status?.description).toContain("Folder not found");
    await plugin.rescanHiddenKnowledge(); tab.refreshHiddenKnowledgeStatus();
    expect(status?.description).toBe("Indexed: .knowledge.");
  });
  it("reports unexpected indexing errors without interrupting the graph", async () => {
    controls.configure.mockRejectedValueOnce(new Error("Unsupported adapter"));
    const plugin = pluginFixture(); await plugin.onload();
    expect(plugin.hiddenKnowledgeStatus).toMatchObject({ state: "error", message: "Unsupported adapter" });
    expect(controls.registerView).toHaveBeenCalledOnce();
  });
});

describe("hidden knowledge settings", () => {
  it("applies root edits explicitly without moving the graph path", async () => {
    const plugin = pluginFixture(); await plugin.onload(); controls.configure.mockClear(); showSettings(plugin);
    const folder = controls.settings.find((setting) => setting.name === "Hidden folder path");
    await folder?.textChange?.(" .research ");
    expect(controls.saveData).not.toHaveBeenCalled(); expect(plugin.settings.hiddenKnowledgeFolder).toBe(".knowledge");
    await folder?.buttonClick?.();
    expect(plugin.settings.hiddenKnowledgeFolder).toBe(".research"); expect(plugin.settings.graphPath).toBe(DEFAULT_SETTINGS.graphPath);
    expect(controls.configure).toHaveBeenCalledExactlyOnceWith(false, ".research");
  });
  it("persists enable changes and exposes the rescan action", async () => {
    const plugin = pluginFixture(); await plugin.onload(); showSettings(plugin);
    await controls.settings.find((setting) => setting.name === "Index hidden knowledge folder")?.toggleChange?.(true);
    expect(controls.saveData).toHaveBeenCalledWith(expect.objectContaining({ hiddenKnowledgeEnabled: true }));
    expect(controls.configure).toHaveBeenLastCalledWith(true, ".knowledge");
    await controls.settings.find((setting) => setting.name === "Indexing status")?.buttonClick?.();
    expect(controls.rescan).toHaveBeenCalledOnce();
  });
});
