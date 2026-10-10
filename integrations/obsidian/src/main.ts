import { Notice, Plugin, TFile, debounce, type TAbstractFile } from "obsidian";

import type { FrontmatterRecord } from "./graph-model";

import { HiddenKnowledgeIndexer, type HiddenKnowledgeStatus } from "./hidden-knowledge";
import { isRecordPath } from "./records";

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
    this.hiddenKnowledgeIndexer?.dispose();
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
