import { describe, expect, it, vi } from "vitest";

import { Indexer, needsEmbedding } from "../src/indexing";

const lag = (changed: number, unembedded: number, embeddingChanged = false) =>
  ({ changed_files: changed, unavailable_bases: [], unembedded, embedding_changed: embeddingChanged });

function fixture(reports: Array<Record<string, unknown>> = [{ unembedded: 0 }]) {
  let release: () => void = () => undefined;
  let gate: Promise<void> | undefined;
  const kgd = {
    configured: () => true,
    command: vi.fn(async (_args: string[]) => {
      await gate;
      return { report: reports.shift() ?? { unembedded: 0 }, failed: false };
    }),
    index: vi.fn(async () => ({ report: reports.shift() ?? { unembedded: 0, embedded: 0 }, failed: false })),
  };
  const warn = vi.fn();
  const hold = () => { gate = new Promise((resolve) => { release = () => { gate = undefined; resolve(); }; }); };
  return { indexer: new Indexer(kgd, warn), kgd, warn, hold, release: () => release() };
}

describe("needsEmbedding", () => {
  it("asks for embedding only when a vector is missing or stale, or the model changed", () => {
    expect(needsEmbedding(undefined)).toBe(false);
    expect(needsEmbedding({ unembedded: 0, embeddingChanged: false })).toBe(false);
    expect(needsEmbedding({ unembedded: 2, embeddingChanged: false })).toBe(true);
    expect(needsEmbedding({ unembedded: 0, embeddingChanged: true })).toBe(true);
  });
});

describe("Indexer", () => {
  it("indexes changed record files without embeddings when a read reports them, and keeps the count it reports", async () => {
    const { indexer, kgd } = fixture([{ unembedded: 1 }]);
    const changed = vi.fn();
    indexer.onChange(changed);
    indexer.observe(lag(0, 0));
    expect(kgd.command).not.toHaveBeenCalled();
    indexer.observe(lag(2, 0));
    await vi.waitFor(() => expect(indexer.state).toEqual({ unembedded: 1, embeddingChanged: false }));
    expect(kgd.command).toHaveBeenCalledExactlyOnceWith(["index", "--no-embed"]);
    // Once for the first lag, once for the count the refresh reported.
    expect(changed).toHaveBeenCalledTimes(2);
  });

  it("runs once more after a refresh for the changes made while it ran, however many arrived", async () => {
    const { indexer, kgd, hold, release } = fixture();
    hold();
    const first = indexer.refresh();
    void indexer.refresh();
    void indexer.refresh();
    release();
    await first;
    expect(kgd.command).toHaveBeenCalledTimes(2);
  });

  it("embeds on request, reporting the count and clearing the state", async () => {
    const { indexer, kgd } = fixture([{ unembedded: 0, embedded: 3, embedding_error: null }]);
    indexer.observe(lag(0, 3));
    const busy: boolean[] = [];
    indexer.onChange(() => busy.push(indexer.busy));
    await expect(indexer.reembed()).resolves.toBe(3);
    expect(kgd.index).toHaveBeenCalledOnce();
    expect(indexer.state).toEqual({ unembedded: 0, embeddingChanged: false });
    expect(busy[0]).toBe(true);
    expect(indexer.busy).toBe(false);
  });

  it("reports an embedding failure and keeps the records awaiting embedding", async () => {
    const { indexer } = fixture([{ unembedded: 3, embedded: 0, embedding_error: "the retrieval extra is missing" }]);
    await expect(indexer.reembed()).rejects.toThrow("the retrieval extra is missing");
    expect(needsEmbedding(indexer.state)).toBe(true);
  });
});
