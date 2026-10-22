import { promises as fs, realpathSync } from "fs";
import { dirname } from "path";
import { FileSystemAdapter, MarkdownView, Notice, Platform, Plugin, TFile, requestUrl } from "obsidian";

import type { Selection } from "./agent-pane";
import { locateSelection, type CaptureFiles } from "./capture";
import { ModelClient, type ReasoningChoice } from "./model";
import { ApiClient } from "./api";
import {
  chatUrl,
  connectionTest,
  formatOf,
  listModels,
  modelGroups,
  levelsOf,
  type Detected,
  type ModelChoice,
  type ModelGroup,
  type Provider,
  type ReasoningChoices,
} from "./providers";
import type { Reading } from "./search";
import type { LocalizeInput, LocalizeResult, LocalizeTarget } from "./localize";
import type { Origin, OriginServices } from "./origin";
import { readWebPage } from "./pages";
import { webSearch, type WebSource } from "./web";

import { HiddenKnowledgeIndexer, type HiddenKnowledgeStatus } from "./hidden-knowledge";
import { HoldTrigger } from "./hold";
import { Indexer, type EmbeddingStatus } from "./indexing";
import { INDEX_TIMEOUT_MS, KgdServer } from "./kgd";
import { lookupQuery } from "./lookup-model";
import { KgdistillerCaptureView, VIEW_TYPE_KGDISTILLER_CAPTURE } from "./capture-view";
import { KgdistillerLookupView, LOOKUP_ICON, VIEW_TYPE_KGDISTILLER_LOOKUP } from "./lookup-view";
import { fold } from "./records";
import { fetchError, USER_AGENT, type Fetch } from "./sources";

import {
  DEFAULT_SETTINGS,
  KgdistillerSettingTab,
  mergeStoredSettings,
  type KgdistillerSettings,
} from "./settings";

/** Record files of this vault's base, as vault paths. */
const ENTRIES_FOLDER = ".knowledge/entries/";
/** Saves within this delay of each other index once. */
const ENTRY_INDEX_DELAY_MS = 300;

/** The reading-view elements whose text is a selection's block. */
const READING_BLOCKS = "p, li, td, th, blockquote, h1, h2, h3, h4, h5, h6";

export default class KgdistillerPlugin extends Plugin {
  settings: KgdistillerSettings = { ...DEFAULT_SETTINGS, providers: [] };
  hiddenKnowledgeStatus: HiddenKnowledgeStatus = { state: "disabled" };
  private hiddenKnowledgeIndexer?: HiddenKnowledgeIndexer;
  private settingsTab?: KgdistillerSettingTab;
  private lastHiddenKnowledgeEnabled?: boolean;
  private hiddenKnowledgeRequest = 0;
  private unloaded = false;
  /** Lookups query every registered base through one resident `kgd mcp`. */
  readonly kgd = new KgdServer(() => ({ command: this.settings.kgdCommand, home: this.settings.knowledgeHome }));
  /** Keyword indexing follows the record files; embedding waits for the owner's Re-embed. */
  readonly indexer = new Indexer(this.kgd, (message) => new Notice(message));
  private entriesTimer: number | undefined;
  /** Model and search requests run directly inside the desktop plugin. */
  private readonly api = new ApiClient();
  /**
   * The model chosen in the sidebar, at its provider, with the reasoning level
   * chosen there or each job's own moved to one the service documents; no provider
   * of that name leaves the agent off.
   */
  readonly model = new ModelClient(() => {
    const provider = this.chosenProvider();
    const model = provider ? this.settings.model : "";
    return {
      endpoint: provider ? chatUrl(provider) : "", model,
      protocol: provider ? formatOf(provider) : "chat-completions", apiKey: provider ? this.secret(provider.apiKey) : "",
      reasoning: this.settings.reasoning,
      levels: provider ? levelsOf(provider) : [],
    };
  }, this.api.post);
  /** Each provider's detected models, or why they could not be listed, by provider name. */
  private readonly detected = new Map<string, Detected>();

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

  /** The registered source open in the main area's editor or reading view. */
  async reading(): Promise<Reading | undefined> {
    const path = this.mainAreaPath();
    return path ? this.registeredSource(path) : undefined;
  }

  /** The registered source at an absolute path, from `kgd source`, which writes nothing; undefined for any other file. */
  async registeredSource(path: string): Promise<Reading | undefined> {
    try {
      const { base, source, type } = (await this.kgd.command(["source", path])).report as unknown as Reading;
      return { base, source, type };
    } catch {
      return undefined;
    }
  }

  /** The file open in the main area, as an absolute path: the note being read while the sidebar has focus. */
  private mainAreaPath(): string | undefined {
    const root = this.vaultRoot();
    const view = this.app.workspace.getMostRecentLeaf()?.view;
    const file = view instanceof MarkdownView ? view.file : null;
    return root && file ? `${root}/${file.path}` : undefined;
  }

  /** The registered source being read, whose base and type a capture made in a side pane uses. */
  async readingPath(): Promise<string | undefined> {
    return (await this.reading()) ? this.mainAreaPath() : undefined;
  }

  /** The current selection, as a lookup reads it. */
  currentSelection(): Promise<Selection | undefined> {
    return this.selection();
  }

  originServices(): OriginServices {
    return { complete: (chat) => this.model.complete(chat), fetch: this.fetchText, webSearch: (query) => this.webSearch(query), readPage: readWebPage };
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
    this.registerView(VIEW_TYPE_KGDISTILLER_CAPTURE, (leaf) => new KgdistillerCaptureView(leaf, this));
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
    this.addCommand({
      id: "show-drafts",
      name: "Show drafts",
      callback: () => void this.captureView().then((view) => view?.listDrafts()),
    });
    // A record file written, edited, renamed or removed in this vault is indexed again without embeddings.
    this.registerEvent(this.app.vault.on("create", (file) => this.entryChanged(file.path)));
    this.registerEvent(this.app.vault.on("modify", (file) => this.entryChanged(file.path)));
    this.registerEvent(this.app.vault.on("delete", (file) => this.entryChanged(file.path)));
    this.registerEvent(this.app.vault.on("rename", (file, old) => {
      this.entryChanged(file.path);
      this.entryChanged(old);
    }));
    this.app.workspace.onLayoutReady(() => void this.indexer.refresh());
    this.watchHold(document);
    this.registerEvent(this.app.workspace.on("window-open", (_workspaceWindow, popout) => this.watchHold(popout.document)));
    this.settingsTab = new KgdistillerSettingTab(this.app, this);
    this.addSettingTab(this.settingsTab);
  }

  onunload(): void {
    this.unloaded = true;
    if (this.entriesTimer !== undefined) window.clearTimeout(this.entriesTimer);
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

  /** Look the selection up, and draft it in the Capture view. */
  async captureSelection(): Promise<void> {
    if (!this.model.configured() || !this.kgd.configured()) {
      new Notice("Set the kgd executable and add a model provider in the kgdistiller settings, and choose a model in the sidebar, to capture knowledge.");
      return;
    }
    const selection = await this.selection();
    if (!selection?.source && !selection?.side) {
      new Notice("Select text in a source file, or a concept in the kgdistiller sidebar, to capture it.");
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
    const dom = activeWindow.getSelection();
    // Text selected in the sidebar or the Capture view, in a record or a draft, is looked up like any text.
    const side = dom?.anchorNode instanceof Node ? (dom.anchorNode instanceof Element ? dom.anchorNode : dom.anchorNode.parentElement) : null;
    const pane = side?.closest(`.workspace-leaf-content[data-type="${VIEW_TYPE_KGDISTILLER_LOOKUP}"], .workspace-leaf-content[data-type="${VIEW_TYPE_KGDISTILLER_CAPTURE}"]`);
    if (pane && dom?.toString().trim()) {
      const text = lookupQuery(dom.toString());
      const context = side?.closest(READING_BLOCKS)?.textContent ?? "";
      return text ? { text, context, side: true } : undefined;
    }
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

  private entryChanged(path: string): void {
    if (!path.startsWith(ENTRIES_FOLDER)) return;
    window.clearTimeout(this.entriesTimer);
    this.entriesTimer = window.setTimeout(() => void this.indexer.refresh(), ENTRY_INDEX_DELAY_MS);
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

  /** Draw an accepted record into a draft in the Capture view, for the owner to revise and accept. */
  revise(uid: string): void {
    void this.captureView().then((view) => view?.drawRecord(uid));
  }

  /** Draft a selection in the Capture view, opened below the lookup sidebar; the owner keeps reading meanwhile. */
  async capture(selection: Selection, origin?: Origin): Promise<void> {
    const view = await this.captureView();
    await view?.startCapture(selection, origin);
  }

  /** Move a foreign record's citation to a passage of a local source with `kgd relocate`, into that source's base. */
  async localize(target: LocalizeTarget, input: LocalizeInput): Promise<LocalizeResult | undefined> {
    return (await this.captureView())?.startLocalize(target, input);
  }

  /** A capture found the selection borrowed and drafted nothing: the lookup sidebar still showing it finds where it comes from. */
  borrowed(selection: Selection): void {
    for (const view of this.lookupViews()) void view.sourcesFor(selection);
  }

  /** The Capture view, opened below the lookup sidebar, or in the right sidebar without one, leaving focus on the note being read. */
  private async captureView(): Promise<KgdistillerCaptureView | undefined> {
    let leaf = this.app.workspace.getLeavesOfType(VIEW_TYPE_KGDISTILLER_CAPTURE)[0];
    if (!leaf) {
      const lookup = this.app.workspace.getLeavesOfType(VIEW_TYPE_KGDISTILLER_LOOKUP)[0];
      const reading = this.app.workspace.getMostRecentLeaf();
      leaf = (lookup ? this.app.workspace.createLeafBySplit(lookup, "horizontal") : this.app.workspace.getRightLeaf(false)) ?? undefined;
      // A split makes the new leaf active; give focus back to the note being read.
      if (lookup && reading) this.app.workspace.setActiveLeaf(reading, { focus: true });
      if (!leaf) {
        new Notice("Could not create a workspace leaf for the kgdistiller Capture view.");
        return undefined;
      }
      await leaf.setViewState({ type: VIEW_TYPE_KGDISTILLER_CAPTURE, active: false });
    }
    await this.app.workspace.revealLeaf(leaf);
    return leaf.view instanceof KgdistillerCaptureView ? leaf.view : undefined;
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

  /** Forget a provider's detected models after its settings change. */
  forgetModels(name: string): void {
    this.detected.delete(name);
  }

  /** The home's embedding model and its vectors, from `kgd embedding`; Re-embed in the sidebar follows it. */
  async embeddingStatus(): Promise<EmbeddingStatus> {
    return this.embedding(["embedding"]);
  }

  /**
   * Make `model` the home's embedding model, or turn the dense lane off with
   * null. `kgd embedding set` downloads the model into the Hugging Face cache
   * when it is not there yet and checks that kgdistiller can run it, so it
   * runs online and may take minutes; nothing is re-embedded.
   */
  async setEmbedding(model: string | null): Promise<EmbeddingStatus> {
    return this.embedding(model === null ? ["embedding", "off"] : ["embedding", "set", model], model !== null);
  }

  private async embedding(args: string[], online = false): Promise<EmbeddingStatus> {
    const status = (await this.kgd.command(args, online ? INDEX_TIMEOUT_MS : undefined, online)).report as unknown as EmbeddingStatus;
    this.indexer.observeStatus(status);
    // The settings and the sidebar both change it; every lookup sidebar shows the result.
    for (const view of this.lookupViews()) view.showEmbedding(status);
    return status;
  }

  /** Detect the provider's models again, then test the model in use there, or the first one it lists. */
  async testProvider(provider: Provider): Promise<string> {
    this.forgetModels(provider.name);
    const entry = (await this.modelCatalog()).get(provider.name);
    if (!entry || "error" in entry) return `Could not list models: ${entry && "error" in entry ? entry.error : "no answer"}`;
    const model = this.settings.provider === provider.name && this.settings.model ? this.settings.model : entry.models[0];
    if (!model) return "The service lists no models.";
    const levels = levelsOf(provider);
    const client = new ModelClient(() => ({
      endpoint: chatUrl(provider), model, protocol: formatOf(provider), apiKey: this.secret(provider.apiKey), levels,
    }), this.api.post);
    return connectionTest((request) => client.complete(request), model, levels);
  }

  /** The sidebar's model choices, grouped by provider, and the model in use. */
  async modelChoices(refresh = false): Promise<{ groups: ModelGroup[]; chosen: ModelChoice }> {
    const detected = await this.modelCatalog(refresh);
    const chosen = { provider: this.settings.provider, model: this.settings.model };
    return { groups: modelGroups(this.settings.providers, detected, chosen), chosen };
  }

  /** The level chosen in the sidebar and the levels the provider in use documents. */
  reasoningChoices(): ReasoningChoices {
    const chosen = this.settings.reasoning;
    const provider = this.chosenProvider();
    return provider && this.settings.model ? { chosen, levels: levelsOf(provider) } : { chosen };
  }

  /** Use another model from now on, keeping the chosen reasoning level only if the new service documents it and otherwise going back to auto; the open sidebars show it. */
  async chooseModel(choice: ModelChoice): Promise<void> {
    this.settings.provider = choice.provider;
    this.settings.model = choice.model;
    // A level the new provider does not document goes back to each job's own.
    const provider = this.chosenProvider();
    if (this.settings.reasoning && !(provider && levelsOf(provider).includes(this.settings.reasoning))) this.settings.reasoning = "";
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
