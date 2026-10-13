/**
 * The agent's part of the lookup sidebar: the judgment of the selection, the
 * Capture action and the sources of an unrecorded term. The deterministic
 * results above it appear first; the judgment follows about a second later,
 * and nothing is written until the owner presses Capture.
 */

import { Notice } from "obsidian";

import { captureSelection, type CaptureFiles, type CaptureResult } from "./capture";
import type { ModelGateway } from "./gateway";
import { judgeSelection, type Judgment } from "./judge";
import type { KgdServer } from "./kgd";
import type { RecordSummary } from "./lookup-model";
import { citedReferences, findSources, type GetJson, type Reference, type Sources } from "./sources";

/** The selected passage a lookup started from. */
export interface Selection {
  /** The selected text as the lookup query. */
  text: string;
  /** The lines around the selection, or its block in reading view. */
  context: string;
  /** Where the selection lies, when its source file and lines are known. */
  source?: { path: string; from: number; to: number };
}

export interface AgentHost {
  kgd: KgdServer;
  gateway: ModelGateway;
  captureFiles: CaptureFiles;
  getJson: GetJson;
}

const DECISIONS: Record<CaptureResult["decision"], string> = {
  new: "Captured",
  unchanged: "Already recorded",
  superseding: "Would overwrite (not written)",
  conflicting: "Conflicts with a record (not written)",
  borrowed: "Belongs to other work (not written)",
};

export class AgentPane {
  private selection: Selection | undefined;
  private request = 0;
  private readonly verdictEl: HTMLElement;
  private readonly actionsEl: HTMLElement;
  private readonly captureEl: HTMLElement;
  private readonly sourcesEl: HTMLElement;

  constructor(
    container: HTMLElement,
    private readonly host: AgentHost,
    private readonly labels: (uid: string) => string | undefined,
    private readonly show: (uid: string) => void,
  ) {
    container.addClass("kgd-lookup-agent");
    this.verdictEl = container.createDiv({ cls: "kgd-lookup-verdict" });
    this.actionsEl = container.createDiv({ cls: "kgd-lookup-actions" });
    this.captureEl = container.createDiv({ cls: "kgd-lookup-capture" });
    this.sourcesEl = container.createDiv({ cls: "kgd-lookup-sources" });
  }

  /** Start over for a new selection; the actions appear at once. */
  reset(selection: Selection): void {
    this.selection = selection;
    this.request++;
    for (const element of [this.verdictEl, this.actionsEl, this.captureEl, this.sourcesEl]) element.empty();
    if (!this.host.gateway.configured()) return;
    if (selection.source) this.button("Capture", true, (button) => this.capture(button));
    this.button("Find sources", false, (button) => this.sources(button));
  }

  /** Judge the selection against the lookup's candidates and show the verdict. */
  async judge(candidates: RecordSummary[]): Promise<void> {
    const selection = this.selection;
    if (!selection || !this.host.gateway.configured()) return;
    const request = this.request;
    this.verdictEl.empty();
    this.verdictEl.createSpan({ cls: "kgd-lookup-hint", text: "Judging…" });
    const started = performance.now();
    let judgment: Judgment;
    try {
      judgment = await judgeSelection((chat) => this.host.gateway.complete(chat), { selection: selection.text, context: selection.context, candidates });
    } catch (error) {
      if (request !== this.request) return;
      this.verdictEl.empty();
      this.verdictEl.createDiv({ cls: "kgd-lookup-error", text: `The agent could not judge: ${message(error)}` });
      return;
    }
    if (request !== this.request) return;
    this.renderJudgment(judgment, performance.now() - started);
    if (judgment.verdict === "matched" && judgment.records[0]) this.show(judgment.records[0]);
  }

  private renderJudgment(judgment: Judgment, ms: number): void {
    const element = this.verdictEl;
    element.empty();
    const line = element.createDiv({ cls: `kgd-lookup-verdict-line is-${judgment.verdict}` });
    const heading = { matched: "Recorded: ", ambiguous: "Possibly: ", unmatched: "Not recorded" }[judgment.verdict];
    line.createSpan({ cls: "kgd-lookup-verdict-head", text: heading });
    judgment.records.forEach((uid, index) => {
      if (index > 0) line.createSpan({ text: " · " });
      const link = line.createEl("a", { text: this.labels(uid) ?? uid, href: "#" });
      link.addEventListener("click", (event) => {
        event.preventDefault();
        this.show(uid);
      });
    });
    line.createSpan({ cls: "kgd-lookup-meta", text: ` · ${seconds(ms)}` });
    if (judgment.explanation) element.createDiv({ cls: "kgd-lookup-explanation", text: judgment.explanation });
  }

  /** Press Capture for the current selection, as the Capture selection command does. */
  async startCapture(): Promise<void> {
    const button = this.actionsEl.querySelector<HTMLButtonElement>("button.mod-cta");
    if (button && !button.disabled) await this.capture(button);
  }

  private async capture(button: HTMLButtonElement): Promise<void> {
    const selection = this.selection;
    if (!selection?.source) return;
    const request = this.request;
    button.disabled = true;
    this.captureEl.empty();
    const status = this.captureEl.createDiv({ cls: "kgd-lookup-hint", text: "Capturing…" });
    const started = performance.now();
    try {
      const result = await captureSelection((chat) => this.host.gateway.complete(chat), this.host.kgd, this.host.captureFiles,
        { ...selection.source, selection: selection.text });
      const ms = performance.now() - started;
      new Notice(`kgdistiller: ${DECISIONS[result.decision]}${result.label ? `: ${result.label}` : ""}`);
      if (request !== this.request) return;
      status.remove();
      this.renderCapture(result, ms);
      if (result.decision === "borrowed" && this.sourcesEl.childElementCount === 0) await this.sources();
    } catch (error) {
      new Notice(`kgdistiller could not capture: ${message(error)}`);
      if (request !== this.request) return;
      status.remove();
      this.captureEl.createDiv({ cls: "kgd-lookup-error", text: `The agent could not capture: ${message(error)}` });
      button.disabled = false;
    }
  }

  private renderCapture(result: CaptureResult, ms: number): void {
    const element = this.captureEl;
    const line = element.createDiv({ cls: `kgd-lookup-capture-line is-${result.decision}` });
    line.createSpan({ cls: "kgd-lookup-verdict-head", text: `${DECISIONS[result.decision]}${result.uid ? ": " : ""}` });
    if (result.uid) {
      const uid = result.uid;
      const link = line.createEl("a", { text: result.label ?? uid, href: "#" });
      link.addEventListener("click", (event) => {
        event.preventDefault();
        this.show(uid);
      });
    }
    line.createSpan({ cls: "kgd-lookup-meta", text: ` · ${seconds(ms)}` });
    if (result.report) element.createDiv({ cls: "kgd-lookup-explanation", text: result.report });
    if (result.proposal) {
      const details = element.createEl("details", { cls: "kgd-lookup-proposal" });
      details.createEl("summary", { text: "The record this passage proposes" });
      details.createEl("pre", { text: result.proposal });
    }
  }

  private async sources(button?: HTMLButtonElement): Promise<void> {
    const selection = this.selection;
    if (!selection) return;
    const request = this.request;
    if (button) button.disabled = true;
    const element = this.sourcesEl;
    element.empty();
    let cited: Reference[] = [];
    if (selection.source) {
      try {
        const lines = (await this.host.captureFiles.read(selection.source.path)).replace(/\r\n?/g, "\n").split("\n");
        cited = citedReferences(lines.slice(selection.source.from - 1, selection.source.to).join("\n"), lines);
      } catch {
        cited = [];
      }
    }
    if (cited.length > 0) this.renderReferences(cited);
    const status = element.createDiv({ cls: "kgd-lookup-hint", text: "Searching OpenAlex and Wikipedia…" });
    let found: Sources;
    try {
      found = await findSources(selection.text, this.host.getJson);
    } catch (error) {
      if (request !== this.request) return;
      status.setText(`The web search failed: ${message(error)}`);
      if (button) button.disabled = false;
      return;
    }
    if (request !== this.request) return;
    status.remove();
    this.renderSources(found);
  }

  private renderReferences(references: Reference[]): void {
    const section = this.sourcesEl.createDiv({ cls: "kgd-lookup-section" });
    section.createEl("h4", { text: "Cited here" });
    section.createDiv({ cls: "kgd-lookup-hint", text: "The works this passage cites, from the source's reference list." });
    const list = section.createEl("ul", { cls: "kgd-lookup-links" });
    for (const reference of references) {
      const item = list.createEl("li");
      item.createSpan({ cls: "kgd-lookup-meta", text: `[${reference.number}] ` });
      if (reference.url) item.createEl("a", { text: reference.text, href: reference.url });
      else item.createSpan({ text: reference.text });
    }
  }

  private renderSources(found: Sources): void {
    const section = this.sourcesEl.createDiv({ cls: "kgd-lookup-section" });
    section.createEl("h4", { text: "On the web" });
    if (found.papers.length === 0 && !found.wikipedia) {
      section.createDiv({ cls: "kgd-empty-state", text: "OpenAlex and Wikipedia found nothing for this term." });
      return;
    }
    if (found.papers.length > 0) {
      section.createDiv({ cls: "kgd-lookup-hint", text: "Likely originating papers, from OpenAlex, best first." });
      const list = section.createEl("ul", { cls: "kgd-lookup-links" });
      for (const paper of found.papers) {
        const item = list.createEl("li");
        item.createEl("a", { text: paper.title, href: paper.url });
        item.createSpan({ cls: "kgd-lookup-meta", text: ` · ${paper.year} · cited ${paper.citations}` });
      }
    }
    const wikipedia = found.wikipedia;
    if (wikipedia) {
      const item = section.createDiv({ cls: "kgd-lookup-wikipedia" });
      item.createEl("a", { text: `Wikipedia: ${wikipedia.title}${wikipedia.section ? ` § ${wikipedia.section}` : ""}`, href: wikipedia.url });
      if (wikipedia.zh) {
        item.createSpan({ text: " · " });
        item.createEl("a", { text: wikipedia.zh.title, href: wikipedia.zh.url });
      }
      if (wikipedia.extract) item.createDiv({ cls: "kgd-lookup-gloss", text: wikipedia.extract });
      item.createDiv({ cls: "kgd-lookup-hint", text: "Text from Wikipedia, CC BY-SA 4.0." });
    }
  }

  private button(text: string, primary: boolean, run: (button: HTMLButtonElement) => Promise<void>): void {
    const button = this.actionsEl.createEl("button", { text, cls: primary ? "mod-cta" : "" });
    button.addEventListener("click", () => void run(button));
  }
}

function seconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)} s`;
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
