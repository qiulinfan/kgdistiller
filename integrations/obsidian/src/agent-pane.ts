/**
 * The agent's part of the lookup sidebar: the judgment of a selection, the
 * Capture action, the sources of an unrecorded term, and the answer to a
 * question typed into the search box. The deterministic results below it
 * appear first; the agent's part follows. Capture hands the selection to the
 * Capture view, which drafts it; nothing is accepted until the owner accepts
 * the draft there.
 */

import { Notice } from "obsidian";

import type { CaptureFiles } from "./capture";
import type { ModelClient } from "./model";
import { judgeSelection, type Judgment } from "./judge";
import type { KgdServer } from "./kgd";
import type { RecordSummary } from "./lookup-model";
import { findOrigin, type Origin, type OriginServices } from "./origin";
import { answerMarkdown, searchKnowledge, stepText, type Reading, type SearchAnswer } from "./search";
import { arxivId, citedReferences, sourceOpening, type Encyclopedia, type Fetch, type Reference } from "./sources";
import type { WebSource } from "./web";

/** The selected passage a lookup started from. */
export interface Selection {
  /** The selected text as the lookup query. */
  text: string;
  /** The lines around the selection, or its block in reading view. */
  context: string;
  /** Where the selection lies, when its source file and lines are known. */
  source?: { path: string; from: number; to: number };
  /** The selection was made in the lookup sidebar or the Capture view, in a record or draft. */
  side?: boolean;
}

export interface AgentHost {
  kgd: KgdServer;
  model: ModelClient;
  captureFiles: CaptureFiles;
  fetchText: Fetch;
  webSearch(query: string): Promise<WebSource[]>;
  /** The registered source in the active editor or reading view, if any. */
  reading(): Promise<Reading | undefined>;
  /** Draft the selection in the Capture view. */
  capture(selection: Selection): void;
}

export class AgentPane {
  private selection: Selection | undefined;
  private request = 0;
  private readonly verdictEl: HTMLElement;
  private readonly answerEl: HTMLElement;
  private readonly actionsEl: HTMLElement;
  private readonly sourcesEl: HTMLElement;

  constructor(
    container: HTMLElement,
    private readonly host: AgentHost,
    private readonly labels: (uid: string) => string | undefined,
    private readonly show: (uid: string) => void,
    private readonly renderMarkdown: (markdown: string, element: HTMLElement) => Promise<void>,
  ) {
    container.addClass("kgd-lookup-agent");
    this.verdictEl = container.createDiv({ cls: "kgd-lookup-verdict" });
    this.answerEl = container.createDiv({ cls: "kgd-lookup-answer" });
    this.actionsEl = container.createDiv({ cls: "kgd-lookup-actions" });
    this.sourcesEl = container.createDiv({ cls: "kgd-lookup-sources" });
  }

  /** Start over for a new selection or question; a selection's actions appear at once, and without one (a listed source) nothing shows. */
  reset(selection: Selection | undefined, question = false): void {
    this.selection = selection;
    this.request++;
    for (const element of [this.verdictEl, this.answerEl, this.actionsEl, this.sourcesEl]) element.empty();
    if (!selection || !this.host.model.configured() || question) return;
    if (selection.source || selection.side) this.button("Capture", true, (button) => this.capture(button));
    this.button("Find sources", false, (button) => this.sources(button));
  }

  /** Answer a question from the knowledge base and the web, showing each step as it runs. */
  async ask(question: string): Promise<void> {
    if (!this.host.model.configured()) return;
    const request = this.request;
    const element = this.answerEl;
    element.empty();
    const steps = element.createEl("ul", { cls: "kgd-lookup-steps" });
    const status = element.createDiv({ cls: "kgd-lookup-hint", text: "Searching…" });
    const started = performance.now();
    let answer: SearchAnswer;
    try {
      const reading = await this.host.reading();
      answer = await searchKnowledge((chat) => this.host.model.complete(chat), this.host.kgd, (query) => this.host.webSearch(query), question, {
        ...(reading ? { reading } : {}),
        onStep: (step) => { if (request === this.request && step.tool !== "answer") steps.createEl("li", { text: stepText(step) }); },
      });
    } catch (error) {
      if (request !== this.request) return;
      status.remove();
      element.createDiv({ cls: "kgd-lookup-error", text: `The agent could not answer: ${message(error)}` });
      return;
    }
    if (request !== this.request) return;
    status.remove();
    await this.renderAnswer(answer, performance.now() - started);
  }

  private async renderAnswer(answer: SearchAnswer, ms: number): Promise<void> {
    const element = this.answerEl;
    const head = element.createDiv({ cls: "kgd-lookup-verdict-line is-answer" });
    head.createSpan({ cls: "kgd-lookup-verdict-head", text: "Answer" });
    head.createSpan({ cls: "kgd-lookup-meta", text: ` · ${seconds(ms)}` });
    const body = element.createDiv({ cls: "kgd-lookup-answer-body" });
    await this.renderMarkdown(answerMarkdown(answer), body);
    // Record citations are links to "#kgd-record-N"; they focus the record here instead of navigating.
    body.addEventListener("click", (event) => {
      const link = (event.target as HTMLElement | null)?.closest("a");
      const target = link?.getAttribute("data-href") ?? link?.getAttribute("href") ?? "";
      const match = /^#kgd-record-(\d+)$/.exec(target);
      const record = match ? answer.records[Number(match[1]) - 1] : undefined;
      if (!record) return;
      event.preventDefault();
      event.stopPropagation();
      this.show(record.uid);
    }, true);
    if (answer.records.length > 0) {
      const section = element.createDiv({ cls: "kgd-lookup-section" });
      section.createEl("h4", { text: "From the knowledge base" });
      const list = section.createEl("ul", { cls: "kgd-lookup-citations" });
      for (const [index, record] of answer.records.entries()) {
        const item = list.createEl("li");
        item.createSpan({ cls: "kgd-lookup-meta", text: `K${index + 1} ` });
        const link = item.createEl("a", { text: record.label, href: "#" });
        link.addEventListener("click", (event) => {
          event.preventDefault();
          this.show(record.uid);
        });
      }
    }
    // Papers, linked to arXiv, are listed apart from other web pages.
    const papers = answer.web.filter((source) => arxivId(source.url) !== undefined);
    const pages = answer.web.filter((source) => arxivId(source.url) === undefined);
    for (const [title, sources] of [["Papers", papers], ["From the web", pages]] as const) {
      if (sources.length === 0) continue;
      const section = element.createDiv({ cls: "kgd-lookup-section" });
      section.createEl("h4", { text: title });
      const list = section.createEl("ul", { cls: "kgd-lookup-citations" });
      for (const source of sources) {
        const item = list.createEl("li");
        item.createSpan({ cls: "kgd-lookup-meta", text: `W${source.number} ` });
        item.createEl("a", { text: source.title, href: source.url });
        const id = arxivId(source.url);
        if (id) item.createSpan({ cls: "kgd-lookup-meta", text: ` · arXiv:${id}` });
      }
    }
  }

  /** Judge the selection against the lookup's candidates and show the verdict. */
  async judge(candidates: RecordSummary[]): Promise<void> {
    const selection = this.selection;
    if (!selection || !this.host.model.configured()) return;
    const request = this.request;
    this.verdictEl.empty();
    this.verdictEl.createSpan({ cls: "kgd-lookup-hint", text: "Judging…" });
    const started = performance.now();
    let judgment: Judgment;
    try {
      judgment = await judgeSelection((chat) => this.host.model.complete(chat), { selection: selection.text, context: selection.context, candidates });
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

  /** Hand the selection to the Capture view, once per lookup. */
  private async capture(button: HTMLButtonElement): Promise<void> {
    const selection = this.selection;
    if (!selection?.source && !selection?.side) return;
    button.disabled = true;
    button.setText("Drafting in Capture");
    this.host.capture(selection);
  }

  /** The capture of this selection found it borrowed: find where it comes from, unless that is already shown. */
  async sourcesFor(selection: Selection): Promise<void> {
    if (selection !== this.selection || this.sourcesEl.childElementCount > 0) return;
    await this.sources();
  }

  /**
   * Find where the selected term comes from: the references the selection
   * cites at once, then the work the agent judges to have introduced the term
   * and the Wikipedia article it judges to be about it.
   */
  private async sources(button?: HTMLButtonElement): Promise<void> {
    const selection = this.selection;
    if (!selection) return;
    const request = this.request;
    if (button) button.disabled = true;
    const element = this.sourcesEl;
    element.empty();
    const started = performance.now();
    const section = element.createDiv({ cls: "kgd-lookup-section" });
    section.createEl("h4", { text: "Where it comes from" });
    const status = section.createDiv({ cls: "kgd-lookup-hint", text: "Searching the web and Wikipedia, then judging…" });
    const lines = selection.source
      ? (await this.host.captureFiles.read(selection.source.path).catch(() => "")).replace(/\r\n?/g, "\n").split("\n")
      : [];
    const source = selection.source && lines.some((line) => line.trim()) ? sourceOpening(lines, selection.source.path) : undefined;
    const references = selection.source ? citedReferences(lines.slice(selection.source.from - 1, selection.source.to).join("\n"), lines) : [];
    if (references.length > 0) this.renderReferences(references);
    let origin: Origin;
    let failures: string[];
    try {
      ({ origin, failures } = await findOrigin(this.originServices(),
        { term: selection.text, context: selection.context, ...(source ? { source } : {}), references }));
    } catch (error) {
      if (request !== this.request) return;
      status.remove();
      section.createDiv({ cls: "kgd-lookup-error", text: `The agent could not find where it comes from: ${message(error)}` });
      if (button) button.disabled = false;
      return;
    }
    if (request !== this.request) return;
    status.remove();
    this.renderOrigin(section, origin, performance.now() - started);
    this.renderWikipedia(section, origin.wikipedia);
    for (const failure of failures) section.createDiv({ cls: "kgd-lookup-hint", text: failure });
  }

  private originServices(): OriginServices {
    return { complete: (chat) => this.host.model.complete(chat), fetch: this.host.fetchText, webSearch: (query) => this.host.webSearch(query) };
  }

  private renderOrigin(element: HTMLElement, origin: Origin, ms: number): void {
    const line = element.createDiv({ cls: `kgd-lookup-verdict-line${origin.work ? " is-origin" : ""}` });
    const head = {
      source: "Introduced by this source: ",
      reference: `Introduced by [${origin.reference ?? ""}]: `,
      web: "Introduced by: ",
      none: "No originating work found",
    }[origin.choice];
    line.createSpan({ cls: "kgd-lookup-verdict-head", text: head });
    const work = origin.work;
    if (work) {
      if (work.url) line.createEl("a", { text: work.title, href: work.url });
      else line.createSpan({ text: work.title });
      const where = work.arxiv ? `arXiv:${work.arxiv}` : origin.arxivError ? `arXiv did not answer: ${origin.arxivError}` : "not on arXiv";
      line.createSpan({ cls: "kgd-lookup-meta", text: ` · ${[work.year, where].filter(Boolean).join(" · ")}` });
    }
    line.createSpan({ cls: "kgd-lookup-meta", text: ` · ${seconds(ms)}` });
    if (origin.reason) element.createDiv({ cls: "kgd-lookup-explanation", text: origin.reason });
  }

  private renderWikipedia(element: HTMLElement, wikipedia: Encyclopedia | undefined): void {
    if (!wikipedia) {
      element.createDiv({ cls: "kgd-lookup-hint", text: "No Wikipedia article is about it in this sense." });
      return;
    }
    const item = element.createDiv({ cls: "kgd-lookup-wikipedia" });
    item.createEl("a", { text: `Wikipedia: ${wikipedia.title}${wikipedia.section ? ` § ${wikipedia.section}` : ""}`, href: wikipedia.url });
    if (wikipedia.zh) {
      item.createSpan({ text: " · " });
      item.createEl("a", { text: wikipedia.zh.title, href: wikipedia.zh.url });
    }
    if (wikipedia.extract) item.createDiv({ cls: "kgd-lookup-gloss", text: wikipedia.extract });
    if (wikipedia.cites) {
      const cites = item.createDiv();
      cites.createSpan({ cls: "kgd-lookup-meta", text: "It cites first: " });
      cites.createEl("a", { text: wikipedia.cites.title || `arXiv:${wikipedia.cites.arxiv}`, href: wikipedia.cites.url });
      cites.createSpan({ cls: "kgd-lookup-meta", text: ` · arXiv:${wikipedia.cites.arxiv}` });
    }
    item.createDiv({ cls: "kgd-lookup-hint", text: "Text from Wikipedia, CC BY-SA 4.0." });
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
      if (reference.url && !arxivId(reference.url)) item.createSpan({ cls: "kgd-lookup-meta", text: " · not on arXiv" });
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
