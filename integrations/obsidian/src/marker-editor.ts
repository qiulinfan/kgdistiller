/**
 * Knowledge markers in an editor: a CodeMirror extension that draws each
 * marker of the visible lines as a purple chip showing its text, and as its
 * raw text marked purple while the selection touches it, so it can be edited.
 * Cmd/Ctrl+click on either opens the record it names. The same extension
 * serves Markdown and plain-text editors and, through latex-live, TeX ones;
 * the host says which format an editor writes and opens the record.
 */

import { Prec, RangeSetBuilder, type Extension } from "@codemirror/state";
import { Decoration, EditorView, ViewPlugin, WidgetType, type DecorationSet, type ViewUpdate } from "@codemirror/view";
import { Platform } from "obsidian";

import { markerAt, markerLabel, scanLine, touches, type LineMarker, type MarkerFormat } from "./markers";

export interface MarkerEditorHost {
  /** The marker format of the editor's file; undefined leaves the editor alone. */
  format(view: EditorView): MarkerFormat | undefined;
  /** Whether untouched markers become chips; otherwise every marker shows its raw text, marked. */
  chips(view: EditorView): boolean;
  /** Open the record a marker on a line (1-based) of the editor's file names. */
  open(view: EditorView, marker: { name: string; line: number }): void;
  /** The editor took focus: the jump command acts on its cursor. */
  focused(view: EditorView): void;
}

const RAW = Decoration.mark({ class: "kgd-marker-raw" });

class ChipWidget extends WidgetType {
  constructor(readonly label: string, readonly name: string) {
    super();
  }

  eq(other: ChipWidget): boolean {
    return other.label === this.label && other.name === this.name;
  }

  toDOM(view: EditorView): HTMLElement {
    return markerChip(this.label, this.name);
  }

  /** A plain click reaches the editor, which puts the cursor at the chip and so reveals the raw marker. */
  ignoreEvent(): boolean {
    return false;
  }
}

/** The chip a marker shows as, in an editor or in reading view; no link, so Obsidian neither follows nor creates a note. */
export function markerChip(label: string, name: string): HTMLElement {
  return createSpan({
    cls: "kgd-marker-chip",
    text: label,
    attr: { "data-kgd-marker": name, "aria-label": `Knowledge: ${name} (${Platform.isMacOS ? "⌘" : "Ctrl"}-click to open)` },
  });
}

/** Whether a mouse event holds the platform's command key: ⌘ on macOS, Ctrl elsewhere. */
export function modifierHeld(event: MouseEvent): boolean {
  return Platform.isMacOS ? event.metaKey : event.ctrlKey;
}

function decorations(view: EditorView, host: MarkerEditorHost): DecorationSet {
  const builder = new RangeSetBuilder<Decoration>();
  const format = host.format(view);
  if (!format) return builder.finish();
  const chips = host.chips(view);
  const { doc, selection } = view.state;
  let previous = -1;
  for (const { from, to } of view.visibleRanges) {
    for (let position = from; position <= to;) {
      const line = doc.lineAt(position);
      position = line.to + 1;
      if (line.number <= previous) continue;
      previous = line.number;
      for (const marker of scanLine(line.text, format)) {
        const start = line.from + marker.from;
        const end = line.from + marker.to;
        const raw = !chips || touches({ from: start, to: end }, selection.ranges);
        builder.add(start, end, raw ? RAW : Decoration.replace({ widget: new ChipWidget(markerLabel(marker), marker.name) }));
      }
    }
  }
  return builder.finish();
}

/** The marker at a document position, with its line number. */
function markerAtPosition(view: EditorView, format: MarkerFormat, position: number): (LineMarker & { line: number }) | undefined {
  const line = view.state.doc.lineAt(position);
  const marker = markerAt(line.text, format, position - line.from);
  return marker ? { ...marker, line: line.number } : undefined;
}

/** The marker the cursor of an editor is on, for the jump command. */
export function markerAtCursor(view: EditorView, format: MarkerFormat): { name: string; line: number } | undefined {
  return markerAtPosition(view, format, view.state.selection.main.head);
}

/**
 * Cmd/Ctrl+click on a chip or a raw marker opens its record. A plain click on
 * the internal link Obsidian draws inside a raw marker only moves the cursor
 * there, so it never follows the link or creates a note named after it.
 * Listening in the capture phase runs before Obsidian's own link handling.
 */
function clicked(view: EditorView, host: MarkerEditorHost, event: MouseEvent): boolean {
  if (event.button !== 0) return false;
  const format = host.format(view);
  // Popout windows have their own Element class, so the target is not checked with instanceof.
  const target = event.target as Element | null;
  if (!format || typeof target?.closest !== "function" || !view.contentDOM.contains(target)) return false;
  const chip = target.closest<HTMLElement>(".kgd-marker-chip");
  const position = chip ? view.posAtDOM(chip) : view.posAtCoords({ x: event.clientX, y: event.clientY });
  if (position === null) return false;
  const marker = markerAtPosition(view, format, position);
  if (!marker) return false;
  if (modifierHeld(event)) {
    if (event.type === "mousedown") host.open(view, marker);
    return true;
  }
  if (chip || !target.closest(".cm-hmd-internal-link, .internal-link, a")) return false;
  if (event.type === "mousedown") {
    view.dispatch({ selection: { anchor: position } });
    view.focus();
  }
  return true;
}

export function markerEditorExtension(host: MarkerEditorHost): Extension {
  const plugin = ViewPlugin.fromClass(class {
    decorations: DecorationSet;
    private readonly listener = (event: MouseEvent): void => {
      if (!clicked(this.view, host, event)) return;
      event.preventDefault();
      event.stopPropagation();
    };

    constructor(readonly view: EditorView) {
      this.decorations = decorations(view, host);
      for (const type of ["mousedown", "click", "auxclick"] as const) view.dom.addEventListener(type, this.listener, true);
      if (view.hasFocus) host.focused(view);
    }

    update(update: ViewUpdate): void {
      if (update.focusChanged && update.view.hasFocus) host.focused(update.view);
      // Switching between live preview and source mode arrives as a transaction too.
      if (update.docChanged || update.viewportChanged || update.selectionSet || update.transactions.length > 0) {
        this.decorations = decorations(update.view, host);
      }
    }

    destroy(): void {
      for (const type of ["mousedown", "click", "auxclick"] as const) this.view.dom.removeEventListener(type, this.listener, true);
    }
  }, { decorations: (value) => value.decorations });
  return Prec.high(plugin);
}

/**
 * Reading view renders `--[[NAME|TEXT]]--` as "--", an internal link and
 * "--": each such link becomes the marker's chip, which follows no link and
 * creates no note, and whose Cmd/Ctrl+click opens the record.
 */
export function chipRenderedMarkers(element: HTMLElement, open: (name: string) => void): void {
  for (const link of Array.from(element.querySelectorAll<HTMLElement>("a.internal-link"))) {
    const before = link.previousSibling;
    const after = link.nextSibling;
    if (before?.nodeType !== TEXT_NODE || after?.nodeType !== TEXT_NODE) continue;
    const head = before.textContent ?? "";
    const tail = after.textContent ?? "";
    const name = (link.getAttribute("data-href") ?? "").trim();
    if (!head.endsWith("--") || !tail.startsWith("--") || !name || /[[\]|\n]/.test(name)) continue;
    before.textContent = head.slice(0, -2);
    after.textContent = tail.slice(2);
    const shown = link.textContent ?? "";
    const chip = markerChip(shown.trim() ? shown : name, name);
    chip.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (modifierHeld(event)) open(name);
    });
    link.replaceWith(chip);
  }
}

const TEXT_NODE = 3;
