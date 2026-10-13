/**
 * The lookup sidebar: what the knowledge base holds for a selected passage.
 * It shows the records sharing the selection's name, the records the three
 * search lanes rank as related, and for the record in focus its links, the
 * relations about it, the records built on it, its `requires` chain and a
 * graph of its neighbours. Every answer comes from `kgd mcp`, so it covers
 * every registered base; same name is not same concept, so name matches are
 * shown as candidates to compare.
 */

import cytoscape, { type Core, type EventObject } from "cytoscape";
import { Component, ItemView, MarkdownRenderer, Notice, normalizePath, TFile, type App, type WorkspaceLeaf } from "obsidian";

import type { GraphElementData } from "./graph-model";
import { FCOSE_LAYOUT, graphStylesheet } from "./graph-style";
import type { KgdServer } from "./kgd";
import {
  chainTree,
  inboundGroups,
  lagging,
  linksByRole,
  NEIGHBOUR_STEPS,
  NEIGHBOUR_WALK_DEPTH,
  neighbourGraph,
  rankText,
  type ChainNode,
  type FullRecord,
  type GetResult,
  type Lag,
  type NeighborsResult,
  type RecordSummary,
  type ResolveResult,
  type SearchResult,
} from "./lookup-model";
import { entryPath, firstLine } from "./records";

export const VIEW_TYPE_KGDISTILLER_LOOKUP = "kgdistiller-lookup-view";
export const LOOKUP_ICON = "scan-search";
const SEARCH_LIMIT = 10;
const CHAIN_DEPTH = 3;
/** The most uids one `kg_get` call takes. */
const MAX_UIDS = 128;

export interface LookupHost {
  app: App;
  kgd: KgdServer;
  /** The real path of this vault's root, when the vault lives on the local filesystem. */
  vaultRoot(): string | undefined;
}

type Settled<T> = { value: T; error?: undefined } | { value?: undefined; error: string };

const settle = <T>(promise: Promise<T>): Promise<Settled<T>> =>
  promise.then((value) => ({ value }), (error: unknown) => ({ error: error instanceof Error ? error.message : String(error) }));

export class KgdistillerLookupView extends ItemView {
  private queryEl: HTMLElement | null = null;
  private statusEl: HTMLElement | null = null;
  private namesEl: HTMLElement | null = null;
  private recordEl: HTMLElement | null = null;
  private relatedEl: HTMLElement | null = null;
  private query = "";
  private resolved: Settled<ResolveResult> | undefined;
  private searched: Settled<SearchResult> | undefined;
  /** Labels of the records that use the query as a pending term; uids are never shown. */
  private ownerLabels = new Map<string, string>();
  private focus: string | undefined;
  /** Each lookup and each focused record bumps these, so slower earlier answers are dropped. */
  private lookupRequest = 0;
  private focusRequest = 0;
  private renderComponent: Component | null = null;
  private neighbours: Core | null = null;

  constructor(leaf: WorkspaceLeaf, private readonly host: LookupHost) {
    super(leaf);
  }

  getViewType(): string {
    return VIEW_TYPE_KGDISTILLER_LOOKUP;
  }

  getDisplayText(): string {
    return "kgdistiller Lookup";
  }

  getIcon(): string {
    return LOOKUP_ICON;
  }

  async onClose(): Promise<void> {
    this.clearNeighbours();
  }

  async onOpen(): Promise<void> {
    this.contentEl.empty();
    this.contentEl.addClass("kgd-lookup-view");
    this.queryEl = this.contentEl.createDiv({ cls: "kgd-lookup-query" });
    this.statusEl = this.contentEl.createDiv({ cls: "kgd-lookup-status" });
    // The record in focus, with its chain, sits between the name matches and the related records.
    this.namesEl = this.contentEl.createDiv({ cls: "kgd-lookup-names" });
    this.recordEl = this.contentEl.createDiv({ cls: "kgd-lookup-record" });
    this.relatedEl = this.contentEl.createDiv({ cls: "kgd-lookup-related" });
    this.queryEl.setText("Select text and hold the command key, or run Look up selection.");
  }

  /** Look the query up in every base: names first, then the ranked search, then the best candidate in focus. */
  async lookup(query: string): Promise<void> {
    const request = ++this.lookupRequest;
    this.query = query;
    this.resolved = undefined;
    this.searched = undefined;
    this.focus = undefined;
    ++this.focusRequest;
    this.ownerLabels = new Map();
    this.queryEl?.setText(query);
    this.clearNeighbours();
    this.recordEl?.empty();
    this.renderResults();
    // kgd mcp answers one call at a time, so asking in this order costs nothing.
    this.resolved = await settle(this.host.kgd.call<ResolveResult>("kg_resolve", { terms: [query] }));
    if (request !== this.lookupRequest) return;
    const owners = [...new Set((this.resolved.value?.terms[0]?.pending ?? []).map((use) => use.owner))].slice(0, MAX_UIDS);
    if (owners.length > 0) {
      const got = await settle(this.host.kgd.call<GetResult>("kg_get", { uids: owners }));
      if (request !== this.lookupRequest) return;
      this.ownerLabels = new Map((got.value?.records ?? []).map((record) => [record.uid, record.label]));
    }
    this.renderResults();
    this.searched = await settle(this.host.kgd.call<SearchResult>("kg_search", { query, limit: SEARCH_LIMIT }));
    if (request !== this.lookupRequest) return;
    this.renderResults();
    const best = this.resolved.value?.terms[0]?.senses[0]?.uid ?? this.searched.value?.results[0]?.uid;
    if (best && this.focus === undefined) await this.show(best);
  }

  private senses(): RecordSummary[] {
    return this.resolved?.value?.terms[0]?.senses ?? [];
  }

  private renderResults(): void {
    const container = this.namesEl;
    const relatedContainer = this.relatedEl;
    if (!container || !relatedContainer) return;
    container.empty();
    relatedContainer.empty();
    this.renderStatus();
    if (!this.resolved) return;
    const senses = this.senses();
    const pending = this.resolved.value?.terms[0]?.pending ?? [];
    if (senses.length > 0) {
      const section = this.section(container, "Same name", "Records whose label or alias is this name. A shared name does not make it the same concept: compare the definitions.");
      for (const record of senses) this.hit(section, record);
    }
    if (pending.length > 0) {
      const section = this.section(container, "Pending", "Records that use this name as a pending term: no base defines it yet.");
      for (const use of pending) {
        const row = section.createDiv({ cls: "kgd-lookup-hit" });
        row.createSpan({ cls: "kgd-lookup-label", text: this.ownerLabels.get(use.owner) ?? use.owner });
        row.createSpan({ cls: "kgd-lookup-meta", text: ` · ${use.role}` });
        row.addEventListener("click", () => void this.show(use.owner));
      }
    }
    const search = this.searched?.value;
    if (search) {
      const shown = new Set(senses.map((record) => record.uid));
      const related = search.results.filter((hit) => !shown.has(hit.uid));
      if (related.length > 0) {
        const section = this.section(relatedContainer, "Related", `Ranked by ${search.lanes.join(", ")}.`);
        for (const hit of related) this.hit(section, hit, rankText(search.lanes, hit.ranks));
      }
      if (senses.length === 0 && pending.length === 0 && search.results.length === 0) {
        container.createDiv({ cls: "kgd-empty-state", text: "Nothing in the knowledge base matches this selection." });
      }
    }
  }

  private renderStatus(): void {
    const status = this.statusEl;
    if (!status) return;
    status.empty();
    if (!this.query) return;
    if (!this.resolved || !this.searched) status.createSpan({ text: "Looking up…" });
    for (const error of [this.resolved?.error, this.searched?.error]) {
      if (error) status.createDiv({ cls: "kgd-lookup-error", text: error });
    }
    const lag: Lag | undefined = this.searched?.value?.lag ?? this.resolved?.value?.lag;
    if (lagging(lag)) {
      const line = status.createDiv({ cls: "kgd-lookup-lag" });
      line.createSpan({ text: "The index is behind the record files. " });
      const button = line.createEl("button", { text: "Run kgd index" });
      button.addEventListener("click", () => void this.reindex(button));
    }
  }

  private async reindex(button: HTMLButtonElement): Promise<void> {
    button.disabled = true;
    button.setText("Indexing…");
    try {
      const { failed } = await this.host.kgd.index();
      if (failed) new Notice("kgd index reported problems; run `kgd index` in a terminal to see them.");
    } catch (error) {
      new Notice(`kgd index failed: ${error instanceof Error ? error.message : String(error)}`);
    }
    if (this.query) await this.lookup(this.query);
  }

  private section(container: HTMLElement, title: string, hint: string): HTMLElement {
    const section = container.createDiv({ cls: "kgd-lookup-section" });
    section.createEl("h4", { text: title });
    section.createDiv({ cls: "kgd-lookup-hint", text: hint });
    return section;
  }

  private hit(section: HTMLElement, record: RecordSummary, ranks?: string): void {
    const row = section.createDiv({ cls: `kgd-lookup-hit${record.uid === this.focus ? " is-selected" : ""}` });
    row.createSpan({ cls: "kgd-lookup-label", text: record.label });
    row.createSpan({ cls: "kgd-lookup-meta", text: ` · ${record.kind}` });
    if (record.gloss) row.createDiv({ cls: "kgd-lookup-gloss", text: record.gloss });
    if (ranks) row.createDiv({ cls: "kgd-lookup-ranks", text: ranks });
    row.addEventListener("click", () => void this.show(record.uid));
  }

  /** Put one record in focus: its content, links, inbound links, `requires` chain and neighbours. */
  async show(uid: string): Promise<void> {
    const request = ++this.focusRequest;
    this.focus = uid;
    this.renderResults();
    const pane = this.recordEl;
    if (!pane) return;
    this.clearNeighbours();
    pane.empty();
    pane.createDiv({ cls: "kgd-lookup-hint", text: "Loading…" });
    const [got, chain, around] = await Promise.all([
      settle(this.host.kgd.call<GetResult>("kg_get", { uids: [uid] })),
      settle(this.host.kgd.call<NeighborsResult>("kg_neighbors", { uids: [uid], role: ["requires"], dir: "out", depth: CHAIN_DEPTH })),
      settle(this.host.kgd.call<NeighborsResult>("kg_neighbors", { uids: [uid], dir: "both", depth: NEIGHBOUR_WALK_DEPTH })),
    ]);
    if (request !== this.focusRequest) return;
    pane.empty();
    const record = got.value?.records[0];
    if (!record) {
      pane.createDiv({ cls: "kgd-lookup-error", text: got.error ?? `No record ${uid} in the index.` });
      return;
    }
    this.renderRecord(pane, record, chain, around);
  }

  private renderRecord(pane: HTMLElement, record: FullRecord, chain: Settled<NeighborsResult>, around: Settled<NeighborsResult>): void {
    pane.createDiv({ cls: `kgd-kind kgd-kind-${record.class}`, text: record.class });
    pane.createEl("h3", { text: record.label });
    this.row(pane, "Kind", record.kind);
    this.row(pane, "Source", `${record.base} · ${record.source} · L${record.lines}`);
    this.row(pane, "Understanding", record.understanding);
    if (record.epistemic) this.row(pane, "Epistemic", record.epistemic);
    if (record.aliases.length > 0) this.row(pane, "Aliases", record.aliases.join(", "));

    const actions = pane.createDiv({ cls: "kgd-detail-actions" });
    const action = (text: string, primary: boolean, run: () => Promise<void>): void => {
      const button = actions.createEl("button", { text, cls: primary ? "mod-cta" : "" });
      button.addEventListener("click", () => void run());
    };
    action("Open record", true, () => this.openInBase(record.base, entryPath(record.id)));
    action("Open source", false, () => this.openInBase(record.base, record.source, firstLine(record.lines)));

    this.renderNeighbours(pane, around, record.uid);

    const body = pane.createDiv({ cls: "kgd-record-body" });
    void this.renderMarkdown(record, body);

    const groups = linksByRole(record.out);
    if (groups.length > 0) {
      pane.createEl("h4", { text: "Links" });
      const list = pane.createEl("ul", { cls: "kgd-lookup-links" });
      for (const group of groups) {
        const item = list.createEl("li");
        item.createSpan({ cls: "kgd-lookup-role", text: `${group.role}: ` });
        group.links.forEach((link, index) => {
          if (index > 0) item.createSpan({ text: ", " });
          if (link.uid === undefined) item.createSpan({ cls: "kgd-pending-term", text: link.term ?? "" });
          else this.recordLink(item, link.uid, link.label ?? link.uid, link.exists ?? false);
        });
      }
    }
    const inbound = inboundGroups(record.in);
    if (inbound.about.length > 0) {
      pane.createEl("h4", { text: "About it" });
      pane.createDiv({ cls: "kgd-lookup-hint", text: "Relations it takes part in, by kind, with its role in each." });
      const list = pane.createEl("ul", { cls: "kgd-lookup-links" });
      for (const group of inbound.about) {
        const item = list.createEl("li");
        item.createSpan({ cls: "kgd-lookup-role", text: `${group.kind} (${group.links.length})` });
        const links = item.createEl("ul");
        for (const link of group.links) {
          const entry = links.createEl("li");
          this.recordLink(entry, link.uid, link.label, true);
          entry.createSpan({ cls: "kgd-lookup-meta", text: ` · as ${link.role}` });
        }
      }
    }
    if (inbound.builtOn.length > 0) {
      pane.createEl("h4", { text: "Built on it" });
      pane.createDiv({ cls: "kgd-lookup-hint", text: "Records that list it in requires." });
      const list = pane.createEl("ul", { cls: "kgd-lookup-links" });
      for (const link of inbound.builtOn) {
        const item = list.createEl("li");
        this.recordLink(item, link.uid, link.label, true);
        item.createSpan({ cls: "kgd-lookup-meta", text: ` · ${link.kind}` });
      }
    }
    if (chain.error) pane.createDiv({ cls: "kgd-lookup-error", text: chain.error });
    const tree = chain.value ? chainTree(chain.value, record.uid) : undefined;
    if (tree && tree.children.length > 0) {
      pane.createEl("h4", { text: "Requires chain" });
      this.renderChain(pane.createEl("ul", { cls: "kgd-chain" }), tree.children);
    }
  }

  /** The records within NEIGHBOUR_STEPS steps of the focus, drawn as a small typed graph; a click focuses one. */
  private renderNeighbours(pane: HTMLElement, around: Settled<NeighborsResult>, focus: string): void {
    pane.createEl("h4", { text: `Neighbours within ${NEIGHBOUR_STEPS} steps` });
    if (!around.value) {
      pane.createDiv({ cls: "kgd-lookup-error", text: around.error ?? "No neighbours." });
      return;
    }
    const graph = neighbourGraph(around.value, focus);
    if (graph.total <= 1) {
      pane.createDiv({ cls: "kgd-lookup-hint", text: "No relation or requires link joins it to another record." });
      return;
    }
    const shown = graph.shown < graph.total ? `The ${graph.shown} nearest of ${graph.total} records. ` : "";
    pane.createDiv({
      cls: "kgd-lookup-hint",
      text: `${shown}A relation is an edge; statements about one record are under About it. Click a record or an edge to look at it; scroll over the graph to zoom.`,
    });
    const container = pane.createDiv({ cls: "kgd-lookup-graph" });
    this.neighbours = cytoscape({
      container,
      elements: graph.elements,
      layout: FCOSE_LAYOUT,
      style: graphStylesheet(this.contentEl),
      // Over the graph the wheel zooms, as in the graph view; elsewhere it scrolls the panel.
      wheelSensitivity: 0.22,
      minZoom: 0.15,
      maxZoom: 3,
    });
    this.neighbours.on("tap", "node, edge", (event: EventObject) => {
      const data = event.target.data() as GraphElementData;
      if (data.uid && data.element !== "dangling" && data.uid !== this.focus) void this.show(data.uid);
    });
  }

  private clearNeighbours(): void {
    this.neighbours?.destroy();
    this.neighbours = null;
  }

  private renderChain(list: HTMLElement, nodes: readonly ChainNode[]): void {
    for (const node of nodes) {
      const item = list.createEl("li");
      if (node.uid === undefined) {
        item.createSpan({ cls: "kgd-pending-term", text: node.label });
        item.createSpan({ cls: "kgd-lookup-meta", text: " · pending" });
        continue;
      }
      this.recordLink(item, node.uid, node.label, node.exists);
      if (node.kind) item.createSpan({ cls: "kgd-lookup-meta", text: ` · ${node.kind}` });
      if (node.repeated) item.createSpan({ cls: "kgd-lookup-meta", text: " · shown above" });
      if (node.children.length > 0) this.renderChain(item.createEl("ul"), node.children);
    }
  }

  private recordLink(container: HTMLElement, uid: string, label: string, exists: boolean): void {
    if (!exists) {
      container.createSpan({ cls: "kgd-lookup-missing", text: `${label} (missing)` });
      return;
    }
    const link = container.createEl("a", { text: label, href: "#" });
    link.addEventListener("click", (event) => {
      event.preventDefault();
      void this.show(uid);
    });
  }

  private row(pane: HTMLElement, label: string, value: string): void {
    const row = pane.createDiv({ cls: "kgd-detail-row" });
    row.createEl("strong", { text: `${label}: ` });
    row.createSpan({ text: value });
  }

  /** Render the record's prose and Evidence quotes; records of other bases render the same way. */
  private async renderMarkdown(record: FullRecord, body: HTMLElement): Promise<void> {
    const request = this.focusRequest;
    if (this.renderComponent) this.removeChild(this.renderComponent);
    const component = this.addChild(new Component());
    this.renderComponent = component;
    if (record.body) await MarkdownRenderer.render(this.app, record.body, body.createDiv({ cls: "kgd-prose" }), "", component);
    if (request !== this.focusRequest || record.evidence.length === 0) return;
    body.createEl("h4", { text: "Evidence" });
    const quotes = record.evidence.map((quote) => quote.split("\n").map((line) => `> ${line}`).join("\n")).join("\n\n");
    await MarkdownRenderer.render(this.app, quotes, body.createDiv({ cls: "kgd-evidence" }), "", component);
  }

  /**
   * Open a base-relative path: in this vault when the record's base is this
   * vault, otherwise through an obsidian:// link to the file in its own vault.
   * `line` is 1-based. (Not named `open`: that would replace Obsidian's
   * internal `View.open`, and the view would never be mounted.)
   */
  private async openInBase(base: string, path: string, line?: number): Promise<void> {
    let roots: Map<string, string>;
    try {
      roots = await this.host.kgd.baseRoots();
    } catch (error) {
      new Notice(`kgdistiller cannot list the bases: ${error instanceof Error ? error.message : String(error)}`);
      return;
    }
    const root = roots.get(base);
    if (root === undefined) {
      new Notice(`kgdistiller does not know base ${base}.`);
      return;
    }
    if (root === this.host.vaultRoot()) {
      const file = this.app.vault.getAbstractFileByPath(normalizePath(path));
      if (!(file instanceof TFile)) {
        new Notice(`kgdistiller cannot open ${path}: it is not in the vault index.`);
        return;
      }
      await this.app.workspace.getLeaf(false).openFile(file, line === undefined ? undefined : { eState: { line: line - 1 } });
      return;
    }
    window.open(`obsidian://open?path=${encodeURIComponent(`${root}/${path}`)}`);
  }
}
