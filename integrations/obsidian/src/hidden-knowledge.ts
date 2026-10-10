import { type App, normalizePath } from "obsidian";

import { KNOWLEDGE_DIRECTORY } from "./records";

// Adapted from Hidden Folders Access (MIT), commit
// de3734d36997a98b81a6a6644984748af1e6b3b0, hidden-folders-indexer.ts.
// See THIRD_PARTY_NOTICES.md for the full upstream notice.
type Reconcile = (path: string, normalized: string, silent?: boolean) => Promise<void>;
type ListChild = (parent: string, name: string) => Promise<void>;
type Filesystem = Pick<typeof import("node:fs/promises"), "lstat" | "readdir">;

interface InternalAdapter {
  getBasePath(): string;
  insensitive?: boolean;
  list(path: string): Promise<{ files: string[]; folders: string[] }>;
  listRecursive(path: string): Promise<void>;
  files: Record<string, { type: string; realpath?: string }>;
  listRecursiveChild: ListChild;
  reconcileFile: Reconcile;
  reconcileFileInternal: Reconcile;
  reconcileFolderCreation: Reconcile;
  reconcileDeletion: Reconcile;
  watchHiddenRecursive(path: string): Promise<void>;
  stopWatchPath(path: string): void;
  watchers: Record<string, unknown>;
  trigger(event: string, ...args: unknown[]): void;
}

export interface HiddenKnowledgeStatus {
  state: "disabled" | "enabled" | "missing" | "unsupported" | "invalid" | "error" | "disposed";
  message?: string;
}

interface Session {
  exclusions: string[];
  adapter: InternalAdapter;
  originalList: ListChild;
  originalReconcile: Reconcile;
  listWrapper: ListChild;
  reconcileWrapper: Reconcile;
  previousWatchers: Map<string, unknown>;
  ownedWatchers: Map<string, unknown>;
  scanned?: Set<string>;
  reconciling: number;
}

const root = KNOWLEDGE_DIRECTORY;

/** Folders under the hidden root that stay out of native indexing; nothing by default. */
export const DEFAULT_HIDDEN_KNOWLEDGE_EXCLUSIONS: readonly string[] = [];

const under = (path: string, folder: string): boolean => path === folder || path.startsWith(`${folder}/`);
const excluded = (session: Session, path: string): boolean =>
  session.exclusions.some((prefix) => under(path, `${root}/${prefix}`));
const sameList = (left: readonly string[], right: readonly string[]): boolean =>
  left.length === right.length && left.every((value, index) => value === right[index]);
const missing = (error: unknown): boolean => typeof error === "object" && error !== null &&
  "code" in error && error.code === "ENOENT";
const errorMessage = (error: unknown): string => error instanceof Error ? error.message : String(error);

/** The product's hidden knowledge tree joins the native Vault and metadata cache; no files are converted. */
export class HiddenKnowledgeIndexer {
  private desired: { enabled: boolean; exclusions: unknown } =
    { enabled: false, exclusions: [...DEFAULT_HIDDEN_KNOWLEDGE_EXCLUSIONS] };
  private revision = 0;
  private disposed = false;
  private queue: Promise<void> = Promise.resolve();
  private session?: Session;
  private status: HiddenKnowledgeStatus = { state: "disabled" };
  private fs?: Filesystem;

  constructor(
    private readonly app: App,
    private readonly loadFilesystem: () => Filesystem = () => require("node:fs/promises") as Filesystem,
  ) {}

  /** Exclusions are folder paths relative to the hidden root; each covers its whole subtree. */
  configure(enabled: boolean, exclusions: unknown): Promise<HiddenKnowledgeStatus> {
    if (this.disposed) return Promise.resolve({ state: "disposed" });
    // Copy arrays only; any other stored value reaches validation unchanged.
    this.desired = { enabled, exclusions: Array.isArray(exclusions) ? [...exclusions] : exclusions };
    return this.schedule(false);
  }

  rescan(): Promise<HiddenKnowledgeStatus> {
    if (this.disposed) return Promise.resolve({ state: "disposed" });
    return this.schedule(true);
  }

  /**
   * Unload deactivates hooks and stops owned watchers, retaining the native cache.
   * An already-running native traversal keeps inert wrappers until it settles,
   * so restoring the hidden filter cannot remove its cached files mid-traversal.
   * Obsidian owns workspace restoration; removing cached files here closes tabs.
   * An explicit disable uses removeSession(true) to hide old entries.
   */
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.revision++;
    const session = this.session;
    if (session) {
      if (!session.reconciling) this.restoreHooks(session);
      this.stopWatchers(session);
    }
    this.status = { state: "disposed" };
  }

  private schedule(rescan: boolean): Promise<HiddenKnowledgeStatus> {
    const revision = ++this.revision;
    const task = this.queue.then(async () => {
      if (this.disposed || revision !== this.revision) return this.status;
      try {
        return await this.apply(revision, rescan);
      } catch (error) {
        let message = errorMessage(error);
        if (this.session) {
          try { await this.removeSession(!this.disposed); }
          catch (cleanupError) { message += `; cache cleanup: ${errorMessage(cleanupError)}`; }
        }
        if (this.disposed) return this.status;
        this.status = { state: "error", message };
        return this.status;
      }
    });
    this.queue = task.then(() => undefined, () => undefined);
    return task;
  }

  private current(revision: number): boolean {
    return !this.disposed && this.revision === revision;
  }

  private active(session: Session): boolean {
    return !this.disposed && this.session === session && this.desired.enabled;
  }

  private validateExclusions(exclusions: unknown): string | undefined {
    const valid = (entry: unknown): boolean => typeof entry === "string" && entry.trim() === entry &&
      entry.split("/").every((part) => part !== "" && part !== "." && part !== ".." &&
        !/[\\:\x00-\x1f]/.test(part));
    if (!Array.isArray(exclusions) || !exclusions.every(valid)) {
      return "Excluded folders must be relative folder paths under the hidden folder, without empty, \".\" or \"..\" segments.";
    }
    return undefined;
  }

  private adapter(): InternalAdapter | undefined {
    const candidate = this.app.vault.adapter as unknown as Partial<InternalAdapter>;
    const methods: (keyof InternalAdapter)[] = ["getBasePath", "list", "listRecursive", "listRecursiveChild",
      "reconcileFile", "reconcileFileInternal", "reconcileFolderCreation", "reconcileDeletion",
      "watchHiddenRecursive", "stopWatchPath", "trigger"];
    if (methods.some((name) => typeof candidate[name] !== "function") ||
        !candidate.watchers || typeof candidate.watchers !== "object" ||
        !candidate.files || typeof candidate.files !== "object") return undefined;
    return candidate as InternalAdapter;
  }

  private async apply(revision: number, rescan: boolean): Promise<HiddenKnowledgeStatus> {
    const { enabled } = this.desired;
    const invalid = this.validateExclusions(this.desired.exclusions);
    if (this.session && (!enabled || invalid)) {
      await this.removeSession(true);
    }
    if (!this.current(revision)) return this.status;
    if (!enabled) return this.status = { state: "disabled" };
    if (invalid) return this.status = { state: "invalid", message: invalid };
    const exclusions = this.desired.exclusions as string[];
    const adapter = this.adapter();
    if (!adapter) return this.status = { state: "unsupported",
      message: "Hidden knowledge needs the supported desktop FileSystemAdapter internals." };
    // Obsidian desktop exposes CommonJS require; browser dynamic import cannot
    // resolve node: URLs. Keep the require lazy, after the desktop adapter guard.
    this.fs ??= this.loadFilesystem();
    if (!this.current(revision)) return this.status;
    const listing = await adapter.list("/");
    if (!this.current(revision)) return this.status;
    const exists = listing.folders.some((path) => path.replace(/^\/+|\/+$/g, "") === root);
    if (!exists) {
      if (this.session) await this.removeSession(true);
      return this.status = { state: "missing", message: "The .knowledge folder does not exist; rescan after creating it." };
    }
    if (!await this.safePhysicalPath(adapter, root)) {
      if (this.session) await this.removeSession(true);
      return this.status = { state: "invalid", message: "The hidden knowledge root must be a real folder, not a symbolic link." };
    }
    if (!this.current(revision)) return this.status;
    const existing = this.session;
    const exclusionsChanged = existing !== undefined && !sameList(existing.exclusions, exclusions);
    if (existing && !rescan && !exclusionsChanged) return this.status = { state: "enabled" };
    // A changed exclusion list takes the rescan path: the absent sweep evicts newly
    // excluded cache entries and the traversal admits newly included files.
    if (existing) existing.exclusions = [...exclusions];
    const session = existing ?? this.installHooks(adapter, exclusions);
    try {
      // Both arguments are logical vault paths, not absolute filesystem paths.
      session.scanned = new Set<string>();
      await this.reconcile(session, root, normalizePath(root), true);
      if (this.current(revision)) {
        const absent = Object.keys(adapter.files).filter((path) => under(path, root) && !session.scanned!.has(path))
          .sort((a, b) => b.length - a.length);
        for (const path of absent) {
          if (!this.current(revision)) break;
          await adapter.reconcileDeletion(path, path, true);
        }
      }
      session.scanned = undefined;
      if (this.current(revision)) await adapter.watchHiddenRecursive(root);
    } finally {
      this.captureWatchers(session);
      if (!this.current(revision)) {
        this.restoreHooks(session);
        this.stopWatchers(session);
      }
    }
    if (!this.current(revision)) {
      if (!this.disposed) await this.removeSession(!this.desired.enabled);
      return this.status;
    }
    return this.status = { state: "enabled" };
  }

  private installHooks(adapter: InternalAdapter, exclusions: readonly string[]): Session {
    const session: Session = {
      exclusions: [...exclusions], adapter, originalList: adapter.listRecursiveChild, originalReconcile: adapter.reconcileFile,
      previousWatchers: new Map(Object.entries(adapter.watchers)), ownedWatchers: new Map(), reconciling: 0,
      listWrapper: async () => undefined, reconcileWrapper: async () => undefined,
    };
    session.listWrapper = async (parent, name) => {
      const path = parent ? `${parent}/${name}` : name;
      if (this.disposed && session.reconciling && under(path, root)) return;
      if (!this.active(session) || !under(path, root)) return session.originalList.call(adapter, parent, name);
      if (excluded(session, path)) return;
      await this.reconcile(session, path, normalizePath(path), true);
    };
    session.reconcileWrapper = async (path, normalized, silent) => {
      if (this.disposed && session.reconciling && under(normalized, root)) return;
      if (!this.active(session) || !under(normalized, root)) {
        return session.originalReconcile.call(adapter, path, normalized, silent);
      }
      // Watcher events below an excluded folder are dropped, never handed to the native filter.
      if (excluded(session, normalized) || excluded(session, path)) return;
      if (!under(path, root) || normalized !== normalizePath(path) ||
          !await this.safePhysicalPath(adapter, path) || !this.active(session)) return;
      const parent = path.slice(0, path.lastIndexOf("/"));
      const normalizedParent = normalized.slice(0, normalized.lastIndexOf("/"));
      if (!adapter.files[normalized] && path.includes("/") && under(parent, root)) {
        await session.reconcileWrapper(parent, normalizedParent, silent);
      }
      if (!this.active(session)) return;
      if (adapter.insensitive) {
        try {
          const directory = path.includes("/") ? `${adapter.getBasePath()}/${parent}` : adapter.getBasePath();
          const names = await this.fs!.readdir(directory);
          if (!this.active(session)) return;
          if (!names.map((name) => normalizePath(name)).includes(normalized.slice(normalized.lastIndexOf("/") + 1))) {
            await adapter.reconcileDeletion(path, normalized, silent ?? true);
            return;
          }
        } catch (error) {
          if (!missing(error)) throw error;
          if (this.active(session)) await adapter.reconcileDeletion(path, normalized, silent ?? true);
          return;
        }
      }
      await this.reconcile(session, path, normalized, silent ?? true);
    };
    this.session = session;
    adapter.listRecursiveChild = session.listWrapper;
    adapter.reconcileFile = session.reconcileWrapper;
    return session;
  }

  private async safePhysicalPath(adapter: InternalAdapter, path: string): Promise<boolean> {
    const parts = path.split("/");
    if (parts.some((part, index) => !part || part === "." || part === ".." ||
      part.includes("\\") || (index > 0 && part.startsWith(".")))) return false;
    const base = adapter.getBasePath();
    for (let index = 0; index < parts.length; index++) {
      try {
        const stat = await this.fs!.lstat(`${base}/${parts.slice(0, index + 1).join("/")}`);
        if (stat.isSymbolicLink() || ((index === 0 || index < parts.length - 1) && !stat.isDirectory())) return false;
      } catch (error) {
        // Missing paths still reach native deletion reconciliation.
        if (missing(error)) return true;
        throw error;
      }
    }
    return true;
  }

  private async reconcile(session: Session, path: string, normalized: string, silent: boolean): Promise<void> {
    if (!under(path, root) || excluded(session, path)) return;
    const safe = await this.safePhysicalPath(session.adapter, path);
    if (!this.active(session)) return;
    if (!safe) {
      if (session.adapter.files[normalized]) await session.adapter.reconcileDeletion(path, normalized, true);
      return;
    }
    const wasFolder = session.adapter.files[normalized]?.type === "folder";
    session.scanned?.add(normalized);
    session.adapter.trigger("raw", normalized);
    session.reconciling++;
    try {
      await session.adapter.reconcileFileInternal(path, normalized);
      if (wasFolder && session.scanned && this.active(session)) await session.adapter.listRecursive(path);
    } catch (error) {
      if (!missing(error)) throw error;
      if (this.active(session)) await session.adapter.reconcileDeletion(path, normalized, silent);
    } finally {
      session.reconciling--;
      if (this.disposed && !session.reconciling) {
        this.restoreHooks(session);
        this.stopWatchers(session);
      }
    }
  }

  private restoreHooks(session: Session): void {
    // Another plugin may have wrapped ours meanwhile. Do not overwrite its hooks.
    if (session.adapter.listRecursiveChild === session.listWrapper) {
      session.adapter.listRecursiveChild = session.originalList;
    }
    if (session.adapter.reconcileFile === session.reconcileWrapper) {
      session.adapter.reconcileFile = session.originalReconcile;
    }
  }

  private captureWatchers(session: Session): void {
    for (const [path, watcher] of Object.entries(session.adapter.watchers)) {
      if (under(path, root) && !session.previousWatchers.has(path) && !session.ownedWatchers.has(path)) {
        session.ownedWatchers.set(path, watcher);
      }
    }
  }

  private stopWatchers(session: Session): void {
    this.captureWatchers(session);
    for (const [path, watcher] of [...session.ownedWatchers].sort(([a], [b]) => b.length - a.length)) {
      if (session.adapter.watchers[path] !== watcher) continue;
      try {
        session.adapter.stopWatchPath(path);
      } catch (error) {
        console.error(`kgdistiller: could not stop hidden-folder watcher ${path}`, error);
      }
    }
    session.ownedWatchers.clear();
  }

  private async removeSession(clearCache: boolean): Promise<void> {
    const session = this.session;
    if (!session) return;
    this.session = undefined;
    this.restoreHooks(session);
    this.stopWatchers(session);
    if (!clearCache) return;
    const paths = this.app.vault.getAllLoadedFiles().map((file) => file.path)
      .filter((path) => under(path, root)).sort((a, b) => b.length - a.length);
    for (const path of paths) {
      if (this.disposed) return;
      // This internal operation removes cache entries only, never disk files.
      session.adapter.trigger("raw", path);
      await session.adapter.reconcileDeletion(path, path, true);
    }
  }
}
