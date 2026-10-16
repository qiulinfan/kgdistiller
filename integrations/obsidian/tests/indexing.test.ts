import { describe, expect, it, vi } from "vitest";

import { embeddingChoices, embeddingSummary, Indexer, needsEmbedding } from "../src/indexing";

const lag = (changed: number, unembedded: number, embeddingChanged = false) =>
  ({ changed_files: changed, unembedded, embedding_changed: embeddingChanged });

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

describe("embeddingSummary", () => {
  const base = { embedding: "BAAI/bge-m3", precision: "int8", vectors: "BAAI/bge-m3 int8", unembedded: 0, embedding_changed: false, cached: [] };

  it("says what Re-embed would do", () => {
    expect(embeddingSummary(base)).toBe("BAAI/bge-m3 (int8): every record's vector is current.");
    expect(embeddingSummary({ ...base, unembedded: 2 })).toContain("2 records changed since embedding");
    expect(embeddingSummary({ ...base, vectors: "BAAI/bge-m3", embedding_changed: true }))
      .toBe("The index holds BAAI/bge-m3 vectors. Re-embed in the kgdistiller sidebar to embed every record with BAAI/bge-m3 (int8).");
    expect(embeddingSummary({ ...base, embedding: null, precision: null, embedding_changed: true })).toContain("dense lane is off");
  });

  it("feeds the Re-embed button", () => {
    const indexer = new Indexer({ configured: () => true, command: vi.fn(), index: vi.fn() }, vi.fn());
    indexer.observeStatus({ ...base, embedding_changed: true });
    expect(needsEmbedding(indexer.state)).toBe(true);
  });
});

describe("the sidebar's embedding choices", () => {
  it("lists the cached models with the one in use, then off", () => {
    const status = { embedding: "BAAI/bge-m3", precision: "int8", vectors: null, unembedded: 0, embedding_changed: true, cached: ["intfloat/multilingual-e5-large", "BAAI/bge-m3"] };
    expect(embeddingChoices(status)).toEqual([
      { value: "BAAI/bge-m3", label: "BAAI/bge-m3" },
      { value: "intfloat/multilingual-e5-large", label: "intfloat/multilingual-e5-large" },
      { value: "", label: "Off (keywords and names)" },
    ]);
    expect(embeddingChoices({ ...status, embedding: "x/uncached", cached: [] }).map((choice) => choice.value)).toEqual(["x/uncached", ""]);
  });
});
