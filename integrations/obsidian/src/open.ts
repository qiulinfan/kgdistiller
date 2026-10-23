/** Opening a file of a registered base from the plugin's views. */

import { MarkdownView, normalizePath, Notice, TFile, type App, type PaneType } from "obsidian";

import type { KgdServer } from "./kgd";
import { columnOffset } from "./markers";

/**
 * Open a base-relative path: in this vault when the base is this vault,
 * otherwise through an obsidian:// link to the file in its own vault. `line`
 * is 1-based, and `column`, kgd's 1-based character column on it, puts the
 * cursor there in a Markdown editor; `pane` is where Obsidian opens it, the
 * most recent leaf by default.
 */
export async function openInBase(app: App, kgd: Pick<KgdServer, "baseRoots">, vaultRoot: string | undefined, base: string, path: string,
  { line, column, pane = false }: { line?: number; column?: number; pane?: PaneType | false } = {}): Promise<void> {
  let roots: Map<string, string>;
  try {
    roots = await kgd.baseRoots();
  } catch (error) {
    new Notice(`kgdistiller cannot list the bases: ${error instanceof Error ? error.message : String(error)}`);
    return;
  }
  const root = roots.get(base);
  if (root === undefined) {
    new Notice(`kgdistiller does not know base ${base}.`);
    return;
  }
  if (root === vaultRoot) {
    const file = app.vault.getAbstractFileByPath(normalizePath(path));
    if (!(file instanceof TFile)) {
      new Notice(`kgdistiller cannot open ${path}: it is not in the vault index.`);
      return;
    }
    const leaf = app.workspace.getLeaf(pane);
    await leaf.openFile(file, line === undefined ? undefined : { eState: { line: line - 1 } });
    if (line !== undefined && column !== undefined && leaf.view instanceof MarkdownView) {
      const { editor } = leaf.view;
      editor.setCursor({ line: line - 1, ch: columnOffset(editor.getLine(line - 1), column) });
    }
    return;
  }
  window.open(`obsidian://open?path=${encodeURIComponent(`${root}/${path}`)}`);
}
