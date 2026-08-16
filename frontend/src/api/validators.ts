import { apiError, apiResponse, recallRequest } from "./validators.generated.js";
import type {
  ApiEnvelope,
  ApiErrorPayload,
  ApiResult,
  RecallRequest,
  VaultGeneration
} from "./contracts";
import { ContractFailure } from "./errors";

const SHA256 = /^[0-9a-f]{64}$/u;
const HANDLE = /^([a-z0-9]+(?:[._-][a-z0-9]+)*):([a-z0-9]+(?:-[a-z0-9]+)*)$/u;
const RFC3339_Z = /^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?Z$/u;
const WINDOWS_RESERVED = new Set([
  "con", "prn", "aux", "nul",
  ...Array.from({ length: 9 }, (_, index) => `com${index + 1}`),
  ...Array.from({ length: 9 }, (_, index) => `lpt${index + 1}`)
]);
const routeKinds = new Set([
  "status", "vaults", "roots", "node", "neighbors", "stale",
  "source", "versions", "diff", "excerpt", "search", "context"
]);

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${canonical(record[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

async function sha256Json(value: unknown): Promise<string> {
  const bytes = new TextEncoder().encode(canonical(value));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function sha256Text(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function splitHandle(handle: string): [string, string] {
  const match = HANDLE.exec(handle);
  if (!match?.[1] || !match[2]) throw new ContractFailure("qualified handle is malformed");
  return [match[1], match[2]];
}

function compareText(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

function assertPortablePath(value: string): void {
  const encoded = new TextEncoder().encode(value);
  const parts = value.split("/");
  if (
    value === "" || encoded.byteLength > 4096 || value.includes("\0") || value.includes("\\") ||
    value.normalize("NFC") !== value || value.startsWith("/") || value.endsWith("/") ||
    /^[A-Za-z]:/u.test(value) || parts.some((part) => part === "" || part === "." || part === "..")
  ) throw new ContractFailure("portable source path is malformed");
  for (const part of parts) {
    const reserved = part.split(".", 1)[0]?.toLocaleLowerCase("en-US") ?? "";
    if (
      part.endsWith(" ") || part.endsWith(".") || WINDOWS_RESERVED.has(reserved) ||
      [...part].some((character) => {
        const codepoint = character.codePointAt(0) ?? 0;
        return codepoint < 32 || codepoint === 127 || '<>:"|?*'.includes(character);
      })
    ) throw new ContractFailure("portable source path is not host neutral");
  }
}

function isRealRfc3339(value: string): boolean {
  const match = RFC3339_Z.exec(value);
  if (!match) return false;
  const fields = match.slice(1, 7).map(Number);
  const [year, month, day, hour, minute, second] = fields;
  if (
    year === undefined || month === undefined || day === undefined || hour === undefined ||
    minute === undefined || second === undefined || month < 1 || month > 12 || day < 1 ||
    hour > 23 || minute > 59 || second > 59
  ) return false;
  const instant = new Date(value);
  return !Number.isNaN(instant.valueOf()) && instant.getUTCFullYear() === year &&
    instant.getUTCMonth() + 1 === month && instant.getUTCDate() === day &&
    instant.getUTCHours() === hour && instant.getUTCMinutes() === minute &&
    instant.getUTCSeconds() === second;
}

function splitlinesCount(value: string): number {
  if (value === "") return 0;
  let count = 0;
  let finalStart = 0;
  for (let index = 0; index < value.length; index += 1) {
    const codepoint = value.charCodeAt(index);
    const separator = codepoint === 0x0a || codepoint === 0x0b || codepoint === 0x0c ||
      (codepoint >= 0x1c && codepoint <= 0x1e) || codepoint === 0x85 ||
      codepoint === 0x2028 || codepoint === 0x2029 || codepoint === 0x0d;
    if (!separator) continue;
    if (codepoint === 0x0d && value.charCodeAt(index + 1) === 0x0a) index += 1;
    count += 1;
    finalStart = index + 1;
  }
  return count + (finalStart < value.length ? 1 : 0);
}

async function validateGenerations(value: ApiEnvelope): Promise<void> {
  const seen = new Set<string>();
  const orderedIds = value.vault_generations.map((row) => row.vault_id);
  if (orderedIds.join("\n") !== [...orderedIds].sort().join("\n")) {
    throw new ContractFailure("Vault generations are not canonical");
  }
  for (const row of value.vault_generations) {
    if (seen.has(row.vault_id)) throw new ContractFailure("Vault generations are duplicated");
    seen.add(row.vault_id);
    const projected = {
      vault_manifest_sha256: row.vault_manifest_sha256,
      graph_manifest_sha256: row.graph_manifest_sha256,
      graph_sha256: row.graph_sha256,
      source_ledger_generation_sha256: row.source_ledger_generation_sha256,
      authority_generation_sha256: row.authority_generation_sha256
    };
    if (row.generation !== await sha256Json(projected)) {
      throw new ContractFailure("Vault generation projection is inconsistent");
    }
  }
  const incompleteIds = value.incomplete_vaults.map((row) => row.vault_id);
  if (
    incompleteIds.join("\n") !== [...incompleteIds].sort().join("\n") ||
    new Set(incompleteIds).size !== incompleteIds.length ||
    incompleteIds.some((id) => seen.has(id))
  ) {
    throw new ContractFailure("Vault health rows are inconsistent");
  }
  const projected = {
    registry_generation: value.registry_generation,
    vaults: value.vault_generations.map((row) => ({ vault_id: row.vault_id, generation: row.generation })),
    incomplete_vaults: value.incomplete_vaults.map((row) => ({ vault_id: row.vault_id, code: row.code }))
  };
  if (value.generation !== await sha256Json(projected)) {
    throw new ContractFailure("federation generation projection is inconsistent");
  }
  if ((value.status === "partial") !== (value.incomplete_vaults.length > 0)) {
    throw new ContractFailure("partial status is inconsistent");
  }
}

async function validateRenderedFields(result: ApiResult, complete: Set<string>): Promise<void> {
  const record = result as unknown as Record<string, unknown>;
  if (result.kind === "neighbors" && !complete.has(splitHandle(result.center)[0])) {
    throw new ContractFailure("neighbor center belongs to an unavailable Vault");
  }
  const nodes = Array.isArray(record.nodes) ? [...record.nodes] : [];
  const edges = Array.isArray(record.edges) ? [...record.edges] : [];
  const sources: Array<{
    vault_id: string; document_id: string; path: string; status: string;
    current_version_id: string; version_count: number;
  }> = [];
  if (result.kind === "node") nodes.push(result.node);
  if (result.kind === "source") sources.push(result.source);
  if (result.kind === "stale") {
    const staleKeys: string[] = [];
    for (const item of result.items) {
      if (item.kind === "node") {
        nodes.push(item.node);
        staleKeys.push(`node/${item.node.handle}`);
        const stateMatches = (item.reason === "needs-review" && item.node.curation_status === "needs-review") ||
          (item.reason === "pending" && item.node.curation_status === "pending") ||
          (item.reason === "orphaned" && item.node.source_status === "orphaned");
        if (!stateMatches) throw new ContractFailure("stale node reason is inconsistent");
      } else if (item.kind === "edge") {
        edges.push(item.edge);
        staleKeys.push(`edge/${item.edge.source}/${item.edge.relation}/${item.edge.target}`);
        if (item.reason !== "needs-review" || item.edge.curation_status !== "needs-review") {
          throw new ContractFailure("stale edge reason is inconsistent");
        }
      } else {
        sources.push(item.source);
        staleKeys.push(`source/${item.source.vault_id}/${item.source.document_id}`);
        if (item.reason !== item.source.status) throw new ContractFailure("stale source reason is inconsistent");
      }
    }
    const ordered = [...staleKeys].sort(compareText);
    if (new Set(staleKeys).size !== staleKeys.length || staleKeys.some((key, index) => key !== ordered[index])) {
      throw new ContractFailure("stale items are not canonical");
    }
    if (result.truncated) {
      if (staleKeys.length === 0 || result.next_cursor !== staleKeys.at(-1)) {
        throw new ContractFailure("stale cursor is inconsistent");
      }
    } else if (result.next_cursor !== null) throw new ContractFailure("untruncated stale page has a cursor");
  }
  const laneOrder = new Map([["identity", 0], ["taxonomy", 1], ["lexical", 2], ["graph", 3]]);
  const laneRanks = new Map<string, Array<{ handle: string; score: number; rank: number }>>();
  for (const value of nodes) {
    const node = value as {
      handle: string; vault_id: string; node_id: string; parents: string[];
      score?: number; lane_evidence?: Array<{
        lane: string; rank: number; score: number; reason: string;
        match_kind: string | null; matched_fields: string[]; matched_terms: string[];
        scope: string | null; seed: string | null;
        path: Array<{ source: string; relation: string; target: string }>;
      }>;
      authority?: string | null;
      provenance?: { authority: string; line: number; definition_start_line: number; definition_end_line: number } | null;
      open_actions?: { authority: string; line: number } | null;
    };
    const [vault, nodeId] = splitHandle(node.handle);
    if (vault !== node.vault_id || nodeId !== node.node_id || !complete.has(vault)) {
      throw new ContractFailure("node identity is inconsistent");
    }
    for (const parent of node.parents) {
      if (splitHandle(parent)[0] !== vault) throw new ContractFailure("node parent crosses a Vault");
    }
    if ("provenance" in node || "open_actions" in node) {
      if (node.authority === null) {
        if (node.provenance !== null || node.open_actions !== null) throw new ContractFailure("node provenance is inconsistent");
      } else if (
        !node.authority || !node.provenance || !node.open_actions ||
        node.provenance.authority !== node.authority || node.open_actions.authority !== node.authority ||
        node.provenance.line !== node.open_actions.line ||
        node.provenance.definition_start_line > node.provenance.line ||
        node.provenance.definition_end_line < node.provenance.line
      ) throw new ContractFailure("node provenance is inconsistent");
      if (node.authority !== null) {
        assertPortablePath(node.authority);
        assertPortablePath(node.provenance?.authority ?? "");
        assertPortablePath(node.open_actions?.authority ?? "");
      }
    }
    if (node.lane_evidence) {
      const lanes = node.lane_evidence.map((row) => row.lane);
      const canonicalLanes = [...lanes].sort((left, right) => (laneOrder.get(left) ?? 99) - (laneOrder.get(right) ?? 99));
      if (new Set(lanes).size !== lanes.length || lanes.join("\n") !== canonicalLanes.join("\n")) {
        throw new ContractFailure("search lanes are not canonical");
      }
      const total = node.lane_evidence.reduce((sum, row) => sum + row.score, 0);
      if (Math.abs(total - (node.score ?? -1)) > 1e-9) throw new ContractFailure("search score is inconsistent");
      for (const row of node.lane_evidence) {
        const ranked = laneRanks.get(row.lane) ?? [];
        ranked.push({ handle: node.handle, score: row.score, rank: row.rank });
        laneRanks.set(row.lane, ranked);
        const emptyLexical = row.matched_fields.length === 0 && row.matched_terms.length === 0;
        if (row.scope !== null && splitHandle(row.scope)[0] !== vault) throw new ContractFailure("taxonomy scope crosses a Vault");
        if (row.seed !== null && splitHandle(row.seed)[0] !== vault) throw new ContractFailure("graph seed crosses a Vault");
        for (const step of row.path) {
          if (splitHandle(step.source)[0] !== vault || splitHandle(step.target)[0] !== vault) {
            throw new ContractFailure("lane path crosses a Vault");
          }
        }
        let valid = false;
        if (row.lane === "identity") {
          valid = new Set(["exact-id/id", "exact-label/label", "reviewed-alias/alias"]).has(`${row.reason}/${row.match_kind}`) && emptyLexical && row.scope === null && row.seed === null && row.path.length === 0;
        } else if (row.lane === "taxonomy") {
          valid = row.reason === "scope-member" && row.match_kind === null && emptyLexical && row.scope !== null && row.seed === null;
          if (valid && row.path.length > 0) {
            valid = row.path[0]?.source === row.scope && row.path.at(-1)?.target === node.handle && row.path.every((step, index) => step.relation === "contains" && (index === 0 || row.path[index - 1]?.target === step.source));
          } else if (valid) valid = row.scope === node.handle;
        } else if (row.lane === "lexical") {
          valid = new Set(["token-overlap", "phrase-match"]).has(row.reason) && row.match_kind === null && !emptyLexical && row.scope === null && row.seed === null && row.path.length === 0;
        } else if (row.lane === "graph") {
          let cursor = row.seed;
          valid = cursor !== null;
          for (const step of row.path) {
            if (step.source === cursor) cursor = step.target;
            else if (step.target === cursor) cursor = step.source;
            else valid = false;
          }
          valid = valid && cursor === node.handle && row.match_kind === null && emptyLexical && row.scope === null && ((row.reason === "trusted-seed" && row.path.length === 0) || (row.reason === "trusted-edge" && row.path.length > 0));
        }
        if (!valid) throw new ContractFailure("search lane evidence is inconsistent");
      }
    }
  }
  for (const rows of laneRanks.values()) {
    rows.sort((left, right) => right.score - left.score || compareText(left.handle, right.handle));
    if (rows.some((row, index) => row.rank !== index + 1)) throw new ContractFailure("search lane ranks are inconsistent");
  }
  for (const value of edges) {
    const edge = value as { source: string; target: string };
    const sourceVault = splitHandle(edge.source)[0];
    if (splitHandle(edge.target)[0] !== sourceVault || !complete.has(sourceVault)) {
      throw new ContractFailure("edge crosses a Vault");
    }
  }
  const evidence = Array.isArray(record.evidence) ? record.evidence : [];
  for (const value of evidence) {
    const row = value as {
      kind: "concept" | "relation"; handle: string; source: string | null;
      relation: string | null; target: string | null; document_id: string;
      version_id: string; start_line: number; end_line: number;
      start_column: number | null; end_column: number | null; excerpt: string;
      excerpt_sha256: string;
    };
    const vault = splitHandle(row.handle)[0];
    if (!complete.has(vault)) throw new ContractFailure("evidence belongs to an unavailable Vault");
    const concept = row.kind === "concept";
    if (concept) {
      if (row.source !== null || row.relation !== null || row.target !== null) {
        throw new ContractFailure("concept evidence has relation endpoints");
      }
    } else if (
      row.source === null || row.relation === null || row.target === null || row.source !== row.handle ||
      splitHandle(row.source)[0] !== vault || splitHandle(row.target)[0] !== vault
    ) throw new ContractFailure("relation evidence is inconsistent");
    if (!row.version_id.startsWith(`doc:${row.document_id}:`) || row.end_line < row.start_line) {
      throw new ContractFailure("evidence source coordinates are inconsistent");
    }
    if ((row.start_column === null) !== (row.end_column === null)) {
      throw new ContractFailure("evidence columns are incomplete");
    }
    if (row.start_column !== null && row.start_line === row.end_line && (row.end_column ?? 0) <= row.start_column) {
      throw new ContractFailure("evidence columns are reversed");
    }
    if (row.excerpt_sha256 !== await sha256Text(row.excerpt)) {
      throw new ContractFailure("evidence excerpt digest is inconsistent");
    }
    assertPortablePath((value as { source_path: string }).source_path);
  }
  for (const source of sources) {
    if (!complete.has(source.vault_id) || source.current_version_id !== `doc:${source.document_id}:v${String(source.version_count).padStart(8, "0")}`) {
      throw new ContractFailure("source identity is inconsistent");
    }
    assertPortablePath(source.path);
  }
  if (result.kind === "versions") {
    let previous = Number.POSITIVE_INFINITY;
    for (const version of result.versions) {
      if (!version.version_id.startsWith(`doc:${result.document_id}:v`) || version.sequence >= previous || !isRealRfc3339(version.captured_at)) {
        throw new ContractFailure("source version history is inconsistent");
      }
      if (version.version_id !== `doc:${result.document_id}:v${String(version.sequence).padStart(8, "0")}`) throw new ContractFailure("source version sequence is inconsistent");
      const predecessor = version.sequence === 1 ? null : `doc:${result.document_id}:v${String(version.sequence - 1).padStart(8, "0")}`;
      if (version.predecessor_version_id !== predecessor) throw new ContractFailure("source version predecessor is inconsistent");
      assertPortablePath(version.captured_path);
      previous = version.sequence;
    }
    if (result.truncated) {
      if (result.versions.length === 0 || result.next_before_sequence !== result.versions.at(-1)?.sequence) {
        throw new ContractFailure("source version cursor is inconsistent");
      }
    } else if (result.next_before_sequence !== null) throw new ContractFailure("untruncated source versions have a cursor");
  }
  if (result.kind === "diff") {
    if (!result.to_version_id.startsWith(`doc:${result.document_id}:`) || (result.from_version_id !== null && !result.from_version_id.startsWith(`doc:${result.document_id}:`))) throw new ContractFailure("source diff identity is inconsistent");
    assertPortablePath(result.path);
    if (
      result.max_bytes !== 1024 * 1024 || result.max_lines !== 10_000 ||
      splitlinesCount(result.text) !== result.emitted_lines ||
      new TextEncoder().encode(result.text).byteLength > result.max_bytes
    ) throw new ContractFailure("source diff bounds are inconsistent");
  }
  if (result.kind === "excerpt") {
    if (!result.version_id.startsWith(`doc:${result.document_id}:`)) throw new ContractFailure("source excerpt identity is inconsistent");
    assertPortablePath(result.path);
    if (result.lines.length > 0) {
      if (
        result.line < result.start || result.line > result.end || result.start !== result.lines[0]?.number ||
        result.end !== result.lines.at(-1)?.number || result.lines.some((row, index) => row.number !== result.start + index)
      ) throw new ContractFailure("source excerpt lines are inconsistent");
    } else if (result.start !== 1 || result.end !== 0 || result.line !== 1) {
      throw new ContractFailure("empty source excerpt bounds are inconsistent");
    }
    const excerpt = result.lines.map((row) => row.text).join("\n");
    if (result.excerpt_sha256 !== await sha256Text(excerpt)) throw new ContractFailure("source excerpt digest is inconsistent");
  }
  if (Array.isArray(record.omissions) && record.omissions.length > 0 && record.truncated !== true) {
    throw new ContractFailure("API omissions require a truncated result");
  }
  if (Array.isArray(record.resolutions)) {
    for (const value of record.resolutions) {
      const resolution = value as { status: string; match_kind: string | null; matches: string[]; overflow: boolean };
      if (resolution.matches.some((handle) => !complete.has(splitHandle(handle)[0]))) {
        throw new ContractFailure("resolution belongs to an unavailable Vault");
      }
      let valid = false;
      if (resolution.status === "missing") valid = resolution.matches.length === 0 && resolution.match_kind === null && !resolution.overflow;
      else if (resolution.status === "alias") valid = resolution.matches.length === 1 && resolution.match_kind === "alias" && !resolution.overflow;
      else if (resolution.status === "exact") valid = resolution.matches.length === 1 && ["id", "label"].includes(resolution.match_kind ?? "") && !resolution.overflow;
      else valid = (resolution.matches.length >= 2 || resolution.overflow) &&
        (["id", "label", "alias", "mixed"].includes(resolution.match_kind ?? "") ||
          (resolution.matches.length === 0 && resolution.overflow && resolution.match_kind === null));
      if (!valid) throw new ContractFailure("resolution fields are inconsistent");
    }
  }
}

export async function assertApiResponse(value: unknown): Promise<ApiEnvelope> {
  if (!apiResponse(value)) throw new ContractFailure("API response violates its schema");
  const envelope = value as ApiEnvelope;
  if (!routeKinds.has(envelope.route) || envelope.route !== envelope.result.kind) {
    throw new ContractFailure("API route and result kind disagree");
  }
  await validateGenerations(envelope);
  await validateRenderedFields(envelope.result, new Set(envelope.vault_generations.map((row) => row.vault_id)));
  if (envelope.result.kind === "status") {
    if (
      envelope.result.api_version !== 1 || envelope.result.read_only !== true ||
      envelope.result.healthy_vaults !== envelope.vault_generations.length ||
      envelope.result.incomplete_vaults !== envelope.incomplete_vaults.length ||
      envelope.result.registered_vaults !== envelope.vault_generations.length + envelope.incomplete_vaults.length
    ) throw new ContractFailure("status counts are inconsistent");
  }
  if (envelope.result.kind === "vaults") {
    const rows = envelope.result.vaults;
    if (rows.length !== envelope.vault_generations.length) throw new ContractFailure("Vault cards are incomplete");
    rows.forEach((row, index) => {
      const generation = envelope.vault_generations[index] as VaultGeneration | undefined;
      if (!generation || Object.keys(generation).some((key) => row[key as keyof VaultGeneration] !== generation[key as keyof VaultGeneration])) {
        throw new ContractFailure("Vault card generation is inconsistent");
      }
      if (Object.values(row.source_freshness).reduce((sum, count) => sum + count, 0) !== row.counts.documents) {
        throw new ContractFailure("Vault source freshness counts are inconsistent");
      }
    });
  }
  return envelope;
}

export function assertApiError(value: unknown): ApiErrorPayload {
  if (!apiError(value)) throw new ContractFailure("API error violates its schema");
  return value as ApiErrorPayload;
}

export function assertRecallRequest(value: unknown): RecallRequest {
  if (!recallRequest(value)) throw new ContractFailure("recall request violates its schema");
  return value as RecallRequest;
}

export function isSha256(value: string): boolean {
  return SHA256.test(value);
}
