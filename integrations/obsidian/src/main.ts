import { Notice, Plugin, TAbstractFile, normalizePath } from "obsidian";

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

  async onload(): Promise<void> {
    await this.loadPluginSettings();
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
    this.addSettingTab(new KgdistillerSettingTab(this.app, this));

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
    this.app.workspace.detachLeavesOfType(VIEW_TYPE_KGDISTILLER_GRAPH);
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
  }

  private async loadPluginSettings(): Promise<void> {
    const stored = (await this.loadData()) as Partial<KgdistillerSettings> | null;
    this.settings = { ...DEFAULT_SETTINGS, ...(stored ?? {}) };
  }
}
