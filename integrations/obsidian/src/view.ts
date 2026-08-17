import cytoscape, { type Core, type EventObject } from "cytoscape";
import {
  App,
  ItemView,
  MarkdownView,
  Notice,
  normalizePath,
  setIcon,
  TFile,
  WorkspaceLeaf,
} from "obsidian";

import { isSafeVaultPath, parseGraphContract, type KgGraphContract } from "./contract";
import {
  fieldOptions,
  graphElements,
  relationOptions,
  type GraphElementData,
  type GraphFilters,
} from "./graph-model";
import type { KgdistillerSettings } from "./settings";

export const VIEW_TYPE_KGDISTILLER_GRAPH = "kgdistiller-graph-view";
export const KGDISTILLER_ICON = "flask-conical";

export interface GraphViewHost {
  app: App;
  settings: KgdistillerSettings;
}

export class KgdistillerGraphView extends ItemView {
  private graph: KgGraphContract | null = null;
  private graphPath = "";
  private cytoscape: Core | null = null;
  private resizeObserver: ResizeObserver | null = null;
  private toolbarEl: HTMLElement | null = null;
  private graphEl: HTMLElement | null = null;
  private detailEl: HTMLElement | null = null;
  private statusEl: HTMLElement | null = null;
  private filters: GraphFilters;

  constructor(leaf: WorkspaceLeaf, private readonly host: GraphViewHost) {
    super(leaf);
    this.filters = {
      relation: "",
      field: "",
      showSources: host.settings.showSources,
      showDefinitions: host.settings.showDefinitions,
      showReferences: host.settings.showReferences,
    };
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
    await this.refresh();
  }

  async onClose(): Promise<void> {
    this.resizeObserver?.disconnect();
    this.resizeObserver = null;
    this.cytoscape?.destroy();
    this.cytoscape = null;
  }

  async refresh(): Promise<void> {
    if (!this.toolbarEl || !this.graphEl || !this.statusEl) return;
    try {
      const configuredPath = normalizePath(this.host.settings.graphPath.trim());
      if (!isSafeVaultPath(configuredPath, /\.json$/)) {
        throw new Error("The semantic graph setting must be a safe vault-relative JSON path.");
      }
      const file = this.app.vault.getAbstractFileByPath(configuredPath);
      if (!(file instanceof TFile)) {
        throw new Error(
          `No semantic graph exists at ${configuredPath}. Run kgdistiller export obsidian --replace.`,
        );
      }
      this.graphPath = configuredPath;
      this.graph = await parseGraphContract(await this.app.vault.read(file));
      this.filters.showSources = this.host.settings.showSources;
      this.filters.showDefinitions = this.host.settings.showDefinitions;
      this.filters.showReferences = this.host.settings.showReferences;
      if (this.filters.relation && !relationOptions(this.graph).includes(this.filters.relation)) {
        this.filters.relation = "";
      }
      if (this.filters.field && !fieldOptions(this.graph).includes(this.filters.field)) {
        this.filters.field = "";
      }
      this.renderToolbar();
      this.renderGraph();
      this.setStatus(
        `${this.graph.counts.concepts} concepts · ${this.graph.counts.semantic_edges} semantic edges · ${this.graph.counts.references} references`,
        false,
      );
    } catch (error) {
      this.graph = null;
      this.cytoscape?.destroy();
      this.cytoscape = null;
      this.graphEl.empty();
      this.graphEl.createDiv({
        cls: "kgd-empty-state",
        text: error instanceof Error ? error.message : String(error),
      });
      this.setStatus("Graph unavailable", true);
    }
  }

  private renderToolbar(): void {
    if (!this.toolbarEl || !this.graph) return;
    this.toolbarEl.empty();
    this.addSelect(
      "Relation",
      "All semantic relations",
      relationOptions(this.graph),
      this.filters.relation,
      (value) => {
        this.filters.relation = value;
        this.renderGraph();
      },
    );
    this.addSelect("Field", "All fields", fieldOptions(this.graph), this.filters.field, (value) => {
      this.filters.field = value;
      this.renderGraph();
    });
    this.addToggle("Sources", this.filters.showSources, (value) => {
      this.filters.showSources = value;
      this.renderGraph();
    });
    this.addToggle("Definitions", this.filters.showDefinitions, (value) => {
      this.filters.showDefinitions = value;
      this.renderGraph();
    });
    this.addToggle("References", this.filters.showReferences, (value) => {
      this.filters.showReferences = value;
      this.renderGraph();
    });
    const fitButton = this.toolbarEl.createEl("button", {
      cls: "clickable-icon kgd-icon-button",
      attr: { "aria-label": "Fit graph" },
    });
    setIcon(fitButton, "scan");
    fitButton.addEventListener("click", () => this.cytoscape?.fit(undefined, 36));
    const refreshButton = this.toolbarEl.createEl("button", {
      cls: "clickable-icon kgd-icon-button",
      attr: { "aria-label": "Reload graph" },
    });
    setIcon(refreshButton, "refresh-cw");
    refreshButton.addEventListener("click", () => void this.refresh());
  }

  private addSelect(
    label: string,
    emptyLabel: string,
    options: string[],
    selected: string,
    onChange: (value: string) => void,
  ): void {
    if (!this.toolbarEl) return;
    const wrapper = this.toolbarEl.createEl("label", { cls: "kgd-control" });
    wrapper.createSpan({ text: label });
    const select = wrapper.createEl("select", { attr: { "aria-label": label } });
    select.createEl("option", { text: emptyLabel, value: "" });
    for (const option of options) select.createEl("option", { text: option, value: option });
    select.value = selected;
    select.addEventListener("change", () => onChange(select.value));
  }

  private addToggle(label: string, checked: boolean, onChange: (value: boolean) => void): void {
    if (!this.toolbarEl) return;
    const wrapper = this.toolbarEl.createEl("label", { cls: "kgd-toggle" });
    const input = wrapper.createEl("input", { type: "checkbox" });
    input.checked = checked;
    wrapper.createSpan({ text: label });
    input.addEventListener("change", () => onChange(input.checked));
  }

  private renderGraph(): void {
    if (!this.graphEl || !this.graph) return;
    this.cytoscape?.destroy();
    this.graphEl.empty();
    const elements = graphElements(this.graph, this.graphPath, this.filters);
    if (elements.length === 0) {
      this.graphEl.createDiv({ cls: "kgd-empty-state", text: "No nodes match these filters." });
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
          selector: 'node[kind = "source"]',
          style: {
            shape: "round-rectangle",
            "background-color": "#d97706",
            width: 42,
            height: 24,
          },
        },
        {
          selector: 'node[status = "needs-review"]',
          style: { "border-color": "#dc2626", "border-width": 4 },
        },
        {
          selector: 'node[status = "pending"]',
          style: { "border-color": "#ca8a04", "border-width": 4 },
        },
        {
          selector: "edge",
          style: {
            width: 2,
            "curve-style": "bezier",
            "target-arrow-shape": "triangle",
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
          selector: 'edge[kind = "semantic"]',
          style: {
            "line-color": "data(color)",
            "target-arrow-color": "data(color)",
            width: 3,
          },
        },
        {
          selector: 'edge[kind = "definition"]',
          style: {
            "line-color": "#059669",
            "target-arrow-color": "#059669",
            "line-style": "dotted",
          },
        },
        {
          selector: 'edge[kind = "reference"]',
          style: {
            "line-color": "#0284c7",
            "target-arrow-color": "#0284c7",
            "line-style": "dashed",
          },
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
      text: "Select a node or edge to inspect it. Semantic edges retain their direction, relation type, and evidence.",
    });
    const list = this.detailEl.createEl("ul", { cls: "kgd-legend" });
    list.createEl("li", { text: "Solid colored: concept → concept semantic relation" });
    list.createEl("li", { text: "Green dotted: source → concept definition" });
    list.createEl("li", { text: "Blue dashed: source → concept reference" });
  }

  private showDetails(data: GraphElementData): void {
    if (!this.detailEl) return;
    this.detailEl.empty();
    this.detailEl.createEl("div", { cls: `kgd-kind kgd-kind-${data.kind}`, text: data.kind });
    this.detailEl.createEl("h3", { text: data.label });
    if (data.conceptId) this.detailRow("Concept ID", data.conceptId);
    if (data.relation) this.detailRow("Relation", data.relation);
    if (data.fields) this.detailRow("Fields", data.fields);
    if (data.status) this.detailRow("Curation", data.status);
    if (data.authority) this.detailRow("Authority", data.authority);
    if (data.line) {
      this.detailRow("Location", data.lineEnd && data.lineEnd !== data.line ? `lines ${data.line}–${data.lineEnd}` : `line ${data.line}`);
    }
    if (data.evidence) {
      this.detailEl.createEl("h4", { text: "Evidence" });
      this.detailEl.createEl("blockquote", { text: data.evidence });
    }
    if (data.kind === "concept" || data.kind === "source") {
      this.addOpenButton(data);
    } else if ((data.kind === "definition" || data.kind === "reference") && data.notePath) {
      this.addOpenButton(data, "Open source");
    }
  }

  private detailRow(label: string, value: string): void {
    if (!this.detailEl) return;
    const row = this.detailEl.createDiv({ cls: "kgd-detail-row" });
    row.createEl("strong", { text: `${label}: ` });
    row.createSpan({ text: value });
  }

  private addOpenButton(data: GraphElementData, label = "Open note"): void {
    if (!this.detailEl) return;
    const button = this.detailEl.createEl("button", { cls: "mod-cta", text: label });
    button.addEventListener("click", () => {
      const opensMarkdownAuthority =
        data.kind !== "concept" && data.authority?.toLowerCase().endsWith(".md");
      const path = opensMarkdownAuthority ? data.authority : data.notePath;
      if (path) void this.openVaultPath(path, opensMarkdownAuthority ? data.line : undefined);
    });
  }

  private async openVaultPath(path: string, line?: number): Promise<void> {
    const file = this.app.vault.getAbstractFileByPath(normalizePath(path));
    if (!(file instanceof TFile)) {
      new Notice(`kgdistiller note is missing: ${path}`);
      return;
    }
    const leaf = this.app.workspace.getLeaf(false);
    await leaf.openFile(file);
    if (line && leaf.view instanceof MarkdownView) {
      leaf.view.editor.setCursor({ line: Math.max(0, line - 1), ch: 0 });
      leaf.view.editor.focus();
    }
  }

  private setStatus(text: string, error: boolean): void {
    if (!this.statusEl) return;
    this.statusEl.setText(text);
    this.statusEl.toggleClass("kgd-status-error", error);
  }
}
