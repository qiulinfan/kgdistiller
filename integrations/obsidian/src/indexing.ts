/**
 * The derived index, kept current for keyword search and embedded on request.
 *
 * A record file that changes is indexed again without embeddings
 * (`kgd index --no-embed`, tens of milliseconds, no model), so keyword search
 * and the count of records awaiting embedding stay exact. Embedding a changed
 * record is the owner's call: `kgd index` runs only from the Re-embed button.
 * A record's vector stays valid while the text it was computed from is
 * unchanged, so touching a file, or editing a field the text leaves out, asks
 * for no embedding.
 */

import type { Lag } from "./lookup-model";

/** What the index reports about embedding: records whose vector is missing or stale, and a changed model. */
export interface EmbeddingState {
  unembedded: number;
  embeddingChanged: boolean;
}

interface IndexCommands {
  configured(): boolean;
  command(args: string[], timeout?: number): Promise<{ report: Record<string, unknown>; failed: boolean }>;
  index(): Promise<{ report: Record<string, unknown>; failed: boolean }>;
}

/** Whether Re-embed has work: a record without a current vector, or a model change. */
export function needsEmbedding(state: EmbeddingState | undefined): boolean {
  return state !== undefined && (state.unembedded > 0 || state.embeddingChanged);
}

export class Indexer {
  private current: EmbeddingState | undefined;
  private readonly listeners = new Set<() => void>();
  private refreshing: Promise<void> | undefined;
  private again = false;
  private embedding = false;

  constructor(private readonly kgd: IndexCommands, private readonly warn: (message: string) => void) {}

  get state(): EmbeddingState | undefined {
    return this.current;
  }

  /** Whether `kgd index` is embedding on the owner's request. */
  get busy(): boolean {
    return this.embedding;
  }

  /** Call `listener` whenever the state or `busy` changes; the returned function stops it. */
  onChange(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** The lag a read reported: record files the index has not seen yet are indexed without embeddings. */
  observe(lag: Lag): void {
    this.set({ unembedded: lag.unembedded, embeddingChanged: lag.embedding_changed });
    if (lag.changed_files > 0 && !this.refreshing) void this.refresh();
  }

  /** The report of an index run, such as the one `kgd accept` ends with. */
  observeReport(report: unknown): void {
    const unembedded = (report as { unembedded?: unknown } | undefined)?.unembedded;
    if (typeof unembedded === "number") this.set({ unembedded, embeddingChanged: false });
  }

  /** Index the changed record files without embeddings; calls made meanwhile run once more afterwards. */
  refresh(): Promise<void> {
    if (!this.kgd.configured()) return Promise.resolve();
    if (this.refreshing) {
      this.again = true;
      return this.refreshing;
    }
    this.refreshing = (async () => {
      do {
        this.again = false;
        try {
          const { report, failed } = await this.kgd.command(["index", "--no-embed"]);
          this.observeReport(report);
          if (failed) this.warn("kgd index reported problems; run `kgd index --no-embed` in a terminal to see them.");
        } catch (error) {
          this.warn(`kgd index failed: ${error instanceof Error ? error.message : String(error)}`);
        }
      } while (this.again);
    })().finally(() => { this.refreshing = undefined; });
    return this.refreshing;
  }

  /** Embed every record whose vector is missing or stale; resolves to the number embedded. */
  async reembed(): Promise<number> {
    this.embedding = true;
    this.notify();
    try {
      await this.refreshing;
      const { report, failed } = await this.kgd.index();
      this.observeReport(report);
      const error = report.embedding_error;
      if (typeof error === "string") throw new Error(error);
      if (failed) this.warn("kgd index reported problems; run `kgd index` in a terminal to see them.");
      return typeof report.embedded === "number" ? report.embedded : 0;
    } finally {
      this.embedding = false;
      this.notify();
    }
  }

  private set(state: EmbeddingState): void {
    if (this.current?.unembedded === state.unembedded && this.current.embeddingChanged === state.embeddingChanged) return;
    this.current = state;
    this.notify();
  }

  private notify(): void {
    for (const listener of this.listeners) listener();
  }
}
