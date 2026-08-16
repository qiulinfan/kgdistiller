import type {
  ApiEnvelope,
  ApiResult,
  NodeSummary,
  SourceDetail,
  VaultGeneration,
  VaultSummary
} from "../src/api/contracts";

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const row = value as Record<string, unknown>;
    return `{${Object.keys(row).sort().map((key) => `${JSON.stringify(key)}:${canonical(row[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function generation(vaultId: string): Promise<VaultGeneration> {
  const projection = {
    vault_manifest_sha256: "a".repeat(64),
    graph_manifest_sha256: "b".repeat(64),
    graph_sha256: "c".repeat(64),
    source_ledger_generation_sha256: null,
    authority_generation_sha256: "d".repeat(64)
  };
  return {
    vault_id: vaultId,
    generation: await sha256(canonical(projection)),
    ...projection,
    live_source_generation_sha256: null
  };
}

export async function envelope<T extends ApiResult>(result: T, vaultIds = ["alpha"]): Promise<ApiEnvelope<T>> {
  const rows = await Promise.all([...vaultIds].sort().map(generation));
  const registry = "e".repeat(64);
  const federation = await sha256(canonical({
    registry_generation: registry,
    vaults: rows.map((row) => ({ vault_id: row.vault_id, generation: row.generation })),
    incomplete_vaults: []
  }));
  return {
    schema: "qlkg-api-response-v1",
    route: result.kind,
    status: "complete",
    generation: federation,
    registry_generation: registry,
    vault_generations: rows,
    incomplete_vaults: [],
    result
  };
}

export function node(handle = "alpha:root", type: NodeSummary["type"] = "field", label = "Root"): NodeSummary {
  const [vaultId = "", nodeId = ""] = handle.split(":", 2);
  return {
    handle,
    vault_id: vaultId,
    node_id: nodeId,
    type,
    label,
    curation_status: type === "knowledge" ? "current" : "not-applicable",
    source_status: type === "knowledge" ? "active" : "meta",
    parents: []
  };
}

export function source(path = "Sources/note.md", vaultId = "alpha"): SourceDetail {
  const document = "11111111-1111-1111-1111-111111111111";
  return {
    vault_id: vaultId,
    document_id: document,
    path,
    format: "markdown",
    status: "captured",
    current_version_id: `doc:${document}:v00000001`,
    normalized_text_sha256: "f".repeat(64),
    version_count: 1
  };
}

export function vaultCard(row: VaultGeneration): VaultSummary {
  return {
    ...row,
    label: row.vault_id,
    health: "current",
    counts: { nodes: 0, edges: 0, references: 0, documents: 0 },
    source_freshness: { current: 0, changed: 0, missing: 0, unavailable: 0 }
  };
}

export function jsonResponse(payload: unknown, generationValue: string, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Kgdistiller-Generation": generationValue,
      ETag: `"${"a".repeat(64)}"`
    }
  });
}
