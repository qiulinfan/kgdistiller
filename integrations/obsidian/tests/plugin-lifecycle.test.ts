import { beforeEach, describe, expect, it, vi } from "vitest";

const controls = vi.hoisted(() => ({
  headings: [] as string[], stored: null as Record<string, unknown> | null,
  configure: vi.fn(), rescan: vi.fn(), dispose: vi.fn(), saveData: vi.fn(), registerView: vi.fn(), notice: vi.fn(),
  lookup: vi.fn(), capture: vi.fn(), ribbons: [] as Array<{ icon: string; title: string }>,
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
vi.mock("../src/lookup-view", () => ({ VIEW_TYPE_KGDISTILLER_LOOKUP: "kgdistiller-lookup", LOOKUP_ICON: "scan-search",
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

import KgdistillerPlugin from "../src/main";
import { DEFAULT_SETTINGS, KgdistillerSettingTab } from "../src/settings";
import { HOLD_MS } from "../src/hold";
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
  const plugin = new KgdistillerPlugin({
    workspace: {
      getLeavesOfType: vi.fn((type: string) => (type === "kgdistiller-lookup" ? lookupLeaves : [])),
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
});

describe("community lifecycle conventions", () => {
  it("unloading stops indexing and keeps the sidebar leaf's workspace location", async () => {
    const plugin = pluginFixture(); await plugin.onload(); plugin.onunload();
    expect(controls.dispose).toHaveBeenCalledOnce();
    expect(plugin.app.workspace.detachLeavesOfType).not.toHaveBeenCalled();
  });
  it("settings use Obsidian headings without writing HTML headings", () => {
    showSettings(pluginFixture());
    expect(controls.headings).toEqual(["Hidden knowledge folder", "Lookup", "Agent", "Web search"]);
  });
  it("registers one sidebar, opened from the ribbon, and no graph view or its commands", async () => {
    const plugin = pluginFixture(); await plugin.onload();
    expect(controls.registerView).toHaveBeenCalledExactlyOnceWith("kgdistiller-lookup", expect.any(Function));
    expect(controls.ribbons).toEqual([{ icon: "scan-search", title: "Open kgdistiller" }]);
    expect(controls.commands.map((command) => command.id)).toEqual([
      "rescan-hidden-knowledge-folder", "look-up-selection", "capture-selection", "search-knowledge",
    ]);
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
    expect(controls.registerView).toHaveBeenCalledOnce();
  });
});

describe("stored settings", () => {
  it("defaults to hidden indexing off until kgd obsidian install turns it on, and lookups and the agent off", () => {
    expect(DEFAULT_SETTINGS).toEqual({
      hiddenKnowledgeEnabled: false, kgdCommand: "", knowledgeHome: "", modelEndpoint: "", agentModel: "", modelProtocol: "chat-completions", modelKey: "", exaKey: "",
    });
  });
  it("replaces wrongly typed stored values with defaults and reports only those", async () => {
    controls.stored = { hiddenKnowledgeEnabled: true, kgdCommand: 7, modelEndpoint: false, unknown: true };
    const plugin = pluginFixture(); await plugin.onload();
    expect(plugin.settings).toEqual({
      hiddenKnowledgeEnabled: true, kgdCommand: "", knowledgeHome: "", modelEndpoint: "", agentModel: "", modelProtocol: "chat-completions", modelKey: "", exaKey: "",
    });
    expect(controls.notice).toHaveBeenCalledExactlyOnceWith(expect.stringContaining("used defaults: kgdCommand, modelEndpoint."));
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
      "Lookup", "kgd executable", "Knowledge home", "Agent", "Model API endpoint", "Model", "Model API format", "Model API key", "Web search", "Exa API key",
    ]);
    expect(controls.settings.find((setting) => setting.name === "Index hidden knowledge folder")?.description)
      .toContain("kgd obsidian install");
  });
  it("persists enable changes and exposes the rescan action", async () => {
    const plugin = pluginFixture(); await plugin.onload(); showSettings(plugin);
    await controls.settings.find((setting) => setting.name === "Index hidden knowledge folder")?.toggleChange?.(true);
    expect(controls.saveData).toHaveBeenCalledWith({
      hiddenKnowledgeEnabled: true, kgdCommand: "", knowledgeHome: "", modelEndpoint: "", agentModel: "", modelProtocol: "chat-completions", modelKey: "", exaKey: "",
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
