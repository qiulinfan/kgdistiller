import { Notice, Platform, Plugin, TAbstractFile, normalizePath } from "obsidian";

import { HiddenKnowledgeIndexer, type HiddenKnowledgeStatus } from "./hidden-knowledge";

import {
  KGDISTILLER_ICON,
  KgdistillerGraphView,
  VIEW_TYPE_KGDISTILLER_GRAPH,
} from "./view";
import {
  DEFAULT_SETTINGS,
  KgdistillerSettingTab,
  type KgdistillerSettings,
} from "./settings";

export default class KgdistillerPlugin extends Plugin {
  settings: KgdistillerSettings = { ...DEFAULT_SETTINGS };
  hiddenKnowledgeStatus: HiddenKnowledgeStatus = {
    state: "disabled",
    root: DEFAULT_SETTINGS.hiddenKnowledgeFolder,
  };
  private hiddenKnowledgeIndexer?: HiddenKnowledgeIndexer;
  private settingsTab?: KgdistillerSettingTab;
  private lastHiddenKnowledgeConfiguration?: string;
  private hiddenKnowledgeRequest = 0;
  private unloaded = false;

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
      callback: () => void this.refreshGraphViews(),
    });
    this.addCommand({
      id: "rescan-hidden-knowledge-folder",
      name: "Rescan hidden knowledge folder",
      callback: () => void this.rescanHiddenKnowledge(),
    });
    this.settingsTab = new KgdistillerSettingTab(this.app, this);
    this.addSettingTab(this.settingsTab);

    const refreshIfGraph = (file: TAbstractFile, oldPath?: string): void => {
      const expected = normalizePath(this.settings.graphPath);
      if (file.path === expected || oldPath === expected) void this.refreshGraphViews();
    };
    this.registerEvent(this.app.vault.on("create", (file) => refreshIfGraph(file)));
    this.registerEvent(this.app.vault.on("modify", (file) => refreshIfGraph(file)));
    this.registerEvent(this.app.vault.on("delete", (file) => refreshIfGraph(file)));
    this.registerEvent(
      this.app.vault.on("rename", (file, oldPath) => refreshIfGraph(file, oldPath)),
    );
  }

  onunload(): void {
    this.unloaded = true;
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

  async refreshGraphViews(): Promise<void> {
    await Promise.all(
      this.app.workspace
        .getLeavesOfType(VIEW_TYPE_KGDISTILLER_GRAPH)
        .map(async (leaf) => {
          if (leaf.view instanceof KgdistillerGraphView) await leaf.view.refresh();
        }),
    );
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
    const root = this.settings.hiddenKnowledgeFolder;
    const configuration = JSON.stringify([enabled, root]);
    const changed = configuration !== this.lastHiddenKnowledgeConfiguration;
    if (!changed && !rescan) return;
    this.lastHiddenKnowledgeConfiguration = configuration;
    const request = ++this.hiddenKnowledgeRequest;
    const updateStatus = (status: HiddenKnowledgeStatus): void => {
      if (request === this.hiddenKnowledgeRequest) this.setHiddenKnowledgeStatus(status);
    };

    try {
      if (Platform.isMobile) {
        await indexer.configure(false, root);
        updateStatus({
          state: "unsupported", root,
          message: "Hidden folder indexing is available on desktop only. The graph view remains available.",
        });
      } else if (enabled && this.hasExternalHiddenFolderIndexer()) {
        await indexer.configure(false, root);
        updateStatus({
          state: "unsupported", root,
          message: "Hidden Folders Access is enabled. Disable it, then rescan here to avoid running two hidden-folder indexers.",
        });
      } else {
        const wasEnabled = this.hiddenKnowledgeStatus.state === "enabled";
        const status = !changed && rescan && enabled && wasEnabled
          ? await indexer.rescan()
          : await indexer.configure(enabled, root);
        updateStatus(status);
      }
    } catch (error) {
      updateStatus({
        state: "error", root,
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
    const stored = (await this.loadData()) as Partial<KgdistillerSettings> | null;
    this.settings = { ...DEFAULT_SETTINGS, ...(stored ?? {}) };
  }
}
