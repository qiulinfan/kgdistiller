import { beforeEach, describe, expect, it, vi } from "vitest";

const controls = vi.hoisted(() => ({
  headings: [] as string[], stored: null as Record<string, unknown> | null,
  configure: vi.fn(), rescan: vi.fn(), dispose: vi.fn(), saveData: vi.fn(), registerView: vi.fn(), notice: vi.fn(),
  lookup: vi.fn(), capture: vi.fn(), showModels: vi.fn(), listModels: vi.fn(), post: vi.fn(),
  drafted: vi.fn(), relist: vi.fn(), sourcesFor: vi.fn(),
  ribbons: [] as Array<{ icon: string; title: string }>,
  domEvents: [] as Array<{ target: unknown; type: string; callback: (event: unknown) => void }>,
  secrets: new Map<string, string>(),
  commands: [] as Array<{ id: string; callback: () => void }>,
  settings: [] as Array<{
    name: string; description?: string;
    toggleChange?: (value: boolean) => void | Promise<void>;
    textChange?: (value: string) => void | Promise<void>;
    textValue?: string;
    secretChange?: (value: string) => unknown;
    dropdownChange?: (value: string) => unknown;
    buttonClick?: () => void | Promise<void>;
    buttons?: Array<{ text: string; click?: () => void | Promise<void> }>;
  }>,
}));
vi.mock("obsidian", () => {
  class Plugin {
    constructor(public app: unknown, _manifest: unknown) {}
    loadData(): Promise<unknown> { return Promise.resolve(controls.stored); }
    saveData(data: unknown): Promise<void> { controls.saveData(data); return Promise.resolve(); }
    registerView(...args: unknown[]): void { controls.registerView(...args); }
    addRibbonIcon(icon: string, title: string): void { controls.ribbons.push({ icon, title }); }
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
        setValue: (value: string) => { this.record.textValue = value; return control; },
        onChange: (fn: (value: string) => void | Promise<void>) => { this.record.textChange = fn; return control; },
      };
      callback(control); return this;
    }
    addButton(callback: (button: unknown) => void): this {
      const button: { text: string; click?: () => void | Promise<void> } = { text: "" };
      const control = {
        setButtonText: (text: string) => { button.text = text; return control; },
        setWarning: () => control, setCta: () => control, setDisabled: () => control,
        onClick: (fn: () => void | Promise<void>) => { button.click = fn; this.record.buttonClick ??= fn; return control; },
      };
      callback(control); (this.record.buttons ??= []).push(button); return this;
    }
  }
  class SecretComponent {
    constructor(_app: unknown, private record: (typeof controls.settings)[number]) {}
    setValue(): this { return this; }
    onChange(callback: (value: string) => unknown): this { this.record.secretChange = callback; return this; }
  }
  class ItemView { constructor(public leaf: unknown) {} }
  class Modal {
    contentEl = { empty: () => undefined };
    constructor(public app: unknown) {}
    setTitle(): this { return this; }
    open(): void { (this as unknown as { onOpen(): void }).onOpen(); }
    close(): void { (this as unknown as { onClose(): void }).onClose(); }
  }
  class TFile { path = ""; }
  const debounce = (callback: () => void) => Object.assign(() => callback(), { cancel: () => undefined, run: () => undefined });
  class FileSystemAdapter {}
  class MarkdownView {}
  return { Plugin, PluginSettingTab, Setting, SecretComponent, ItemView, Modal, TFile, FileSystemAdapter, MarkdownView, debounce, setIcon: () => undefined,
    Platform: { isMacOS: true }, requestUrl: vi.fn(),
    Notice: class { constructor(message: string) { controls.notice(message); } }, normalizePath: (path: string) => path };
});
vi.mock("../src/hidden-knowledge", () => ({ HiddenKnowledgeIndexer: class {
  configure(enabled: boolean): unknown { return controls.configure(enabled); }
  rescan(): unknown { return controls.rescan(); }
  dispose(): void { controls.dispose(); }
} }));
vi.mock("../src/providers", async (importOriginal) => ({
  ...await importOriginal<typeof import("../src/providers")>(), listModels: controls.listModels,
}));
// The model's requests reach controls.post instead of the network.
vi.mock("../src/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("../src/api")>(),
  ApiClient: class { post = controls.post; get = vi.fn(); postText = vi.fn(); cancel(): void {} close(): void {} },
}));
vi.mock("../src/capture-view", () => ({ VIEW_TYPE_KGDISTILLER_CAPTURE: "kgdistiller-capture",
  KgdistillerCaptureView: class {
    startCapture(selection: { text: string }): Promise<void> { controls.drafted(selection.text); return Promise.resolve(); }
    listDrafts(): Promise<void> { controls.relist(); return Promise.resolve(); }
  } }));
vi.mock("../src/lookup-view", () => ({ VIEW_TYPE_KGDISTILLER_LOOKUP: "kgdistiller-lookup", LOOKUP_ICON: "scan-search",
  KgdistillerLookupView: class {
    showModels(): Promise<void> { controls.showModels(); return Promise.resolve(); }
    sourcesFor(selection: { text: string }): Promise<void> { controls.sourcesFor(selection.text); return Promise.resolve(); }
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

import KgdistillerPlugin from "../src/main";
import { DEFAULT_SETTINGS, KgdistillerSettingTab } from "../src/settings";
import { HOLD_MS } from "../src/hold";
import { KgdistillerCaptureView } from "../src/capture-view";
import { KgdistillerLookupView } from "../src/lookup-view";

type Handler = (...args: unknown[]) => void;
interface Harness {
  plugin: KgdistillerPlugin;
  handlers: Map<string, Handler[]>;
}

function harness(): Harness {
  const handlers = new Map<string, Handler[]>();
  const on = (source: string) => vi.fn((name: string, callback: Handler) => {
    const key = `${source}:${name}`;
    handlers.set(key, [...(handlers.get(key) ?? []), callback]);
    return { key };
  });
  const lookupLeaves = [{ view: new KgdistillerLookupView(undefined as never, undefined as never) }];
  // A split below the lookup leaf becomes the Capture view once its view state is set.
  const captureLeaves: Array<{ view: unknown }> = [];
  const split = (): { view: unknown; setViewState(): Promise<void> } => {
    const leaf = { view: undefined as unknown, setViewState: async () => {
      leaf.view = new KgdistillerCaptureView(undefined as never, undefined as never);
      captureLeaves.push(leaf);
    } };
    return leaf;
  };
  const readingLeaf = { view: "the note being read" };
  const plugin = new KgdistillerPlugin({
    workspace: {
      getLeavesOfType: vi.fn((type: string) => (type === "kgdistiller-lookup" ? lookupLeaves : type === "kgdistiller-capture" ? captureLeaves : [])),
      createLeafBySplit: vi.fn(split),
      getMostRecentLeaf: vi.fn(() => readingLeaf), setActiveLeaf: vi.fn(), onLayoutReady: vi.fn(),
      detachLeavesOfType: vi.fn(), on: on("workspace"), revealLeaf: vi.fn(async () => undefined), activeEditor: null,
      getActiveViewOfType: vi.fn(() => null),
    },
    vault: { on: on("vault") },
    secretStorage: { getSecret: (id: string) => controls.secrets.get(id) ?? null },
    plugins: { enabledPlugins: new Set<string>(), plugins: {} },
  } as never, {} as never);
  return { plugin, handlers };
}
const pluginFixture = (): KgdistillerPlugin => harness().plugin;
function showSettings(plugin: KgdistillerPlugin): KgdistillerSettingTab {
  const tab = new KgdistillerSettingTab(plugin.app, plugin);
  tab.containerEl = { empty: vi.fn(), createEl: vi.fn(() => { throw new Error("direct HTML heading"); }) } as unknown as typeof tab.containerEl;
  tab.display(); return tab;
}
beforeEach(() => {
  controls.headings.length = 0; controls.commands.length = 0; controls.settings.length = 0; controls.ribbons.length = 0;
  controls.domEvents.length = 0; domSelection.text = "";
  controls.stored = null; controls.secrets.clear();
  vi.clearAllMocks();
  controls.configure.mockImplementation(async (enabled: boolean) => ({ state: enabled ? "enabled" : "disabled" }));
  controls.rescan.mockResolvedValue({ state: "enabled" });
  controls.listModels.mockReset();
  controls.listModels.mockResolvedValue([]);
  controls.post.mockReset();
  controls.post.mockResolvedValue({ choices: [{ message: { content: "ok" } }] });
});

describe("community lifecycle conventions", () => {
  it("unloading stops indexing and keeps the sidebar leaf's workspace location", async () => {
    const plugin = pluginFixture(); await plugin.onload(); plugin.onunload();
    expect(controls.dispose).toHaveBeenCalledOnce();
    expect(plugin.app.workspace.detachLeavesOfType).not.toHaveBeenCalled();
  });
  it("settings use Obsidian headings without writing HTML headings", () => {
    showSettings(pluginFixture());
    expect(controls.headings).toEqual(["Hidden knowledge folder", "Lookup", "Model providers", "Web search"]);
  });
  it("registers the lookup sidebar, opened from the ribbon, and the Capture view, and no graph view or its commands", async () => {
    const plugin = pluginFixture(); await plugin.onload();
    expect(controls.registerView.mock.calls).toEqual([["kgdistiller-lookup", expect.any(Function)], ["kgdistiller-capture", expect.any(Function)]]);
    expect(controls.ribbons).toEqual([{ icon: "scan-search", title: "Open kgdistiller" }]);
    expect(controls.commands.map((command) => command.id)).toEqual([
      "rescan-hidden-knowledge-folder", "look-up-selection", "capture-selection", "search-knowledge", "show-drafts",
    ]);
  });

  it("drafts captures in one Capture view below the lookup sidebar, and finds sources for a borrowed one", async () => {
    const { plugin } = harness(); await plugin.onload();
    const selection = { text: "masked autoencoder", context: "", source: { path: "/vault/a.md", from: 1, to: 1 } };
    plugin.capture(selection);
    await vi.waitFor(() => expect(controls.drafted).toHaveBeenCalledWith("masked autoencoder"));
    plugin.capture({ ...selection, text: "mask token" });
    await vi.waitFor(() => expect(controls.drafted).toHaveBeenCalledWith("mask token"));
    const workspace = plugin.app.workspace as unknown as {
      createLeafBySplit: ReturnType<typeof vi.fn>; setActiveLeaf: ReturnType<typeof vi.fn>; getMostRecentLeaf(): unknown; getLeavesOfType(type: string): unknown[];
    };
    expect(workspace.createLeafBySplit).toHaveBeenCalledExactlyOnceWith(workspace.getLeavesOfType("kgdistiller-lookup")[0], "horizontal");
    expect(workspace.setActiveLeaf).toHaveBeenCalledExactlyOnceWith(workspace.getMostRecentLeaf(), { focus: true });
    plugin.borrowed(selection);
    expect(controls.sourcesFor).toHaveBeenCalledExactlyOnceWith("masked autoencoder");
    controls.commands.find((command) => command.id === "show-drafts")!.callback();
    await vi.waitFor(() => expect(controls.relist).toHaveBeenCalledOnce());
  });
});

describe("record indexing", () => {
  it("indexes this vault's record files without embeddings once a burst of saves settles, and nothing else", async () => {
    vi.useFakeTimers();
    (globalThis as { window?: unknown }).window = globalThis;
    try {
      const { plugin, handlers } = harness(); await plugin.onload();
      const refresh = vi.spyOn(plugin.indexer, "refresh").mockResolvedValue();
      const fire = (event: string, path: string) => { for (const callback of handlers.get(`vault:${event}`) ?? []) callback({ path }, "old.md"); };
      fire("modify", ".knowledge/entries/elbo.md");
      fire("create", ".knowledge/entries/vae.md");
      fire("modify", "notes/a.md");
      fire("modify", ".knowledge/drafts/x.md");
      vi.advanceTimersByTime(300);
      expect(refresh).toHaveBeenCalledOnce();
      fire("delete", ".knowledge/entries/vae.md");
      vi.advanceTimersByTime(300);
      expect(refresh).toHaveBeenCalledTimes(2);
    } finally {
      delete (globalThis as { window?: unknown }).window;
      vi.useRealTimers();
    }
  });
});

describe("hidden knowledge lifecycle", () => {
  it("keeps existing installs disabled and ignores keys outside the defaults", async () => {
    controls.stored = { kgdCommand: "/bin/kgd", showDrafts: false, retiredSetting: "old/graph.json", unexpected: false };
    const plugin = pluginFixture(); await plugin.onload();
    expect(controls.configure).toHaveBeenCalledWith(false);
    expect(plugin.settings.kgdCommand).toBe("/bin/kgd");
    for (const key of ["showDrafts", "retiredSetting", "unexpected"]) expect(plugin.settings).not.toHaveProperty(key);
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
  it("does not rescan when other settings change", async () => {
    controls.stored = { hiddenKnowledgeEnabled: true };
    const plugin = pluginFixture(); await plugin.onload(); controls.configure.mockClear();
    plugin.settings.kgdCommand = "/bin/kgd"; await plugin.savePluginSettings();
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
  it("reports unexpected indexing errors without interrupting the sidebar", async () => {
    controls.configure.mockRejectedValueOnce(new Error("Unsupported adapter"));
    const plugin = pluginFixture(); await plugin.onload();
    expect(plugin.hiddenKnowledgeStatus).toMatchObject({ state: "error", message: "Unsupported adapter" });
    expect(controls.registerView).toHaveBeenCalledTimes(2);
  });
});

describe("stored settings", () => {
  it("defaults to hidden indexing off until kgd obsidian install turns it on, and lookups and the agent off", () => {
    expect(DEFAULT_SETTINGS).toEqual({
      hiddenKnowledgeEnabled: false, kgdCommand: "", knowledgeHome: "", providers: [], provider: "", model: "",
      reasoning: "", exaKey: "",
    });
  });
  it("replaces wrongly typed stored values with defaults and reports only those", async () => {
    const provider = { name: "deepseek", kind: "deepseek", baseUrl: "", format: "deepseek", levels: [], apiKey: "deepseek-key" };
    controls.stored = { hiddenKnowledgeEnabled: true, kgdCommand: 7, model: false, reasoning: "turbo",
      providers: [provider, { ...provider, format: "x" }], unknown: true };
    const plugin = pluginFixture(); await plugin.onload();
    expect(plugin.settings).toEqual({
      hiddenKnowledgeEnabled: true, kgdCommand: "", knowledgeHome: "", providers: [provider], provider: "", model: "",
      reasoning: "", exaKey: "",
    });
    expect(controls.notice).toHaveBeenCalledExactlyOnceWith(expect.stringContaining("used defaults: kgdCommand, model, reasoning, providers."));
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
      "Hidden knowledge folder", "Index hidden knowledge folder", "Indexing status",
      "Lookup", "kgd executable", "Knowledge home", "Model providers", "Provider", "Web search", "Exa API key",
    ]);
    expect(controls.settings.find((setting) => setting.name === "Index hidden knowledge folder")?.description)
      .toContain("kgd obsidian install");
  });
  it("persists enable changes and exposes the rescan action", async () => {
    const plugin = pluginFixture(); await plugin.onload(); showSettings(plugin);
    await controls.settings.find((setting) => setting.name === "Index hidden knowledge folder")?.toggleChange?.(true);
    expect(controls.saveData).toHaveBeenCalledWith({
      hiddenKnowledgeEnabled: true, kgdCommand: "", knowledgeHome: "", providers: [], provider: "", model: "",
      reasoning: "", exaKey: "",
    });
    expect(controls.configure).toHaveBeenLastCalledWith(true);
    await controls.settings.find((setting) => setting.name === "Indexing status")?.buttonClick?.();
    expect(controls.rescan).toHaveBeenCalledOnce();
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
  const last = (name: string) => controls.settings.filter((setting) => setting.name === name).at(-1)!;
  const names = () => controls.settings.map((setting) => setting.name);
  const buttonOf = (setting: (typeof controls.settings)[number], text: string) => setting.buttons!.find((item) => item.text === text)!.click!;
  const button = (setting: (typeof controls.settings)[number], text: string) => buttonOf(setting, text)();
  /** Run an action that redraws the settings, keeping only the redrawn ones. */
  const redraw = async (action: () => unknown): Promise<void> => { controls.settings.length = 0; await action(); };

  it("adds a provider of a known service without a base URL, detects its models and stores secret references only", async () => {
    controls.listModels.mockResolvedValue(["deepseek-flash", "deepseek-v4-pro"]);
    const plugin = pluginFixture(); await plugin.onload(); showSettings(plugin);
    await redraw(buttonOf(last("Provider"), "Add"));
    // The dialog asks for the service and the name; nothing is added before Add.
    expect(names()).toEqual(["Service", "Name", ""]);
    expect(plugin.settings.providers).toEqual([]);
    await last("Service").dropdownChange!("openai");
    expect(last("Name").textValue).toBe("openai");
    await last("Service").dropdownChange!("deepseek");
    expect(last("Name").textValue).toBe("deepseek");
    await last("Name").textChange!(" work ");
    await last("Service").dropdownChange!("deepseek");
    expect(last("Name").textValue).toBe("deepseek");
    await redraw(buttonOf(last(""), "Add"));
    expect(plugin.settings.providers).toEqual([{ name: "work", kind: "deepseek", baseUrl: "", format: "deepseek", levels: [], apiKey: "" }]);
    expect(names()).toEqual(expect.arrayContaining(["Provider", "Service", "Name", "API key", "Models"]));
    expect(names()).not.toContain("Base URL");
    expect(last("Service").description).toBe("https://api.deepseek.com, with the thinking switch.");
    controls.secrets.set("deepseek-key", "synthetic-secret-value");
    await last("API key").secretChange!("deepseek-key");
    await button(last("Models"), "Detect");
    expect(controls.listModels).toHaveBeenLastCalledWith(plugin.settings.providers[0], "synthetic-secret-value", expect.any(Function));
    expect(last("Models").description).toBe("deepseek-flash, deepseek-v4-pro");
    expect(plugin.model.configured()).toBe(false);
    await plugin.chooseModel({ provider: "work", model: "deepseek-flash" });
    expect(plugin.model.configured()).toBe(true);
    expect(controls.showModels).toHaveBeenCalled();
    expect(JSON.stringify(controls.saveData.mock.calls)).not.toContain("synthetic-secret-value");
    plugin.onunload();
  });

  it("shows one provider at a time, picked from the list", async () => {
    controls.stored = { providers: [
      { name: "deepseek", kind: "deepseek", baseUrl: "", format: "deepseek", apiKey: "" },
      { name: "work", kind: "custom", baseUrl: "https://llm.example.org/v1", format: "chat-completions", apiKey: "" },
    ] };
    const plugin = pluginFixture(); await plugin.onload(); showSettings(plugin);
    expect(names().filter((name) => name === "Service")).toHaveLength(1);
    expect(names()).not.toContain("Base URL");
    const pick = last("Provider").dropdownChange!;
    await redraw(() => pick("1"));
    expect(names()).toEqual(expect.arrayContaining(["Service", "Name", "Base URL", "API format", "API key", "Models"]));
    expect(last("Provider").description).toBe("2 providers.");
  });

  it("keeps a provider's name when its service changes, and shows only the fields that service takes", async () => {
    controls.stored = { providers: [{ name: "deepseek", kind: "deepseek", baseUrl: "", format: "deepseek", apiKey: "" }],
      provider: "deepseek", model: "deepseek-flash" };
    const plugin = pluginFixture(); await plugin.onload(); showSettings(plugin);
    const toOllama = last("Service").dropdownChange!;
    await redraw(() => toOllama("ollama"));
    expect(plugin.settings).toMatchObject({ providers: [{ name: "deepseek", kind: "ollama", baseUrl: "", format: "chat-completions" }], provider: "deepseek" });
    expect(last("Base URL").description).toBe("Leave empty for http://localhost:11434/v1.");
    expect(names()).not.toContain("API format");
    const toCustom = last("Service").dropdownChange!;
    await redraw(() => toCustom("custom"));
    expect(names()).toEqual(expect.arrayContaining(["Base URL", "API format"]));
  });

  it("tests a provider's connection from its settings and shows the result", async () => {
    controls.stored = { providers: [{ name: "deepseek", kind: "deepseek", baseUrl: "", format: "deepseek", apiKey: "" }] };
    const plugin = pluginFixture(); await plugin.onload();
    const report = "Connected: deepseek-flash answered at reasoning none in 0.9 s with the tool call the agent needs.";
    const test = vi.spyOn(plugin, "testProvider").mockResolvedValue(report);
    showSettings(plugin);
    await button(last("Connection"), "Test");
    expect(test).toHaveBeenCalledExactlyOnceWith(plugin.settings.providers[0]);
    expect(last("Connection").description).toBe(report);
  });

  it("tests a provider with one request at its lowest documented level", async () => {
    controls.stored = { providers: [{ name: "local", kind: "custom", baseUrl: "http://localhost:8080/v1", format: "chat-completions", levels: ["low", "high"], apiKey: "" }] };
    controls.listModels.mockResolvedValue(["qwen3"]);
    controls.post.mockResolvedValue({ choices: [{ message: { content: null, tool_calls: [{ id: "1", type: "function", function: { name: "ok", arguments: "{}" } }] } }] });
    const plugin = pluginFixture(); await plugin.onload();
    await expect(plugin.testProvider(plugin.settings.providers[0]!)).resolves.toMatch(
      /^Connected: qwen3 answered at reasoning low in \d+\.\d s with the tool call the agent needs\.$/);
    expect(controls.post).toHaveBeenCalledOnce();
    expect(controls.post.mock.calls[0]![2]).toMatchObject({ model: "qwen3", reasoning_effort: "low" });
  });

  it("lists the provider's documented levels at once and sends each job's level moved to one of them", async () => {
    controls.stored = { providers: [{ name: "ds", kind: "deepseek", baseUrl: "", format: "deepseek", apiKey: "" }], provider: "ds", model: "deepseek-flash" };
    const plugin = pluginFixture(); await plugin.onload();
    expect(plugin.reasoningChoices()).toEqual({ chosen: "", levels: ["none", "low", "high", "max"] });
    await plugin.model.complete({ messages: [], reasoning_effort: "medium" });
    expect(controls.post.mock.calls.at(-1)![2]).toMatchObject({ thinking: { type: "enabled" }, reasoning_effort: "high" });
    await plugin.chooseReasoning("max");
    await plugin.model.complete({ messages: [], reasoning_effort: "none" });
    expect(controls.post.mock.calls.at(-1)![2]).toMatchObject({ reasoning_effort: "max" });
    // Another model of the same service keeps the level; a provider that does not document it goes back to auto.
    await plugin.chooseModel({ provider: "ds", model: "deepseek-v4-pro" });
    expect(plugin.settings.reasoning).toBe("max");
    plugin.settings.providers.push({ name: "local", kind: "custom", baseUrl: "http://localhost:8080/v1", format: "chat-completions", levels: ["low"], apiKey: "" });
    await plugin.chooseModel({ provider: "local", model: "qwen3" });
    expect(plugin.settings.reasoning).toBe("");
    expect(plugin.reasoningChoices()).toEqual({ chosen: "", levels: ["low"] });
  });

  it("takes another service's reasoning levels from its settings, refusing unknown ones", async () => {
    controls.stored = { providers: [{ name: "local", kind: "custom", baseUrl: "http://localhost:8080/v1", format: "chat-completions", levels: [], apiKey: "" }] };
    const plugin = pluginFixture(); await plugin.onload(); showSettings(plugin);
    await last("Reasoning levels").textChange!("high, none turbo");
    expect(last("Reasoning levels").description).toContain("Not a level: turbo");
    expect(plugin.settings.providers[0]!.levels).toEqual([]);
    await last("Reasoning levels").textChange!("high, none low");
    expect(plugin.settings.providers[0]!.levels).toEqual(["none", "low", "high"]);
  });

  it("says what the connection test could not do before sending a request", async () => {
    controls.stored = { providers: [{ name: "deepseek", kind: "deepseek", baseUrl: "", format: "deepseek", apiKey: "" }] };
    const plugin = pluginFixture(); await plugin.onload();
    await expect(plugin.testProvider(plugin.settings.providers[0]!)).resolves.toBe("The service lists no models.");
    controls.listModels.mockRejectedValue(new Error("API request failed (HTTP 401). Check the API key and access permissions."));
    await expect(plugin.testProvider(plugin.settings.providers[0]!)).resolves.toBe(
      "Could not list models: API request failed (HTTP 401). Check the API key and access permissions.");
  });

  it("follows a renamed provider, refuses a duplicate name and forgets a removed one in use", async () => {
    controls.stored = { providers: [
      { name: "deepseek", kind: "deepseek", baseUrl: "", format: "deepseek", apiKey: "" },
      { name: "openai", kind: "openai", baseUrl: "", format: "chat-completions", apiKey: "" },
    ], provider: "deepseek", model: "deepseek-flash" };
    const plugin = pluginFixture(); await plugin.onload(); showSettings(plugin);
    await last("Name").textChange!("ds");
    expect(plugin.settings).toMatchObject({ provider: "ds", model: "deepseek-flash" });
    await last("Name").textChange!("openai");
    expect(plugin.settings.providers[0]!.name).toBe("ds");
    expect(last("Name").description).toContain("a name that no other provider has");
    await button(last("Provider"), "Remove");
    expect(plugin.settings).toMatchObject({ providers: [{ name: "openai" }], provider: "", model: "" });
  });

  it("lists every provider's models for the sidebar and says why one listed none", async () => {
    controls.stored = { providers: [
      { name: "deepseek", kind: "deepseek", baseUrl: "", format: "deepseek", apiKey: "" },
      { name: "local", kind: "lm-studio", baseUrl: "", format: "chat-completions", apiKey: "" },
    ], provider: "deepseek", model: "deepseek-flash" };
    controls.listModels.mockImplementation(async (provider: { name: string }) => {
      if (provider.name === "local") throw new Error("API connection failed (ECONNREFUSED).");
      return ["deepseek-flash", "deepseek-v4-pro"];
    });
    const plugin = pluginFixture(); await plugin.onload();
    await expect(plugin.modelChoices()).resolves.toEqual({
      groups: [
        { provider: "deepseek", models: ["deepseek-flash", "deepseek-v4-pro"] },
        { provider: "local", models: [], error: "API connection failed (ECONNREFUSED)." },
      ],
      chosen: { provider: "deepseek", model: "deepseek-flash" },
    });
    await plugin.modelChoices();
    expect(controls.listModels).toHaveBeenCalledTimes(2);
    await plugin.modelChoices(true);
    expect(controls.listModels).toHaveBeenCalledTimes(4);
  });

  it("captures only a selection in a source file, with kgd and a model configured", async () => {
    const plugin = pluginFixture(); await plugin.onload();
    expect(controls.commands.map((command) => command.id)).toContain("capture-selection");
    await plugin.captureSelection();
    expect(controls.notice).toHaveBeenLastCalledWith(expect.stringContaining("add a model provider in the kgdistiller settings"));
    plugin.settings.kgdCommand = "/bin/kgd";
    plugin.settings.providers = [{ name: "test", kind: "custom", baseUrl: "https://example.org/v1", format: "chat-completions", levels: [], apiKey: "" }];
    Object.assign(plugin.settings, { provider: "test", model: "test-model" });
    domSelection.text = "masked autoencoder";
    await plugin.captureSelection();
    expect(controls.notice).toHaveBeenLastCalledWith("Select text in a source file to capture it.");
    expect(controls.capture).not.toHaveBeenCalled();
  });
});
