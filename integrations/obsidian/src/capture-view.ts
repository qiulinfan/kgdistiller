/**
 * The Capture view: the agent's captures while it drafts them, and every
 * draft waiting for the owner. Capture never accepts: it writes a draft, which
 * appears here in full as `kg_drafts` reads it from the file, with each value's
 * target and what `kgd accept` would refuse. Accept admits the draft with the
 * drafts it links (`kgd accept`, then `kgd index --no-embed`); Open draft
 * opens the file beside the source for editing; Discard deletes it. The view
 * lists the drafts again whenever a draft file of this vault changes, so an
 * edit is checked as soon as it is saved. Record ids and uids are never shown.
 */

import { Component, ItemView, MarkdownRenderer, Modal, Notice, Setting, setIcon, type App, type WorkspaceLeaf } from "obsidian";

import type { Selection } from "./agent-pane";
import { captureSelection, type CaptureFiles, type CaptureResult } from "./capture";
import { acceptanceGroup, groupProblems, valuesByRole, valueText, type Draft, type DraftsResult } from "./drafts";
import type { KgdServer } from "./kgd";
import type { ModelClient } from "./model";
import { openInBase } from "./open";
import { firstLine } from "./records";

export const VIEW_TYPE_KGDISTILLER_CAPTURE = "kgdistiller-capture-view";
export const CAPTURE_ICON = "inbox";
/** Draft files saved in quick succession are listed again once. */
const RELIST_DELAY_MS = 300;
const DRAFTS_FOLDER = ".knowledge/drafts/";

export interface CaptureHost {
  app: App;
  kgd: KgdServer;
  model: ModelClient;
  captureFiles: CaptureFiles;
  /** The real path of this vault's root, when the vault lives on the local filesystem. */
  vaultRoot(): string | undefined;
  /** A capture found the item borrowed: show where it comes from in the lookup sidebar. */
  borrowed(selection: Selection): void;
}

/** One capture: drafting until `result` or `error` arrives; a new draft leaves the list for the draft itself. */
interface Job {
  selection: Selection;
  started: number;
  ms?: number;
  result?: CaptureResult;
  error?: string;
}

/** What the agent said about a draft it wrote in this session. */
interface Note {
  item: string;
  report: string;
  ms: number;
}

const DECISIONS: Record<Exclude<CaptureResult["decision"], "new">, string> = {
  unchanged: "Already recorded",
  superseding: "Would overwrite (not written)",
  conflicting: "Conflicts with a record (not written)",
  borrowed: "Belongs to other work (not filed)",
};

export class KgdistillerCaptureView extends ItemView {
  private jobs: Job[] = [];
  private drafts: Draft[] = [];
  private listError: string | undefined;
  private readonly notes = new Map<string, Note>();
  private readonly expanded = new Set<string>();
  /** Drafts being accepted or discarded; their buttons wait. */
  private readonly busy = new Set<string>();
  private listRequest = 0;
  private relistTimer: number | undefined;
  private ticker: number | undefined;
  private renderComponent: Component | null = null;
  private countEl: HTMLElement | null = null;
  private jobsEl: HTMLElement | null = null;
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
    this.draftsEl = this.contentEl.createDiv({ cls: "kgd-capture-drafts" });
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

  /** Draft the selection in the background; its draft, or what the agent decided instead, appears here. */
  async startCapture(selection: Selection): Promise<void> {
    if (!selection.source) return;
    const job: Job = { selection, started: performance.now() };
    this.jobs.unshift(job);
    this.renderJobs();
    try {
      job.result = await captureSelection((chat) => this.host.model.complete(chat), this.host.kgd, this.host.captureFiles,
        { ...selection.source, selection: selection.text });
      job.ms = performance.now() - job.started;
    } catch (error) {
      job.ms = performance.now() - job.started;
      job.error = message(error);
      new Notice(`kgdistiller could not capture: ${job.error}`);
      this.renderJobs();
      return;
    }
    const result = job.result;
    if (result.uid && (result.decision === "new" || result.draft)) this.expanded.add(result.uid);
    if (result.decision === "new" && result.uid) {
      // The draft itself takes the job's place.
      this.notes.set(result.uid, { item: result.item, report: result.report, ms: job.ms });
      this.jobs = this.jobs.filter((other) => other !== job);
      new Notice(`kgdistiller drafted ${result.label ?? "the selection"}; it waits for you in Capture.`);
      await this.listDrafts();
    } else {
      const label = result.draft ? `Already a draft: ${result.label}` : `${DECISIONS[result.decision as Exclude<CaptureResult["decision"], "new">]}${result.label ? `: ${result.label}` : ""}`;
      new Notice(`kgdistiller: ${label}`);
      if (result.decision === "borrowed") this.host.borrowed(selection);
      if (result.draft) await this.listDrafts();
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
      const result = job.result;
      if (!result && !job.error) {
        line.createSpan({ cls: "kgd-lookup-hint", text: "Drafting: " });
        line.createSpan({ text: excerpt(job.selection.text) });
        line.createSpan({ cls: "kgd-lookup-meta", attr: { "data-started": String(job.started) }, text: ` · ${seconds(performance.now() - job.started)}` });
        continue;
      }
      if (job.error) {
        line.createSpan({ cls: "kgd-lookup-error", text: `Could not capture “${excerpt(job.selection.text)}”: ${job.error}` });
      } else if (result) {
        const head = result.draft ? "Already a draft" : DECISIONS[result.decision as Exclude<CaptureResult["decision"], "new">];
        line.createSpan({ cls: "kgd-lookup-verdict-head", text: `${head}${result.label ? `: ${result.label}` : ""}` });
      }
      line.createSpan({ cls: "kgd-lookup-meta", text: ` · ${seconds(job.ms ?? 0)}` });
      const dismiss = line.createEl("button", { cls: "clickable-icon kgd-capture-dismiss", attr: { "aria-label": "Dismiss" } });
      setIcon(dismiss, "x");
      dismiss.addEventListener("click", () => {
        this.jobs = this.jobs.filter((other) => other !== job);
        this.renderJobs();
      });
      if (result?.report) item.createDiv({ cls: "kgd-lookup-explanation", text: result.report });
      if (result?.proposal) {
        const details = item.createEl("details", { cls: "kgd-lookup-proposal" });
        details.createEl("summary", { text: "The record this passage proposes" });
        details.createEl("pre", { text: result.proposal });
      }
    }
    // The drafting lines count their seconds while any capture runs.
    const drafting = this.jobs.some((job) => !job.result && !job.error);
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
    if (!element) return;
    if (this.renderComponent) this.removeChild(this.renderComponent);
    this.renderComponent = this.addChild(new Component());
    element.empty();
    const count = this.drafts.length;
    this.countEl?.setText(count === 0 ? "No drafts" : `${count} draft${count === 1 ? "" : "s"} to confirm`);
    if (this.listError) {
      element.createDiv({ cls: "kgd-lookup-error", text: this.listError });
      return;
    }
    if (count === 0) {
      element.createDiv({ cls: "kgd-lookup-hint", text: "Select a passage in a source and press Capture: the agent drafts it here for you to read, edit and accept." });
      return;
    }
    for (const draft of this.drafts) this.renderDraft(element, draft);
  }

  private renderDraft(parent: HTMLElement, draft: Draft): void {
    const group = acceptanceGroup(draft, this.drafts);
    const problems = group ? groupProblems(group) : ["a draft it links is not listed; list the drafts again"];
    const item = parent.createDiv({ cls: "kgd-capture-item" });
    const head = item.createDiv({ cls: "kgd-capture-item-head" });
    head.createSpan({ cls: "kgd-capture-name", text: draft.label ?? "A draft kgd cannot read" });
    if (draft.kind) head.createSpan({ cls: `kgd-kind kgd-kind-${draft.class ?? "node"}`, text: draft.kind });
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
      const source = where.createEl("a", { text: draft.source ?? "", href: "#" });
      source.addEventListener("click", (event) => {
        event.preventDefault();
        if (draft.source) void openInBase(this.app, this.host.kgd, this.host.vaultRoot(), draft.base, draft.source, { line: firstLine(draft.lines) });
      });
      where.appendText(` · L${draft.lines ?? ""}${this.isLocal(draft) ? "" : ` · ${draft.base}`}`);
      if (draft.aliases?.length) card.createDiv({ cls: "kgd-capture-row", text: `Also called: ${draft.aliases.join(", ")}` });
      const component = this.renderComponent!;
      if (draft.body) void MarkdownRenderer.render(this.app, draft.body, card.createDiv({ cls: "kgd-prose" }), "", component);
      for (const { role, values } of valuesByRole(draft)) {
        const row = card.createDiv({ cls: "kgd-capture-row" });
        row.createSpan({ cls: "kgd-capture-role", text: role });
        for (const value of values) {
          const shown = valueText(value, draft.base);
          const chip = row.createSpan({ cls: `kgd-capture-value is-${shown.status}`, text: shown.text });
          if (shown.note) chip.createSpan({ cls: "kgd-lookup-meta", text: ` · ${shown.note}` });
        }
      }
      if (draft.evidence?.length) {
        card.createEl("h4", { text: `Evidence · L${draft.lines ?? ""}` });
        const quotes = draft.evidence.map((quote) => quote.split("\n").map((line) => `> ${line}`).join("\n")).join("\n\n");
        void MarkdownRenderer.render(this.app, quotes, card.createDiv({ cls: "kgd-evidence" }), "", component);
      }
      const facts = [draft.epistemic ? `Epistemic: ${draft.epistemic}` : "", `Understanding: ${draft.understanding ?? "unknown"}`].filter(Boolean);
      card.createDiv({ cls: "kgd-lookup-meta", text: facts.join(" · ") });
      const note = this.notes.get(draft.uid);
      if (note) {
        const said = card.createDiv({ cls: "kgd-capture-note" });
        said.createDiv({ text: note.item });
        if (note.report) said.createDiv({ cls: "kgd-lookup-meta", text: `${note.report} · drafted in ${seconds(note.ms)}` });
      }
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
    const accept = actions.createEl("button", { cls: "mod-cta", text: group.length > 1 ? `Accept with ${group.length - 1} draft${group.length > 2 ? "s" : ""}` : "Accept" });
    accept.disabled = busy || problems.length > 0;
    accept.addEventListener("click", () => void this.acceptGroup(group));
    const edit = actions.createEl("button", { text: "Open draft" });
    edit.addEventListener("click", () => void this.openDraft(draft));
    const discard = actions.createEl("button", { cls: "mod-warning", text: "Discard" });
    discard.disabled = busy;
    discard.addEventListener("click", () => new DiscardModal(this.app, draft, () => void this.discardDraft(draft)).open());
  }

  /** Accept a draft with the drafts it links, then index them without embeddings. */
  private async acceptGroup(group: Draft[]): Promise<void> {
    for (const member of group) this.busy.add(member.uid);
    this.renderDrafts();
    const started = performance.now();
    try {
      const accepted = await this.host.kgd.command(["accept", ...group.map((member) => member.path)]);
      const refused = (accepted.report.refused as Array<{ message?: string }> | undefined) ?? [];
      if (accepted.failed || refused.length) {
        throw new Error(refused.map((item) => item.message).filter(Boolean).join("; ") || "kgd accept refused the drafts");
      }
      const indexed = await this.host.kgd.command(["index", "--no-embed"]);
      const labels = group.map((member) => member.label ?? member.id).join(", ");
      new Notice(`kgdistiller accepted ${labels} in ${seconds(performance.now() - started)}${indexed.failed ? "; kgd index reported problems" : ""}.`);
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
