import { App, Plugin, PluginSettingTab, Setting } from "obsidian";

import { KNOWLEDGE_DIRECTORY } from "./records";
import type { HiddenKnowledgeStatus } from "./hidden-knowledge";

export interface KgdistillerSettings {
  showDrafts: boolean;
  hiddenKnowledgeEnabled: boolean;
  /** Absolute path of the kgd executable that lookups run; empty turns lookups off. */
  kgdCommand: string;
  /** KGDISTILLER_HOME for lookups; empty keeps kgd's default home. */
  knowledgeHome: string;
  /** Absolute path of the omp executable whose model gateway the agent uses; empty turns the agent off. */
  ompCommand: string;
  /** The model the agent asks: one OMP knows, or an OMP role such as `@smol`. */
  agentModel: string;
}

export const DEFAULT_SETTINGS: KgdistillerSettings = {
  showDrafts: true,
  hiddenKnowledgeEnabled: false,
  kgdCommand: "",
  knowledgeHome: "",
  ompCommand: "",
  agentModel: "@smol",
};

/**
 * Overlay stored plugin data on the defaults, keeping only values whose type
 * matches the default. Unknown keys are ignored; wrongly typed known keys are
 * returned so the caller can report them.
 */
export function mergeStoredSettings(stored: unknown): { settings: KgdistillerSettings; rejected: string[] } {
  const settings: KgdistillerSettings = { ...DEFAULT_SETTINGS };
  const rejected: string[] = [];
  if (stored === null || stored === undefined) return { settings, rejected };
  if (typeof stored !== "object" || Array.isArray(stored)) return { settings, rejected: ["(all)"] };
  const record = stored as Record<string, unknown>;
  const target = settings as unknown as Record<string, unknown>;
  for (const [key, fallback] of Object.entries(DEFAULT_SETTINGS)) {
    if (!(key in record)) continue;
    if (typeof record[key] === typeof fallback) target[key] = record[key];
    else rejected.push(key);
  }
  return { settings, rejected };
}

export interface SettingsHost {
  settings: KgdistillerSettings;
  hiddenKnowledgeStatus: HiddenKnowledgeStatus;
  savePluginSettings(): Promise<void>;
  applyGraphSettings(): void;
  rescanHiddenKnowledge(): Promise<void>;
  /** Called after the kgd executable or the knowledge home changed. */
  kgdSettingsChanged(): void;
  /** Called after the omp executable or the agent's model changed. */
  agentSettingsChanged(): void;
}

export class KgdistillerSettingTab extends PluginSettingTab {
  private hiddenStatusSetting?: Setting;
  constructor(app: App, private readonly host: SettingsHost & Plugin) {
    super(app, host);
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();
    new Setting(containerEl).setName("Graph view").setHeading();
    new Setting(containerEl)
      .setName("Show drafts")
      .setDesc(`Draw proposed records from ${KNOWLEDGE_DIRECTORY}/drafts with a dashed outline and a draft badge. The view's toolbar can change this for the open view.`)
      .addToggle((toggle) =>
        toggle.setValue(this.host.settings.showDrafts).onChange(async (value) => {
          this.host.settings.showDrafts = value;
          await this.host.savePluginSettings();
          this.host.applyGraphSettings();
        }),
      );

    new Setting(containerEl).setName("Hidden knowledge folder").setHeading();
    new Setting(containerEl)
      .setName("Index hidden knowledge folder")
      .setDesc(`Include the vault-root ${KNOWLEDGE_DIRECTORY} folder in native editing, links, backlinks, search and graph indexing. \`kgd obsidian install\` turns this on. Uses internal Obsidian APIs; incompatible versions are reported below.`)
      .addToggle((toggle) =>
        toggle
          .setValue(this.host.settings.hiddenKnowledgeEnabled)
          .onChange(async (value) => {
            this.host.settings.hiddenKnowledgeEnabled = value;
            await this.host.savePluginSettings();
            this.refreshHiddenKnowledgeStatus();
          }),
      );

    this.hiddenStatusSetting = new Setting(containerEl)
      .setName("Indexing status")
      .addButton((button) =>
        button
          .setButtonText("Rescan")
          .onClick(async () => {
            await this.host.rescanHiddenKnowledge();
            this.refreshHiddenKnowledgeStatus();
          }),
      );
    this.refreshHiddenKnowledgeStatus();

    new Setting(containerEl).setName("Lookup").setHeading();
    new Setting(containerEl)
      .setName("kgd executable")
      .setDesc("Absolute path of the kgd command, such as the output of `command -v kgd` in a terminal. Selecting text and holding the command key, or Look up selection, then queries every registered base through `kgd mcp`. Leave empty to turn lookups off.")
      .addText((text) =>
        text
          .setPlaceholder("/path/to/kgd")
          .setValue(this.host.settings.kgdCommand)
          .onChange(async (value) => {
            this.host.settings.kgdCommand = value.trim();
            await this.host.savePluginSettings();
            this.host.kgdSettingsChanged();
          }),
      );
    new Setting(containerEl)
      .setName("Knowledge home")
      .setDesc("The KGDISTILLER_HOME that lookups use. Leave empty for kgd's default home, ~/.knowledge.")
      .addText((text) =>
        text
          .setPlaceholder("~/.knowledge")
          .setValue(this.host.settings.knowledgeHome)
          .onChange(async (value) => {
            this.host.settings.knowledgeHome = value.trim();
            await this.host.savePluginSettings();
            this.host.kgdSettingsChanged();
          }),
      );

    new Setting(containerEl).setName("Agent").setHeading();
    new Setting(containerEl)
      .setName("omp executable")
      .setDesc("Absolute path of the omp command (oh-my-pi), such as the output of `command -v omp`. The agent judges each lookup and captures selections through `omp auth-gateway stdio`, which uses the models and credentials configured in OMP; this plugin stores no API key. Leave empty to turn the agent off.")
      .addText((text) =>
        text
          .setPlaceholder("/path/to/omp")
          .setValue(this.host.settings.ompCommand)
          .onChange(async (value) => {
            this.host.settings.ompCommand = value.trim();
            await this.host.savePluginSettings();
            this.host.agentSettingsChanged();
          }),
      );
    new Setting(containerEl)
      .setName("Model")
      .setDesc("A model OMP knows, such as deepseek/deepseek-v4-flash, or an OMP role such as @smol. A small, fast model such as DeepSeek V4 Flash keeps a judgment near 1.5 s and a capture near 6 s.")
      .addText((text) =>
        text
          .setPlaceholder(DEFAULT_SETTINGS.agentModel)
          .setValue(this.host.settings.agentModel)
          .onChange(async (value) => {
            this.host.settings.agentModel = value.trim() || DEFAULT_SETTINGS.agentModel;
            await this.host.savePluginSettings();
            this.host.agentSettingsChanged();
          }),
      );
  }

  refreshHiddenKnowledgeStatus(): void {
    const status = this.host.hiddenKnowledgeStatus;
    const messages: Record<HiddenKnowledgeStatus["state"], string> = {
      disabled: "Disabled. Hidden knowledge files are not indexed by this plugin.",
      enabled: `Indexed: ${KNOWLEDGE_DIRECTORY}.`,
      missing: `Folder not found: ${KNOWLEDGE_DIRECTORY}. Create it, then rescan.`,
      unsupported: "Hidden folder indexing is unavailable in this environment.",
      invalid: "The hidden knowledge folder must be a real folder, not a symbolic link.",
      error: "Hidden folder indexing failed.",
      disposed: "Hidden folder indexing has stopped.",
    };
    this.hiddenStatusSetting?.setDesc(status.message || messages[status.state]);
  }
}
