/**
 * The Capture view: the agent's captures and localizations while it drafts
 * them, and every draft waiting for the owner, listed by source. Every way
 * knowledge enters the base ends in a draft here (docs/model.md, "Ingestion"):
 * a capture, a compile and a replacement of a recorded item; a localization
 * only moves a record's citation, with `kgd relocate`. The agent never
 * accepts. A draft appears in full as `kg_drafts` reads it
 * from the file, with each value's target, whether it is foreign or replaces
 * an accepted record, and what `kgd accept` would refuse. Accept admits it
 * with the drafts it links (`kgd accept`, which indexes and embeds them);
 * Open draft opens the file beside the source for editing; Discard deletes
 * it. The left of the view lists the sources the drafts cite, by file name,
 * and every foreign draft under "foreign". The view lists the drafts again
 * whenever a draft file of this vault changes, so an edit is checked as soon
 * as it is saved. Record ids and uids are never shown.
 */

import { Component, ItemView, MarkdownRenderer, Modal, Notice, Setting, SuggestModal, setIcon, type App, type WorkspaceLeaf } from "obsidian";

import type { Selection } from "./agent-pane";
import { captureSelection, type CaptureFiles, type CaptureResult } from "./capture";
import {
  acceptanceGroup, drawIntoDraft, editableRoles, groupProblems, linkFor, RECORD_DRAG_TYPE, roleLinks, sourceGroups, valuesByRole, valueText,
  type Draft, type DraftsResult,
} from "./drafts";
import type { SearchResult } from "./lookup-model";
import { INDEX_TIMEOUT_MS, type KgdServer } from "./kgd";
import type { Indexer } from "./indexing";
import { draftTarget, localizeRecord, type LocalizeInput, type LocalizeTarget } from "./localize";
import type { ModelClient } from "./model";
import { openInBase } from "./open";
import type { OriginServices } from "./origin";
import { firstLine } from "./records";

export const VIEW_TYPE_KGDISTILLER_CAPTURE = "kgdistiller-capture-view";
export const CAPTURE_ICON = "inbox";
/** Draft files saved in quick succession are listed again once. */
const RELIST_DELAY_MS = 300;
const DRAFTS_FOLDER = ".knowledge/drafts/";
/** The source list's entry for every draft. */
const ALL = "";

export interface CaptureHost {
  app: App;
  kgd: KgdServer;
  model: ModelClient;
  captureFiles: CaptureFiles;
  /** The model, the fetcher and the web search that find where a borrowed concept comes from. */
  originServices(): OriginServices;
  /** The registered source open in the main area, whose base and type a side-pane capture uses. */
  readingPath(): Promise<string | undefined>;
  /** The current selection, as a lookup reads it. */
  currentSelection(): Promise<Selection | undefined>;
  /** The real path of this vault's root, when the vault lives on the local filesystem. */
  vaultRoot(): string | undefined;
  /** A capture found the item borrowed and drafted nothing: show where it comes from in the lookup sidebar. */
  borrowed(selection: Selection): void;
  indexer: Indexer;
}

/** What a finished job says: a headline, the agent's words, and the draft it wrote, if any. */
interface Outcome {
  headline: string;
  report: string;
  /** The draft written, which takes the job's place in the list. */
  draft?: string;
  /** The record a changed meaning proposes when it cannot be drafted here, as a file. */
  proposal?: string;
}

/** One capture or localization: running until `outcome` or `error` arrives. */
interface Job {
  title: string;
  started: number;
  ms?: number;
  outcome?: Outcome;
  error?: string;
}

/** What the agent said about a draft it wrote in this session. */
interface Note {
  report: string;
  ms: number;
}

/** How a capture's decision reads when it wrote no new draft of its own. */
function captureOutcome(result: CaptureResult): Outcome {
  const label = result.label ? `: ${result.label}` : "";
  if (result.decision === "unchanged") {
    return { headline: `${result.draft ? "Already a draft" : "Already recorded"}${label}`, report: result.report };
  }
  if (result.draft && result.uid) {
    const what = result.foreign ? "Drafted as foreign knowledge" : result.replaces ? "Drafted a replacement" : "Drafted";
    return { headline: `${what}${label}`, report: result.report, draft: result.uid };
  }
  if (result.decision === "borrowed") return { headline: `Belongs to other work, not drafted${label}`, report: result.report };
  return { headline: `Changes a record of another base, not drafted${label}`, report: result.report, ...(result.proposal ? { proposal: result.proposal } : {}) };
}

export class KgdistillerCaptureView extends ItemView {
  private jobs: Job[] = [];
  private drafts: Draft[] = [];
  private listError: string | undefined;
  private readonly notes = new Map<string, Note>();
  private readonly expanded = new Set<string>();
  /** Drafts being accepted, discarded or localized; their buttons wait. */
  private readonly busy = new Set<string>();
  /** The source whose drafts are shown; ALL shows every draft. */
  private chosen = ALL;
  private listRequest = 0;
  private relistTimer: number | undefined;
  private ticker: number | undefined;
  private renderComponent: Component | null = null;
  private countEl: HTMLElement | null = null;
  private jobsEl: HTMLElement | null = null;
  private sourcesEl: HTMLElement | null = null;
  private draftsEl: HTMLElement | null = null;

  constructor(leaf: WorkspaceLeaf, private readonly host: CaptureHost) {
    super(leaf);
  }

  getViewType(): string {
    return VIEW_TYPE_KGDISTILLER_CAPTURE;
  }

  getDisplayText(): string {
    return "kgdistiller Capture";
  }

  getIcon(): string {
    return CAPTURE_ICON;
  }

  async onOpen(): Promise<void> {
    this.contentEl.empty();
    this.contentEl.addClass("kgd-capture-view");
    const head = this.contentEl.createDiv({ cls: "kgd-capture-head" });
    this.countEl = head.createSpan({ cls: "kgd-capture-count" });
    const relist = head.createEl("button", { cls: "clickable-icon", attr: { "aria-label": "List the drafts again" } });
    setIcon(relist, "refresh-cw");
    relist.addEventListener("click", () => void this.listDrafts());
    this.jobsEl = this.contentEl.createDiv({ cls: "kgd-capture-jobs" });
    // A record dragged here from the lookup sidebar is drawn into a draft for revision.
    this.registerDomEvent(this.contentEl, "dragover", (event) => {
      if (!event.dataTransfer?.types.includes(RECORD_DRAG_TYPE)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
      this.contentEl.addClass("is-drop-target");
    });
    this.registerDomEvent(this.contentEl, "dragleave", (event) => {
      if (!this.contentEl.contains(event.relatedTarget as Node | null)) this.contentEl.removeClass("is-drop-target");
    });
    this.registerDomEvent(this.contentEl, "drop", (event) => {
      this.contentEl.removeClass("is-drop-target");
      const uid = event.dataTransfer?.getData(RECORD_DRAG_TYPE);
      if (!uid) return;
      event.preventDefault();
      void this.drawRecord(uid);
    });
    const body = this.contentEl.createDiv({ cls: "kgd-capture-body" });
    this.sourcesEl = body.createDiv({ cls: "kgd-capture-sources" });
    this.draftsEl = body.createDiv({ cls: "kgd-capture-drafts" });
    // A draft edited in the editor, or written or removed elsewhere in this vault, is checked again once saved.
    this.registerEvent(this.app.vault.on("create", (file) => this.draftFileChanged(file.path)));
    this.registerEvent(this.app.vault.on("modify", (file) => this.draftFileChanged(file.path)));
    this.registerEvent(this.app.vault.on("delete", (file) => this.draftFileChanged(file.path)));
    this.registerEvent(this.app.vault.on("rename", (file, old) => {
      this.draftFileChanged(file.path);
      this.draftFileChanged(old);
    }));
    await this.listDrafts();
  }

  async onClose(): Promise<void> {
    window.clearTimeout(this.relistTimer);
    window.clearInterval(this.ticker);
    this.renderComponent?.unload();
  }

  private draftFileChanged(path: string): void {
    if (!path.startsWith(DRAFTS_FOLDER)) return;
    window.clearTimeout(this.relistTimer);
    this.relistTimer = window.setTimeout(() => void this.listDrafts(), RELIST_DELAY_MS);
  }

  /**
   * Draft the selection in the background; its draft, or what the agent
   * decided instead, appears here. A selection made in a source is drafted
   * from its lines; one made in a side pane, in a record or draft, can only be
   * drafted as foreign knowledge, in the base and type of the source being read.
   */
  async startCapture(selection: Selection): Promise<void> {
    const path = selection.source?.path ?? await this.host.readingPath();
    if (!path) {
      new Notice("Open the registered source you are reading in the main area to capture a selection made beside it.");
      return;
    }
    const input = selection.source
      ? { path, from: selection.source.from, to: selection.source.to, selection: selection.text }
      : { path, selection: selection.text, context: selection.context };
    await this.run(selection.text, async () => {
      const result = await captureSelection((chat) => this.host.model.complete(chat), this.host.kgd, this.host.captureFiles,
        this.host.originServices(), input);
      if (result.decision === "borrowed" && !result.draft) this.host.borrowed(selection);
      return captureOutcome(result);
    });
  }

  /** Draw an accepted record into a draft with its id, opened here for revision; accepting it replaces the record. */
  async drawRecord(uid: string): Promise<void> {
    try {
      const { created } = await drawIntoDraft(await this.host.kgd.baseRoots(), this.host.captureFiles, uid);
      this.expanded.add(uid);
      new Notice(created
        ? "kgdistiller drew the record into a draft: edit it, then accept it to replace the record."
        : "A draft of this record already waits in Capture.");
    } catch (error) {
      new Notice(`kgdistiller could not draw the record into a draft: ${message(error)}`);
    }
    await this.listDrafts();
  }

  /** Move a foreign record's or draft's citation to a passage of a local source with `kgd relocate`. */
  async startLocalize(target: LocalizeTarget, input: LocalizeInput): Promise<void> {
    await this.run(`Localize ${target.label}`, async () => {
      const result = await localizeRecord((chat) => this.host.model.complete(chat), this.host.kgd, this.host.captureFiles, target, input);
      if (!result.localized) return { headline: `Not localized: ${result.label}`, report: result.report };
      await this.listDrafts();
      return { headline: `Localized: ${result.label}`, report: result.report };
    });
  }

  private async run(title: string, work: () => Promise<Outcome>): Promise<void> {
    const job: Job = { title, started: performance.now() };
    this.jobs.unshift(job);
    this.renderJobs();
    try {
      job.outcome = await work();
    } catch (error) {
      job.error = message(error);
      new Notice(`kgdistiller could not finish: ${job.error}`);
    }
    job.ms = performance.now() - job.started;
    const outcome = job.outcome;
    if (outcome?.draft) {
      // The draft itself takes the job's place.
      this.notes.set(outcome.draft, { report: outcome.report, ms: job.ms });
      this.expanded.add(outcome.draft);
      this.jobs = this.jobs.filter((other) => other !== job);
      new Notice(`kgdistiller: ${outcome.headline}; it waits for you in Capture.`);
      await this.listDrafts();
    } else if (outcome) {
      new Notice(`kgdistiller: ${outcome.headline}`);
    }
    this.renderJobs();
  }

  /** List every base's drafts with kg_drafts and show them. */
  async listDrafts(): Promise<void> {
    if (!this.host.kgd.configured()) {
      this.drafts = [];
      this.listError = "Set the kgd executable in the kgdistiller settings to list drafts.";
      this.renderDrafts();
      return;
    }
    const request = ++this.listRequest;
    try {
      const listed = await this.host.kgd.call<DraftsResult>("kg_drafts", {});
      if (request !== this.listRequest) return;
      this.drafts = listed.drafts;
      this.listError = undefined;
    } catch (error) {
      if (request !== this.listRequest) return;
      this.listError = `kgd could not list the drafts: ${message(error)}`;
    }
    this.renderDrafts();
  }

  private renderJobs(): void {
    const element = this.jobsEl;
    if (!element) return;
    element.empty();
    for (const job of this.jobs) {
      const item = element.createDiv({ cls: "kgd-capture-job" });
      const line = item.createDiv({ cls: "kgd-capture-job-line" });
      const outcome = job.outcome;
      if (!outcome && !job.error) {
        line.createSpan({ cls: "kgd-lookup-hint", text: "Drafting: " });
        line.createSpan({ text: excerpt(job.title) });
        line.createSpan({ cls: "kgd-lookup-meta", attr: { "data-started": String(job.started) }, text: ` · ${seconds(performance.now() - job.started)}` });
        continue;
      }
      if (job.error) line.createSpan({ cls: "kgd-lookup-error", text: `Could not finish “${excerpt(job.title)}”: ${job.error}` });
      else if (outcome) line.createSpan({ cls: "kgd-lookup-verdict-head", text: outcome.headline });
      line.createSpan({ cls: "kgd-lookup-meta", text: ` · ${seconds(job.ms ?? 0)}` });
      const dismiss = line.createEl("button", { cls: "clickable-icon kgd-capture-dismiss", attr: { "aria-label": "Dismiss" } });
      setIcon(dismiss, "x");
      dismiss.addEventListener("click", () => {
        this.jobs = this.jobs.filter((other) => other !== job);
        this.renderJobs();
      });
      if (outcome?.report) item.createDiv({ cls: "kgd-lookup-explanation", text: outcome.report });
      if (outcome?.proposal) {
        const details = item.createEl("details", { cls: "kgd-lookup-proposal" });
        details.createEl("summary", { text: "The record this passage proposes" });
        details.createEl("pre", { text: outcome.proposal });
      }
    }
    // The drafting lines count their seconds while any job runs.
    const drafting = this.jobs.some((job) => !job.outcome && !job.error);
    if (drafting && this.ticker === undefined) {
      this.ticker = window.setInterval(() => {
        for (const meta of Array.from(element.querySelectorAll<HTMLElement>("[data-started]"))) {
          meta.setText(` · ${seconds(performance.now() - Number(meta.dataset.started))}`);
        }
      }, 1000);
    } else if (!drafting && this.ticker !== undefined) {
      window.clearInterval(this.ticker);
      this.ticker = undefined;
    }
  }

  private renderDrafts(): void {
    const element = this.draftsEl;
    const sources = this.sourcesEl;
    if (!element || !sources) return;
    if (this.renderComponent) this.removeChild(this.renderComponent);
    this.renderComponent = this.addChild(new Component());
    element.empty();
    sources.empty();
    const count = this.drafts.length;
    this.countEl?.setText(count === 0 ? "No drafts" : `${count} draft${count === 1 ? "" : "s"} to confirm`);
    if (this.listError) {
      element.createDiv({ cls: "kgd-lookup-error", text: this.listError });
      return;
    }
    if (count === 0) {
      sources.hide();
      element.createDiv({ cls: "kgd-lookup-hint", text: "Select a passage in a source, or a concept in the sidebar, and press Capture: the agent drafts it here for you to read, edit and accept." });
      return;
    }
    sources.show();
    const groups = sourceGroups(this.drafts);
    if (this.chosen !== ALL && !groups.some((group) => group.key === this.chosen)) this.chosen = ALL;
    const entry = (key: string, name: string, title: string, size: number, foreign = false) => {
      const row = sources.createDiv({ cls: `kgd-capture-source${key === this.chosen ? " is-active" : ""}${foreign ? " is-foreign" : ""}`, attr: { title } });
      row.createSpan({ cls: "kgd-capture-source-name", text: name });
      row.createSpan({ cls: "kgd-lookup-meta", text: String(size) });
      row.addEventListener("click", () => {
        this.chosen = key;
        this.renderDrafts();
      });
    };
    entry(ALL, "All", "Every draft", count);
    for (const group of groups) entry(group.key, group.name, group.title, group.drafts.length, group.key === "foreign");
    const shown = this.chosen === ALL ? this.drafts : groups.find((group) => group.key === this.chosen)?.drafts ?? [];
    for (const draft of shown) this.renderDraft(element, draft);
  }

  private renderDraft(parent: HTMLElement, draft: Draft): void {
    const group = acceptanceGroup(draft, this.drafts);
    const problems = group ? groupProblems(group) : ["a draft it links is not listed; list the drafts again"];
    const item = parent.createDiv({ cls: "kgd-capture-item" });
    const head = item.createDiv({ cls: "kgd-capture-item-head" });
    head.createSpan({ cls: "kgd-capture-name", text: draft.label ?? "A draft kgd cannot read" });
    if (draft.kind) head.createSpan({ cls: `kgd-kind kgd-kind-${draft.class ?? "node"}`, text: draft.kind });
    if (draft.foreign) head.createSpan({ cls: "kgd-capture-badge", text: "foreign" });
    if (draft.replaces) head.createSpan({ cls: "kgd-capture-badge", text: "replaces" });
    head.createSpan({ cls: `kgd-capture-state ${problems.length ? "is-blocked" : "is-ready"}`, text: problems.length ? "needs a fix" : "ready" });
    const open = this.expanded.has(draft.uid);
    head.setAttr("aria-expanded", String(open));
    head.addEventListener("click", () => {
      if (open) this.expanded.delete(draft.uid);
      else this.expanded.add(draft.uid);
      this.renderDrafts();
    });
    if (open) this.renderCard(item, draft, group ?? [draft], problems);
  }

  private renderCard(item: HTMLElement, draft: Draft, group: Draft[], problems: string[]): void {
    const card = item.createDiv({ cls: "kgd-capture-card" });
    if (draft.label !== null) {
      const where = card.createDiv({ cls: "kgd-lookup-meta" });
      where.appendText(`${draft.class === "relation" ? "Relation" : "Node"} · `);
      if (draft.foreign) {
        where.appendText("foreign · ");
        where.createEl("a", { text: draft.source ?? "", href: draft.source ?? "" });
      } else {
        const source = where.createEl("a", { text: draft.source ?? "", href: "#" });
        source.addEventListener("click", (event) => {
          event.preventDefault();
          if (draft.source) void openInBase(this.app, this.host.kgd, this.host.vaultRoot(), draft.base, draft.source, { line: firstLine(draft.lines) });
        });
        where.appendText(` · L${draft.lines ?? ""}`);
      }
      if (!this.isLocal(draft)) where.appendText(` · ${draft.base}`);
      if (draft.replaces) card.createDiv({ cls: "kgd-capture-row", text: "Accepting it replaces the accepted record with the same name and id." });
      this.renderFields(card, draft);
      this.renderStatement(card, draft);
      this.renderValues(card, draft);
      const component = this.renderComponent!;
      if (draft.evidence?.length) {
        card.createEl("h4", { text: draft.foreign ? "Evidence · from the page" : `Evidence · L${draft.lines ?? ""}` });
        const quotes = draft.evidence.map((quote) => quote.split("\n").map((line) => `> ${line}`).join("\n")).join("\n\n");
        void MarkdownRenderer.render(this.app, quotes, card.createDiv({ cls: "kgd-evidence" }), "", component);
      }
      const note = this.notes.get(draft.uid);
      if (note?.report) card.createDiv({ cls: "kgd-capture-note", text: `${note.report} · drafted in ${seconds(note.ms)}` });
    }
    const check = card.createDiv({ cls: `kgd-capture-check ${problems.length ? "is-blocked" : "is-ready"}` });
    if (problems.length) {
      check.createDiv({ text: "kgd accept would refuse it:" });
      const list = check.createEl("ul");
      for (const problem of problems) list.createEl("li", { text: problem });
    } else {
      const others = group.slice(1).map((member) => member.label ?? member.id);
      check.setText(others.length ? `Ready to accept, together with the draft${others.length > 1 ? "s" : ""} ${others.join(", ")}.` : "Ready to accept.");
    }
    const actions = card.createDiv({ cls: "kgd-lookup-actions" });
    const busy = group.some((member) => this.busy.has(member.uid));
    const acceptText = busy ? "Accepting and embedding…" : group.length > 1 ? `Accept with ${group.length - 1} draft${group.length > 2 ? "s" : ""}` : "Accept";
    const accept = actions.createEl("button", { cls: "mod-cta", text: acceptText });
    accept.disabled = busy || problems.length > 0;
    accept.addEventListener("click", () => void this.acceptGroup(group));
    const edit = actions.createEl("button", { text: "Open draft" });
    edit.addEventListener("click", () => void this.openDraft(draft));
    if (draft.foreign && draft.label !== null) {
      const localize = actions.createEl("button", { text: "Localize to selection" });
      localize.disabled = busy;
      localize.addEventListener("click", () => void this.localizeDraft(draft));
    }
    const discard = actions.createEl("button", { cls: "mod-warning", text: "Discard" });
    discard.disabled = busy;
    discard.addEventListener("click", () => new DiscardModal(this.app, draft, () => void this.discardDraft(draft)).open());
  }

  /**
   * The draft's label, kind, other names, epistemic label and understanding, each
   * edited in place: a change runs `kgd edit` on that one field, and the
   * drafts are listed again, so what `kgd accept` would refuse shows at once.
   */
  private renderFields(card: HTMLElement, draft: Draft): void {
    const choices = draft.choices;
    const field = (name: string) => {
      const row = card.createDiv({ cls: "kgd-capture-row kgd-capture-field" });
      row.createSpan({ cls: "kgd-capture-role", text: name });
      return row;
    };
    const label = field("label").createEl("input", { type: "text", cls: "kgd-capture-input", value: draft.label ?? "" });
    const saveLabel = () => {
      const value = label.value.trim();
      if (value && value !== draft.label) void this.editDraft(draft, "label", [value]);
    };
    label.addEventListener("keydown", (event) => {
      if (event.key === "Enter") label.blur();
    });
    label.addEventListener("blur", saveLabel);
    const kinds = [...Object.keys(choices?.node_kinds ?? {}), ...Object.keys(choices?.relation_kinds ?? {})];
    const kind = field("kind").createEl("select", { cls: "dropdown" });
    for (const name of new Set([...kinds, draft.kind ?? ""])) {
      if (!name) continue;
      const option = kind.createEl("option", { text: name, value: name });
      option.selected = name === draft.kind;
    }
    kind.addEventListener("change", () => void this.editDraft(draft, "kind", [kind.value]));
    const aliases = field("aliases");
    const names = draft.aliases ?? [];
    names.forEach((alias, index) => {
      const chip = aliases.createSpan({ cls: "kgd-capture-value", text: alias });
      this.removeButton(chip, `Remove ${alias}`, () => this.editDraft(draft, "aliases", names.filter((_, other) => other !== index)));
    });
    const added = aliases.createEl("input", { type: "text", cls: "kgd-capture-input is-short", placeholder: "add a name" });
    added.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && added.value.trim()) void this.editDraft(draft, "aliases", [...names, added.value.trim()]);
    });
    const facts = field("epistemic");
    const epistemic = facts.createEl("select", { cls: "dropdown" });
    for (const name of new Set(["", ...(choices?.epistemic ?? []), draft.epistemic ?? ""])) {
      const option = epistemic.createEl("option", { text: name || "—", value: name });
      option.selected = name === (draft.epistemic ?? "");
    }
    epistemic.addEventListener("change", () => void this.editDraft(draft, "epistemic", epistemic.value ? [epistemic.value] : []));
    const understood = facts.createEl("label", { cls: "kgd-capture-understood" });
    const box = understood.createEl("input", { type: "checkbox" });
    box.checked = (draft.understanding ?? "yes") === "yes";
    understood.appendText(" understood");
    box.addEventListener("change", () => void this.editDraft(draft, "understanding", [box.checked ? "yes" : "no"]));
  }

  /** The statement rendered as in the editor, with Edit turning it into a text box saved with `kgd edit … body`. */
  private renderStatement(card: HTMLElement, draft: Draft): void {
    const box = card.createDiv({ cls: "kgd-capture-statement" });
    const shown = box.createDiv({ cls: "kgd-prose" });
    if (draft.body) void MarkdownRenderer.render(this.app, draft.body, shown, "", this.renderComponent!);
    const edit = box.createEl("button", { cls: "clickable-icon", attr: { "aria-label": "Edit the statement" } });
    setIcon(edit, "pencil");
    edit.addEventListener("click", () => {
      box.empty();
      const text = box.createEl("textarea", { cls: "kgd-capture-textarea" });
      text.value = draft.body ?? "";
      const buttons = box.createDiv({ cls: "kgd-lookup-actions" });
      buttons.createEl("button", { cls: "mod-cta", text: "Save" }).addEventListener("click", () => void this.editDraft(draft, "body", [text.value]));
      buttons.createEl("button", { text: "Cancel" }).addEventListener("click", () => this.renderDrafts());
      text.focus();
    });
  }

  /**
   * The edges: one row per role the kind declares and one for `requires`, each
   * value a chip naming the record it links, with × to remove it and + to add
   * a record or a draft of this base found by name.
   */
  private renderValues(card: HTMLElement, draft: Draft): void {
    const byRole = new Map(valuesByRole(draft).map(({ role, values }) => [role, values]));
    for (const role of editableRoles(draft)) {
      const row = card.createDiv({ cls: "kgd-capture-row" });
      row.createSpan({ cls: "kgd-capture-role", text: role });
      for (const value of byRole.get(role) ?? []) {
        const shown = valueText(value, draft.base);
        const chip = row.createSpan({ cls: `kgd-capture-value is-${shown.status}`, text: shown.text });
        if (shown.note) chip.createSpan({ cls: "kgd-lookup-meta", text: ` · ${shown.note}` });
        this.removeButton(chip, `Remove ${shown.text}`, () => this.editDraft(draft, role, roleLinks(draft, role, value.pos)));
      }
      const add = row.createEl("button", { cls: "clickable-icon kgd-capture-add", attr: { "aria-label": `Link a record as ${role}` } });
      setIcon(add, "plus");
      add.addEventListener("click", () => new RecordSuggestModal(this.app, this.host.kgd, draft.base, (uid) => {
        void this.editDraft(draft, role, [...roleLinks(draft, role), linkFor(uid, draft.base)]);
      }).open());
    }
  }

  private removeButton(chip: HTMLElement, label: string, run: () => Promise<void>): void {
    const remove = chip.createEl("button", { cls: "clickable-icon kgd-capture-remove", attr: { "aria-label": label } });
    setIcon(remove, "x");
    remove.addEventListener("click", (event) => {
      event.stopPropagation();
      void run();
    });
  }

  /** Set one field of a draft with `kgd edit`, then list the drafts again; a renamed draft keeps its card open. */
  private async editDraft(draft: Draft, key: string, values: string[]): Promise<void> {
    this.busy.add(draft.uid);
    try {
      const { report, failed } = await this.host.kgd.command(["edit", draft.path, key, ...values]);
      if (failed) throw new Error(String((report.error as { message?: string } | undefined)?.message ?? "kgd edit refused the change"));
      const uid = typeof report.uid === "string" ? report.uid : draft.uid;
      if (uid !== draft.uid) {
        this.expanded.delete(draft.uid);
        this.expanded.add(uid);
        const note = this.notes.get(draft.uid);
        if (note) this.notes.set(uid, note);
        this.notes.delete(draft.uid);
      }
    } catch (error) {
      new Notice(`kgdistiller could not edit the draft: ${kgdReason(error)}`);
    } finally {
      this.busy.delete(draft.uid);
      await this.listDrafts();
    }
  }

  /** Localize a foreign draft to the passage selected in a local source. */
  private async localizeDraft(draft: Draft): Promise<void> {
    const selection = await this.host.currentSelection();
    if (!selection?.source) {
      new Notice("Select the concept's passage in a registered local source first, then press Localize to selection.");
      return;
    }
    await this.startLocalize(draftTarget(draft), { ...selection.source, selection: selection.text });
  }

  /** Accept a draft with the drafts it links; `kgd accept` indexes them and embeds the records it creates or replaces. */
  private async acceptGroup(group: Draft[]): Promise<void> {
    for (const member of group) this.busy.add(member.uid);
    this.renderDrafts();
    const started = performance.now();
    try {
      const accepted = await this.host.kgd.command(["accept", ...group.map((member) => member.path)], INDEX_TIMEOUT_MS);
      const refused = (accepted.report.refused as Array<{ message?: string }> | undefined) ?? [];
      if (accepted.failed || refused.length) {
        throw new Error(refused.map((item) => item.message).filter(Boolean).join("; ") || "kgd accept refused the drafts");
      }
      const indexed = accepted.report.index as { embedding_error?: string | null } | undefined;
      this.host.indexer.observeReport(indexed);
      const labels = group.map((member) => member.label ?? member.id).join(", ");
      const unembedded = indexed?.embedding_error ? `; not embedded: ${indexed.embedding_error}` : "";
      new Notice(`kgdistiller accepted ${labels} in ${seconds(performance.now() - started)}${unembedded}.`);
      for (const member of group) {
        this.notes.delete(member.uid);
        this.expanded.delete(member.uid);
      }
    } catch (error) {
      new Notice(`kgdistiller could not accept: ${message(error)}`);
    } finally {
      for (const member of group) this.busy.delete(member.uid);
      await this.listDrafts();
    }
  }

  private async discardDraft(draft: Draft): Promise<void> {
    this.busy.add(draft.uid);
    try {
      await this.host.captureFiles.remove(draft.path);
      this.notes.delete(draft.uid);
      this.expanded.delete(draft.uid);
      new Notice(`kgdistiller discarded ${draft.label ?? draft.id}.`);
    } catch (error) {
      new Notice(`kgdistiller could not discard the draft: ${message(error)}`);
    } finally {
      this.busy.delete(draft.uid);
      await this.listDrafts();
    }
  }

  /** Open the draft file beside the source, for any edit the card does not offer. */
  private async openDraft(draft: Draft): Promise<void> {
    await openInBase(this.app, this.host.kgd, this.host.vaultRoot(), draft.base, `${DRAFTS_FOLDER}${draft.id}.md`, { pane: "split" });
  }

  /** Whether the draft lies in this vault, so its base need not be named. */
  private isLocal(draft: Draft): boolean {
    const root = this.host.vaultRoot();
    return root !== undefined && draft.path.startsWith(`${root}/`);
  }
}

/** Finds records, and drafts of the draft's base, by name or words, for a value to link. */
class RecordSuggestModal extends SuggestModal<{ uid: string; label: string; kind: string; where: string }> {
  constructor(app: App, private readonly kgd: KgdServer, private readonly base: string,
    private readonly chosen: (uid: string) => void) {
    super(app);
    this.setPlaceholder("Find a record or a draft to link");
  }

  async getSuggestions(query: string): Promise<Array<{ uid: string; label: string; kind: string; where: string }>> {
    if (!query.trim()) return [];
    const [searched, drafts] = await Promise.all([
      this.kgd.call<SearchResult>("kg_search", { query, limit: 10 }),
      this.kgd.call<DraftsResult>("kg_drafts", { base: [this.base], names: [query] }),
    ]);
    return [
      ...drafts.drafts.filter((draft) => draft.label !== null).map((draft) => ({ uid: draft.uid, label: draft.label!, kind: draft.kind ?? "", where: "draft" })),
      ...searched.results.map((hit) => ({ uid: hit.uid, label: hit.label, kind: hit.kind, where: hit.base === this.base ? "" : hit.base })),
    ];
  }

  renderSuggestion(item: { label: string; kind: string; where: string }, element: HTMLElement): void {
    element.createSpan({ text: item.label });
    element.createSpan({ cls: "kgd-lookup-meta", text: ` · ${[item.kind, item.where].filter(Boolean).join(" · ")}` });
  }

  onChooseSuggestion(item: { uid: string }): void {
    this.chosen(item.uid);
  }
}

/** The reason a kgd command gave for an error, read from the JSON it prints on standard error. */
function kgdReason(error: unknown): string {
  const text = message(error);
  return /"message":\s*"((?:[^"\\]|\\.)*)"/.exec(text)?.[1]?.replace(/\\"/g, "\"") ?? text;
}

/** Asks before a draft is deleted: nothing remembers a discarded draft. */
class DiscardModal extends Modal {
  constructor(app: App, private readonly draft: Draft, private readonly confirmed: () => void) {
    super(app);
  }

  onOpen(): void {
    this.setTitle(`Discard ${this.draft.label ?? "this draft"}?`);
    const linked = this.draft.linked_by.length;
    this.contentEl.createEl("p", {
      text: linked
        ? `The draft file is deleted. ${linked} other draft${linked === 1 ? " links" : "s link"} it and cannot be accepted until that link is edited.`
        : "The draft file is deleted, and nothing remembers it.",
    });
    new Setting(this.contentEl)
      .addButton((button) => button.setButtonText("Discard").setWarning().onClick(() => {
        this.close();
        this.confirmed();
      }))
      .addButton((button) => button.setButtonText("Cancel").onClick(() => this.close()));
  }

  onClose(): void {
    this.contentEl.empty();
  }
}

function excerpt(text: string): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > 60 ? `${flat.slice(0, 60)}…` : flat;
}

function seconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)} s`;
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
