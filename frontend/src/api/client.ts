import type {
  ApiEnvelope,
  ApiRoute,
  DiffResult,
  ExcerptResult,
  NeighborsResult,
  NodeResult,
  RecallRequest,
  ContextResult,
  RootsResult,
  SourceResult,
  StaleResult,
  StatusResult,
  SearchResult,
  VaultsResult,
  VersionsResult
} from "./contracts";
import { ApiFault, ContractFailure, GenerationMismatch } from "./errors";
import { assertApiError, assertApiResponse, assertRecallRequest } from "./validators";

type Fetcher = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
type QueryValue = string | number | boolean | null | undefined;
const MAX_RESPONSE_BYTES = 4 * 1024 * 1024;
const MAX_CACHE_ENTRIES = 128;
const MAX_CACHE_BODY_BYTES = 256 * 1024;
const MAX_CACHE_WEIGHT = 16 * 1024 * 1024;
const CACHE_WEIGHT_MULTIPLIER = 32;
const GENERATION_HEADER = "Kgdistiller-Generation";

function segment(value: string, pattern: RegExp, label: string, maximum: number): string {
  if (value.length < 1 || value.length > maximum || !pattern.test(value)) throw new ContractFailure(`${label} is malformed`);
  return encodeURIComponent(value);
}

function queryString(values: Record<string, QueryValue>): string {
  const result = new URLSearchParams();
  for (const key of Object.keys(values).sort()) {
    const value = values[key];
    if (value !== undefined && value !== null && value !== "") result.set(key, String(value));
  }
  const encoded = result.toString();
  return encoded ? `?${encoded}` : "";
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const row = value as Record<string, unknown>;
    return `{${Object.keys(row).sort().map((key) => `${JSON.stringify(key)}:${stableJson(row[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

async function readBounded(response: Response): Promise<{ value: unknown; bytes: number }> {
  if (response.headers.get("Content-Type") !== "application/json; charset=utf-8") {
    throw new ContractFailure("API response content type is not canonical JSON");
  }
  const reader = response.body?.getReader();
  if (!reader) throw new ContractFailure("API response has no body");
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const item = await reader.read();
    if (item.done) break;
    size += item.value.byteLength;
    if (size > MAX_RESPONSE_BYTES) {
      await reader.cancel();
      throw new ContractFailure("API response exceeds its byte bound");
    }
    chunks.push(item.value);
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return {
      value: JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as unknown,
      bytes: size
    };
  } catch {
    throw new ContractFailure("API response is not strict JSON");
  }
}

interface CacheEntry {
  etag: string;
  payload: ApiEnvelope;
  weight: number;
}

type ExpectedPayload = (payload: ApiEnvelope) => boolean;

function vaultOf(handle: string): string {
  return handle.split(":", 1)[0] ?? "";
}

function recallVaults(payload: ApiEnvelope): string[] {
  if (payload.result.kind !== "search" && payload.result.kind !== "context") return [];
  return [
    ...payload.result.nodes.map((node) => node.vault_id),
    ...payload.result.edges.flatMap((edge) => [vaultOf(edge.source), vaultOf(edge.target)]),
    ...payload.result.evidence.map((row) => vaultOf(row.handle)),
    ...payload.result.resolutions.flatMap((row) => row.matches.map(vaultOf))
  ];
}

function routeVaults(payload: ApiEnvelope): string[] {
  if (payload.result.kind === "node") {
    return [
      payload.result.node.vault_id,
      ...payload.result.edges.flatMap((edge) => [vaultOf(edge.source), vaultOf(edge.target)]),
      ...payload.result.evidence.flatMap((row) => [vaultOf(row.handle), ...(row.source ? [vaultOf(row.source)] : []), ...(row.target ? [vaultOf(row.target)] : [])])
    ];
  }
  if (payload.result.kind === "neighbors") {
    return [
      vaultOf(payload.result.center),
      ...payload.result.nodes.map((node) => node.vault_id),
      ...payload.result.edges.flatMap((edge) => [vaultOf(edge.source), vaultOf(edge.target)])
    ];
  }
  return [];
}

export class ApiClient {
  readonly #fetch: Fetcher;
  readonly #cache = new Map<string, CacheEntry>();
  #cacheWeight = 0;

  constructor(fetcher: Fetcher = globalThis.fetch.bind(globalThis)) {
    this.#fetch = fetcher;
  }

  clearGeneration(): void {
    this.#cache.clear();
    this.#cacheWeight = 0;
  }

  async #request<T extends ApiEnvelope>(
    method: "GET" | "POST",
    path: string,
    expectedKind: ApiRoute,
    options: { generation?: string; body?: RecallRequest; signal?: AbortSignal; expected?: ExpectedPayload } = {}
  ): Promise<T> {
    const body = options.body ? stableJson(assertRecallRequest(options.body)) : undefined;
    const key = `${options.generation ?? "bootstrap"}\n${method}\n${path}\n${body ?? ""}`;
    const cached = this.#cache.get(key);
    const headers = new Headers({ Accept: "application/json" });
    if (options.generation) headers.set(GENERATION_HEADER, options.generation);
    if (body !== undefined) headers.set("Content-Type", "application/json; charset=utf-8");
    if (cached) headers.set("If-None-Match", cached.etag);
    const response = await this.#fetch(path, { method, headers, body, signal: options.signal });
    if (response.status === 304) {
      if (!cached) throw new ContractFailure("304 response has no generation cache entry");
      if (cached.payload.route !== expectedKind || cached.payload.result.kind !== expectedKind) {
        throw new ContractFailure("cached API response belongs to another route");
      }
      if (options.expected && !options.expected(cached.payload)) throw new ContractFailure("cached API response identity is inconsistent");
      const headerGeneration = response.headers.get(GENERATION_HEADER);
      if (headerGeneration !== cached.payload.generation || (options.generation && headerGeneration !== options.generation)) {
        throw new GenerationMismatch();
      }
      return cached.payload as T;
    }
    const decoded = await readBounded(response);
    if (!response.ok) {
      const error = assertApiError(decoded.value);
      const errorGeneration = response.headers.get(GENERATION_HEADER);
      if (errorGeneration !== error.current_generation) throw new GenerationMismatch();
      if (error.route !== null && error.route !== expectedKind) throw new ContractFailure("API error belongs to another route");
      throw new ApiFault(response.status, error);
    }
    const payload = await assertApiResponse(decoded.value);
    if (payload.route !== expectedKind || payload.result.kind !== expectedKind) {
      throw new ContractFailure("API response belongs to another route");
    }
    if (options.expected && !options.expected(payload)) throw new ContractFailure("API response identity is inconsistent");
    const headerGeneration = response.headers.get(GENERATION_HEADER);
    if (headerGeneration !== payload.generation || (options.generation && payload.generation !== options.generation)) {
      throw new GenerationMismatch();
    }
    const etag = response.headers.get("ETag");
    if (!etag || !/^"[0-9a-f]{64}"$/u.test(etag)) throw new ContractFailure("API ETag is malformed");
    const previous = this.#cache.get(key);
    if (previous) {
      this.#cache.delete(key);
      this.#cacheWeight -= previous.weight;
    }
    if (method === "GET" && decoded.bytes <= MAX_CACHE_BODY_BYTES) {
      const weight = decoded.bytes * CACHE_WEIGHT_MULTIPLIER + new TextEncoder().encode(key).byteLength * 2 + 1024;
      if (weight <= MAX_CACHE_WEIGHT) {
        this.#cache.set(key, { etag, payload, weight });
        this.#cacheWeight += weight;
      }
    }
    while (this.#cache.size > MAX_CACHE_ENTRIES || this.#cacheWeight > MAX_CACHE_WEIGHT) {
      const oldest = this.#cache.keys().next().value as string | undefined;
      if (oldest === undefined) break;
      this.#cacheWeight -= this.#cache.get(oldest)?.weight ?? 0;
      this.#cache.delete(oldest);
    }
    return payload as T;
  }

  status(signal?: AbortSignal): Promise<ApiEnvelope<StatusResult>> {
    return this.#request("GET", "/api/v1/status", "status", { signal });
  }

  vaults(signal?: AbortSignal): Promise<ApiEnvelope<VaultsResult>> {
    return this.#request("GET", "/api/v1/vaults", "vaults", { signal });
  }

  roots(vault: string, generation: string, options: { limit?: number; includeStale?: boolean; signal?: AbortSignal } = {}): Promise<ApiEnvelope<RootsResult>> {
    const id = segment(vault, /^[a-z0-9]+(?:[._-][a-z0-9]+)*$/u, "Vault identity", 64);
    return this.#request("GET", `/api/v1/vaults/${id}/roots${queryString({ limit: options.limit, include_stale: options.includeStale })}`, "roots", {
      generation,
      signal: options.signal,
      expected: (payload) => payload.result.kind === "roots" && payload.result.nodes.every((node) => node.vault_id === vault)
    });
  }

  node(handle: string, generation: string, options: { includeStale?: boolean; signal?: AbortSignal } = {}): Promise<ApiEnvelope<NodeResult>> {
    const [vault, node] = handle.split(":", 2);
    const vaultId = segment(vault ?? "", /^[a-z0-9]+(?:[._-][a-z0-9]+)*$/u, "Vault identity", 64);
    const nodeId = segment(node ?? "", /^[a-z0-9]+(?:-[a-z0-9]+)*$/u, "node identity", 256);
    return this.#request("GET", `/api/v1/vaults/${vaultId}/nodes/${nodeId}${queryString({ include_stale: options.includeStale })}`, "node", {
      generation,
      signal: options.signal,
      expected: (payload) => payload.result.kind === "node" && payload.result.node.handle === handle && routeVaults(payload).every((item) => item === vaultId)
    });
  }

  neighbors(handle: string, generation: string, options: { limit?: number; includeStale?: boolean; direction?: "incoming" | "outgoing" | "both"; edgeTypes?: string[]; signal?: AbortSignal } = {}): Promise<ApiEnvelope<NeighborsResult>> {
    const [vault, node] = handle.split(":", 2);
    const vaultId = segment(vault ?? "", /^[a-z0-9]+(?:[._-][a-z0-9]+)*$/u, "Vault identity", 64);
    const nodeId = segment(node ?? "", /^[a-z0-9]+(?:-[a-z0-9]+)*$/u, "node identity", 256);
    return this.#request("GET", `/api/v1/vaults/${vaultId}/nodes/${nodeId}/neighbors${queryString({ limit: options.limit, include_stale: options.includeStale, direction: options.direction, edge_types: options.edgeTypes?.join(",") })}`, "neighbors", {
      generation,
      signal: options.signal,
      expected: (payload) => payload.result.kind === "neighbors" && payload.result.center === handle && routeVaults(payload).every((item) => item === vaultId)
    });
  }

  stale(vault: string, generation: string, options: { limit?: number; cursor?: string; signal?: AbortSignal } = {}): Promise<ApiEnvelope<StaleResult>> {
    const id = segment(vault, /^[a-z0-9]+(?:[._-][a-z0-9]+)*$/u, "Vault identity", 64);
    return this.#request("GET", `/api/v1/vaults/${id}/stale${queryString({ limit: options.limit, cursor: options.cursor })}`, "stale", {
      generation,
      signal: options.signal,
      expected: (payload) => payload.result.kind === "stale" && payload.result.items.every((item) => item.kind === "node" ? item.node.vault_id === vault : item.kind === "source" ? item.source.vault_id === vault : vaultOf(item.edge.source) === vault && vaultOf(item.edge.target) === vault)
    });
  }

  source(vault: string, document: string, generation: string, signal?: AbortSignal): Promise<ApiEnvelope<SourceResult>> {
    return this.#request("GET", this.#sourcePath(vault, document), "source", {
      generation,
      signal,
      expected: (payload) => payload.result.kind === "source" && payload.result.source.vault_id === vault && payload.result.source.document_id === document
    });
  }

  versions(vault: string, document: string, generation: string, options: { limit?: number; beforeSequence?: number; signal?: AbortSignal } = {}): Promise<ApiEnvelope<VersionsResult>> {
    return this.#request("GET", `${this.#sourcePath(vault, document)}/versions${queryString({ limit: options.limit, before_sequence: options.beforeSequence })}`, "versions", { generation, signal: options.signal, expected: (payload) => payload.result.kind === "versions" && payload.result.document_id === document });
  }

  diff(vault: string, document: string, generation: string, options: { from?: string; to?: string; signal?: AbortSignal } = {}): Promise<ApiEnvelope<DiffResult>> {
    return this.#request("GET", `${this.#sourcePath(vault, document)}/diff${queryString({ from: options.from, to: options.to })}`, "diff", { generation, signal: options.signal, expected: (payload) => payload.result.kind === "diff" && payload.result.document_id === document });
  }

  excerpt(vault: string, document: string, generation: string, options: { version?: string; line?: number; radius?: number; signal?: AbortSignal } = {}): Promise<ApiEnvelope<ExcerptResult>> {
    return this.#request("GET", `${this.#sourcePath(vault, document)}/excerpt${queryString({ version: options.version, line: options.line, radius: options.radius })}`, "excerpt", { generation, signal: options.signal, expected: (payload) => payload.result.kind === "excerpt" && payload.result.document_id === document });
  }

  search(request: RecallRequest, generation: string, signal?: AbortSignal): Promise<ApiEnvelope<SearchResult>> {
    const allowed = new Set(request.vault_ids);
    return this.#request("POST", "/api/v1/search", "search", { generation, body: request, signal, expected: (payload) => allowed.size === 0 || recallVaults(payload).every((vault) => allowed.has(vault)) });
  }

  context(request: RecallRequest, generation: string, signal?: AbortSignal): Promise<ApiEnvelope<ContextResult>> {
    const allowed = new Set(request.vault_ids.length ? request.vault_ids : request.handles.map(vaultOf));
    return this.#request("POST", "/api/v1/context", "context", { generation, body: request, signal, expected: (payload) => allowed.size === 0 || recallVaults(payload).every((vault) => allowed.has(vault)) });
  }

  #sourcePath(vault: string, document: string): string {
    const vaultId = segment(vault, /^[a-z0-9]+(?:[._-][a-z0-9]+)*$/u, "Vault identity", 64);
    const documentId = segment(document, /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/u, "document identity", 36);
    return `/api/v1/vaults/${vaultId}/sources/${documentId}`;
  }
}
