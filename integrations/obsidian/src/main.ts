import { promises as fs, realpathSync } from "fs";
import { dirname } from "path";
import { FileSystemAdapter, MarkdownView, Notice, Platform, Plugin, TFile, requestUrl } from "obsidian";

import type { Selection } from "./agent-pane";
import { locateSelection, type CaptureFiles } from "./capture";
import { ModelClient, type ReasoningChoice } from "./model";
import { ApiClient } from "./api";
import {
  chatUrl,
  connectionReport,
  formatOf,
  listModels,
  modelGroups,
  probeLevels,
  type Detected,
  type DetectedLevels,
  type LevelProbe,
  type ModelChoice,
  type ModelGroup,
  type Provider,
  type ReasoningChoices,
} from "./providers";
import type { Reading } from "./search";
import { webSearch, type WebSource } from "./web";

import { HiddenKnowledgeIndexer, type HiddenKnowledgeStatus } from "./hidden-knowledge";
import { HoldTrigger } from "./hold";
import { KgdServer } from "./kgd";
import { lookupQuery } from "./lookup-model";
import { KgdistillerLookupView, LOOKUP_ICON, VIEW_TYPE_KGDISTILLER_LOOKUP } from "./lookup-view";
import { fold } from "./records";
import { fetchError, USER_AGENT, type Fetch } from "./sources";

import {
  DEFAULT_SETTINGS,
  KgdistillerSettingTab,
  mergeStoredSettings,
  type KgdistillerSettings,
} from "./settings";

/** The reading-view elements whose text is a selection's block. */
const READING_BLOCKS = "p, li, td, th, blockquote, h1, h2, h3, h4, h5, h6";

export default class KgdistillerPlugin extends Plugin {
  settings: KgdistillerSettings = { ...DEFAULT_SETTINGS, providers: [], reasoningLevels: [] };
  hiddenKnowledgeStatus: HiddenKnowledgeStatus = { state: "disabled" };
  private hiddenKnowledgeIndexer?: HiddenKnowledgeIndexer;
  private settingsTab?: KgdistillerSettingTab;
  private lastHiddenKnowledgeEnabled?: boolean;
  private hiddenKnowledgeRequest = 0;
  private unloaded = false;
  /** Lookups query every registered base through one resident `kgd mcp`. */
  readonly kgd = new KgdServer(() => ({ command: this.settings.kgdCommand, home: this.settings.knowledgeHome }));
  /** Model and search requests run directly inside the desktop plugin. */
  private readonly api = new ApiClient();
  /**
   * The model chosen in the sidebar, at its provider, with the reasoning level
   * chosen there or each job's own moved to one the model accepts; no provider
   * of that name leaves the agent off.
   */
  readonly model = new ModelClient(() => {
    const provider = this.chosenProvider();
    const model = provider ? this.settings.model : "";
    return {
      endpoint: provider ? chatUrl(provider) : "", model,
      protocol: provider ? formatOf(provider) : "chat-completions", apiKey: provider ? this.secret(provider.apiKey) : "",
      reasoning: this.settings.reasoning,
      levels: async () => {
        const found = provider && model ? await this.modelLevels(provider, model) : undefined;
        return found && "levels" in found ? found.levels : undefined;
      },
    };
  }, this.api.post);
  /** Each provider's detected models, or why they could not be listed, by provider name. */
  private readonly detected = new Map<string, Detected>();
  /** This session's detections of a model's reasoning levels, under way or failed, by provider name and model id. */
  private readonly probing = new Map<string, Promise<LevelProbe>>();

  private secret(id: string): string {
    return id ? this.app.secretStorage.getSecret(id) ?? "" : "";
  }
  /** Capture reads sources and writes drafts in base roots, which may lie outside this vault. */
  readonly captureFiles: CaptureFiles = {
    read: (path) => fs.readFile(path, "utf8"),
    ids: async (root) => {
      const ids = new Set<string>();
      for (const folder of ["entries", "drafts"]) {
        const names = await fs.readdir(`${root}/.knowledge/${folder}`).catch(() => [] as string[]);
        for (const name of names) if (name.endsWith(".md")) ids.add(fold(name.slice(0, -3)));
      }
      return ids;
    },
    create: async (path, text) => {
      await fs.mkdir(dirname(path), { recursive: true });
      await fs.writeFile(path, text, { encoding: "utf8", flag: "wx" });
    },
    remove: (path) => fs.rm(path, { force: true }),
  };
  /** The agent's web searches call Exa's hosted MCP server directly, with the owner's Exa key when one is set. */
  webSearch(query: string): Promise<WebSource[]> {
    return webSearch(this.secret(this.settings.exaKey), query, this.api.postText);
  }

  /** The registered source open in the main area's editor or reading view, from `kgd sheet --json`, which writes nothing. */
  async reading(): Promise<Reading | undefined> {
    const root = this.vaultRoot();
    // The question is typed in the sidebar, so the note being read is the main area's most recent leaf, not the active one.
    const view = this.app.workspace.getMostRecentLeaf()?.view;
    const file = view instanceof MarkdownView ? view.file : null;
    if (!root || !file) return undefined;
    try {
      const { report, failed } = await this.kgd.command(["sheet", `${root}/${file.path}`, "--json"]);
      const { base, source, type } = report as Partial<Reading>;
      return !failed && base && source && type ? { base, source, type } : undefined;
    } catch {
      return undefined;
    }
  }
  /** Source lookups name the plugin to Wikipedia and arXiv, as Wikimedia asks. */
  readonly fetchText: Fetch = async (url) => {
    const response = await requestUrl({ url, headers: { "User-Agent": USER_AGENT, "Api-User-Agent": USER_AGENT }, throw: false });
    if (response.status >= 400) throw fetchError(url, response.status, response.text);
    return response.text;
  };
  private readonly hold = new HoldTrigger(Platform.isMacOS ? "Meta" : "Control", () => void this.lookUpSelection(false));
  /**
   * The text last selected in a reading view, with its block's text. Opening
   * the command palette moves the page's selection into the palette's input,
   * so commands read it from here; a click that clears the selection in the
   * page clears it here too.
   */
  private readingSelection: { text: string; block: string } | undefined;

  async onload(): Promise<void> {
    await this.loadPluginSettings();
    this.hiddenKnowledgeIndexer = new HiddenKnowledgeIndexer(this.app);
    // Restore hidden files before Obsidian restores their open workspace leaves.
    await this.configureHiddenKnowledge();
    if (this.unloaded) return;
    this.addCommand({
      id: "rescan-hidden-knowledge-folder",
      name: "Rescan hidden knowledge folder",
      callback: () => void this.rescanHiddenKnowledge(),
    });
    this.registerView(
      VIEW_TYPE_KGDISTILLER_LOOKUP,
      (leaf) => new KgdistillerLookupView(leaf, this),
    );
    this.addRibbonIcon(LOOKUP_ICON, "Open kgdistiller", () => void this.lookupView().then((view) => view?.focusSearch()));
    this.addCommand({
      id: "look-up-selection",
      name: "Look up selection",
      callback: () => void this.lookUpSelection(true),
    });
    this.addCommand({
      id: "capture-selection",
      name: "Capture selection",
      callback: () => void this.captureSelection(),
    });
    this.addCommand({
      id: "search-knowledge",
      name: "Search knowledge",
      callback: () => void this.lookupView().then((view) => view?.focusSearch()),
    });
    this.watchHold(document);
    this.registerEvent(this.app.workspace.on("window-open", (_workspaceWindow, popout) => this.watchHold(popout.document)));
    this.settingsTab = new KgdistillerSettingTab(this.app, this);
    this.addSettingTab(this.settingsTab);
  }

  onunload(): void {
    this.unloaded = true;
    this.hold.reset();
    this.kgd.stop();
    this.api.close();
    this.hiddenKnowledgeIndexer?.dispose();
  }

  /**
   * Long-pressing the command key with text selected looks the text up, in
   * the main window and in popouts; selections in reading view are remembered.
   */
  private watchHold(target: Document): void {
    this.registerDomEvent(target, "keydown", (event) => this.hold.keydown(event), true);
    this.registerDomEvent(target, "keyup", (event) => this.hold.keyup(event), true);
    this.registerDomEvent(target, "mousedown", () => this.hold.cancel(), true);
    this.registerDomEvent(target, "selectionchange", () => this.rememberReadingSelection(target));
    const view = target.defaultView;
    if (view) this.registerDomEvent(view, "blur", () => this.hold.reset());
  }

  private rememberReadingSelection(target: Document): void {
    const selection = target.getSelection();
    const node = selection?.anchorNode;
    const element = node instanceof Element ? node : node?.parentElement;
    if (!element?.closest(".markdown-reading-view")) return;
    const text = selection?.toString() ?? "";
    this.readingSelection = text.trim() ? { text, block: element.closest(READING_BLOCKS)?.textContent ?? "" } : undefined;
  }

  /** Look up the selected text; a long press without a selection or without kgd configured does nothing. */
  async lookUpSelection(fromCommand: boolean): Promise<void> {
    const selection = await this.selection();
    if (!selection) {
      if (fromCommand) new Notice("Select text to look up.");
      return;
    }
    if (!this.kgd.configured()) {
      if (fromCommand) new Notice("Set the kgd executable in the kgdistiller settings to look up knowledge.");
      return;
    }
    const view = await this.lookupView();
    await view?.lookup(selection);
  }

  /** Look the selection up, then capture it from the lookup sidebar. */
  async captureSelection(): Promise<void> {
    if (!this.model.configured() || !this.kgd.configured()) {
      new Notice("Set the kgd executable and add a model provider in the kgdistiller settings, and choose a model in the sidebar, to capture knowledge.");
      return;
    }
    const selection = await this.selection();
    if (!selection?.source) {
      new Notice("Select text in a source file to capture it.");
      return;
    }
    const view = await this.lookupView();
    await view?.lookup(selection, { capture: true });
  }

  /**
   * The current selection with the lines around it: from the editor with its
   * line numbers, or from reading view, whose text is located in the file.
   */
  private async selection(): Promise<Selection | undefined> {
    const root = this.vaultRoot();
    const pathOf = (file: TFile | null | undefined) => (root && file ? `${root}/${file.path}` : undefined);
    const active = this.app.workspace.activeEditor;
    const editor = active?.editor;
    const fromEditor = editor?.getSelection() ?? "";
    if (editor && fromEditor.trim()) {
      const from = editor.getCursor("from");
      const to = editor.getCursor("to");
      const last = to.ch === 0 && to.line > from.line ? to.line - 1 : to.line;
      const context = Array.from({ length: last - from.line + 1 }, (_, offset) => editor.getLine(from.line + offset)).join("\n");
      const text = lookupQuery(fromEditor);
      const path = pathOf(active?.file);
      return { text, context, ...(path ? { source: { path, from: from.line + 1, to: last + 1 } } : {}) };
    }
    const markdown = this.app.workspace.getActiveViewOfType(MarkdownView);
    const path = pathOf(markdown?.file);
    const dom = activeWindow.getSelection();
    const reading = markdown?.getMode() === "preview" && !dom?.toString().trim() ? this.readingSelection : undefined;
    const selected = reading?.text ?? dom?.toString() ?? "";
    const text = lookupQuery(selected);
    if (!text) return undefined;
    const context = reading?.block ?? dom?.anchorNode?.parentElement?.closest(READING_BLOCKS)?.textContent ?? "";
    if (!path || markdown?.getMode() !== "preview") return { text, context };
    const lines = (await this.captureFiles.read(path).catch(() => "")).replace(/\r\n?/g, "\n").split("\n");
    const range = locateSelection(selected, lines, context);
    return { text, context, ...(range ? { source: { path, from: range[0], to: range[1] } } : {}) };
  }

  /** The lookup view, opened in the right sidebar without taking focus from the editor. */
  private async lookupView(): Promise<KgdistillerLookupView | undefined> {
    let leaf = this.app.workspace.getLeavesOfType(VIEW_TYPE_KGDISTILLER_LOOKUP)[0];
    if (!leaf) {
      leaf = this.app.workspace.getRightLeaf(false) ?? undefined;
      if (!leaf) {
        new Notice("Could not create a workspace leaf for the kgdistiller lookup.");
        return undefined;
      }
      await leaf.setViewState({ type: VIEW_TYPE_KGDISTILLER_LOOKUP, active: false });
    }
    await this.app.workspace.revealLeaf(leaf);
    return leaf.view instanceof KgdistillerLookupView ? leaf.view : undefined;
  }

  /** The real path of the vault root, which `kgd base list` reports for its base. */
  vaultRoot(): string | undefined {
    const adapter = this.app.vault.adapter;
    if (!(adapter instanceof FileSystemAdapter)) return undefined;
    try {
      return realpathSync(adapter.getBasePath());
    } catch {
      return undefined;
    }
  }

  kgdSettingsChanged(): void {
    this.kgd.stop();
  }

  agentSettingsChanged(): void {
    this.api.cancel();
    // Detections cancelled here, or failed under the old settings, are made again.
    this.probing.clear();
    for (const view of this.lookupViews()) void view.showModels();
  }

  /** Each provider's detected models, detecting those not yet known, or every provider's again with `refresh`. */
  async modelCatalog(refresh = false): Promise<ReadonlyMap<string, Detected>> {
    await Promise.all(this.settings.providers.map(async (provider) => {
      if (!refresh && this.detected.has(provider.name)) return;
      try {
        this.detected.set(provider.name, { models: await listModels(provider, this.secret(provider.apiKey), this.api.get) });
      } catch (error) {
        this.detected.set(provider.name, { error: error instanceof Error ? error.message : String(error) });
      }
    }));
    return this.detected;
  }

  /** Forget a provider's detected models and their reasoning levels; they are detected again when next needed. */
  forgetModels(name: string): void {
    this.detected.delete(name);
    this.settings.reasoningLevels = this.settings.reasoningLevels.filter((entry) => entry.provider !== name);
    // A provider's name holds no line break, so the key's first line is the provider.
    for (const key of [...this.probing.keys()]) if (key.startsWith(`${name}\n`)) this.probing.delete(key);
  }

  /**
   * Ask a provider's model at each reasoning level and store the levels it
   * accepts. A detection that a settings change or a refresh replaced while
   * it ran stores nothing.
   */
  private detectLevels(provider: Provider, model: string): Promise<LevelProbe> {
    const key = `${provider.name}\n${model}`;
    const client = new ModelClient(() => ({
      endpoint: chatUrl(provider), model, protocol: formatOf(provider), apiKey: this.secret(provider.apiKey),
    }), this.api.post);
    const detection: Promise<LevelProbe> = probeLevels((request) => client.complete(request)).then(async (probe) => {
      if (this.probing.get(key) !== detection) return probe;
      const levels = probe.answers.map((answer) => answer.level);
      this.settings.reasoningLevels = [
        ...this.settings.reasoningLevels.filter((entry) => entry.provider !== provider.name || entry.model !== model),
        { provider: provider.name, model, levels },
      ];
      await this.savePluginSettings();
      return probe;
    });
    this.probing.set(key, detection);
    return detection;
  }

  /** The reasoning levels a provider's model accepts, as stored or detected now; `refresh` detects them again. */
  private async modelLevels(provider: Provider, model: string, refresh = false): Promise<DetectedLevels> {
    const stored = this.settings.reasoningLevels.find((entry) => entry.provider === provider.name && entry.model === model);
    if (stored && !refresh) return { levels: stored.levels };
    try {
      const probe = await ((!refresh && this.probing.get(`${provider.name}\n${model}`)) || this.detectLevels(provider, model));
      return { levels: probe.answers.map((answer) => answer.level) };
    } catch (error) {
      return { error: error instanceof Error ? error.message : String(error) };
    }
  }

  /**
   * Detect the provider's models again, then ask the model in use there, or
   * the first one it lists, at each reasoning level; the sidebars then show
   * the levels it accepts.
   */
  async testProvider(provider: Provider): Promise<string> {
    this.forgetModels(provider.name);
    const entry = (await this.modelCatalog()).get(provider.name);
    if (!entry || "error" in entry) return `Could not list models: ${entry && "error" in entry ? entry.error : "no answer"}`;
    const model = this.settings.provider === provider.name && this.settings.model ? this.settings.model : entry.models[0];
    if (!model) return "The service lists no models.";
    try {
      return connectionReport(model, await this.detectLevels(provider, model));
    } catch (error) {
      return `${model} did not answer: ${error instanceof Error ? error.message : String(error)}`;
    } finally {
      for (const view of this.lookupViews()) void view.showModels();
    }
  }

  /** The sidebar's model choices, grouped by provider, and the model in use. */
  async modelChoices(refresh = false): Promise<{ groups: ModelGroup[]; chosen: ModelChoice }> {
    const detected = await this.modelCatalog(refresh);
    const chosen = { provider: this.settings.provider, model: this.settings.model };
    return { groups: modelGroups(this.settings.providers, detected, chosen), chosen };
  }

  /** The level chosen in the sidebar and the levels the model in use accepts, detected when not yet known or with `refresh`. */
  async reasoningChoices(refresh = false): Promise<ReasoningChoices> {
    const chosen = this.settings.reasoning;
    const provider = this.chosenProvider();
    if (!provider || !this.settings.model) return { chosen };
    return { chosen, ...await this.modelLevels(provider, this.settings.model, refresh) };
  }

  /** Use another model from now on, at each job's own reasoning level, since models accept different levels; the open sidebars show it. */
  async chooseModel(choice: ModelChoice): Promise<void> {
    this.settings.provider = choice.provider;
    this.settings.model = choice.model;
    this.settings.reasoning = "";
    await this.savePluginSettings();
    for (const view of this.lookupViews()) void view.showModels();
  }

  /** Send every request at this reasoning level from now on, or empty for each job's own. */
  async chooseReasoning(choice: ReasoningChoice): Promise<void> {
    this.settings.reasoning = choice;
    await this.savePluginSettings();
    for (const view of this.lookupViews()) void view.showModels();
  }

  private chosenProvider(): Provider | undefined {
    return this.settings.providers.find((candidate) => candidate.name === this.settings.provider);
  }

  private lookupViews(): KgdistillerLookupView[] {
    return this.app.workspace.getLeavesOfType(VIEW_TYPE_KGDISTILLER_LOOKUP)
      .flatMap((leaf) => (leaf.view instanceof KgdistillerLookupView ? [leaf.view] : []));
  }

  async savePluginSettings(): Promise<void> {
    await this.saveData(this.settings);
    await this.configureHiddenKnowledge();
  }

  async rescanHiddenKnowledge(): Promise<void> {
    await this.configureHiddenKnowledge(true);
  }

  private async configureHiddenKnowledge(rescan = false): Promise<void> {
    const indexer = this.hiddenKnowledgeIndexer;
    if (!indexer || this.unloaded) return;
    const enabled = this.settings.hiddenKnowledgeEnabled;
    const changed = enabled !== this.lastHiddenKnowledgeEnabled;
    if (!changed && !rescan) return;
    this.lastHiddenKnowledgeEnabled = enabled;
    const request = ++this.hiddenKnowledgeRequest;
    const updateStatus = (status: HiddenKnowledgeStatus): void => {
      if (request === this.hiddenKnowledgeRequest) this.setHiddenKnowledgeStatus(status);
    };

    try {
      if (enabled && this.hasExternalHiddenFolderIndexer()) {
        await indexer.configure(false);
        updateStatus({
          state: "unsupported",
          message: "Hidden Folders Access is enabled. Disable it, then rescan here to avoid running two hidden-folder indexers.",
        });
      } else {
        const wasEnabled = this.hiddenKnowledgeStatus.state === "enabled";
        const status = !changed && rescan && enabled && wasEnabled
          ? await indexer.rescan()
          : await indexer.configure(enabled);
        updateStatus(status);
      }
    } catch (error) {
      updateStatus({
        state: "error",
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }

  private hasExternalHiddenFolderIndexer(): boolean {
    const plugins = (this.app as unknown as {
      plugins?: { enabledPlugins?: Set<string>; plugins?: Record<string, unknown> };
    }).plugins;
    return Boolean(
      plugins?.enabledPlugins?.has("hidden-folders-access") ||
      plugins?.plugins?.["hidden-folders-access"],
    );
  }

  private setHiddenKnowledgeStatus(status: HiddenKnowledgeStatus): void {
    if (this.unloaded) return;
    this.hiddenKnowledgeStatus = status;
    this.settingsTab?.refreshHiddenKnowledgeStatus();
  }

  private async loadPluginSettings(): Promise<void> {
    const { settings, rejected } = mergeStoredSettings(await this.loadData());
    this.settings = settings;
    if (rejected.length > 0) {
      new Notice(`kgdistiller ignored invalid stored settings and used defaults: ${rejected.join(", ")}.`);
    }
  }
}
