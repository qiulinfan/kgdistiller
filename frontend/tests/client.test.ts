import { describe, expect, it, vi } from "vitest";
import { ApiClient } from "../src/api/client";
import { ContractFailure, GenerationMismatch } from "../src/api/errors";
import { envelope, jsonResponse, node, vaultCard } from "./fixtures";

describe("ApiClient closed transport", () => {
  it("rejects a valid envelope from the wrong route", async () => {
    const base = await envelope({ kind: "status", api_version: 1, read_only: true, registered_vaults: 1, healthy_vaults: 1, incomplete_vaults: 0 });
    const payload = { ...base, route: "vaults" as const, result: { kind: "vaults" as const, vaults: base.vault_generations.map(vaultCard) } };
    const fetcher = vi.fn(async () => jsonResponse(payload, payload.generation));
    const client = new ApiClient(fetcher);
    await expect(client.roots("alpha", payload.generation)).rejects.toBeInstanceOf(ContractFailure);
  });

  it("binds an error generation token to its HTTP header", async () => {
    const error = {
      schema: "qlkg-api-error-v1" as const,
      status: "error" as const,
      route: "roots" as const,
      vault_id: "alpha",
      error: { code: "stale-generation", message: "request generation is stale" },
      current_generation: "b".repeat(64),
      retryable: true
    };
    const fetcher = vi.fn(async () => jsonResponse(error, "c".repeat(64), 409));
    await expect(new ApiClient(fetcher).roots("alpha", "a".repeat(64))).rejects.toBeInstanceOf(GenerationMismatch);
  });

  it("does not cache a response above the per-entry body bound", async () => {
    const nodes = Array.from({ length: 400 }, (_, index) => node(`alpha:n-${index}`, "field", `${index}-${"界".repeat(248)}`));
    const payload = await envelope({ kind: "roots" as const, nodes, omissions: [], truncated: false });
    const seen: Array<string | null> = [];
    const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      seen.push(new Headers(init?.headers).get("If-None-Match"));
      return jsonResponse(payload, payload.generation);
    });
    const client = new ApiClient(fetcher);
    await client.roots("alpha", payload.generation);
    await client.roots("alpha", payload.generation);
    expect(seen).toEqual([null, null]);
  });

  it("binds a neighbor center to the requested handle", async () => {
    const payload = await envelope({ kind: "neighbors" as const, center: "alpha:other", nodes: [node("alpha:other")], edges: [], omissions: [], truncated: false });
    const fetcher = vi.fn(async () => jsonResponse(payload, payload.generation));
    await expect(new ApiClient(fetcher).neighbors("alpha:root", payload.generation)).rejects.toBeInstanceOf(ContractFailure);
  });

  it("rejects cross-Vault rows even when both Vaults are complete", async () => {
    const payload = await envelope({
      kind: "neighbors" as const,
      center: "alpha:root",
      nodes: [node("alpha:root"), node("beta:y"), node("beta:z")],
      edges: [{ source: "beta:y", relation: "implies" as const, target: "beta:z", evidence: null, curation_status: "current" as const }],
      omissions: [],
      truncated: false
    }, ["alpha", "beta"]);
    const fetcher = vi.fn(async () => jsonResponse(payload, payload.generation));
    await expect(new ApiClient(fetcher).neighbors("alpha:root", payload.generation)).rejects.toBeInstanceOf(ContractFailure);
  });
});
