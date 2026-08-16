import { describe, expect, it, vi } from "vitest";
import { ApiClient } from "../src/api/client";
import { ApiFault } from "../src/api/errors";
import type { NodeDetail } from "../src/api/contracts";
import { GenerationStore } from "../src/state/generation";
import { parseRoute, sameRoute } from "../src/state/router";
import { envelope, node, vaultCard } from "./fixtures";

describe("route identity", () => {
  it("only treats an exact canonical route as a refresh", () => {
    expect(sameRoute({ name: "node", handle: "alpha:a" }, { name: "node", handle: "alpha:a" })).toBe(true);
    expect(sameRoute({ name: "node", handle: "alpha:a" }, { name: "node", handle: "alpha:b" })).toBe(false);
    expect(sameRoute({ name: "search", query: "one", vault: null, scope: null }, { name: "search", query: "two", vault: null, scope: null })).toBe(false);
    expect(() => parseRoute("#/node?handle=alpha%3Aa?unknown=1")).toThrow(/malformed/u);
  });

  it("ignores an obsolete navigation's late generation fault", async () => {
    const status = await envelope({
      kind: "status" as const,
      api_version: 1 as const,
      read_only: true as const,
      registered_vaults: 1,
      healthy_vaults: 1,
      incomplete_vaults: 0
    });
    const vaults = await envelope({ kind: "vaults" as const, vaults: status.vault_generations.map(vaultCard) });
    const clearGeneration = vi.fn();
    const client = {
      status: vi.fn(async () => status),
      vaults: vi.fn(async () => vaults),
      clearGeneration
    } as unknown as ApiClient;
    const store = new GenerationStore(client);
    let rejectOld!: (error: Error) => void;
    let announceOld: (() => void) | null = null;
    const oldStarted = new Promise<void>((resolve) => { announceOld = resolve; });
    const oldResponse = new Promise<never>((_resolve, reject) => { rejectOld = reject; });
    const oldNavigation = store.navigate(
      { name: "node", handle: "alpha:old" },
      async () => {
        announceOld?.();
        return oldResponse;
      }
    );
    await oldStarted;

    const summary = node("alpha:new", "knowledge", "New node");
    const detail: NodeDetail = {
      ...summary,
      aliases: [],
      text: null,
      authority: null,
      provenance: null,
      open_actions: null
    };
    const current = await envelope({
      kind: "node" as const,
      node: detail,
      edges: [],
      evidence: [],
      omissions: [],
      truncated: false
    });
    await store.navigate({ name: "node", handle: summary.handle }, async () => current);
    rejectOld(new ApiFault(409, {
      schema: "qlkg-api-error-v1",
      status: "error",
      route: "node",
      vault_id: "alpha",
      error: { code: "stale-generation", message: "request generation is stale" },
      current_generation: "f".repeat(64),
      retryable: true
    }));
    await oldNavigation;

    expect(clearGeneration).not.toHaveBeenCalled();
    expect(store.state.route).toEqual({ name: "node", handle: summary.handle });
    expect(store.state.generation).toBe(current.generation);
    expect(store.state.response?.result).toMatchObject({ kind: "node", node: { handle: summary.handle } });
  });
});
