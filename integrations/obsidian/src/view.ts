import cytoscape, { type Core, type EventObject } from "cytoscape";
import {
  App,
  ItemView,
  Notice,
  normalizePath,
  setIcon,
  TFile,
  WorkspaceLeaf,
} from "obsidian";

import {
  graphElements,
  graphModel,
  kindOptions,
  openTarget,
  valueLabel,
  type FrontmatterRecord,
  type GraphElementData,
  type GraphFilters,
  type KnowledgeModel,
  type ResolvedValue,
} from "./graph-model";
import { DRAFTS_DIRECTORY, ENTRIES_DIRECTORY } from "./records";
import type { KgdistillerSettings } from "./settings";

export const VIEW_TYPE_KGDISTILLER_GRAPH = "kgdistiller-graph-view";
export const KGDISTILLER_ICON = "flask-conical";

export interface GraphViewHost {
  app: App;
  settings: KgdistillerSettings;
  /** The live records from the metadata cache. */
  knowledgeRecords(): FrontmatterRecord[];
}

export class KgdistillerGraphView extends ItemView {
  private model: KnowledgeModel | null = null;
  private cytoscape: Core | null = null;
  private resizeObserver: ResizeObserver | null = null;
  private toolbarEl: HTMLElement | null = null;
  private graphEl: HTMLElement | null = null;
  private detailEl: HTMLElement | null = null;
  private statusEl: HTMLElement | null = null;
  private filters: GraphFilters;

  constructor(leaf: WorkspaceLeaf, private readonly host: GraphViewHost) {
    super(leaf);
    this.filters = { kind: "", showDrafts: host.settings.showDrafts };
  }

  getViewType(): string {
    return VIEW_TYPE_KGDISTILLER_GRAPH;
  }

  getDisplayText(): string {
    return "kgdistiller Graph";
  }

  getIcon(): string {
    return KGDISTILLER_ICON;
  }

  async onOpen(): Promise<void> {
    this.contentEl.empty();
    this.contentEl.addClass("kgd-graph-view");
    this.toolbarEl = this.contentEl.createDiv({ cls: "kgd-toolbar" });
    const body = this.contentEl.createDiv({ cls: "kgd-body" });
    this.graphEl = body.createDiv({ cls: "kgd-canvas" });
    this.detailEl = body.createEl("aside", { cls: "kgd-details" });
    this.statusEl = this.contentEl.createDiv({ cls: "kgd-status" });
    this.showHelp();
    this.resizeObserver = new ResizeObserver(() => this.cytoscape?.resize());
    this.resizeObserver.observe(this.graphEl);
    this.refresh();
  }

  async onClose(): Promise<void> {
    this.resizeObserver?.disconnect();
    this.resizeObserver = null;
    this.cytoscape?.destroy();
    this.cytoscape = null;
  }

  /** Re-render from the plugin's current records; called whenever the model changes. */
  refresh(): void {
    if (!this.toolbarEl || !this.graphEl) return;
    this.model = graphModel(this.host.knowledgeRecords());
    if (this.filters.kind && !kindOptions(this.model, this.filters.showDrafts).includes(this.filters.kind)) {
      this.filters.kind = "";
    }
    this.renderToolbar();
    this.renderGraph();
    const records = [...this.model.records.values()];
    const drafts = records.filter((record) => record.draft).length;
    const relations = records.filter((record) => !record.draft && record.recordClass === "relation").length;
    this.setStatus(`${records.length - drafts - relations} nodes · ${relations} relations · ${drafts} drafts`);
  }

  /** Apply the stored drafts setting, then re-render. */
  applySettings(): void {
    this.filters.showDrafts = this.host.settings.showDrafts;
    this.refresh();
  }

  private renderToolbar(): void {
    if (!this.toolbarEl || !this.model) return;
    this.toolbarEl.empty();
    const wrapper = this.toolbarEl.createEl("label", { cls: "kgd-control" });
    wrapper.createSpan({ text: "Kind" });
    const select = wrapper.createEl("select", { attr: { "aria-label": "Kind" } });
    select.createEl("option", { text: "All kinds", value: "" });
    for (const kind of kindOptions(this.model, this.filters.showDrafts)) {
      select.createEl("option", { text: kind, value: kind });
    }
    select.value = this.filters.kind;
    select.addEventListener("change", () => {
      this.filters.kind = select.value;
      this.renderGraph();
    });

    const toggle = this.toolbarEl.createEl("label", { cls: "kgd-toggle" });
    const input = toggle.createEl("input", { type: "checkbox" });
    input.checked = this.filters.showDrafts;
    toggle.createSpan({ text: "Show drafts" });
    input.addEventListener("change", () => {
      this.filters.showDrafts = input.checked;
      this.refresh();
    });

    const fitButton = this.toolbarEl.createEl("button", {
      cls: "clickable-icon kgd-icon-button",
      attr: { "aria-label": "Fit graph" },
    });
    setIcon(fitButton, "scan");
    fitButton.addEventListener("click", () => this.cytoscape?.fit(undefined, 36));
  }

  private renderGraph(): void {
    if (!this.graphEl || !this.model) return;
    this.cytoscape?.destroy();
    this.cytoscape = null;
    this.graphEl.empty();
    if (this.model.records.size === 0) {
      this.graphEl.createDiv({
        cls: "kgd-empty-state",
        text: `No records in ${ENTRIES_DIRECTORY} or ${DRAFTS_DIRECTORY}. ` +
          "Enable hidden-folder indexing in the kgdistiller settings so Obsidian reads the knowledge folder.",
      });
      return;
    }
    const elements = graphElements(this.model, this.filters);
    if (elements.length === 0) {
      this.graphEl.createDiv({ cls: "kgd-empty-state", text: "No records match these filters." });
      return;
    }
    const computedStyle = getComputedStyle(this.contentEl);
    const themeColor = (name: string, fallback: string): string =>
      computedStyle.getPropertyValue(name).trim() || fallback;
    const textNormal = themeColor("--text-normal", "#1f2937");
    const textMuted = themeColor("--text-muted", "#64748b");
    const backgroundPrimary = themeColor("--background-primary", "#ffffff");
    const accent = themeColor("--interactive-accent", "#7c3aed");
    this.cytoscape = cytoscape({
      container: this.graphEl,
      elements,
      wheelSensitivity: 0.22,
      minZoom: 0.15,
      maxZoom: 3,
      layout: {
        name: "cose",
        animate: false,
        fit: true,
        padding: 32,
        nodeRepulsion: () => 700000,
        idealEdgeLength: () => 130,
      },
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)",
            color: textNormal,
            "font-size": 11,
            "text-wrap": "wrap",
            "text-max-width": "120px",
            "text-valign": "bottom",
            "text-margin-y": 7,
            "background-color": accent,
            "border-width": 2,
            "border-color": backgroundPrimary,
            width: 30,
            height: 30,
          },
        },
        {
          selector: "node.kgd-relation",
          style: { shape: "diamond", "background-color": "data(color)", width: 22, height: 22, "font-size": 9 },
        },
        {
          selector: "node.kgd-stub",
          style: { "background-color": "#9ca3af", width: 20, height: 20, color: textMuted },
        },
        {
          selector: "node.kgd-dangling",
          style: { "background-color": "#dc2626", width: 18, height: 18, color: "#dc2626" },
        },
        {
          selector: "node.understanding-understood",
          style: { "border-color": "#059669", "border-width": 4 },
        },
        {
          selector: "node.understanding-not-yet-understood",
          style: { "border-color": "#ca8a04", "border-width": 4 },
        },
        {
          selector: "node.kgd-draft",
          style: { "border-style": "dashed", "border-color": textMuted, "border-width": 3, "background-opacity": 0.55 },
        },
        {
          selector: "edge",
          style: {
            width: 2,
            "curve-style": "bezier",
            "line-color": textMuted,
            "target-arrow-color": textMuted,
            "arrow-scale": 0.8,
            label: "data(label)",
            "font-size": 9,
            color: textMuted,
            "text-background-color": backgroundPrimary,
            "text-background-opacity": 0.85,
            "text-background-padding": "2px",
          },
        },
        {
          selector: "edge.kgd-relation-edge, edge.kgd-role",
          style: { "line-color": "data(color)", "target-arrow-color": "data(color)" },
        },
        {
          selector: "edge.kgd-relation-edge",
          style: { width: 3 },
        },
        {
          selector: "edge.kgd-directed, edge.kgd-role",
          style: { "target-arrow-shape": "triangle" },
        },
        {
          selector: "edge.kgd-requires",
          style: { "line-style": "dashed", "target-arrow-shape": "vee" },
        },
        {
          selector: "edge.kgd-draft",
          style: { "line-style": "dashed", opacity: 0.7 },
        },
        {
          selector: "edge.kgd-dangling",
          style: { "line-color": "#dc2626", "target-arrow-color": "#dc2626" },
        },
        {
          selector: ":selected",
          style: { "overlay-color": accent, "overlay-opacity": 0.18 },
        },
      ],
    });
    this.cytoscape.on("tap", "node, edge", (event: EventObject) => {
      this.showDetails(event.target.data() as GraphElementData);
    });
  }

  private showHelp(): void {
    if (!this.detailEl) return;
    this.detailEl.empty();
    this.detailEl.createEl("h3", { text: "Graph semantics" });
    this.detailEl.createEl("p", {
      text: "Select a node or edge to inspect its record. The graph follows the records in the vault as you edit them.",
    });
    const list = this.detailEl.createEl("ul", { cls: "kgd-legend" });
    list.createEl("li", { text: "Circle: node record; green ring understood, amber ring not yet understood" });
    list.createEl("li", { text: "Coloured edge: relation with two participants, labelled with its kind" });
    list.createEl("li", { text: "Diamond: any other relation, with one edge per role value" });
    list.createEl("li", { text: "Dashed arrow: requires" });
    list.createEl("li", { text: "Dashed outline: draft, not yet accepted" });
    list.createEl("li", { text: "Grey: record in another base; red: missing record" });
  }

  private showDetails(data: GraphElementData): void {
    if (!this.detailEl || !this.model) return;
    this.detailEl.empty();
    const record = data.path ? this.model.records.get(data.path) : undefined;
    if (!record) {
      this.detailEl.createEl("div", { cls: `kgd-kind kgd-kind-${data.element}`, text: data.element });
      this.detailEl.createEl("h3", { text: data.label });
      if (data.element === "stub") this.detailRow("Record in another base", data.uid ?? data.label);
      if (data.element === "dangling") this.detailRow("Missing record", `${ENTRIES_DIRECTORY}/${data.label}.md`);
      if (data.role) this.detailRow("Role", data.role);
      return;
    }
    const badge = record.draft ? "draft" : record.recordClass;
    this.detailEl.createEl("div", { cls: `kgd-kind kgd-kind-${badge}`, text: badge });
    this.detailEl.createEl("h3", { text: record.label });
    this.detailRow("Kind", record.kind || "(none)");
    this.detailRow("Class", record.recordClass);
    if (record.understanding && record.understanding !== "unknown") this.detailRow("Understanding", record.understanding);
    if (record.epistemic) this.detailRow("Epistemic", record.epistemic);
    if (record.source) this.detailRow("Source", record.lines ? `${record.source} · L${record.lines}` : record.source);
    for (const role of record.roles) this.detailRow(role.role, this.values(role.values));
    if (record.requires.length > 0) this.detailRow("requires", this.values(record.requires));
    const target = openTarget(data);
    if (target) {
      const button = this.detailEl.createEl("button", { cls: "mod-cta", text: "Open record" });
      button.addEventListener("click", () => void this.openVaultPath(target.path));
    }
  }

  private values(values: ResolvedValue[]): string {
    return values.map((value) => valueLabel(this.model!, value)).join(", ");
  }

  private detailRow(label: string, value: string): void {
    if (!this.detailEl) return;
    const row = this.detailEl.createDiv({ cls: "kgd-detail-row" });
    row.createEl("strong", { text: `${label}: ` });
    row.createSpan({ text: value });
  }

  private async openVaultPath(path: string): Promise<void> {
    const file = this.app.vault.getAbstractFileByPath(normalizePath(path));
    if (!(file instanceof TFile)) {
      new Notice(`kgdistiller cannot open ${path}: it is not in the vault index.`);
      return;
    }
    await this.app.workspace.getLeaf(false).openFile(file);
  }

  private setStatus(text: string): void {
    this.statusEl?.setText(text);
  }
}
