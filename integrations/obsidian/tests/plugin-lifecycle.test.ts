import { beforeEach, describe, expect, it, vi } from "vitest";

const controls = vi.hoisted(() => ({
  headings: [] as string[], mobile: false, stored: null as Record<string, unknown> | null,
  configure: vi.fn(), rescan: vi.fn(), dispose: vi.fn(), saveData: vi.fn(), registerView: vi.fn(), notice: vi.fn(),
  viewRefresh: vi.fn(), viewApply: vi.fn(),
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
  class TFile { path = ""; }
  const debounce = (callback: () => void) => Object.assign(() => callback(), { cancel: () => undefined, run: () => undefined });
  return { Plugin, PluginSettingTab, Setting, ItemView, TFile, debounce, setIcon: () => undefined,
    Notice: class { constructor(message: string) { controls.notice(message); } }, normalizePath: (path: string) => path,
    Platform: { get isMobile() { return controls.mobile; } } };
});
vi.mock("../src/hidden-knowledge", () => ({ DEFAULT_HIDDEN_KNOWLEDGE_EXCLUSIONS: [], HiddenKnowledgeIndexer: class {
  configure(enabled: boolean, exclusions: string[]): unknown { return controls.configure(enabled, exclusions); }
  rescan(): unknown { return controls.rescan(); }
  dispose(): void { controls.dispose(); }
} }));
vi.mock("../src/view", () => ({ KGDISTILLER_ICON: "network", VIEW_TYPE_KGDISTILLER_GRAPH: "kgdistiller-graph",
  KgdistillerGraphView: class { refresh(): void { controls.viewRefresh(); } applySettings(): void { controls.viewApply(); } } }));

import { TFile } from "obsidian";

import KgdistillerPlugin from "../src/main";
import { DEFAULT_SETTINGS, KgdistillerSettingTab } from "../src/settings";
import { KgdistillerGraphView } from "../src/view";

const fileAt = (path: string): TFile => Object.assign(new TFile(), { path });
type Handler = (...args: unknown[]) => void;
interface Harness {
  plugin: KgdistillerPlugin;
  frontmatter: Map<string, Record<string, unknown>>;
  emit(source: "cache" | "vault", name: string, ...args: unknown[]): void;
  handlers: Map<string, Handler[]>;
}

function harness(initial: Record<string, Record<string, unknown>> = {}): Harness {
  const frontmatter = new Map(Object.entries(initial));
  const handlers = new Map<string, Handler[]>();
  const on = (source: string) => vi.fn((name: string, callback: Handler) => {
    const key = `${source}:${name}`;
    handlers.set(key, [...(handlers.get(key) ?? []), callback]);
    return { key };
  });
  const leaves = [{ view: new KgdistillerGraphView(undefined as never, undefined as never) }];
  const plugin = new KgdistillerPlugin({
    workspace: { getLeavesOfType: vi.fn(() => leaves), detachLeavesOfType: vi.fn(), on: on("workspace") },
    vault: { on: on("vault"), getMarkdownFiles: vi.fn(() => [...frontmatter.keys()].map((path) => fileAt(path))) },
    metadataCache: {
      on: on("cache"),
      getFileCache: vi.fn((file: { path: string }) => frontmatter.has(file.path) ? { frontmatter: frontmatter.get(file.path) } : null),
    },
    plugins: { enabledPlugins: new Set<string>(), plugins: {} },
  } as never, {} as never);
  const emit = (source: "cache" | "vault", name: string, ...args: unknown[]): void => {
    for (const callback of handlers.get(`${source}:${name}`) ?? []) callback(...args);
  };
  return { plugin, frontmatter, emit, handlers };
}
const pluginFixture = (): KgdistillerPlugin => harness().plugin;
function showSettings(plugin: KgdistillerPlugin): KgdistillerSettingTab {
  const tab = new KgdistillerSettingTab(plugin.app, plugin);
  tab.containerEl = { empty: vi.fn(), createEl: vi.fn(() => { throw new Error("direct HTML heading"); }) } as unknown as typeof tab.containerEl;
  tab.display(); return tab;
}
beforeEach(() => {
  controls.headings.length = 0; controls.commands.length = 0; controls.settings.length = 0;
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
    expect(controls.headings).toEqual(["Graph view", "Hidden knowledge folder"]);
  });
});

describe("hidden knowledge lifecycle", () => {
  it("keeps existing installs disabled and ignores keys outside the defaults", async () => {
    controls.stored = { showDrafts: false, retiredSetting: "old/graph.json", unexpected: false };
    const plugin = pluginFixture(); await plugin.onload();
    expect(controls.configure).toHaveBeenCalledWith(false, []);
    expect(plugin.settings.showDrafts).toBe(false);
    for (const key of ["retiredSetting", "unexpected"]) expect(plugin.settings).not.toHaveProperty(key);
    expect(controls.notice).not.toHaveBeenCalled();
  });
  it("awaits initial indexing before completing plugin load for workspace restoration", async () => {
    controls.stored = { hiddenKnowledgeEnabled: true };
    let finish!: (status: unknown) => void;
    controls.configure.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const plugin = pluginFixture(); const loading = plugin.onload();
    await vi.waitFor(() => expect(controls.configure).toHaveBeenCalledWith(true, []));
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
    plugin.settings.showDrafts = false; await plugin.savePluginSettings();
    expect(controls.configure).not.toHaveBeenCalled(); expect(controls.rescan).not.toHaveBeenCalled();
    plugin.settings.hiddenKnowledgeExclusions = ["archive"]; await plugin.savePluginSettings();
    expect(controls.configure).toHaveBeenCalledExactlyOnceWith(true, ["archive"]);
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
    plugin.settings.hiddenKnowledgeExclusions = ["archive"];
    await plugin.savePluginSettings();
    finish({ state: "error", message: "stale failure" }); await firstSave;
    expect(plugin.hiddenKnowledgeStatus).toEqual({ state: "enabled" });
  });
  it("leaves the graph available on mobile without enabling hidden indexing", async () => {
    controls.mobile = true; controls.stored = { hiddenKnowledgeEnabled: true };
    const plugin = pluginFixture(); await plugin.onload();
    expect(controls.configure).toHaveBeenCalledExactlyOnceWith(false, []);
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
    expect(controls.configure).toHaveBeenCalledExactlyOnceWith(false, []);
    expect(plugin.hiddenKnowledgeStatus).toMatchObject({ state: "unsupported", message: expect.stringContaining("Hidden Folders Access") });
    plugins.enabledPlugins.clear(); plugins.plugins = {}; await plugin.rescanHiddenKnowledge();
    expect(controls.configure).toHaveBeenLastCalledWith(true, []);
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
  it("defaults to drawing drafts and indexing the whole knowledge folder", () => {
    expect(DEFAULT_SETTINGS).toEqual({ showDrafts: true, hiddenKnowledgeEnabled: false, hiddenKnowledgeExclusions: [] });
  });
  it("replaces wrongly typed stored values with defaults and reports them", async () => {
    controls.stored = { hiddenKnowledgeExclusions: "archive", showDrafts: "no", hiddenKnowledgeEnabled: true, unknown: true };
    const plugin = pluginFixture(); await plugin.onload();
    expect(plugin.settings.hiddenKnowledgeExclusions).toEqual([]);
    expect(plugin.settings.showDrafts).toBe(true);
    expect(plugin.settings.hiddenKnowledgeEnabled).toBe(true);
    expect(plugin.settings).not.toHaveProperty("unknown");
    expect(controls.notice).toHaveBeenCalledWith(expect.stringContaining("showDrafts, hiddenKnowledgeExclusions"));
    expect(() => showSettings(plugin)).not.toThrow();
  });
  it("rejects exclusion lists with non-string entries", async () => {
    controls.stored = { hiddenKnowledgeEnabled: true, hiddenKnowledgeExclusions: ["archive", null] };
    const plugin = pluginFixture(); await plugin.onload();
    expect(controls.configure).toHaveBeenCalledWith(true, []);
    expect(controls.notice).toHaveBeenCalledWith(expect.stringContaining("hiddenKnowledgeExclusions"));
  });
});

describe("hidden knowledge settings", () => {
  it("excludes nothing by default and reconfigures the indexer when the exclusions change", async () => {
    controls.stored = { hiddenKnowledgeEnabled: true };
    const plugin = pluginFixture(); await plugin.onload();
    expect(controls.configure).toHaveBeenCalledExactlyOnceWith(true, []);
    controls.configure.mockClear(); showSettings(plugin);
    const exclusions = controls.settings.find((setting) => setting.name === "Excluded folders");
    expect(exclusions?.description).toContain("stay out of native indexing");
    await exclusions?.textChange?.("archive, scratch\nreviews/old");
    expect(controls.configure).not.toHaveBeenCalled();
    await exclusions?.buttonClick?.();
    expect(plugin.settings.hiddenKnowledgeExclusions).toEqual(["archive", "scratch", "reviews/old"]);
    expect(controls.configure).toHaveBeenCalledExactlyOnceWith(true, ["archive", "scratch", "reviews/old"]);
    controls.configure.mockClear();
    plugin.settings.hiddenKnowledgeExclusions = []; await plugin.savePluginSettings();
    expect(controls.configure).toHaveBeenCalledExactlyOnceWith(true, []);
  });
  it("always indexes the product .knowledge folder and ignores a stored folder path", async () => {
    controls.stored = { hiddenKnowledgeEnabled: true, hiddenKnowledgeFolder: ".research" };
    const plugin = pluginFixture(); await plugin.onload(); showSettings(plugin);
    expect(controls.configure).toHaveBeenCalledExactlyOnceWith(true, []);
    expect(plugin.settings).not.toHaveProperty("hiddenKnowledgeFolder");
    expect(controls.settings.map((setting) => setting.name)).not.toContain("Hidden folder path");
  });
  it("drops leading and trailing slashes from typed exclusions", async () => {
    controls.stored = { hiddenKnowledgeEnabled: true };
    const plugin = pluginFixture(); await plugin.onload(); controls.configure.mockClear(); showSettings(plugin);
    const exclusions = controls.settings.find((setting) => setting.name === "Excluded folders");
    await exclusions?.textChange?.("archive/, /scratch/\n//reviews/old/, /");
    await exclusions?.buttonClick?.();
    expect(plugin.settings.hiddenKnowledgeExclusions).toEqual(["archive", "scratch", "reviews/old"]);
    expect(controls.configure).toHaveBeenCalledExactlyOnceWith(true, ["archive", "scratch", "reviews/old"]);
  });
  it("persists enable changes and exposes the rescan action", async () => {
    const plugin = pluginFixture(); await plugin.onload(); showSettings(plugin);
    await controls.settings.find((setting) => setting.name === "Index hidden knowledge folder")?.toggleChange?.(true);
    expect(controls.saveData).toHaveBeenCalledWith(expect.objectContaining({ hiddenKnowledgeEnabled: true }));
    expect(controls.configure).toHaveBeenLastCalledWith(true, []);
    await controls.settings.find((setting) => setting.name === "Indexing status")?.buttonClick?.();
    expect(controls.rescan).toHaveBeenCalledOnce();
  });
});

describe("live record model", () => {
  const MEASURE = { label: "Measure", kind: "definition" };
  const PREREQUISITE = { label: "Sigma algebra prerequisite for measure", kind: "prerequisite-for", prerequisite: ["[[sigma-algebra]]"], dependent: ["[[measure]]"] };

  it("builds the model from entries and drafts in the metadata cache only", async () => {
    const { plugin } = harness({
      ".knowledge/entries/measure.md": MEASURE,
      ".knowledge/drafts/null-set.md": { label: "Null set", kind: "definition" },
      ".knowledge/sheets/notes/chapter.tex.md": {},
      "notes/chapter.md": { label: "Chapter" },
    });
    await plugin.onload();
    expect(plugin.knowledgeRecords()).toEqual([
      { path: ".knowledge/entries/measure.md", frontmatter: MEASURE },
      { path: ".knowledge/drafts/null-set.md", frontmatter: { label: "Null set", kind: "definition" } },
    ]);
  });

  it("listens to metadata changes, deletions and renames, never to focus or leaf changes", async () => {
    const { plugin, handlers } = harness();
    await plugin.onload();
    expect([...handlers.keys()].sort()).toEqual(["cache:changed", "cache:deleted", "cache:resolved", "vault:rename"]);
  });

  it("rebuilds once the metadata cache is resolved and re-renders open views", async () => {
    const { plugin, frontmatter, emit } = harness();
    await plugin.onload();
    expect(plugin.knowledgeRecords()).toEqual([]);
    frontmatter.set(".knowledge/entries/measure.md", MEASURE);
    emit("cache", "resolved");
    expect(plugin.knowledgeRecords().map((record) => record.path)).toEqual([".knowledge/entries/measure.md"]);
    expect(controls.viewRefresh).toHaveBeenCalledOnce();
    frontmatter.set(".knowledge/entries/other.md", MEASURE);
    emit("cache", "resolved");
    expect(plugin.knowledgeRecords()).toHaveLength(1);
  });

  it("updates records incrementally on changed, deleted and rename events", async () => {
    const { plugin, emit } = harness({ ".knowledge/entries/measure.md": MEASURE });
    await plugin.onload();
    emit("cache", "changed", fileAt(".knowledge/entries/prerequisite.md"), "", { frontmatter: PREREQUISITE });
    expect(plugin.knowledgeRecords().map((record) => record.path)).toEqual([
      ".knowledge/entries/measure.md", ".knowledge/entries/prerequisite.md",
    ]);
    emit("cache", "changed", fileAt(".knowledge/entries/measure.md"), "", {});
    expect(plugin.knowledgeRecords()[0]).toEqual({ path: ".knowledge/entries/measure.md", frontmatter: {} });
    emit("cache", "deleted", fileAt(".knowledge/entries/prerequisite.md"), null);
    expect(plugin.knowledgeRecords().map((record) => record.path)).toEqual([".knowledge/entries/measure.md"]);
    expect(controls.viewRefresh).toHaveBeenCalledTimes(3);
  });

  it("moves a renamed record and drops one renamed out of the knowledge folders", async () => {
    const { plugin, frontmatter, emit } = harness({ ".knowledge/drafts/measure.md": MEASURE });
    await plugin.onload();
    frontmatter.set(".knowledge/entries/measure.md", MEASURE);
    emit("vault", "rename", fileAt(".knowledge/entries/measure.md"), ".knowledge/drafts/measure.md");
    expect(plugin.knowledgeRecords()).toEqual([{ path: ".knowledge/entries/measure.md", frontmatter: MEASURE }]);
    emit("vault", "rename", fileAt("archive/measure.md"), ".knowledge/entries/measure.md");
    expect(plugin.knowledgeRecords()).toEqual([]);
    expect(controls.viewRefresh).toHaveBeenCalledTimes(2);
  });

  it("ignores events for files outside entries and drafts", async () => {
    const { plugin, emit } = harness();
    await plugin.onload();
    emit("cache", "changed", fileAt("notes/chapter.md"), "", { frontmatter: MEASURE });
    emit("cache", "changed", fileAt(".knowledge/sheets/notes/chapter.tex.md"), "", { frontmatter: {} });
    emit("cache", "deleted", fileAt("notes/other.md"), null);
    emit("vault", "rename", fileAt("notes/new.md"), "notes/old.md");
    expect(plugin.knowledgeRecords()).toEqual([]);
    expect(controls.viewRefresh).not.toHaveBeenCalled();
  });

  it("re-reads the cache on the reload command and applies the drafts setting to open views", async () => {
    const { plugin, frontmatter } = harness();
    await plugin.onload();
    frontmatter.set(".knowledge/entries/measure.md", MEASURE);
    controls.commands.find((command) => command.id === "reload-typed-graph")!.callback();
    expect(plugin.knowledgeRecords()).toHaveLength(1);
    expect(controls.viewRefresh).toHaveBeenCalledOnce();
    showSettings(plugin);
    await controls.settings.find((setting) => setting.name === "Show drafts")?.toggleChange?.(false);
    expect(plugin.settings.showDrafts).toBe(false);
    expect(controls.saveData).toHaveBeenLastCalledWith(expect.objectContaining({ showDrafts: false }));
    expect(controls.viewApply).toHaveBeenCalledOnce();
  });
});

describe("graph view", () => {
  async function view(records: Array<{ path: string; frontmatter: Record<string, unknown> }>, kind = "") {
    const { KgdistillerGraphView: View } = await vi.importActual<typeof import("../src/view")>("../src/view");
    const instance = Object.create(View.prototype) as Record<string, unknown> & { refresh(): void };
    Object.assign(instance, {
      host: { settings: { ...DEFAULT_SETTINGS }, knowledgeRecords: () => records },
      filters: { kind, showDrafts: true }, model: null, toolbarEl: {}, graphEl: {},
      renderToolbar: vi.fn(), renderGraph: vi.fn(), setStatus: vi.fn(),
    });
    instance.refresh();
    return instance;
  }

  it("renders from the live records and counts nodes, relations and drafts", async () => {
    const instance = await view([
      { path: ".knowledge/entries/measure.md", frontmatter: { label: "Measure", kind: "definition" } },
      { path: ".knowledge/entries/link.md", frontmatter: { label: "Link", kind: "implies", premise: ["[[measure]]"], conclusion: ["[[measure]]"] } },
      { path: ".knowledge/drafts/null-set.md", frontmatter: { label: "Null set", kind: "definition" } },
    ]);
    expect(instance.renderGraph).toHaveBeenCalledOnce();
    expect(instance.setStatus).toHaveBeenCalledWith("1 nodes · 1 relations · 1 drafts");
  });

  it("clears a kind filter that no longer matches any record", async () => {
    const instance = await view([{ path: ".knowledge/entries/measure.md", frontmatter: { label: "Measure", kind: "definition" } }], "implies");
    expect((instance.filters as { kind: string }).kind).toBe("");
  });
});
