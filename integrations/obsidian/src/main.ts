import { promises as fs, realpathSync } from "fs";
import { dirname } from "path";
import { FileSystemAdapter, MarkdownView, Notice, Platform, Plugin, TFile, debounce, requestUrl, type TAbstractFile } from "obsidian";

import type { Selection } from "./agent-pane";
import { locateSelection, type CaptureFiles } from "./capture";
import { ModelGateway } from "./gateway";
import { webSearch, type WebSource } from "./web";
import type { FrontmatterRecord } from "./graph-model";

import { HiddenKnowledgeIndexer, type HiddenKnowledgeStatus } from "./hidden-knowledge";
import { HoldTrigger } from "./hold";
import { KgdServer } from "./kgd";
import { lookupQuery } from "./lookup-model";
import { KgdistillerLookupView, VIEW_TYPE_KGDISTILLER_LOOKUP } from "./lookup-view";
import { fold, isRecordPath } from "./records";
import { USER_AGENT, type GetJson } from "./sources";

import {
  KGDISTILLER_ICON,
  KgdistillerGraphView,
  VIEW_TYPE_KGDISTILLER_GRAPH,
} from "./view";
import {
  DEFAULT_SETTINGS,
  KgdistillerSettingTab,
  mergeStoredSettings,
  type KgdistillerSettings,
} from "./settings";

/** The reading-view elements whose text is a selection's block. */
const READING_BLOCKS = "p, li, td, th, blockquote, h1, h2, h3, h4, h5, h6";

export default class KgdistillerPlugin extends Plugin {
  settings: KgdistillerSettings = { ...DEFAULT_SETTINGS };
  hiddenKnowledgeStatus: HiddenKnowledgeStatus = { state: "disabled" };
  private hiddenKnowledgeIndexer?: HiddenKnowledgeIndexer;
  private settingsTab?: KgdistillerSettingTab;
  private lastHiddenKnowledgeEnabled?: boolean;
  private hiddenKnowledgeRequest = 0;
  private unloaded = false;
  /** Frontmatter of every file under .knowledge/entries and .knowledge/drafts, by vault path. */
  private records = new Map<string, Readonly<Record<string, unknown>>>();
  private modelResolved = false;
  /** Metadata events arrive per file; re-render open views once a burst settles. */
  private readonly scheduleRefresh = debounce(() => this.refreshGraphViews(), 250, true);
  /** Lookups query every registered base through one resident `kgd mcp`. */
  readonly kgd = new KgdServer(() => ({ command: this.settings.kgdCommand, home: this.settings.knowledgeHome }));
  /** The agent's model calls go through one resident `omp auth-gateway stdio`. */
  readonly gateway = new ModelGateway(() => ({ command: this.settings.ompCommand, model: this.settings.agentModel }));
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
  /** The agent's web searches go through OMP's own search chain, with the agent's model. */
  webSearch(query: string): Promise<WebSource[]> {
    return webSearch(this.settings.ompCommand.trim(), this.settings.agentModel.trim(), query);
  }
  /** Source lookups name the plugin to OpenAlex and Wikipedia, as Wikimedia asks. */
  readonly getJson: GetJson = async (url) =>
    (await requestUrl({ url, headers: { "User-Agent": USER_AGENT, "Api-User-Agent": USER_AGENT } })).json as unknown;
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
    this.registerView(
      VIEW_TYPE_KGDISTILLER_GRAPH,
      (leaf) => new KgdistillerGraphView(leaf, this),
    );
    this.addRibbonIcon(KGDISTILLER_ICON, "Open kgdistiller Graph", () => {
      void this.activateGraphView();
    });
    this.addCommand({
      id: "open-typed-graph",
      name: "Open typed graph",
      callback: () => void this.activateGraphView(),
    });
    this.addCommand({
      id: "reload-typed-graph",
      name: "Reload typed graph",
      callback: () => {
        this.rebuildModel();
        this.refreshGraphViews();
      },
    });
    this.addCommand({
      id: "rescan-hidden-knowledge-folder",
      name: "Rescan hidden knowledge folder",
      callback: () => void this.rescanHiddenKnowledge(),
    });
    this.registerView(
      VIEW_TYPE_KGDISTILLER_LOOKUP,
      (leaf) => new KgdistillerLookupView(leaf, this),
    );
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

    this.rebuildModel();
    const cache = this.app.metadataCache;
    // `resolved` fires after the initial indexing and again after later batches;
    // the full build happens once, and later changes arrive per file.
    this.registerEvent(cache.on("resolved", () => {
      if (this.modelResolved) return;
      this.modelResolved = true;
      this.rebuildModel();
      this.refreshGraphViews();
    }));
    this.registerEvent(cache.on("changed", (file, _data, metadata) => {
      if (!isRecordPath(file.path)) return;
      this.records.set(file.path, metadata.frontmatter ?? {});
      this.scheduleRefresh();
    }));
    this.registerEvent(cache.on("deleted", (file) => {
      if (this.records.delete(file.path)) this.scheduleRefresh();
    }));
    this.registerEvent(this.app.vault.on("rename", (file, oldPath) => this.renameRecord(file, oldPath)));
  }

  /** The live records the graph model is built from. */
  knowledgeRecords(): FrontmatterRecord[] {
    return [...this.records].map(([path, frontmatter]) => ({ path, frontmatter }));
  }

  /** Read every record and draft from the metadata cache. */
  rebuildModel(): void {
    this.records.clear();
    for (const file of this.app.vault.getMarkdownFiles()) {
      if (!isRecordPath(file.path)) continue;
      this.records.set(file.path, this.app.metadataCache.getFileCache(file)?.frontmatter ?? {});
    }
  }

  private renameRecord(file: TAbstractFile, oldPath: string): void {
    const removed = this.records.delete(oldPath);
    const added = file instanceof TFile && isRecordPath(file.path);
    if (added) this.records.set(file.path, this.app.metadataCache.getFileCache(file)?.frontmatter ?? {});
    if (removed || added) this.scheduleRefresh();
  }

  onunload(): void {
    this.unloaded = true;
    this.scheduleRefresh.cancel();
    this.hold.reset();
    this.kgd.stop();
    this.gateway.stop();
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
    if (!this.gateway.configured() || !this.kgd.configured()) {
      new Notice("Set the kgd and omp executables in the kgdistiller settings to capture knowledge.");
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
    this.gateway.stop();
  }

  async activateGraphView(): Promise<void> {
    let leaf = this.app.workspace.getLeavesOfType(VIEW_TYPE_KGDISTILLER_GRAPH)[0];
    if (!leaf) {
      leaf = this.app.workspace.getRightLeaf(false) ?? undefined;
      if (!leaf) {
        new Notice("Could not create a workspace leaf for the kgdistiller graph.");
        return;
      }
      await leaf.setViewState({ type: VIEW_TYPE_KGDISTILLER_GRAPH, active: true });
    }
    await this.app.workspace.revealLeaf(leaf);
  }

  refreshGraphViews(): void {
    for (const leaf of this.app.workspace.getLeavesOfType(VIEW_TYPE_KGDISTILLER_GRAPH)) {
      if (leaf.view instanceof KgdistillerGraphView) leaf.view.refresh();
    }
  }

  applyGraphSettings(): void {
    for (const leaf of this.app.workspace.getLeavesOfType(VIEW_TYPE_KGDISTILLER_GRAPH)) {
      if (leaf.view instanceof KgdistillerGraphView) leaf.view.applySettings();
    }
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
