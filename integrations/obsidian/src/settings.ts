import { App, Modal, Plugin, PluginSettingTab, SecretComponent, Setting, type TextComponent } from "obsidian";

import { kindOf, newProviderName, PROVIDER_KINDS, storedProviders, type Detected, type Provider } from "./providers";
import { KNOWLEDGE_DIRECTORY } from "./records";
import type { HiddenKnowledgeStatus } from "./hidden-knowledge";

export interface KgdistillerSettings {
  hiddenKnowledgeEnabled: boolean;
  /** Absolute path of the kgd executable that lookups run; empty turns lookups off. */
  kgdCommand: string;
  /** KGDISTILLER_HOME for lookups; empty keeps kgd's default home. */
  knowledgeHome: string;
  /** The Chat Completions services the agent may call. */
  providers: Provider[];
  /** The provider and model the agent uses, chosen in the sidebar; empty turns the agent off. */
  provider: string;
  model: string;
  /** A SecretStorage identifier, never a key; empty searches the web at Exa's free rate limits. */
  exaKey: string;
}

export const DEFAULT_SETTINGS: Readonly<KgdistillerSettings> = {
  hiddenKnowledgeEnabled: false,
  kgdCommand: "",
  knowledgeHome: "",
  providers: [],
  provider: "",
  model: "",
  exaKey: "",
};

const NOT_LISTED = "Not listed yet; press Detect.";
const NAME_HINT = "Shown in the sidebar's model list; no two providers share a name.";

/**
 * Overlay stored plugin data on the defaults, keeping only values whose type
 * matches the default and the providers that are complete. Unknown keys are
 * ignored; wrongly typed known keys, and a provider list with anything else
 * in it, are returned so the caller can report them.
 */
export function mergeStoredSettings(stored: unknown): { settings: KgdistillerSettings; rejected: string[] } {
  const settings: KgdistillerSettings = { ...DEFAULT_SETTINGS, providers: [] };
  const rejected: string[] = [];
  if (stored === null || stored === undefined) return { settings, rejected };
  if (typeof stored !== "object" || Array.isArray(stored)) return { settings, rejected: ["(all)"] };
  const record = stored as Record<string, unknown>;
  const target = settings as unknown as Record<string, unknown>;
  for (const [key, fallback] of Object.entries(DEFAULT_SETTINGS)) {
    if (key === "providers" || !(key in record)) continue;
    if (typeof record[key] === typeof fallback) target[key] = record[key];
    else rejected.push(key);
  }
  const { providers, dropped } = storedProviders(record.providers);
  settings.providers = providers;
  if (dropped) rejected.push("providers");
  return { settings, rejected };
}

export interface SettingsHost {
  settings: KgdistillerSettings;
  hiddenKnowledgeStatus: HiddenKnowledgeStatus;
  savePluginSettings(): Promise<void>;
  rescanHiddenKnowledge(): Promise<void>;
  /** Called after the kgd executable or the knowledge home changed. */
  kgdSettingsChanged(): void;
  /** Called after model or search API settings change. */
  agentSettingsChanged(): void;
  /** Each provider's detected models, detecting those not yet known. */
  modelCatalog(): Promise<ReadonlyMap<string, Detected>>;
  /** Forget a provider's detected models after its settings change. */
  forgetModels(name: string): void;
  /** Send the provider one short request of the kind the agent sends, and say how it went. */
  testProvider(provider: Provider): Promise<string>;
}

/** Asks for a new provider's service and name, and adds it once both are set. */
class AddProviderModal extends Modal {
  constructor(app: App, private readonly providers: readonly Provider[], private readonly add: (provider: Provider) => Promise<void>) {
    super(app);
  }

  onOpen(): void {
    this.setTitle("Add a model provider");
    let kind = PROVIDER_KINDS[0]!;
    let name = newProviderName(this.providers, kind.id);
    // Until the owner types a name, the suggested one follows the service.
    let typed = false;
    let nameText: TextComponent | undefined;
    new Setting(this.contentEl).setName("Service").addDropdown((dropdown) => {
      for (const option of PROVIDER_KINDS) dropdown.addOption(option.id, option.label);
      dropdown.setValue(kind.id).onChange((id) => {
        kind = PROVIDER_KINDS.find((option) => option.id === id) ?? kind;
        if (typed) return;
        name = newProviderName(this.providers, kind.id);
        nameText?.setValue(name);
      });
    });
    const named = new Setting(this.contentEl).setName("Name").setDesc(NAME_HINT).addText((text) => {
      nameText = text;
      text.setValue(name).onChange((value) => {
        name = value.trim();
        typed = true;
      });
    });
    new Setting(this.contentEl)
      .addButton((button) => button.setButtonText("Add").setCta().onClick(async () => {
        if (!name || this.providers.some((provider) => provider.name === name)) {
          named.setDesc("Give the provider a name that no other provider has.");
          return;
        }
        this.close();
        await this.add({ name, kind: kind.id, baseUrl: "", format: kind.format, apiKey: "" });
      }))
      .addButton((button) => button.setButtonText("Cancel").onClick(() => this.close()));
  }

  onClose(): void {
    this.contentEl.empty();
  }
}

export class KgdistillerSettingTab extends PluginSettingTab {
  private hiddenStatusSetting?: Setting;
  /** The position of the provider whose settings are shown. */
  private editing = 0;
  constructor(app: App, private readonly host: SettingsHost & Plugin) {
    super(app, host);
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();
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

    new Setting(containerEl).setName("Model providers").setHeading()
      .setDesc("The services the agent's models come from. The agent judges lookups, captures selections, finds sources and answers searches "
        + "with the model chosen at the top of the kgdistiller sidebar, which lists every provider's models. Pick a provider here to change it.");
    const { providers } = this.host.settings;
    this.editing = Math.min(this.editing, Math.max(providers.length - 1, 0));
    const current = providers[this.editing];
    const picker = new Setting(containerEl).setName("Provider")
      .setDesc(current ? `${providers.length} provider${providers.length === 1 ? "" : "s"}.` : "No provider yet: add one, such as DeepSeek or OpenAI.");
    if (current) {
      picker.addDropdown((dropdown) => {
        providers.forEach((provider, index) => dropdown.addOption(String(index), `${provider.name} · ${kindOf(provider).label}`));
        dropdown.setValue(String(this.editing)).onChange((value) => {
          this.editing = Number(value);
          this.display();
        });
      });
    }
    picker.addButton((button) => button.setButtonText("Add").onClick(() => {
      new AddProviderModal(this.app, providers, async (provider) => {
        providers.push(provider);
        this.editing = providers.length - 1;
        await this.host.savePluginSettings();
        this.display();
      }).open();
    }));
    if (current) {
      picker.addButton((button) => button.setButtonText("Remove").setWarning().onClick(async () => {
        const { settings } = this.host;
        settings.providers.splice(this.editing, 1);
        this.host.forgetModels(current.name);
        if (settings.provider === current.name) {
          settings.provider = "";
          settings.model = "";
        }
        await this.host.savePluginSettings();
        this.host.agentSettingsChanged();
        this.display();
      }));
      void this.showModels(current, this.providerSettings(current));
    }

    new Setting(containerEl).setName("Web search").setHeading();
    const exa = new Setting(containerEl).setName("Exa API key")
      .setDesc("Optional. Web search asks Exa's hosted MCP server (mcp.exa.ai) directly and works without a key at Exa's free rate limits; choose or add an Exa key in Obsidian SecretStorage to use your Exa plan's limits.");
    new SecretComponent(this.app, exa.controlEl).setValue(this.host.settings.exaKey).onChange(async (id) => {
      this.host.settings.exaKey = id;
      await this.host.savePluginSettings();
      this.host.agentSettingsChanged();
    });
  }

  /** The settings of the provider being changed; returns the row that shows its models. */
  private providerSettings(provider: Provider): Setting {
    const { containerEl } = this;
    const kind = kindOf(provider);
    const changed = async (forget: boolean): Promise<void> => {
      if (forget) {
        this.host.forgetModels(provider.name);
        models.setDesc(NOT_LISTED);
      }
      await this.host.savePluginSettings();
      this.host.agentSettingsChanged();
    };
    new Setting(containerEl)
      .setName("Service")
      .setDesc(kind.ownUrl ? (kind.baseUrl ? `Runs on this machine, by default at ${kind.baseUrl}.` : "Any service with OpenAI's Chat Completions and models endpoints.")
        : `${kind.baseUrl}${kind.format === "deepseek" ? ", in DeepSeek's format" : ""}.`)
      .addDropdown((dropdown) => {
        for (const option of PROVIDER_KINDS) dropdown.addOption(option.id, option.label);
        dropdown.setValue(provider.kind).onChange(async (id) => {
          const next = PROVIDER_KINDS.find((option) => option.id === id);
          if (!next) return;
          this.host.forgetModels(provider.name);
          provider.kind = next.id;
          provider.baseUrl = "";
          provider.format = next.format;
          await this.host.savePluginSettings();
          this.host.agentSettingsChanged();
          this.display();
        });
      });
    const name = new Setting(containerEl).setName("Name").setDesc(NAME_HINT);
    name.addText((text) => text.setValue(provider.name).onChange(async (value) => {
      const next = value.trim();
      if (!next || this.host.settings.providers.some((other) => other !== provider && other.name === next)) {
        name.setDesc("Give this provider a name that no other provider has.");
        return;
      }
      name.setDesc(NAME_HINT);
      if (next === provider.name) return;
      this.host.forgetModels(provider.name);
      if (this.host.settings.provider === provider.name) this.host.settings.provider = next;
      provider.name = next;
      await changed(true);
    }));
    if (kind.ownUrl) {
      new Setting(containerEl)
        .setName("Base URL")
        .setDesc(kind.baseUrl ? `Leave empty for ${kind.baseUrl}.` : "The URL that /chat/completions and /models follow, such as https://api.example.com/v1. Requests go directly to this service.")
        .addText((text) => text.setPlaceholder(kind.baseUrl ?? "https://api.example.com/v1").setValue(provider.baseUrl).onChange(async (value) => {
          provider.baseUrl = value.trim();
          await changed(true);
        }));
    }
    if (kind.ownFormat) {
      new Setting(containerEl)
        .setName("API format")
        .setDesc("Choose DeepSeek for its thinking controls. Other services must support reasoning_effort (none and low).")
        .addDropdown((dropdown) => dropdown
          .addOption("chat-completions", "Chat Completions")
          .addOption("deepseek", "DeepSeek")
          .setValue(provider.format)
          .onChange(async (value) => {
            if (value !== "chat-completions" && value !== "deepseek") return;
            provider.format = value;
            await changed(false);
          }));
    }
    const key = new Setting(containerEl).setName("API key")
      .setDesc(kind.ownUrl && kind.baseUrl ? "A local server usually needs none; otherwise choose or add the secret holding its key."
        : "Choose a secret in Obsidian's SecretStorage, or add one holding the service's key; the plugin stores only the secret's name.");
    new SecretComponent(this.app, key.controlEl).setValue(provider.apiKey).onChange(async (id) => {
      provider.apiKey = id;
      await changed(true);
    });
    const models: Setting = new Setting(containerEl).setName("Models").setDesc(NOT_LISTED)
      .addButton((button) => button.setButtonText("Detect").onClick(async () => {
        this.host.forgetModels(provider.name);
        await this.showModels(provider, models);
      }));
    const connection = new Setting(containerEl).setName("Connection")
      .setDesc("Send one short request of the kind the agent sends, to the model in use or the first one listed.")
      .addButton((button) => button.setButtonText("Test").onClick(async () => {
        button.setDisabled(true);
        connection.setDesc("Testing…");
        connection.setDesc(await this.host.testProvider(provider));
        button.setDisabled(false);
        await this.showModels(provider, models);
      }));
    return models;
  }

  /** Detect the provider's models and show them, or why they could not be listed. */
  private async showModels(provider: Provider, row: Setting): Promise<void> {
    row.setDesc("Detecting…");
    const entry = (await this.host.modelCatalog()).get(provider.name);
    row.setDesc(!entry ? NOT_LISTED
      : "error" in entry ? `Could not list models: ${entry.error}`
      : entry.models.length > 0 ? entry.models.join(", ") : "The service lists no models.");
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
