import { App, Platform, Plugin, PluginSettingTab, Setting } from "obsidian";

import { KNOWLEDGE_DIRECTORY } from "./records";
import { DEFAULT_HIDDEN_KNOWLEDGE_EXCLUSIONS, type HiddenKnowledgeStatus } from "./hidden-knowledge";

export interface KgdistillerSettings {
  showDrafts: boolean;
  hiddenKnowledgeEnabled: boolean;
  hiddenKnowledgeExclusions: string[];
}

export const DEFAULT_SETTINGS: KgdistillerSettings = {
  showDrafts: true,
  hiddenKnowledgeEnabled: false,
  hiddenKnowledgeExclusions: [...DEFAULT_HIDDEN_KNOWLEDGE_EXCLUSIONS],
};

/**
 * Overlay stored plugin data on the defaults, keeping only values whose type
 * matches the default. Rejected keys are returned so the caller can report them.
 */
export function mergeStoredSettings(stored: unknown): { settings: KgdistillerSettings; rejected: string[] } {
  const settings: KgdistillerSettings = { ...DEFAULT_SETTINGS, hiddenKnowledgeExclusions: [...DEFAULT_SETTINGS.hiddenKnowledgeExclusions] };
  const rejected: string[] = [];
  if (stored === null || stored === undefined) return { settings, rejected };
  if (typeof stored !== "object" || Array.isArray(stored)) return { settings, rejected: ["(all)"] };
  const record = stored as Record<string, unknown>;
  const target = settings as unknown as Record<string, unknown>;
  for (const [key, fallback] of Object.entries(DEFAULT_SETTINGS)) {
    if (!(key in record)) continue;
    const value = record[key];
    const accepted = Array.isArray(fallback)
      ? Array.isArray(value) && value.every((entry) => typeof entry === "string")
      : typeof value === typeof fallback;
    if (accepted) target[key] = Array.isArray(value) ? [...value] : value;
    else rejected.push(key);
  }
  return { settings, rejected };
}

/**
 * Split a comma- or newline-separated list of folders. Leading and trailing
 * slashes are dropped so `archive/` and `/archive` mean `archive`; the indexer
 * validates what remains.
 */
export function parseExclusions(value: string): string[] {
  return value.split(/[,\n]/)
    .map((entry) => entry.trim().replace(/^\/+|\/+$/g, ""))
    .filter((entry) => entry !== "");
}

export interface SettingsHost {
  settings: KgdistillerSettings;
  hiddenKnowledgeStatus: HiddenKnowledgeStatus;
  savePluginSettings(): Promise<void>;
  applyGraphSettings(): void;
  rescanHiddenKnowledge(): Promise<void>;
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
      .setDesc(`Draw proposed records from ${KNOWLEDGE_DIRECTORY}/drafts with a dashed outline. The view's toolbar can change this for the open view.`)
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
      .setDesc(`On desktop, include the vault-root ${KNOWLEDGE_DIRECTORY} folder in native editing, links, backlinks, search and graph indexing. Uses internal Obsidian APIs; incompatible versions are reported below.`)
      .addToggle((toggle) =>
        toggle
          .setValue(this.host.settings.hiddenKnowledgeEnabled)
          .setDisabled(Platform.isMobile)
          .onChange(async (value) => {
            this.host.settings.hiddenKnowledgeEnabled = value;
            await this.host.savePluginSettings();
            this.refreshHiddenKnowledgeStatus();
          }),
      );

    let exclusions = this.host.settings.hiddenKnowledgeExclusions.join(", ");
    new Setting(containerEl)
      .setName("Excluded folders")
      .setDesc(`Folders under ${KNOWLEDGE_DIRECTORY} that stay out of native indexing. Separate entries with commas or new lines; leave empty to index everything.`)
      .addTextArea((text) =>
        text
          .setValue(exclusions)
          .setDisabled(Platform.isMobile)
          .onChange((value) => { exclusions = value; }),
      )
      .addButton((button) =>
        button
          .setButtonText("Apply")
          .setDisabled(Platform.isMobile)
          .onClick(async () => {
            this.host.settings.hiddenKnowledgeExclusions = parseExclusions(exclusions);
            await this.host.savePluginSettings();
            this.refreshHiddenKnowledgeStatus();
          }),
      );

    this.hiddenStatusSetting = new Setting(containerEl)
      .setName("Indexing status")
      .addButton((button) =>
        button
          .setButtonText("Rescan")
          .setDisabled(Platform.isMobile)
          .onClick(async () => {
            await this.host.rescanHiddenKnowledge();
            this.refreshHiddenKnowledgeStatus();
          }),
      );
    this.refreshHiddenKnowledgeStatus();
  }

  refreshHiddenKnowledgeStatus(): void {
    const status = this.host.hiddenKnowledgeStatus;
    const messages: Record<HiddenKnowledgeStatus["state"], string> = {
      disabled: "Disabled. Hidden knowledge files are not indexed by this plugin.",
      enabled: `Indexed: ${KNOWLEDGE_DIRECTORY}.`,
      missing: `Folder not found: ${KNOWLEDGE_DIRECTORY}. Create it, then rescan.`,
      unsupported: "Hidden folder indexing is unavailable in this environment.",
      invalid: "An excluded folder is invalid.",
      error: "Hidden folder indexing failed.",
      disposed: "Hidden folder indexing has stopped.",
    };
    this.hiddenStatusSetting?.setDesc(status.message || messages[status.state]);
  }
}
