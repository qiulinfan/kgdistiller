import { beforeEach, describe, expect, it, vi } from "vitest";

const controls = vi.hoisted(() => ({
  headings: [] as string[], stored: null as Record<string, unknown> | null,
  configure: vi.fn(), rescan: vi.fn(), dispose: vi.fn(), saveData: vi.fn(), registerView: vi.fn(), notice: vi.fn(),
  viewRefresh: vi.fn(), viewApply: vi.fn(), lookup: vi.fn(), capture: vi.fn(),
  domEvents: [] as Array<{ target: unknown; type: string; callback: (event: unknown) => void }>,
  secrets: new Map<string, string>(),
  commands: [] as Array<{ id: string; callback: () => void }>,
  settings: [] as Array<{
    name: string; description?: string;
    toggleChange?: (value: boolean) => void | Promise<void>;
    textChange?: (value: string) => void | Promise<void>;
    secretChange?: (value: string) => unknown;
    dropdownChange?: (value: string) => unknown;
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
    registerDomEvent(target: unknown, type: string, callback: (event: unknown) => void): void {
      controls.domEvents.push({ target, type, callback });
    }
  }
  class PluginSettingTab { containerEl: unknown; constructor(public app: unknown, _host: unknown) {} }
  class Setting {
    private record: (typeof controls.settings)[number] = { name: "" };
    controlEl = this.record;
    constructor(_container: unknown) { controls.settings.push(this.record); }
    addDropdown(callback: (dropdown: unknown) => void): this {
      const control = { addOption: () => control, setValue: () => control,
        onChange: (fn: (value: string) => unknown) => { this.record.dropdownChange = fn; return control; } };
      callback(control); return this;
    }
    setName(name: string): this { this.record.name = name; return this; }
    setHeading(): this { controls.headings.push(this.record.name); return this; }
    setDesc(description: string): this { this.record.description = description; return this; }
    addToggle(callback: (toggle: unknown) => void): this {
      const control = {
        setValue: () => control,
        onChange: (fn: (value: boolean) => void | Promise<void>) => { this.record.toggleChange = fn; return control; },
      };
      callback(control); return this;
    }
    addText(callback: (text: unknown) => void): this {
      const control = {
        setPlaceholder: () => control,
        setValue: () => control,
        onChange: (fn: (value: string) => void | Promise<void>) => { this.record.textChange = fn; return control; },
      };
      callback(control); return this;
    }
    addButton(callback: (button: unknown) => void): this {
      const control = {
        setButtonText: () => control,
        onClick: (fn: () => void | Promise<void>) => { this.record.buttonClick = fn; return control; },
      };
      callback(control); return this;
    }
  }
  class SecretComponent {
    constructor(_app: unknown, private record: (typeof controls.settings)[number]) {}
    setValue(): this { return this; }
    onChange(callback: (value: string) => unknown): this { this.record.secretChange = callback; return this; }
  }
  class ItemView { constructor(public leaf: unknown) {} }
  class TFile { path = ""; }
  const debounce = (callback: () => void) => Object.assign(() => callback(), { cancel: () => undefined, run: () => undefined });
  class FileSystemAdapter {}
  class MarkdownView {}
  return { Plugin, PluginSettingTab, Setting, SecretComponent, ItemView, TFile, FileSystemAdapter, MarkdownView, debounce, setIcon: () => undefined,
    Platform: { isMacOS: true }, requestUrl: vi.fn(),
    Notice: class { constructor(message: string) { controls.notice(message); } }, normalizePath: (path: string) => path };
});
vi.mock("../src/hidden-knowledge", () => ({ HiddenKnowledgeIndexer: class {
  configure(enabled: boolean): unknown { return controls.configure(enabled); }
  rescan(): unknown { return controls.rescan(); }
  dispose(): void { controls.dispose(); }
} }));
vi.mock("../src/view", () => ({ KGDISTILLER_ICON: "network", VIEW_TYPE_KGDISTILLER_GRAPH: "kgdistiller-graph",
  KgdistillerGraphView: class { refresh(): void { controls.viewRefresh(); } applySettings(): void { controls.viewApply(); } } }));
vi.mock("../src/lookup-view", () => ({ VIEW_TYPE_KGDISTILLER_LOOKUP: "kgdistiller-lookup",
  KgdistillerLookupView: class {
    lookup(selection: { text: string }, options?: { capture?: boolean }): Promise<void> {
      if (options?.capture) controls.capture(selection.text);
      else controls.lookup(selection.text);
      return Promise.resolve();
    }
  } }));
const mainDocument = { defaultView: { name: "main window" } };
const domSelection = { text: "" };
vi.stubGlobal("document", mainDocument);
vi.stubGlobal("activeWindow", { getSelection: () => ({ toString: () => domSelection.text }) });

import { TFile } from "obsidian";

import { defaultFilters, graphElements, graphModel, type GraphElementData, type GraphFilters } from "../src/graph-model";
import KgdistillerPlugin from "../src/main";
import { DEFAULT_SETTINGS, KgdistillerSettingTab } from "../src/settings";
import { HOLD_MS } from "../src/hold";
import { KgdistillerLookupView } from "../src/lookup-view";
import { KgdistillerGraphView } from "../src/view";
import { recordFixture } from "./fixture";

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
  const lookupLeaves = [{ view: new KgdistillerLookupView(undefined as never, undefined as never) }];
  const plugin = new KgdistillerPlugin({
    workspace: {
      getLeavesOfType: vi.fn((type: string) => (type === "kgdistiller-lookup" ? lookupLeaves : leaves)),
      detachLeavesOfType: vi.fn(), on: on("workspace"), revealLeaf: vi.fn(async () => undefined), activeEditor: null,
      getActiveViewOfType: vi.fn(() => null),
    },
    vault: { on: on("vault"), getMarkdownFiles: vi.fn(() => [...frontmatter.keys()].map((path) => fileAt(path))) },
    metadataCache: {
      on: on("cache"),
      getFileCache: vi.fn((file: { path: string }) => frontmatter.has(file.path) ? { frontmatter: frontmatter.get(file.path) } : null),
    },
    secretStorage: { getSecret: (id: string) => controls.secrets.get(id) ?? null },
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
  controls.domEvents.length = 0; domSelection.text = "";
  controls.stored = null; controls.secrets.clear();
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
    expect(controls.headings).toEqual(["Graph view", "Hidden knowledge folder", "Lookup", "Agent", "Web search"]);
  });
});

describe("hidden knowledge lifecycle", () => {
  it("keeps existing installs disabled and ignores keys outside the defaults", async () => {
    controls.stored = { showDrafts: false, retiredSetting: "old/graph.json", unexpected: false };
    const plugin = pluginFixture(); await plugin.onload();
    expect(controls.configure).toHaveBeenCalledWith(false);
    expect(plugin.settings.showDrafts).toBe(false);
    for (const key of ["retiredSetting", "unexpected"]) expect(plugin.settings).not.toHaveProperty(key);
    expect(controls.notice).not.toHaveBeenCalled();
  });
  it("awaits initial indexing before completing plugin load for workspace restoration", async () => {
    controls.stored = { hiddenKnowledgeEnabled: true };
    let finish!: (status: unknown) => void;
    controls.configure.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const plugin = pluginFixture(); const loading = plugin.onload();
    await vi.waitFor(() => expect(controls.configure).toHaveBeenCalledWith(true));
    expect(controls.registerView).not.toHaveBeenCalled();
    finish({ state: "enabled" }); await loading;
    expect(plugin.hiddenKnowledgeStatus.state).toBe("enabled");
    expect(controls.registerView).toHaveBeenCalledTimes(2);
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
    plugin.settings.hiddenKnowledgeEnabled = false; await plugin.savePluginSettings();
    expect(controls.configure).toHaveBeenCalledExactlyOnceWith(false);
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
    plugin.settings.hiddenKnowledgeEnabled = false;
    await plugin.savePluginSettings();
    finish({ state: "error", message: "stale failure" }); await firstSave;
    expect(plugin.hiddenKnowledgeStatus).toEqual({ state: "disabled" });
  });
  it.each(["enabled", "loaded"])("rejects the external hidden-folder indexer when %s", async (state) => {
    controls.stored = { hiddenKnowledgeEnabled: true };
    const plugin = pluginFixture();
    const plugins = (plugin.app as unknown as { plugins: { enabledPlugins: Set<string>; plugins: Record<string, unknown> } }).plugins;
    if (state === "enabled") plugins.enabledPlugins.add("hidden-folders-access");
    else plugins.plugins["hidden-folders-access"] = {};
    await plugin.onload();
    expect(controls.configure).toHaveBeenCalledExactlyOnceWith(false);
    expect(plugin.hiddenKnowledgeStatus).toMatchObject({ state: "unsupported", message: expect.stringContaining("Hidden Folders Access") });
    plugins.enabledPlugins.clear(); plugins.plugins = {}; await plugin.rescanHiddenKnowledge();
    expect(controls.configure).toHaveBeenLastCalledWith(true);
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
    expect(controls.registerView).toHaveBeenCalledTimes(2);
  });
});

describe("stored settings", () => {
  it("defaults to drawing drafts with hidden indexing off until kgd obsidian install turns it on, and lookups off", () => {
    expect(DEFAULT_SETTINGS).toEqual({
      showDrafts: true, hiddenKnowledgeEnabled: false, kgdCommand: "", knowledgeHome: "", modelEndpoint: "", agentModel: "", modelProtocol: "chat-completions", modelKey: "", searchEndpoint: "https://api.tavily.com/search", searchKey: "",
    });
  });
  it("replaces wrongly typed stored values with defaults and reports only those", async () => {
    controls.stored = { showDrafts: "no", hiddenKnowledgeEnabled: true, kgdCommand: 7, unknown: true };
    const plugin = pluginFixture(); await plugin.onload();
    expect(plugin.settings).toEqual({
      showDrafts: true, hiddenKnowledgeEnabled: true, kgdCommand: "", knowledgeHome: "", modelEndpoint: "", agentModel: "", modelProtocol: "chat-completions", modelKey: "", searchEndpoint: "https://api.tavily.com/search", searchKey: "",
    });
    expect(controls.notice).toHaveBeenCalledExactlyOnceWith(expect.stringContaining("used defaults: showDrafts, kgdCommand."));
    expect(controls.configure).toHaveBeenCalledExactlyOnceWith(true);
    expect(() => showSettings(plugin)).not.toThrow();
  });
});

describe("hidden knowledge settings", () => {
  it("always indexes the product .knowledge folder and ignores a stored folder path", async () => {
    controls.stored = { hiddenKnowledgeEnabled: true, hiddenKnowledgeFolder: ".research" };
    const plugin = pluginFixture(); await plugin.onload(); showSettings(plugin);
    expect(controls.configure).toHaveBeenCalledExactlyOnceWith(true);
    expect(plugin.settings).not.toHaveProperty("hiddenKnowledgeFolder");
    expect(controls.settings.map((setting) => setting.name)).toEqual([
      "Graph view", "Show drafts", "Hidden knowledge folder", "Index hidden knowledge folder", "Indexing status",
      "Lookup", "kgd executable", "Knowledge home", "Agent", "Model API endpoint", "Model", "Model API format", "Model API key", "Web search", "Search API endpoint", "Search API key",
    ]);
    expect(controls.settings.find((setting) => setting.name === "Index hidden knowledge folder")?.description)
      .toContain("kgd obsidian install");
  });
  it("persists enable changes and exposes the rescan action", async () => {
    const plugin = pluginFixture(); await plugin.onload(); showSettings(plugin);
    await controls.settings.find((setting) => setting.name === "Index hidden knowledge folder")?.toggleChange?.(true);
    expect(controls.saveData).toHaveBeenCalledWith({
      showDrafts: true, hiddenKnowledgeEnabled: true, kgdCommand: "", knowledgeHome: "", modelEndpoint: "", agentModel: "", modelProtocol: "chat-completions", modelKey: "", searchEndpoint: "https://api.tavily.com/search", searchKey: "",
    });
    expect(controls.configure).toHaveBeenLastCalledWith(true);
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
    expect([...handlers.keys()].sort())
      .toEqual(["cache:changed", "cache:deleted", "cache:resolved", "vault:rename", "workspace:window-open"]);
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

describe("graph from the mocked metadata cache", () => {
  const ALL: GraphFilters = defaultFilters(true);
  const fixture = (): Record<string, Record<string, unknown>> =>
    Object.fromEntries(recordFixture().map((item) => [item.path, { ...(item.frontmatter ?? {}) }]));
  async function loaded() {
    const state = harness();
    await state.plugin.onload();
    for (const [path, frontmatter] of Object.entries(fixture())) state.frontmatter.set(path, frontmatter);
    state.emit("cache", "resolved");
    const draw = (filters: GraphFilters = ALL): Map<string, GraphElementData> => new Map(
      graphElements(graphModel(state.plugin.knowledgeRecords()), filters)
        .map((element) => [String(element.data.id), { ...(element.data as GraphElementData), classes: element.classes }]),
    );
    return { ...state, draw };
  }
  const record = (id: string): string => `record:.knowledge/entries/${id}.md`;
  const elementsOf = (all: Map<string, GraphElementData>, element: string): GraphElementData[] =>
    [...all.values()].filter((item) => item.element === element);

  it("reads only entries and drafts from the cache", async () => {
    const { plugin } = await loaded();
    expect(plugin.knowledgeRecords()).toHaveLength(13);
  });

  it("draws binary, n-ary, self, relation-as-participant, draft, foreign and pending cases", async () => {
    const { draw } = await loaded();
    const all = draw();
    // binary: one directed typed edge
    expect(all.get(record("sigma-finite-generalizes-measure"))).toMatchObject({
      element: "relation-edge", source: record("sigma-finite"), target: record("measure"),
    });
    // n-ary: a diamond with one role edge per value
    expect(all.get(record("measure-space-triple"))).toMatchObject({ element: "relation" });
    expect(elementsOf(all, "role").filter((item) => item.source === record("measure-space-triple"))).toHaveLength(3);
    // self: a loop
    expect(all.get(record("measure-equals-itself"))).toMatchObject({ source: record("measure"), target: record("measure") });
    // relation as participant: the targeted relations are diamonds
    expect(all.get(record("sigma-algebra-prerequisite-for-measure"))).toMatchObject({ element: "relation" });
    expect(all.get(record("triple-contrasts-prerequisite"))).toMatchObject({
      element: "relation-edge", source: record("measure-space-triple"), target: record("sigma-algebra-prerequisite-for-measure"),
    });
    // draft: classed and hidden by the toggle
    const draftNode = all.get("record:.knowledge/drafts/null-set.md");
    expect(String((draftNode as { classes?: string }).classes)).toContain("kgd-draft");
    expect([...draw({ ...ALL, showDrafts: false }).values()].some((item) => item.draft)).toBe(false);
    // foreign: a grey stub
    expect(all.get("stub:notes:kl-divergence")).toMatchObject({ element: "stub", uid: "notes:kl-divergence" });
    // pending: no ghost by default, one per name key when shown
    expect(elementsOf(all, "pending")).toEqual([]);
    expect(elementsOf(draw({ ...ALL, showPending: true }), "pending").map((item) => item.id).sort())
      .toEqual(["pending:finite sets", "pending:measurable space"]);
  });

  it("updates the drawn graph on cache changes, deletions and renames", async () => {
    const { frontmatter, emit, draw } = await loaded();
    expect(draw().has("dangling:missing-record")).toBe(true);
    emit("cache", "changed", fileAt(".knowledge/entries/missing-premise.md"), "", {
      frontmatter: { ...frontmatter.get(".knowledge/entries/missing-premise.md"), premise: ["[[sigma-algebra]]"] },
    });
    expect(draw().has("dangling:missing-record")).toBe(false);
    expect(draw().get(record("missing-premise"))).toMatchObject({ source: record("sigma-algebra"), target: record("measure-space") });

    emit("cache", "deleted", fileAt(".knowledge/entries/kl-divergence-example.md"), null);
    expect(draw().has("stub:notes:kl-divergence")).toBe(false);

    frontmatter.set(".knowledge/entries/null-set.md", frontmatter.get(".knowledge/drafts/null-set.md")!);
    emit("vault", "rename", fileAt(".knowledge/entries/null-set.md"), ".knowledge/drafts/null-set.md");
    const renamed = draw();
    expect(renamed.has("record:.knowledge/drafts/null-set.md")).toBe(false);
    expect(renamed.get(record("null-set"))).toMatchObject({ element: "node", draft: false });
    expect(renamed.get("record:.knowledge/drafts/null-set-implies-measure-zero.md"))
      .toMatchObject({ source: record("null-set"), target: record("measure") });
  });
});

describe("graph view", () => {
  async function view(records: Array<{ path: string; frontmatter: Record<string, unknown> }>, kind = "") {
    const { KgdistillerGraphView: View } = await vi.importActual<typeof import("../src/view")>("../src/view");
    const instance = Object.create(View.prototype) as Record<string, unknown> & {
      refresh(): void; statusText(showing?: { shown: number; total: number }): string;
    };
    Object.assign(instance, {
      host: { settings: { ...DEFAULT_SETTINGS }, knowledgeRecords: () => records },
      filters: { ...defaultFilters(true), kind }, mode: "full", depth: 1, activePath: undefined,
      model: null, toolbarEl: {}, graphEl: {},
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
    expect(instance.statusText()).toBe("1 nodes · 1 relations · 1 drafts");
    expect(instance.statusText({ shown: 2, total: 3 })).toBe("1 nodes · 1 relations · 1 drafts · showing 2 of 3");
  });

  it("clears a kind filter that no longer matches any record", async () => {
    const instance = await view([{ path: ".knowledge/entries/measure.md", frontmatter: { label: "Measure", kind: "definition" } }], "implies");
    expect((instance.filters as { kind: string }).kind).toBe("");
  });
});

describe("selection lookup", () => {
  it("registers the command and watches the command key in the main window and in popouts", async () => {
    const { plugin, handlers } = harness(); await plugin.onload();
    expect(controls.commands.map((command) => command.id)).toContain("look-up-selection");
    const types = (target: unknown): string[] => controls.domEvents.filter((event) => event.target === target).map((event) => event.type);
    expect(types(mainDocument)).toEqual(["keydown", "keyup", "mousedown", "selectionchange"]);
    expect(types(mainDocument.defaultView)).toEqual(["blur"]);
    const popout = { defaultView: { name: "popout window" } };
    for (const callback of handlers.get("workspace:window-open") ?? []) callback({}, { document: popout });
    expect(types(popout)).toEqual(["keydown", "keyup", "mousedown", "selectionchange"]);
    expect(types(popout.defaultView)).toEqual(["blur"]);
  });

  it("tells a command user what is missing and stays silent on a long press", async () => {
    const plugin = pluginFixture(); await plugin.onload();
    await plugin.lookUpSelection(false);
    expect(controls.notice).not.toHaveBeenCalled();
    await plugin.lookUpSelection(true);
    expect(controls.notice).toHaveBeenLastCalledWith("Select text to look up.");
    domSelection.text = "masked  autoencoder";
    await plugin.lookUpSelection(false);
    expect(controls.notice).toHaveBeenCalledOnce();
    await plugin.lookUpSelection(true);
    expect(controls.notice).toHaveBeenLastCalledWith(expect.stringContaining("Set the kgd executable"));
    expect(controls.lookup).not.toHaveBeenCalled();
  });

  it("looks the selection up after a long press of the command key once kgd is configured", async () => {
    controls.stored = { kgdCommand: "/bin/kgd" };
    const plugin = pluginFixture(); await plugin.onload();
    domSelection.text = " masked\nautoencoder ";
    const keydown = controls.domEvents.find((event) => event.target === mainDocument && event.type === "keydown")!;
    vi.useFakeTimers();
    try {
      keydown.callback({ key: "Meta", repeat: false });
      vi.advanceTimersByTime(HOLD_MS);
    } finally {
      vi.useRealTimers();
    }
    await vi.waitFor(() => expect(controls.lookup).toHaveBeenCalledWith("masked autoencoder"));
    expect(controls.notice).not.toHaveBeenCalled();
  });

  it("restarts kgd after a settings change and stops it on unload", async () => {
    const plugin = pluginFixture(); await plugin.onload();
    const stop = vi.spyOn(plugin.kgd, "stop");
    showSettings(plugin);
    await controls.settings.find((setting) => setting.name === "kgd executable")!.textChange!(" /bin/kgd ");
    await controls.settings.find((setting) => setting.name === "Knowledge home")!.textChange!("/tmp/home");
    expect(plugin.settings).toMatchObject({ kgdCommand: "/bin/kgd", knowledgeHome: "/tmp/home" });
    expect(stop).toHaveBeenCalledTimes(2);
    plugin.onunload();
    expect(stop).toHaveBeenCalledTimes(3);
  });
});

describe("the agent", () => {
  it("configures direct API calls and stores secret references rather than keys", async () => {
    const plugin = pluginFixture(); await plugin.onload(); showSettings(plugin);
    await controls.settings.find((setting) => setting.name === "Model API endpoint")!.textChange!(" https://example.org/v1/chat/completions ");
    await controls.settings.find((setting) => setting.name === "Model")!.textChange!(" test-model ");
    controls.secrets.set("model-key", "synthetic-secret-value");
    await controls.settings.find((setting) => setting.name === "Model API key")!.secretChange!("model-key");
    expect(plugin.settings).toMatchObject({ modelEndpoint: "https://example.org/v1/chat/completions", agentModel: "test-model", modelKey: "model-key" });
    expect(plugin.model.configured()).toBe(true);
    expect(JSON.stringify(controls.saveData.mock.calls)).not.toContain("synthetic-secret-value");
    plugin.onunload();
  });

  it("captures only a selection in a source file, with kgd and a model API configured", async () => {
    const plugin = pluginFixture(); await plugin.onload();
    expect(controls.commands.map((command) => command.id)).toContain("capture-selection");
    await plugin.captureSelection();
    expect(controls.notice).toHaveBeenLastCalledWith(expect.stringContaining("Set the kgd executable, model API endpoint and model"));
    plugin.settings.kgdCommand = "/bin/kgd";
    plugin.settings.modelEndpoint = "https://example.org/v1/chat/completions";
    plugin.settings.agentModel = "test-model";
    domSelection.text = "masked autoencoder";
    await plugin.captureSelection();
    expect(controls.notice).toHaveBeenLastCalledWith("Select text in a source file to capture it.");
    expect(controls.capture).not.toHaveBeenCalled();
  });
});
