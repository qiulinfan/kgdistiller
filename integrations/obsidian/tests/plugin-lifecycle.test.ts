import { beforeEach, describe, expect, it, vi } from "vitest";

const controls = vi.hoisted(() => ({
  headings: [] as string[], mobile: false, stored: null as Record<string, unknown> | null,
  configure: vi.fn(), rescan: vi.fn(), dispose: vi.fn(), saveData: vi.fn(), registerView: vi.fn(), notice: vi.fn(),
  domEvents: [] as Array<{ type: string; callback: () => void }>,
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
    registerDomEvent(_target: unknown, type: string, callback: () => void): void { controls.domEvents.push({ type, callback }); }
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
    addTextArea(callback: (text: unknown) => void): this { return this.addText(callback); }
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
  class ItemView { constructor(public leaf: unknown) {} }
  return { Plugin, PluginSettingTab, Setting, ItemView, MarkdownView: class {}, TFile: class {}, setIcon: () => undefined,
    Notice: class { constructor(message: string) { controls.notice(message); } }, normalizePath: (path: string) => path,
    Platform: { get isMobile() { return controls.mobile; } } };
});
vi.mock("../src/hidden-knowledge", () => ({ DEFAULT_HIDDEN_KNOWLEDGE_EXCLUSIONS: ["build"], HiddenKnowledgeIndexer: class {
  configure(enabled: boolean, exclusions: string[]): unknown { return controls.configure(enabled, exclusions); }
  rescan(): unknown { return controls.rescan(); }
  dispose(): void { controls.dispose(); }
} }));
vi.mock("../src/view", () => ({ KGDISTILLER_ICON: "network", VIEW_TYPE_KGDISTILLER_GRAPH: "kgdistiller-graph", KgdistillerGraphView: class {} }));

import KgdistillerPlugin from "../src/main";
import { DEFAULT_SETTINGS, KgdistillerSettingTab } from "../src/settings";
import { graphFixture } from "./fixture";

// Vitest runs in Node; the plugin registers a focus listener on the Obsidian window.
vi.stubGlobal("window", {});

function pluginFixture(): KgdistillerPlugin {
  return new KgdistillerPlugin({
    workspace: { getLeavesOfType: vi.fn(() => []), detachLeavesOfType: vi.fn(), on: vi.fn() }, vault: { on: vi.fn() },
    plugins: { enabledPlugins: new Set<string>(), plugins: {} },
  } as never, {} as never);
}
function showSettings(plugin: KgdistillerPlugin): KgdistillerSettingTab {
  const tab = new KgdistillerSettingTab(plugin.app, plugin);
  tab.containerEl = { empty: vi.fn(), createEl: vi.fn(() => { throw new Error("direct HTML heading"); }) } as unknown as typeof tab.containerEl;
  tab.display(); return tab;
}
beforeEach(() => {
  controls.headings.length = 0; controls.commands.length = 0; controls.settings.length = 0; controls.domEvents.length = 0;
  controls.mobile = false; controls.stored = null;
  vi.clearAllMocks();
  controls.configure.mockImplementation(async (enabled: boolean) => ({ state: enabled ? "enabled" : "disabled" }));
  controls.rescan.mockResolvedValue({ state: "enabled" });
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
    controls.stored = { showDefinitions: false, unexpected: false };
    const plugin = pluginFixture(); await plugin.onload();
    expect(controls.configure).toHaveBeenCalledWith(false, ["build"]);
    expect(plugin.settings.graphPath).toBe(DEFAULT_SETTINGS.graphPath);
    expect(plugin.settings.showDefinitions).toBe(false);
    expect(plugin.settings).not.toHaveProperty("unexpected");
  });
  it("awaits initial indexing before completing plugin load for workspace restoration", async () => {
    controls.stored = { hiddenKnowledgeEnabled: true };
    let finish!: (status: unknown) => void;
    controls.configure.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const plugin = pluginFixture(); const loading = plugin.onload();
    await vi.waitFor(() => expect(controls.configure).toHaveBeenCalledWith(true, ["build"]));
    expect(controls.registerView).not.toHaveBeenCalled();
    finish({ state: "enabled" }); await loading;
    expect(plugin.hiddenKnowledgeStatus.state).toBe("enabled");
    expect(controls.registerView).toHaveBeenCalledOnce();
    expect(controls.commands.map((command) => command.id)).toContain("rescan-hidden-knowledge-folder");
  });
  it("does not finish UI registration after unloading while indexing", async () => {
    let finish!: (status: unknown) => void;
    controls.configure.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const plugin = pluginFixture(); const loading = plugin.onload();
    await vi.waitFor(() => expect(controls.configure).toHaveBeenCalledOnce());
    plugin.onunload(); finish({ state: "enabled" }); await loading;
    expect(controls.registerView).not.toHaveBeenCalled(); expect(controls.dispose).toHaveBeenCalledOnce();
  });
  it("does not rescan when graph settings change", async () => {
    controls.stored = { hiddenKnowledgeEnabled: true };
    const plugin = pluginFixture(); await plugin.onload(); controls.configure.mockClear();
    plugin.settings.showDefinitions = false; await plugin.savePluginSettings();
    plugin.settings.graphPath = "custom/graph.json"; await plugin.savePluginSettings();
    expect(controls.configure).not.toHaveBeenCalled(); expect(controls.rescan).not.toHaveBeenCalled();
    plugin.settings.hiddenKnowledgeExclusions = ["drafts"]; await plugin.savePluginSettings();
    expect(controls.configure).toHaveBeenCalledExactlyOnceWith(true, ["drafts"]);
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
    plugin.settings.hiddenKnowledgeExclusions = ["drafts"];
    await plugin.savePluginSettings();
    finish({ state: "error", message: "stale failure" }); await firstSave;
    expect(plugin.hiddenKnowledgeStatus).toEqual({ state: "enabled" });
  });
  it("leaves the graph available on mobile without enabling hidden indexing", async () => {
    controls.mobile = true; controls.stored = { hiddenKnowledgeEnabled: true };
    const plugin = pluginFixture(); await plugin.onload();
    expect(controls.configure).toHaveBeenCalledExactlyOnceWith(false, ["build"]);
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
    expect(controls.configure).toHaveBeenCalledExactlyOnceWith(false, ["build"]);
    expect(plugin.hiddenKnowledgeStatus).toMatchObject({ state: "unsupported", message: expect.stringContaining("Hidden Folders Access") });
    plugins.enabledPlugins.clear(); plugins.plugins = {}; await plugin.rescanHiddenKnowledge();
    expect(controls.configure).toHaveBeenLastCalledWith(true, ["build"]);
    expect(plugin.hiddenKnowledgeStatus.state).toBe("enabled");
  });
  it("reports a missing root and clears that status after a successful rescan", async () => {
    controls.stored = { hiddenKnowledgeEnabled: true };
    controls.configure.mockResolvedValueOnce({ state: "missing" });
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

describe("stored settings", () => {
  it("re-checks graph views when Obsidian regains focus or the active leaf changes", async () => {
    const plugin = pluginFixture(); await plugin.onload();
    expect(controls.domEvents.map((event) => event.type)).toEqual(["focus"]);
    expect(plugin.app.workspace.on).toHaveBeenCalledWith("active-leaf-change", expect.any(Function));
  });
  it("replaces wrongly typed stored values with defaults and reports them", async () => {
    controls.stored = { hiddenKnowledgeExclusions: "build", graphPath: 7, showSources: false, unknown: true };
    const plugin = pluginFixture(); await plugin.onload();
    expect(plugin.settings.hiddenKnowledgeExclusions).toEqual(["build"]);
    expect(plugin.settings.graphPath).toBe(DEFAULT_SETTINGS.graphPath);
    expect(plugin.settings.showSources).toBe(false);
    expect(plugin.settings).not.toHaveProperty("unknown");
    expect(controls.notice).toHaveBeenCalledWith(expect.stringContaining("graphPath, hiddenKnowledgeExclusions"));
    expect(() => showSettings(plugin)).not.toThrow();
  });
  it("rejects exclusion lists with non-string entries", async () => {
    controls.stored = { hiddenKnowledgeEnabled: true, hiddenKnowledgeExclusions: ["build", null] };
    const plugin = pluginFixture(); await plugin.onload();
    expect(controls.configure).toHaveBeenCalledWith(true, ["build"]);
    expect(controls.notice).toHaveBeenCalledWith(expect.stringContaining("hiddenKnowledgeExclusions"));
  });
});

describe("hidden knowledge settings", () => {
  it("excludes build/ by default and reconfigures the indexer when the exclusions change", async () => {
    controls.stored = { hiddenKnowledgeEnabled: true };
    const plugin = pluginFixture(); await plugin.onload();
    expect(controls.configure).toHaveBeenCalledExactlyOnceWith(true, ["build"]);
    controls.configure.mockClear(); showSettings(plugin);
    const exclusions = controls.settings.find((setting) => setting.name === "Excluded folders");
    expect(exclusions?.description).toContain("stay out of native indexing");
    await exclusions?.textChange?.("build, drafts\nreviews/old");
    expect(controls.configure).not.toHaveBeenCalled();
    await exclusions?.buttonClick?.();
    expect(plugin.settings.hiddenKnowledgeExclusions).toEqual(["build", "drafts", "reviews/old"]);
    expect(controls.configure).toHaveBeenCalledExactlyOnceWith(true, ["build", "drafts", "reviews/old"]);
    controls.configure.mockClear();
    plugin.settings.hiddenKnowledgeExclusions = []; await plugin.savePluginSettings();
    expect(controls.configure).toHaveBeenCalledExactlyOnceWith(true, []);
  });
  it("always indexes the product .knowledge folder and ignores a stored folder path", async () => {
    controls.stored = { hiddenKnowledgeEnabled: true, hiddenKnowledgeFolder: ".research" };
    const plugin = pluginFixture(); await plugin.onload(); showSettings(plugin);
    expect(controls.configure).toHaveBeenCalledExactlyOnceWith(true, ["build"]);
    expect(plugin.settings).not.toHaveProperty("hiddenKnowledgeFolder");
    expect(controls.settings.map((setting) => setting.name)).not.toContain("Hidden folder path");
  });
  it("drops leading and trailing slashes from typed exclusions", async () => {
    controls.stored = { hiddenKnowledgeEnabled: true };
    const plugin = pluginFixture(); await plugin.onload(); controls.configure.mockClear(); showSettings(plugin);
    const exclusions = controls.settings.find((setting) => setting.name === "Excluded folders");
    await exclusions?.textChange?.("build/, /drafts/\n//reviews/old/, /");
    await exclusions?.buttonClick?.();
    expect(plugin.settings.hiddenKnowledgeExclusions).toEqual(["build", "drafts", "reviews/old"]);
    expect(controls.configure).toHaveBeenCalledExactlyOnceWith(true, ["build", "drafts", "reviews/old"]);
  });
  it("persists enable changes and exposes the rescan action", async () => {
    const plugin = pluginFixture(); await plugin.onload(); showSettings(plugin);
    await controls.settings.find((setting) => setting.name === "Index hidden knowledge folder")?.toggleChange?.(true);
    expect(controls.saveData).toHaveBeenCalledWith(expect.objectContaining({ hiddenKnowledgeEnabled: true }));
    expect(controls.configure).toHaveBeenLastCalledWith(true, ["build"]);
    await controls.settings.find((setting) => setting.name === "Indexing status")?.buttonClick?.();
    expect(controls.rescan).toHaveBeenCalledOnce();
  });
});

describe("graph view loading", () => {
  async function loadView(graphPath: string, files: Record<string, string>) {
    const { KgdistillerGraphView } = await vi.importActual<typeof import("../src/view")>("../src/view");
    const mtimes: Record<string, number> = Object.fromEntries(Object.keys(files).map((path) => [path, 1]));
    const adapter = {
      exists: vi.fn(async (path: string) => path in files),
      read: vi.fn(async (path: string) => files[path]!),
      stat: vi.fn(async (path: string) => path in files ? { mtime: mtimes[path]!, size: files[path]!.length } : null),
    };
    const view = Object.create(KgdistillerGraphView.prototype) as Record<string, unknown> & {
      refresh(): Promise<void>; refreshIfChanged(): Promise<void>;
    };
    Object.assign(view, {
      app: { vault: { adapter, getAbstractFileByPath: vi.fn(() => null), read: vi.fn() } },
      host: { settings: { ...DEFAULT_SETTINGS, graphPath } },
      filters: { relation: "", showSources: true, showDefinitions: true },
      graph: null, toolbarEl: {}, graphEl: { empty: vi.fn(), createDiv: vi.fn() }, statusEl: {}, refreshQueue: Promise.resolve(),
      renderToolbar: vi.fn(), renderGraph: vi.fn(), setStatus: vi.fn(),
    });
    await view.refresh();
    return { view, adapter, files, mtimes };
  }

  it("reads a graph below the excluded .knowledge/build/ folder through the adapter", async () => {
    const graphPath = DEFAULT_SETTINGS.graphPath;
    expect(graphPath.startsWith(".knowledge/build/")).toBe(true);
    const { view, adapter } = await loadView(graphPath, { [graphPath]: JSON.stringify(graphFixture()) });
    expect(adapter.read).toHaveBeenCalledWith(graphPath);
    expect((view.graph as { counts: { concepts: number } } | null)?.counts.concepts).toBe(2);
    expect(view.setStatus).toHaveBeenCalledWith(expect.stringContaining("2 concepts"), false);
  });

  it("reloads an excluded graph only after the file changes", async () => {
    const graphPath = DEFAULT_SETTINGS.graphPath;
    const { view, adapter, mtimes } = await loadView(graphPath, { [graphPath]: JSON.stringify(graphFixture()) });
    expect(adapter.read).toHaveBeenCalledOnce();
    await view.refreshIfChanged();
    expect(adapter.read).toHaveBeenCalledOnce();
    mtimes[graphPath] = 2;
    await view.refreshIfChanged();
    expect(adapter.read).toHaveBeenCalledTimes(2);
  });

  it("coalesces focus and leaf-change re-checks into one reload", async () => {
    const graphPath = DEFAULT_SETTINGS.graphPath;
    const { view, adapter, mtimes } = await loadView(graphPath, { [graphPath]: JSON.stringify(graphFixture()) });
    mtimes[graphPath] = 2;
    await Promise.all([view.refreshIfChanged(), view.refreshIfChanged(), view.refresh()]);
    // The first re-check reloads; the second sees the new stamp; the explicit refresh always reloads.
    expect(adapter.read).toHaveBeenCalledTimes(3);
    expect(view.renderGraph).toHaveBeenCalledTimes(3);
    let active = 0; let overlapped = false;
    const text = JSON.stringify(graphFixture());
    adapter.read.mockImplementation(async () => {
      active++; overlapped ||= active > 1;
      await new Promise((resolve) => setTimeout(resolve, 1));
      active--; return text;
    });
    mtimes[graphPath] = 3;
    await Promise.all([view.refreshIfChanged(), view.refresh(), view.refresh()]);
    expect(overlapped).toBe(false);
  });

  it("contains a failing stat during a re-check and reports it in the view", async () => {
    const graphPath = DEFAULT_SETTINGS.graphPath;
    const { view, adapter } = await loadView(graphPath, { [graphPath]: JSON.stringify(graphFixture()) });
    adapter.stat.mockRejectedValue(new Error("stat failed"));
    await expect(view.refreshIfChanged()).resolves.toBeUndefined();
    expect(view.setStatus).toHaveBeenLastCalledWith("Graph unavailable", true);
    expect((view.graphEl as { createDiv: ReturnType<typeof vi.fn> }).createDiv).toHaveBeenLastCalledWith(
      expect.objectContaining({ text: "stat failed" }),
    );
  });

  it("loads a graph that appears after a missing-graph state", async () => {
    const graphPath = DEFAULT_SETTINGS.graphPath;
    const { view, adapter, files, mtimes } = await loadView(graphPath, {});
    await view.refreshIfChanged();
    expect(adapter.read).not.toHaveBeenCalled();
    files[graphPath] = JSON.stringify(graphFixture()); mtimes[graphPath] = 1;
    await view.refreshIfChanged();
    expect(adapter.read).toHaveBeenCalledWith(graphPath);
  });

  it("reports a missing graph with the export hint", async () => {
    const { view, adapter } = await loadView(".knowledge/build/obsidian/semantic-graph.json", {});
    expect(adapter.read).not.toHaveBeenCalled();
    expect(view.graph).toBeNull();
    expect((view.graphEl as { createDiv: ReturnType<typeof vi.fn> }).createDiv).toHaveBeenCalledWith(expect.objectContaining({
      text: "No semantic graph exists at .knowledge/build/obsidian/semantic-graph.json. Run kgdistiller export obsidian.",
    }));
  });
});
