import type { App } from "obsidian";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_HIDDEN_KNOWLEDGE_EXCLUSIONS, HiddenKnowledgeIndexer } from "../src/hidden-knowledge";
const DEFAULT = [...DEFAULT_HIDDEN_KNOWLEDGE_EXCLUSIONS];
const SCRATCH = ["scratch"];
const filesystem = vi.hoisted(() => ({ lstat: vi.fn(), readdir: vi.fn() }));
const loadFilesystem = () => filesystem as unknown as Pick<typeof import("node:fs/promises"), "lstat" | "readdir">;
vi.mock("obsidian", () => ({ normalizePath: (path: string) => path.normalize("NFC") }));
type DiskKind = "file" | "folder" | "symlink";
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => { resolve = done; });
  return { promise, resolve };
}
function fixture() {
  const disk = new Map<string, DiskKind>([
    [".knowledge", "folder"], [".knowledge/entries", "folder"],
    [".knowledge/entries/definition.md", "file"], [".knowledge/figure.png", "file"],
    [".knowledge/.private", "folder"], [".knowledge/.private/secret.md", "file"],
    [".knowledge/link", "symlink"], [".knowledge/link/escape.md", "file"],
    [".knowledge-copy", "folder"], [".knowledge-copy/other.md", "file"],
    ["ordinary.md", "file"], [".git", "folder"], [".git/config", "file"],
  ]);
  const loaded = new Map<string, { path: string }>([["ordinary.md", { path: "ordinary.md" }]]);
  const watchers: Record<string, unknown> = { "ordinary": {} };
  const events: string[] = [];
  const adapter = {
    watchers, getBasePath: () => "/vault", insensitive: false,
    get files() { return Object.fromEntries([...loaded.keys()].map((path) => [path, { type: disk.get(path) ?? "file" }])); },
    list: vi.fn(async (_path: string) => ({ files: [], folders: [...disk]
      .filter(([path, kind]) => !path.includes("/") && kind !== "file").map(([path]) => `/${path}`) })),
    trigger: vi.fn((event: string, path: unknown) => { events.push(`${event}:${String(path)}`); }),
    listRecursiveChild: vi.fn(async function(parent: string, name: string): Promise<void> {
      const path = parent ? `${parent}/${name}` : name;
      if (path.split("/").some((part) => part.startsWith("."))) await adapter.reconcileDeletion(path, path, true);
      else await adapter.reconcileFileInternal(path, path);
    }),
    reconcileFile: vi.fn(async function(path: string, normalized: string, _silent?: boolean): Promise<void> {
      if (normalized.split("/").some((part) => part.startsWith("."))) await adapter.reconcileDeletion(path, normalized, true);
      else await adapter.reconcileFileInternal(path, normalized);
    }),
    reconcileFileInternal: vi.fn(async function(path: string, normalized: string): Promise<void> {
      const kind = disk.get(path);
      if (!kind) throw Object.assign(new Error("missing"), { code: "ENOENT" });
      if (kind === "folder") await adapter.reconcileFolderCreation(path, normalized);
      else { loaded.set(normalized, { path: normalized }); events.push(`indexed:${normalized}`); }
    }),
    reconcileFolderCreation: vi.fn(async function(path: string, normalized: string): Promise<void> {
      if (loaded.has(normalized)) return;
      loaded.set(normalized, { path: normalized });
      await adapter.listRecursive(path);
    }),
    listRecursive: vi.fn(async (path: string): Promise<void> => {
      const children = [...disk.keys()].filter((key) => key.startsWith(`${path}/`) && !key.slice(path.length + 1).includes("/"));
      for (const child of children) await adapter.listRecursiveChild(path, child.slice(path.length + 1));
    }),
    reconcileDeletion: vi.fn(async (path: string, _normalized: string, _force?: boolean) => {
      for (const key of loaded.keys()) if (key === path || key.startsWith(`${path}/`)) loaded.delete(key);
    }),
    watchHiddenRecursive: vi.fn(async (root: string) => {
      for (const path of loaded.keys()) {
        if ((path === root || path.startsWith(`${root}/`)) && disk.get(path) === "folder") watchers[path] ??= {};
      }
    }),
    stopWatchPath: vi.fn((path: string) => { delete watchers[path]; }),
  };
  filesystem.lstat.mockImplementation(async (full: string) => {
    const kind = disk.get(full.replace(/^\/vault\//, ""));
    if (!kind) throw Object.assign(new Error("missing"), { code: "ENOENT" });
    return { isSymbolicLink: () => kind === "symlink", isDirectory: () => kind === "folder" };
  });
  filesystem.readdir.mockImplementation(async (full: string) => {
    const path = full === "/vault" ? "" : full.replace(/^\/vault\//, "");
    const prefix = path ? `${path}/` : "";
    return [...disk.keys()].filter((key) => key.startsWith(prefix) && !key.slice(prefix.length).includes("/"))
      .map((key) => key.slice(prefix.length));
  });
  const app = { vault: { adapter, configDir: ".obsidian", getAllLoadedFiles: () => [...loaded.values()] } };
  return { disk, loaded, watchers, events, adapter, app, indexer: new HiddenKnowledgeIndexer(app as unknown as App, loadFilesystem) };
}
beforeEach(() => vi.clearAllMocks());

describe("native hidden knowledge indexing", () => {
  it("indexes .knowledge through native reconciliation, preserving other hidden boundaries", async () => {
    const { indexer, loaded, adapter } = fixture();
    expect(await indexer.configure(true, DEFAULT)).toEqual({ state: "enabled" });
    expect([...loaded.keys()].sort()).toEqual([".knowledge", ".knowledge/entries", ".knowledge/entries/definition.md", ".knowledge/figure.png", "ordinary.md"]);
    await adapter.listRecursiveChild(".knowledge-copy", "other.md");
    await adapter.listRecursiveChild(".git", "config");
    await adapter.listRecursiveChild("", "ordinary.md");
    expect(loaded.has(".knowledge-copy/other.md")).toBe(false);
    expect(loaded.has(".git/config")).toBe(false);
    expect(adapter.reconcileFileInternal).toHaveBeenCalledWith("ordinary.md", "ordinary.md");
  });
  it("reports missing roots without creating them, then admits a later manual rescan", async () => {
    const { indexer, disk, adapter } = fixture();
    disk.delete(".knowledge");
    const original = adapter.listRecursiveChild;
    expect((await indexer.configure(true, DEFAULT)).state).toBe("missing");
    expect(adapter.listRecursiveChild).toBe(original);
    expect(adapter.reconcileFolderCreation).not.toHaveBeenCalled();
    disk.set(".knowledge", "folder");
    expect((await indexer.rescan()).state).toBe("enabled");
  });
  it("leaves unsupported adapters alone", async () => {
    const { app, adapter } = fixture();
    const original = adapter.reconcileFile;
    (adapter as unknown as Record<string, unknown>).watchHiddenRecursive = undefined;
    const load = vi.fn(loadFilesystem);
    const indexer = new HiddenKnowledgeIndexer(app as unknown as App, load);
    expect((await indexer.configure(true, DEFAULT)).state).toBe("unsupported");
    expect(load).not.toHaveBeenCalled();
    expect(adapter.reconcileFile).toBe(original);
    expect(filesystem.lstat).not.toHaveBeenCalled();
  });
  it("rejects symbolic root and skips symlinks and traversal in descendants", async () => {
    const { disk, indexer, adapter, loaded } = fixture();
    disk.set(".knowledge", "symlink");
    expect((await indexer.configure(true, DEFAULT)).state).toBe("invalid");
    disk.set(".knowledge", "folder");
    await indexer.rescan();
    await adapter.reconcileFile(".knowledge/link/escape.md", ".knowledge/link/escape.md");
    await adapter.reconcileFile(".knowledge/../ordinary.md", ".knowledge/../ordinary.md");
    await adapter.reconcileFile(".knowledge//entries/definition.md", ".knowledge//entries/definition.md");
    expect(loaded.has(".knowledge/link/escape.md")).toBe(false);
    expect(loaded.has(".knowledge/../ordinary.md")).toBe(false);
    expect(loaded.has(".knowledge//entries/definition.md")).toBe(false);
  });
  it("routes create, modify and deletion watcher events into the native cache", async () => {
    const { indexer, disk, adapter, loaded, events } = fixture();
    await indexer.configure(true, DEFAULT);
    const path = ".knowledge/new.md";
    disk.set(path, "file");
    await adapter.reconcileFile(path, path);
    await adapter.reconcileFile(path, path);
    expect(loaded.has(path)).toBe(true);
    expect(events.filter((event) => event === `indexed:${path}`)).toHaveLength(2);
    disk.delete(path);
    await adapter.reconcileFile(path, path, false);
    expect(loaded.has(path)).toBe(false);
    expect(adapter.reconcileDeletion).toHaveBeenCalledWith(path, path, false);
  });
  it("disable removes only the indexed cache and owned watchers and restores exact functions", async () => {
    const { indexer, adapter, loaded, disk, watchers } = fixture();
    const preexisting = {};
    watchers[".knowledge/borrowed"] = preexisting;
    const list = adapter.listRecursiveChild;
    const reconcile = adapter.reconcileFile;
    const originalDisk = [...disk];
    await indexer.configure(true, DEFAULT);
    await indexer.configure(false, DEFAULT);
    expect([...loaded.keys()]).toEqual(["ordinary.md"]);
    expect([...disk]).toEqual(originalDisk);
    expect(watchers[".knowledge/borrowed"]).toBe(preexisting);
    expect(Object.keys(watchers).sort()).toEqual([".knowledge/borrowed", "ordinary"]);
    expect(adapter.listRecursiveChild).toBe(list);
    expect(adapter.reconcileFile).toBe(reconcile);
  });
  it("unload preserves cached files and leaves while stopping hooks and owned watchers", async () => {
    const { indexer, adapter, loaded, watchers } = fixture();
    const list = adapter.listRecursiveChild;
    await indexer.configure(true, DEFAULT);
    const before = [...loaded.keys()];
    indexer.dispose(); indexer.dispose();
    expect([...loaded.keys()]).toEqual(before);
    expect(adapter.listRecursiveChild).toBe(list);
    expect(Object.keys(watchers)).toEqual(["ordinary"]);
    expect(adapter.reconcileDeletion).not.toHaveBeenCalled();
    expect((await indexer.configure(true, DEFAULT)).state).toBe("disposed");
  });
  it("a fresh instance enables retained native cache after reload", async () => {
    const { indexer, adapter, app, loaded } = fixture();
    const original = adapter.reconcileFile;
    await indexer.configure(true, DEFAULT); indexer.dispose();
    const next = new HiddenKnowledgeIndexer(app as unknown as App, loadFilesystem);
    expect((await next.configure(true, DEFAULT)).state).toBe("enabled");
    expect(loaded.has(".knowledge/entries/definition.md")).toBe(true);
    expect(adapter.listRecursive).toHaveBeenCalledWith(".knowledge/entries");
    next.dispose();
    expect(adapter.reconcileFile).toBe(original);
  });
  it("does not overwrite later plugin wrappers or stop replaced watchers", async () => {
    const { indexer, adapter, watchers } = fixture();
    await indexer.configure(true, DEFAULT);
    const ours = adapter.reconcileFile;
    const later = vi.fn((...args: Parameters<typeof ours>) => ours.apply(adapter, args));
    adapter.reconcileFile = later;
    const replacement = {};
    watchers[".knowledge"] = replacement;
    indexer.dispose();
    expect(adapter.reconcileFile).toBe(later);
    expect(watchers[".knowledge"]).toBe(replacement);
  });
  it("reports watcher failure and clears partial enable", async () => {
    const { indexer, adapter, loaded, watchers } = fixture();
    const original = adapter.listRecursiveChild;
    adapter.watchHiddenRecursive.mockImplementationOnce(async (root) => {
      watchers[root] = {}; throw new Error("watcher unavailable");
    });
    expect(await indexer.configure(true, DEFAULT)).toEqual({ state: "error", message: "watcher unavailable" });
    expect(adapter.listRecursiveChild).toBe(original);
    expect([...loaded.keys()]).toEqual(["ordinary.md"]);
    expect(Object.keys(watchers)).toEqual(["ordinary"]);
  });
  it("unload during listing cannot install late hooks", async () => {
    const { indexer, adapter } = fixture();
    const waiting = deferred(), entered = deferred();
    const original = adapter.listRecursiveChild;
    adapter.list.mockImplementationOnce(async () => {
      entered.resolve(); await waiting.promise; return { files: [], folders: [".knowledge"] };
    });
    const enabling = indexer.configure(true, DEFAULT);
    await entered.promise; indexer.dispose(); waiting.resolve();
    expect((await enabling).state).toBe("disposed");
    expect(adapter.listRecursiveChild).toBe(original);
    expect(adapter.watchHiddenRecursive).not.toHaveBeenCalled();
  });
  it("unload during watcher startup restores hooks immediately and cleans late watchers", async () => {
    const { indexer, adapter, watchers, loaded } = fixture();
    const waiting = deferred(), entered = deferred();
    const original = adapter.listRecursiveChild;
    adapter.watchHiddenRecursive.mockImplementationOnce(async (root) => {
      entered.resolve(); await waiting.promise; watchers[root] = {};
    });
    const enabling = indexer.configure(true, DEFAULT);
    await entered.promise; indexer.dispose();
    expect(adapter.listRecursiveChild).toBe(original);
    waiting.resolve();
    expect((await enabling).state).toBe("disposed");
    expect(Object.keys(watchers)).toEqual(["ordinary"]);
    expect(loaded.has(".knowledge/entries/definition.md")).toBe(true);
  });
  it("a queued disable wins over an in-flight enable", async () => {
    const { indexer, adapter, watchers, loaded } = fixture();
    const waiting = deferred(), entered = deferred();
    const original = adapter.reconcileFile;
    adapter.watchHiddenRecursive.mockImplementationOnce(async (root) => {
      entered.resolve(); await waiting.promise; watchers[root] = {};
    });
    const enabling = indexer.configure(true, DEFAULT);
    await entered.promise;
    const disabling = indexer.configure(false, DEFAULT);
    waiting.resolve(); await enabling;
    expect((await disabling).state).toBe("disabled");
    expect(adapter.reconcileFile).toBe(original);
    expect(Object.keys(watchers)).toEqual(["ordinary"]);
    expect([...loaded.keys()]).toEqual(["ordinary.md"]);
  });
  it("rescan repairs nested additions and missed deletions without deleting retained notes", async () => {
    const { indexer, disk, loaded, adapter } = fixture();
    await indexer.configure(true, DEFAULT);
    disk.set(".knowledge/entries/new.md", "file");
    disk.delete(".knowledge/figure.png");
    adapter.reconcileDeletion.mockClear();
    expect((await indexer.rescan()).state).toBe("enabled");
    expect(loaded.has(".knowledge/entries/new.md")).toBe(true);
    expect(loaded.has(".knowledge/figure.png")).toBe(false);
    expect(adapter.reconcileDeletion.mock.calls.map(([path]) => path)).toEqual([".knowledge/figure.png"]);
  });
  it("a repeated same-root request during enable preserves the hidden editor cache", async () => {
    const { indexer, adapter, loaded } = fixture();
    const waiting = deferred(), entered = deferred();
    adapter.watchHiddenRecursive.mockImplementationOnce(async () => { entered.resolve(); await waiting.promise; });
    const first = indexer.configure(true, DEFAULT);
    await entered.promise;
    const second = indexer.rescan();
    waiting.resolve(); await first;
    expect((await second).state).toBe("enabled");
    expect(loaded.has(".knowledge/entries/definition.md")).toBe(true);
    expect(adapter.reconcileDeletion).not.toHaveBeenCalled();
  });

  it("uses Obsidian-normalized logical keys while reading the original Unicode filename", async () => {
    const { indexer, disk, loaded, adapter } = fixture();
    const raw = ".knowledge/cafe\u0301.md";
    const normalized = raw.normalize("NFC");
    disk.set(raw, "file");
    await indexer.configure(true, DEFAULT);
    expect(adapter.reconcileFileInternal).toHaveBeenCalledWith(raw, normalized);
    expect(loaded.has(normalized)).toBe(true);
    expect(loaded.has(raw)).toBe(false);
  });
  it("unload during native traversal cannot run the restored hidden filter against open files", async () => {
    const { indexer, adapter, loaded } = fixture();
    await indexer.configure(true, DEFAULT);
    const before = [...loaded.keys()];
    const entered = deferred(), waiting = deferred();
    const nativeList = adapter.listRecursive.getMockImplementation()!;
    adapter.listRecursive.mockImplementationOnce(async (path) => {
      entered.resolve(); await waiting.promise; await nativeList(path);
    });
    const rescan = indexer.rescan();
    await entered.promise;
    indexer.dispose();
    waiting.resolve();
    expect((await rescan).state).toBe("disposed");
    expect([...loaded.keys()]).toEqual(before);
    expect(adapter.reconcileDeletion).not.toHaveBeenCalled();
    // The native call has settled, so its guarded wrappers must be gone.
    expect(vi.isMockFunction(adapter.listRecursiveChild)).toBe(true);
  });

  it("creates missing parents before a child-first external watcher event", async () => {
    const { indexer, disk, adapter, loaded } = fixture();
    await indexer.configure(true, DEFAULT);
    disk.set(".knowledge/new", "folder");
    disk.set(".knowledge/new/deep", "folder");
    disk.set(".knowledge/new/deep/entry.md", "file");
    await adapter.reconcileFile(".knowledge/new/deep/entry.md", ".knowledge/new/deep/entry.md");
    expect(loaded.has(".knowledge/new")).toBe(true);
    expect(loaded.has(".knowledge/new/deep")).toBe(true);
    expect(loaded.has(".knowledge/new/deep/entry.md")).toBe(true);
  });
  it("removes old spelling on a case-only rename even when lstat accepts the old name", async () => {
    const { indexer, disk, adapter, loaded } = fixture();
    adapter.insensitive = true;
    const old = ".knowledge/Alpha.md", next = ".knowledge/alpha.md";
    disk.set(old, "file");
    await indexer.configure(true, DEFAULT);
    disk.delete(old); disk.set(next, "file");
    const originalStat = filesystem.lstat.getMockImplementation()!;
    filesystem.lstat.mockImplementation((full: string) => originalStat(full.replace("Alpha.md", "alpha.md")));
    await adapter.reconcileFile(old, old);
    await adapter.reconcileFile(next, next);
    expect(loaded.has(old)).toBe(false);
    expect(loaded.has(next)).toBe(true);
  });

  it("indexes the whole knowledge folder by default", async () => {
    const { indexer, disk, loaded } = fixture();
    expect(DEFAULT).toEqual([]);
    disk.set(".knowledge/sheets", "folder");
    disk.set(".knowledge/sheets/chapter.tex.md", "file");
    expect((await indexer.configure(true, DEFAULT)).state).toBe("enabled");
    expect(loaded.has(".knowledge/sheets/chapter.tex.md")).toBe(true);
    expect(loaded.has(".knowledge/entries/definition.md")).toBe(true);
  });
  it("excludes a listed folder on the initial scan, nested listings and watcher events", async () => {
    const { indexer, disk, loaded, adapter, events } = fixture();
    disk.set(".knowledge/scratch", "folder");
    disk.set(".knowledge/scratch/notes", "folder");
    disk.set(".knowledge/scratch/notes/chapter.tex.md", "file");
    disk.set(".knowledge/scratchy.md", "file");
    const nativeList = adapter.listRecursiveChild;
    const nativeReconcile = adapter.reconcileFile;
    expect((await indexer.configure(true, SCRATCH)).state).toBe("enabled");
    expect([...loaded.keys()].filter((path) => path.includes("scratch")).sort()).toEqual([".knowledge/scratchy.md"]);
    await adapter.listRecursiveChild(".knowledge/scratch", "notes");
    await adapter.listRecursiveChild(".knowledge", "scratch");
    const sheet = ".knowledge/scratch/notes/chapter.tex.md";
    await adapter.reconcileFile(sheet, sheet);
    await adapter.reconcileFile(".knowledge/scratch", ".knowledge/scratch", false);
    expect([...loaded.keys()].some((path) => path.startsWith(".knowledge/scratch/") || path === ".knowledge/scratch")).toBe(false);
    expect(events.some((event) => event.includes(".knowledge/scratch/") || event.endsWith(".knowledge/scratch"))).toBe(false);
    expect(nativeList).not.toHaveBeenCalledWith(".knowledge/scratch", "notes");
    expect(nativeReconcile).not.toHaveBeenCalledWith(sheet, sheet);
    expect(adapter.reconcileDeletion).not.toHaveBeenCalled();
  });
  it("indexes scratch/ when the exclusion list is empty", async () => {
    const { indexer, disk, loaded } = fixture();
    disk.set(".knowledge/scratch", "folder");
    disk.set(".knowledge/scratch/review.md", "file");
    expect((await indexer.configure(true, [])).state).toBe("enabled");
    expect(loaded.has(".knowledge/scratch")).toBe(true);
    expect(loaded.has(".knowledge/scratch/review.md")).toBe(true);
  });
  it("changing exclusions evicts and admits cache entries without touching disk", async () => {
    const { indexer, disk, loaded, adapter } = fixture();
    disk.set(".knowledge/scratch", "folder");
    disk.set(".knowledge/scratch/review.md", "file");
    await indexer.configure(true, SCRATCH);
    const original = [...disk];
    const hooks = adapter.reconcileFile;
    adapter.reconcileDeletion.mockClear();
    expect((await indexer.configure(true, ["entries"])).state).toBe("enabled");
    expect(adapter.reconcileFile).toBe(hooks);
    expect(loaded.has(".knowledge/scratch/review.md")).toBe(true);
    expect(loaded.has(".knowledge/entries")).toBe(false);
    expect(loaded.has(".knowledge/entries/definition.md")).toBe(false);
    expect(adapter.reconcileDeletion.mock.calls.map(([path]) => path))
      .toEqual([".knowledge/entries/definition.md", ".knowledge/entries"]);
    expect([...disk]).toEqual(original);
    const entry = ".knowledge/entries/definition.md";
    await adapter.reconcileFile(entry, entry);
    expect(loaded.has(entry)).toBe(false);
    await indexer.configure(true, SCRATCH);
    expect(loaded.has(entry)).toBe(true);
    expect(loaded.has(".knowledge/scratch/review.md")).toBe(false);
    expect([...disk]).toEqual(original);
  });
  it.each(["", "/scratch", "../x", "a\\b", ".", "scratch/", "a//b", " scratch"])("rejects invalid exclusion %j without hooks", async (entry) => {
    const { indexer, adapter } = fixture();
    const original = adapter.listRecursiveChild;
    expect((await indexer.configure(true, [entry])).state).toBe("invalid");
    expect(adapter.listRecursiveChild).toBe(original);
    expect(adapter.watchHiddenRecursive).not.toHaveBeenCalled();
  });
  it.each([["scratch"], "scratch", null, 7, { scratch: true }])("rejects a non-array or non-string exclusion setting %j", async (value) => {
    const { indexer, adapter } = fixture();
    const original = adapter.listRecursiveChild;
    const exclusions = Array.isArray(value) ? [value] : value;
    expect((await indexer.configure(true, exclusions)).state).toBe("invalid");
    expect(adapter.listRecursiveChild).toBe(original);
    expect(adapter.watchHiddenRecursive).not.toHaveBeenCalled();
  });
  it("an invalid exclusion change removes the indexed cache", async () => {
    const { indexer, loaded } = fixture();
    await indexer.configure(true, DEFAULT);
    expect((await indexer.configure(true, ["../x"])).state).toBe("invalid");
    expect([...loaded.keys()]).toEqual(["ordinary.md"]);
  });
});
