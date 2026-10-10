import cytoscape, { type Core, type ElementDefinition, type EventObject } from "cytoscape";
import fcose from "cytoscape-fcose";
import {
  App,
  Component,
  ItemView,
  MarkdownRenderer,
  Notice,
  normalizePath,
  setIcon,
  TFile,
  WorkspaceLeaf,
} from "obsidian";

import {
  defaultFilters,
  detailsModel,
  focusPaths,
  graphElements,
  graphModel,
  graphOptions,
  neighbourhood,
  type FrontmatterRecord,
  type GraphElementData,
  type GraphFilters,
  type KnowledgeModel,
  type RecordDetails,
} from "./graph-model";
import { DRAFTS_DIRECTORY, ENTRIES_DIRECTORY, recordBody, UNDERSTANDING_STATES } from "./records";
import type { KgdistillerSettings } from "./settings";

cytoscape.use(fcose);

export const VIEW_TYPE_KGDISTILLER_GRAPH = "kgdistiller-graph-view";
export const KGDISTILLER_ICON = "flask-conical";

export type GraphMode = "neighbourhood" | "full";
export type GraphDepth = 1 | 2;

export interface GraphViewHost {
  app: App;
  settings: KgdistillerSettings;
  /** The live records from the metadata cache. */
  knowledgeRecords(): FrontmatterRecord[];
}

/** Records drawn among the elements: nodes, diamonds and typed edges each carry their record path. */
const drawnRecords = (elements: readonly ElementDefinition[]): number =>
  elements.filter((element) => (element.data as GraphElementData).path !== undefined).length;

const FCOSE_LAYOUT: cytoscape.BaseLayoutOptions & Record<string, unknown> = {
  name: "fcose",
  animate: false,
  fit: true,
  padding: 32,
  quality: "default",
  randomize: true,
  nodeRepulsion: () => 9000,
  idealEdgeLength: () => 110,
  edgeElasticity: () => 0.45,
  nodeSeparation: 90,
  gravity: 0.25,
  numIter: 2500,
};

export class KgdistillerGraphView extends ItemView {
  private model: KnowledgeModel | null = null;
  private cytoscape: Core | null = null;
  private resizeObserver: ResizeObserver | null = null;
  private toolbarEl: HTMLElement | null = null;
  private graphEl: HTMLElement | null = null;
  private detailEl: HTMLElement | null = null;
  private statusEl: HTMLElement | null = null;
  private filters: GraphFilters;
  private mode: GraphMode = "neighbourhood";
  private depth: GraphDepth = 1;
  private activePath: string | undefined;
  /** Each selection bumps this, so a slower earlier body render is dropped. */
  private detailsRequest = 0;
  private detailsComponent: Component | null = null;

  constructor(leaf: WorkspaceLeaf, private readonly host: GraphViewHost) {
    super(leaf);
    this.filters = defaultFilters(host.settings.showDrafts);
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
    this.activePath = this.app.workspace.getActiveFile()?.path;
    this.registerEvent(this.app.workspace.on("file-open", (file) => {
      if (file?.path === this.activePath) return;
      this.activePath = file?.path;
      if (this.mode === "neighbourhood") this.renderGraph();
    }));
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
    if (this.filters.kind && !graphOptions(this.model).includes(this.filters.kind)) {
      this.filters.kind = "";
    }
    this.renderToolbar();
    this.renderGraph();
  }

  /** Apply the stored drafts setting, then re-render. */
  applySettings(): void {
    this.filters.showDrafts = this.host.settings.showDrafts;
    this.refresh();
  }

  /** The status line: record counts, plus how much of the filtered graph the neighbourhood shows. */
  statusText(showing?: { shown: number; total: number }): string {
    const records = [...(this.model?.records.values() ?? [])];
    const drafts = records.filter((record) => record.draft).length;
    const relations = records.filter((record) => !record.draft && record.recordClass === "relation").length;
    const counts = `${records.length - drafts - relations} nodes · ${relations} relations · ${drafts} drafts`;
    return showing ? `${counts} · showing ${showing.shown} of ${showing.total}` : counts;
  }

  private renderToolbar(): void {
    if (!this.toolbarEl || !this.model) return;
    const toolbar = this.toolbarEl;
    toolbar.empty();

    const mode = toolbar.createDiv({ cls: "kgd-mode", attr: { role: "group", "aria-label": "Graph mode" } });
    for (const [value, text] of [["neighbourhood", "Neighbourhood"], ["full", "Full graph"]] as const) {
      const button = mode.createEl("button", { text, cls: value === this.mode ? "is-active" : "" });
      button.addEventListener("click", () => {
        if (this.mode === value) return;
        this.mode = value;
        this.renderToolbar();
        this.renderGraph();
      });
    }

    const select = (label: string, options: ReadonlyArray<readonly [string, string]>, value: string, apply: (value: string) => void): void => {
      const wrapper = toolbar.createEl("label", { cls: "kgd-control" });
      wrapper.createSpan({ text: label });
      const element = wrapper.createEl("select", { attr: { "aria-label": label } });
      for (const [optionValue, optionText] of options) element.createEl("option", { text: optionText, value: optionValue });
      element.value = value;
      element.addEventListener("change", () => {
        apply(element.value);
        this.renderGraph();
      });
    };
    if (this.mode === "neighbourhood") {
      select("Depth", [["1", "1"], ["2", "2"]], String(this.depth), (value) => { this.depth = value === "2" ? 2 : 1; });
    }
    select("Kind", [["", "All kinds"], ...graphOptions(this.model).map((kind) => [kind, kind] as const)], this.filters.kind,
      (value) => { this.filters.kind = value; });
    select("Class", [["", "All classes"], ["node", "Nodes"], ["relation", "Relations"]], this.filters.recordClass,
      (value) => { this.filters.recordClass = value === "node" || value === "relation" ? value : ""; });
    select("Understanding", [["", "Any"], ...UNDERSTANDING_STATES.map((state) => [state, state] as const)], this.filters.understanding,
      (value) => { this.filters.understanding = UNDERSTANDING_STATES.find((state) => state === value) ?? ""; });

    const prefix = toolbar.createEl("label", { cls: "kgd-control" });
    prefix.createSpan({ text: "Source" });
    const input = prefix.createEl("input", {
      type: "text",
      attr: { "aria-label": "Source prefix", placeholder: "path prefix" },
    });
    input.value = this.filters.sourcePrefix;
    input.addEventListener("change", () => {
      this.filters.sourcePrefix = input.value.trim();
      this.renderGraph();
    });

    const toggle = (label: string, checked: boolean, apply: (checked: boolean) => void): void => {
      const wrapper = toolbar.createEl("label", { cls: "kgd-toggle" });
      const box = wrapper.createEl("input", { type: "checkbox" });
      box.checked = checked;
      wrapper.createSpan({ text: label });
      box.addEventListener("change", () => {
        apply(box.checked);
        this.renderGraph();
      });
    };
    toggle("Show drafts", this.filters.showDrafts, (checked) => { this.filters.showDrafts = checked; });
    toggle("Show pending terms", this.filters.showPending, (checked) => { this.filters.showPending = checked; });

    const fitButton = toolbar.createEl("button", {
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
      this.setStatus(this.statusText());
      this.emptyState(`No records in ${ENTRIES_DIRECTORY} or ${DRAFTS_DIRECTORY}. ` +
        "Run `kgd obsidian install`, which turns on hidden indexing, or turn on Index hidden knowledge folder " +
        "in the kgdistiller settings so Obsidian reads the knowledge folder.");
      return;
    }
    let elements = graphElements(this.model, this.filters);
    if (this.mode === "neighbourhood") {
      const total = drawnRecords(elements);
      const seeds = focusPaths(this.model, this.activePath);
      elements = seeds.length > 0 ? neighbourhood(elements, seeds, this.depth) : [];
      this.setStatus(this.statusText({ shown: drawnRecords(elements), total }));
      if (seeds.length === 0) {
        this.emptyState("Open a record, a source or its sheet to see its neighbourhood, or switch to the full graph.");
        return;
      }
    } else {
      this.setStatus(this.statusText());
    }
    if (elements.length === 0) {
      this.emptyState("No records match these filters.");
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
      layout: FCOSE_LAYOUT,
      style: [
        {
          selector: "node",
          style: {
            label: "data(display)",
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
          selector: "node[color]",
          style: { "background-color": "data(color)" },
        },
        {
          selector: "node.kgd-relation",
          style: { shape: "diamond", width: 24, height: 24, "font-size": 9 },
        },
        {
          selector: "node.understanding-unknown",
          style: { "border-style": "dashed", "border-color": textMuted, "border-width": 2 },
        },
        {
          selector: "node.understanding-not-yet-understood",
          style: { "border-color": "#ca8a04", "border-width": 4 },
        },
        {
          selector: "node.understanding-understood",
          style: { "border-color": "#059669", "border-width": 4 },
        },
        {
          selector: "node.kgd-draft",
          style: { "border-style": "dashed", "background-opacity": 0.45 },
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
          selector: "node.kgd-pending",
          style: {
            shape: "ellipse",
            width: 14,
            height: 14,
            "background-color": "#9ca3af",
            "background-opacity": 0,
            "border-style": "dotted",
            "border-color": textMuted,
            "border-width": 2,
            color: textMuted,
            "font-style": "italic",
            "font-size": 9,
          },
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
          selector: "edge[color]",
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

  private emptyState(text: string): void {
    this.graphEl?.createDiv({ cls: "kgd-empty-state", text });
  }

  private showHelp(): void {
    if (!this.detailEl) return;
    this.detailEl.empty();
    this.detailEl.createEl("h3", { text: "Graph semantics" });
    this.detailEl.createEl("p", {
      text: "Select a node or edge to inspect its record. The neighbourhood follows the open record, source or sheet; " +
        "the graph follows the records in the vault as you edit them.",
    });
    const list = this.detailEl.createEl("ul", { cls: "kgd-legend" });
    const item = (swatch: string, text: string): void => {
      const entry = list.createEl("li");
      entry.createSpan({ cls: `kgd-swatch kgd-swatch-${swatch}` });
      entry.createSpan({ text });
    };
    item("node", "Circle: node record, filled by kind");
    item("understood", "Green ring: understood");
    item("not-yet-understood", "Amber ring: not yet understood");
    item("unknown", "Dashed grey ring: understanding unknown");
    item("edge", "Coloured edge: relation with exactly two link values, both in roles, no requires link and nothing " +
      "linking to it, labelled with its kind (a diamond instead while pending terms are shown and it has a term)");
    item("relation", "Diamond: every other relation; one edge per role value");
    item("requires", "Dashed arrow: requires");
    item("draft", "Dashed outline, faded fill and a draft line: draft, not yet accepted");
    item("pending", "Hollow dotted circle: pending term, one per name key (Show pending terms)");
    item("stub", "Grey: record in another base");
    item("dangling", "Red: missing record");
  }

  private showDetails(data: GraphElementData): void {
    if (!this.detailEl || !this.model) return;
    const request = ++this.detailsRequest;
    if (this.detailsComponent) this.removeChild(this.detailsComponent);
    this.detailsComponent = null;
    const pane = this.detailEl;
    pane.empty();
    const details = detailsModel(this.model, data, (path) => this.vaultFile(path) !== undefined);
    const header = (badge: string, title: string): void => {
      pane.createEl("div", { cls: `kgd-kind kgd-kind-${badge}`, text: badge });
      pane.createEl("h3", { text: title });
    };
    if (!details) return;
    switch (details.element) {
      case "pending": {
        header("pending", details.term);
        this.detailRow("Name key", details.key);
        pane.createEl("h4", { text: "Used by" });
        const owners = pane.createEl("ul", { cls: "kgd-owners" });
        for (const owner of details.owners) {
          const entry = owners.createEl("li");
          const link = entry.createEl("a", { text: owner.label, href: "#" });
          link.addEventListener("click", (event) => {
            event.preventDefault();
            void this.openVaultPath(owner.path);
          });
          entry.createSpan({ text: ` (${owner.role})` });
        }
        return;
      }
      case "stub":
        header("stub", details.uid);
        this.detailRow("Record in another base", details.uid);
        return;
      case "dangling":
        header("dangling", details.id);
        this.detailRow("Missing record", details.path);
        return;
      case "record":
        this.showRecord(details, request);
    }
  }

  private showRecord(details: RecordDetails, request: number): void {
    const pane = this.detailEl!;
    const badge = details.draft ? "draft" : details.recordClass;
    pane.createEl("div", { cls: `kgd-kind kgd-kind-${badge}`, text: badge });
    pane.createEl("h3", { text: details.label });
    this.detailRow("Kind", details.kind || "(none)");
    this.detailRow("Class", details.recordClass);
    if (details.epistemic) this.detailRow("Epistemic", details.epistemic);
    this.detailRow("Understanding", details.understanding ?? "(invalid value)");
    if (details.source) {
      this.detailRow("Source", details.source.lines ? `${details.source.path} · L${details.source.lines}` : details.source.path);
    }
    for (const role of details.roles) this.detailRow(role.role, role.values.join(", "));
    if (details.requires.length > 0) this.detailRow("requires", details.requires.join(", "));

    const actions = pane.createDiv({ cls: "kgd-detail-actions" });
    const action = (text: string, primary: boolean, run: () => Promise<void>): void => {
      const button = actions.createEl("button", { text, cls: primary ? "mod-cta" : "" });
      button.addEventListener("click", () => void run());
    };
    action("Open record", true, () => this.openVaultPath(details.recordPath));
    const source = details.source;
    if (source && this.vaultFile(source.path)) {
      const text = source.line === undefined ? "Open source" : `Open source at line ${source.line + 1}`;
      action(text, false, () => this.openVaultPath(source.path, source.line));
    }
    const sheet = details.sheet;
    if (sheet) action("Open sheet", false, () => this.openVaultPath(sheet));

    const body = pane.createDiv({ cls: "kgd-record-body" });
    void this.renderBody(details.recordPath, body, request);
  }

  /** Render the record's prose and Evidence from its file; a newer selection drops this render. */
  private async renderBody(path: string, body: HTMLElement, request: number): Promise<void> {
    const file = this.vaultFile(path);
    if (!file) return;
    const text = await this.app.vault.cachedRead(file);
    if (request !== this.detailsRequest) return;
    const { prose, evidence } = recordBody(text);
    const component = this.addChild(new Component());
    this.detailsComponent = component;
    if (prose) await MarkdownRenderer.render(this.app, prose, body.createDiv({ cls: "kgd-prose" }), path, component);
    if (request !== this.detailsRequest || !evidence) return;
    body.createEl("h4", { text: "Evidence" });
    await MarkdownRenderer.render(this.app, evidence, body.createDiv({ cls: "kgd-evidence" }), path, component);
  }

  private detailRow(label: string, value: string): void {
    if (!this.detailEl) return;
    const row = this.detailEl.createDiv({ cls: "kgd-detail-row" });
    row.createEl("strong", { text: `${label}: ` });
    row.createSpan({ text: value });
  }

  private vaultFile(path: string): TFile | undefined {
    const file = this.app.vault.getAbstractFileByPath(normalizePath(path));
    return file instanceof TFile ? file : undefined;
  }

  /** Open a vault file; `line` is 0-based and scrolls Markdown and source views through `eState.line`. */
  private async openVaultPath(path: string, line?: number): Promise<void> {
    const file = this.vaultFile(path);
    if (!file) {
      new Notice(`kgdistiller cannot open ${path}: it is not in the vault index.`);
      return;
    }
    await this.app.workspace.getLeaf(false).openFile(file, line === undefined ? undefined : { eState: { line } });
  }

  private setStatus(text: string): void {
    this.statusEl?.setText(text);
  }
}
