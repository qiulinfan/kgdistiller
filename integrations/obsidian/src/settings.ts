import { App, Modal, Plugin, PluginSettingTab, SecretComponent, Setting, type ButtonComponent, type SettingDefinition, type SettingDefinitionItem, type TextComponent } from "obsidian";

import { isReasoningLevel, REASONING_LEVELS, type ReasoningChoice, type ReasoningLevel } from "./model";
import { kindOf, newProviderName, PROVIDER_KINDS, storedProviders, type Detected, type Provider } from "./providers";
import { KNOWLEDGE_DIRECTORY } from "./records";
import type { HiddenKnowledgeStatus } from "./hidden-knowledge";
import { embeddingSummary, type EmbeddingStatus } from "./indexing";

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
  /** The reasoning level chosen in the sidebar for every request; empty gives each job its own. */
  reasoning: ReasoningChoice;
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
  reasoning: "",
  exaKey: "",
};

const NOT_LISTED = "Not listed yet; press Detect.";
const LEVELS_HINT = "The reasoning levels the service documents as distinct, separated by commas, such as none, low, high. Empty, requests carry no level and the service decides.";
const NAME_HINT = "Shown in the sidebar's model list; no two providers share a name.";

/**
 * Overlay stored plugin data on the defaults, keeping only values whose type
 * matches the default, the providers that are complete and a known reasoning
 * level. Unknown keys are ignored; wrongly typed known keys, a provider list
 * with anything else in it and an unknown level are returned so the caller can
 * report them.
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
  if (settings.reasoning && !isReasoningLevel(settings.reasoning)) {
    settings.reasoning = "";
    rejected.push("reasoning");
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
  /** The home's embedding model and its vectors. */
  embeddingStatus(): Promise<EmbeddingStatus>;
  /** Make a model the home's embedding model, downloading it if needed, or turn the dense lane off with null. */
  setEmbedding(model: string | null): Promise<EmbeddingStatus>;
  /** Send a model of the provider one forced tool call at the lowest documented reasoning level, and say how it went. */
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
        await this.add({ name, kind: kind.id, baseUrl: "", format: kind.format, levels: [], apiKey: "" });
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

  /** The tab as Obsidian 1.13's settings definitions: each row draws itself, and `update()` redraws the tab. */
  getSettingDefinitions(): SettingDefinitionItem[] {
    return [
      { type: "group", heading: "Hidden knowledge folder", items: [
        {
          name: "Index hidden knowledge folder",
          desc: `Include the vault-root ${KNOWLEDGE_DIRECTORY} folder in native editing, links, backlinks, search and graph indexing. \`kgd obsidian install\` turns this on. Uses internal Obsidian APIs; incompatible versions are reported below.`,
          render: (setting) => {
            setting.addToggle((toggle) => toggle.setValue(this.host.settings.hiddenKnowledgeEnabled).onChange(async (value) => {
              this.host.settings.hiddenKnowledgeEnabled = value;
              await this.host.savePluginSettings();
              this.refreshHiddenKnowledgeStatus();
            }));
          },
        },
        {
          name: "Indexing status",
          render: (setting) => {
            this.hiddenStatusSetting = setting.addButton((button) => button.setButtonText("Rescan").onClick(async () => {
              await this.host.rescanHiddenKnowledge();
              this.refreshHiddenKnowledgeStatus();
            }));
            this.refreshHiddenKnowledgeStatus();
          },
        },
      ] },
      { type: "group", heading: "Lookup", items: [
        {
          name: "kgd executable",
          desc: "Absolute path of the kgd command, such as the output of `command -v kgd` in a terminal. Selecting text and holding the command key, or Look up selection, then queries every registered base through `kgd mcp`. Leave empty to turn lookups off.",
          render: (setting) => {
            setting.addText((text) => text.setPlaceholder("/path/to/kgd").setValue(this.host.settings.kgdCommand).onChange(async (value) => {
              this.host.settings.kgdCommand = value.trim();
              await this.host.savePluginSettings();
              this.host.kgdSettingsChanged();
            }));
          },
        },
        {
          name: "Knowledge home",
          desc: "The KGDISTILLER_HOME that lookups use. Leave empty for kgd's default home, ~/.knowledge.",
          render: (setting) => {
            setting.addText((text) => text.setPlaceholder("~/.knowledge").setValue(this.host.settings.knowledgeHome).onChange(async (value) => {
              this.host.settings.knowledgeHome = value.trim();
              await this.host.savePluginSettings();
              this.host.kgdSettingsChanged();
            }));
          },
        },
      ] },
      { type: "group", heading: "Embedding", items: this.embeddingSettings() },
      { type: "group", heading: "Model providers", items: this.providerList() },
      { type: "group", heading: "Web search", items: [
        {
          name: "Exa API key",
          desc: "Optional. Web search asks Exa's hosted MCP server (mcp.exa.ai) directly and works without a key at Exa's free rate limits; choose or add an Exa key in Obsidian SecretStorage to use your Exa plan's limits.",
          render: (setting) => {
            new SecretComponent(this.app, setting.controlEl).setValue(this.host.settings.exaKey).onChange(async (id) => {
              this.host.settings.exaKey = id ?? "";
              await this.host.savePluginSettings();
              this.host.agentSettingsChanged();
            });
          },
        },
      ] },
    ];
  }

  /** The home's embedding model, shown and changed through `kgd embedding`; it is not a vault setting. */
  private embeddingSettings(): SettingDefinition[] {
    let status: Setting | undefined;
    let model = "";
    let field: TextComponent | undefined;
    const buttons: ButtonComponent[] = [];
    const show = (described: EmbeddingStatus): void => {
      model = described.embedding ?? "";
      field?.setValue(model);
      status?.setDesc(embeddingSummary(described));
    };
    const apply = async (next: string | null): Promise<void> => {
      if (next === "") {
        status?.setDesc("Name a model, such as BAAI/bge-m3, or turn the dense lane off.");
        return;
      }
      for (const button of buttons) button.setDisabled(true);
      status?.setDesc(next === null ? "Turning the dense lane off…"
        : `Downloading and checking ${next}… A first download of BAAI/bge-m3 is about 2.3 GB.`);
      try {
        show(await this.host.setEmbedding(next));
      } catch (error) {
        status?.setDesc(error instanceof Error ? error.message : String(error));
      } finally {
        for (const button of buttons) button.setDisabled(false);
      }
    };
    return [
      {
        name: "Dense search model",
        desc: "The model behind the dense search lane. It belongs to the knowledge home, so every vault, terminal and agent "
          + "using that home shares it, and it runs on this computer with int8 weights in one shared process of about 1 GB: "
          + "record text never leaves the machine.",
      },
      { name: "Vectors", render: (setting) => { status = setting; } },
      {
        name: "Embedding model",
        desc: "A Hugging Face id of an XLM-RoBERTa sentence encoder that ships sentencepiece.bpe.model, such as BAAI/bge-m3. "
          + "Use downloads it into the Hugging Face cache and checks it; Re-embed in the sidebar then embeds every record with it.",
        render: (setting) => {
          setting
            .addText((text) => {
              field = text;
              text.setPlaceholder("BAAI/bge-m3").onChange((value) => { model = value.trim(); });
            })
            .addButton((button) => { buttons.push(button); button.setButtonText("Use").setCta().onClick(() => void apply(model)); })
            .addButton((button) => { buttons.push(button); button.setButtonText("Turn off").onClick(() => void apply(null)); });
          if (!this.host.settings.kgdCommand.trim()) {
            status?.setDesc("Set the kgd executable above to see and change the embedding model.");
            for (const button of buttons) button.setDisabled(true);
            return;
          }
          status?.setDesc("Reading the knowledge home…");
          this.host.embeddingStatus().then(show, (error: unknown) => status?.setDesc(error instanceof Error ? error.message : String(error)));
        },
      },
    ];
  }

  /** The provider picker and the settings of the provider being changed. */
  private providerList(): SettingDefinition[] {
    const { providers } = this.host.settings;
    this.editing = Math.min(this.editing, Math.max(providers.length - 1, 0));
    const current = providers[this.editing];
    const items: SettingDefinition[] = [
      {
        name: "Agent models",
        desc: "The services the agent's models come from. The agent judges lookups, captures selections, finds sources and answers searches "
          + "with the model chosen at the top of the kgdistiller sidebar, which lists every provider's models. Pick a provider here to change it.",
      },
      {
        name: "Provider",
        desc: current ? `${providers.length} provider${providers.length === 1 ? "" : "s"}.` : "No provider yet: add one, such as DeepSeek or OpenAI.",
        render: (picker) => {
          if (current) {
            picker.addDropdown((dropdown) => {
              providers.forEach((provider, index) => dropdown.addOption(String(index), `${provider.name} · ${kindOf(provider).label}`));
              dropdown.setValue(String(this.editing)).onChange((value) => {
                this.editing = Number(value);
                this.update();
              });
            });
          }
          picker.addButton((button) => button.setButtonText("Add").onClick(() => {
            new AddProviderModal(this.app, providers, async (provider) => {
              providers.push(provider);
              this.editing = providers.length - 1;
              await this.host.savePluginSettings();
              this.update();
            }).open();
          }));
          if (!current) return;
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
            this.update();
          }));
        },
      },
    ];
    return current ? [...items, ...this.providerSettings(current)] : items;
  }

  /** The settings of the provider being changed; its Models row lists the models it serves once drawn. */
  private providerSettings(provider: Provider): SettingDefinition[] {
    const kind = kindOf(provider);
    let models: Setting | undefined;
    const changed = async (forget: boolean): Promise<void> => {
      if (forget) {
        this.host.forgetModels(provider.name);
        models?.setDesc(NOT_LISTED);
      }
      await this.host.savePluginSettings();
      this.host.agentSettingsChanged();
    };
    const items: SettingDefinition[] = [
      {
        name: "Service",
        desc: kind.ownUrl ? (kind.baseUrl ? `Runs on this machine, by default at ${kind.baseUrl}.` : "Any service with OpenAI's Chat Completions and models endpoints.")
          : `${kind.baseUrl}${kind.format === "deepseek" ? ", with the thinking switch" : ""}.`,
        render: (setting) => {
          setting.addDropdown((dropdown) => {
            for (const option of PROVIDER_KINDS) dropdown.addOption(option.id, option.label);
            dropdown.setValue(provider.kind).onChange(async (id) => {
              const next = PROVIDER_KINDS.find((option) => option.id === id);
              if (!next) return;
              this.host.forgetModels(provider.name);
              provider.kind = next.id;
              provider.baseUrl = "";
              provider.format = next.format;
              provider.levels = [];
              await this.host.savePluginSettings();
              this.host.agentSettingsChanged();
              this.update();
            });
          });
        },
      },
      {
        name: "Name",
        desc: NAME_HINT,
        render: (name) => {
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
        },
      },
    ];
    if (kind.ownUrl) {
      items.push({
        name: "Base URL",
        desc: kind.baseUrl ? `Leave empty for ${kind.baseUrl}.` : "The URL that /chat/completions and /models follow, such as https://api.example.com/v1. Requests go directly to this service.",
        render: (setting) => {
          setting.addText((text) => text.setPlaceholder(kind.baseUrl ?? "https://api.example.com/v1").setValue(provider.baseUrl).onChange(async (value) => {
            provider.baseUrl = value.trim();
            await changed(true);
          }));
        },
      });
    }
    if (kind.ownFormat) {
      items.push({
        name: "API format",
        desc: "How a request names its reasoning. Chat Completions sends reasoning_effort alone; the thinking switch also turns thinking on or off, as DeepSeek and Zhipu do.",
        render: (setting) => {
          setting.addDropdown((dropdown) => dropdown
            .addOption("chat-completions", "Chat Completions")
            .addOption("deepseek", "Thinking switch (DeepSeek, Zhipu)")
            .setValue(provider.format)
            .onChange(async (value) => {
              if (value !== "chat-completions" && value !== "deepseek") return;
              provider.format = value;
              await changed(false);
            }));
        },
      });
    }
    if (kind.ownLevels) {
      items.push({
        name: "Reasoning levels",
        desc: LEVELS_HINT,
        render: (levels) => {
          levels.addText((text) => text.setPlaceholder("none, low, high").setValue(provider.levels.join(", ")).onChange(async (value) => {
            const names = value.split(/[\s,]+/).filter(Boolean);
            const unknown = names.filter((name) => !isReasoningLevel(name));
            if (unknown.length) {
              levels.setDesc(`Not a level: ${unknown.join(", ")}. The levels are ${REASONING_LEVELS.join(", ")}.`);
              return;
            }
            levels.setDesc(LEVELS_HINT);
            provider.levels = REASONING_LEVELS.filter((level): level is ReasoningLevel => names.includes(level));
            await changed(false);
          }));
        },
      });
    }
    items.push(
      {
        name: "API key",
        desc: kind.ownUrl && kind.baseUrl ? "A local server usually needs none; otherwise choose or add the secret holding its key."
          : "Choose a secret in Obsidian's SecretStorage, or add one holding the service's key; the plugin stores only the secret's name.",
        render: (setting) => {
          new SecretComponent(this.app, setting.controlEl).setValue(provider.apiKey).onChange(async (id) => {
            provider.apiKey = id ?? "";
            await changed(true);
          });
        },
      },
      {
        name: "Models",
        desc: NOT_LISTED,
        render: (setting) => {
          models = setting.addButton((button) => button.setButtonText("Detect").onClick(async () => {
            this.host.forgetModels(provider.name);
            await this.showModels(provider, setting);
          }));
          void this.showModels(provider, setting);
        },
      },
      {
        name: "Connection",
        desc: "Send one short request of the kind the agent sends, to the model in use or the first one listed.",
        render: (connection) => {
          connection.addButton((button) => button.setButtonText("Test").onClick(async () => {
            button.setDisabled(true);
            connection.setDesc("Testing…");
            connection.setDesc(await this.host.testProvider(provider));
            button.setDisabled(false);
            if (models) await this.showModels(provider, models);
          }));
        },
      },
    );
    return items;
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
