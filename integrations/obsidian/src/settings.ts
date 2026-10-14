import { App, Plugin, PluginSettingTab, SecretComponent, Setting } from "obsidian";

import { KNOWLEDGE_DIRECTORY } from "./records";
import type { HiddenKnowledgeStatus } from "./hidden-knowledge";

export interface KgdistillerSettings {
  showDrafts: boolean;
  hiddenKnowledgeEnabled: boolean;
  /** Absolute path of the kgd executable that lookups run; empty turns lookups off. */
  kgdCommand: string;
  /** KGDISTILLER_HOME for lookups; empty keeps kgd's default home. */
  knowledgeHome: string;
  /** Full Chat Completions URL; empty turns the agent off. */
  modelEndpoint: string;
  agentModel: string;
  modelProtocol: "chat-completions" | "deepseek";
  /** SecretStorage identifiers, never API key values. */
  modelKey: string;
  /** Empty searches the web at Exa's free rate limits. */
  exaKey: string;
}

export const DEFAULT_SETTINGS: KgdistillerSettings = {
  showDrafts: true,
  hiddenKnowledgeEnabled: false,
  kgdCommand: "",
  knowledgeHome: "",
  modelEndpoint: "",
  agentModel: "",
  modelProtocol: "chat-completions",
  modelKey: "",
  exaKey: "",
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
  if (!["chat-completions", "deepseek"].includes(settings.modelProtocol)) {
    settings.modelProtocol = DEFAULT_SETTINGS.modelProtocol;
    rejected.push("modelProtocol");
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
  /** Called after model or search API settings change. */
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
    this.apiText("Model API endpoint", "Full Chat Completions URL. Leave empty to turn the agent off. Requests go directly to this service.",
      "modelEndpoint", "https://api.example.com/v1/chat/completions");
    this.apiText("Model", "The model ID your API accepts; it must support function tools.", "agentModel", "model-id");
    new Setting(containerEl)
      .setName("Model API format")
      .setDesc("Choose DeepSeek for its thinking controls. Other Chat Completions services must support reasoning_effort (none and low).")
      .addDropdown((dropdown) => dropdown
        .addOption("chat-completions", "Chat Completions")
        .addOption("deepseek", "DeepSeek")
        .setValue(this.host.settings.modelProtocol)
        .onChange(async (value) => {
          if (value !== "chat-completions" && value !== "deepseek") return;
          this.host.settings.modelProtocol = value;
          await this.host.savePluginSettings();
          this.host.agentSettingsChanged();
        }));
    this.apiSecret("Model API key", "Choose or add a secret in Obsidian. Leave empty only for a local API that needs no authentication.", "modelKey");
    new Setting(containerEl).setName("Web search").setHeading();
    this.apiSecret("Exa API key", "Optional. Web search asks Exa's hosted MCP server (mcp.exa.ai) directly and works without a key at Exa's free rate limits; choose or add an Exa key in Obsidian SecretStorage to use your Exa plan's limits.", "exaKey");
  }

  private apiText(name: string, description: string, key: "modelEndpoint" | "agentModel", placeholder: string): void {
    new Setting(this.containerEl).setName(name).setDesc(description).addText((text) => text
      .setPlaceholder(placeholder).setValue(this.host.settings[key]).onChange(async (value) => {
        this.host.settings[key] = value.trim();
        await this.host.savePluginSettings();
        this.host.agentSettingsChanged();
      }));
  }

  private apiSecret(name: string, description: string, key: "modelKey" | "exaKey"): void {
    const setting = new Setting(this.containerEl).setName(name).setDesc(description);
    new SecretComponent(this.app, setting.controlEl).setValue(this.host.settings[key]).onChange(async (id) => {
      this.host.settings[key] = id;
      await this.host.savePluginSettings();
      this.host.agentSettingsChanged();
    });
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
