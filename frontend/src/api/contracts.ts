export type Generation = string;

export interface VaultGeneration {
  vault_id: string;
  generation: Generation;
  vault_manifest_sha256: string;
  graph_manifest_sha256: string;
  graph_sha256: string;
  source_ledger_generation_sha256: string | null;
  authority_generation_sha256: string;
  live_source_generation_sha256: string | null;
}

export interface IncompleteVault {
  vault_id: string;
  code: string;
  message: string;
}

export interface SourceFreshness {
  current: number;
  changed: number;
  missing: number;
  unavailable: number;
}

export interface VaultSummary extends VaultGeneration {
  label: string;
  health: "current";
  counts: { nodes: number; edges: number; references: number; documents: number };
  source_freshness: SourceFreshness;
}

export type NodeType = "knowledge" | "field" | "topic";
export type Relation =
  | "contains"
  | "prerequisite-for"
  | "implies"
  | "generalizes"
  | "contrasts-with"
  | "derived-from";

export interface NodeSummary {
  handle: string;
  vault_id: string;
  node_id: string;
  type: NodeType;
  label: string;
  curation_status: "current" | "needs-review" | "pending" | "not-applicable";
  source_status: "active" | "meta" | "orphaned" | "not-applicable";
  parents: string[];
}

export interface NodeDetail extends NodeSummary {
  aliases: string[];
  text: string | null;
  authority: string | null;
  provenance: {
    authority: string;
    line: number;
    definition_start_line: number;
    definition_end_line: number;
    definition_sha256: string;
  } | null;
  open_actions: {
    kind: "open-authority";
    authority: string;
    line: number;
  } | null;
}

export interface Edge {
  source: string;
  relation: Relation;
  target: string;
  evidence: string | null;
  curation_status: "current" | "needs-review" | "not-applicable";
}

export interface LaneEvidence {
  lane: "identity" | "taxonomy" | "lexical" | "graph";
  rank: number;
  score: number;
  reason:
    | "exact-id"
    | "exact-label"
    | "reviewed-alias"
    | "scope-member"
    | "token-overlap"
    | "phrase-match"
    | "trusted-seed"
    | "trusted-edge";
  match_kind: "id" | "label" | "alias" | null;
  matched_fields: Array<"label" | "alias" | "body">;
  matched_terms: string[];
  scope: string | null;
  seed: string | null;
  path: Array<{ source: string; relation: Relation; target: string }>;
}

export interface SearchNode extends NodeSummary {
  score: number;
  lane_evidence: LaneEvidence[];
}

export interface Evidence {
  kind: "concept" | "relation";
  handle: string;
  source: string | null;
  relation: Relation | null;
  target: string | null;
  document_id: string;
  version_id: string;
  source_path: string;
  format: "markdown" | "typst" | "latex";
  start_line: number;
  end_line: number;
  start_column: number | null;
  end_column: number | null;
  excerpt: string;
  excerpt_sha256: string;
}

export interface Omission {
  kind: "node" | "edge" | "evidence" | "vault";
  id: string;
  reason: "limit" | "token-budget" | "scope" | "incomplete-vault" | "stale";
}

export interface Resolution {
  query: string;
  status: "exact" | "alias" | "ambiguous" | "missing";
  match_kind: "id" | "label" | "alias" | "mixed" | null;
  matches: string[];
  overflow: boolean;
}

export interface SourceDetail {
  vault_id: string;
  document_id: string;
  path: string;
  format: "markdown" | "typst" | "latex";
  status: "captured" | "reviewed-empty" | "distilled" | "stale" | "failed";
  current_version_id: string;
  normalized_text_sha256: string;
  version_count: number;
}

export interface SourceVersion {
  version_id: string;
  sequence: number;
  captured_at: string;
  captured_path: string;
  format: "markdown" | "typst" | "latex";
  predecessor_version_id: string | null;
  raw_sha256: string;
  normalized_text_sha256: string;
  byte_count: number;
  derivation_status:
    | "planned"
    | "committed"
    | "reviewed-empty"
    | "carried-forward"
    | "superseded"
    | "failed"
    | null;
}

export interface StatusResult {
  kind: "status";
  api_version: 1;
  read_only: true;
  registered_vaults: number;
  healthy_vaults: number;
  incomplete_vaults: number;
}

export interface VaultsResult { kind: "vaults"; vaults: VaultSummary[] }
export interface RootsResult { kind: "roots"; nodes: NodeSummary[]; omissions: Omission[]; truncated: boolean }
export interface NodeResult { kind: "node"; node: NodeDetail; edges: Edge[]; evidence: Evidence[]; omissions: Omission[]; truncated: boolean }
export interface NeighborsResult { kind: "neighbors"; center: string; nodes: NodeSummary[]; edges: Edge[]; omissions: Omission[]; truncated: boolean }

export type StaleItem =
  | { kind: "node"; node: NodeSummary; reason: "needs-review" | "pending" | "orphaned" }
  | { kind: "edge"; edge: Edge; reason: "needs-review" }
  | { kind: "source"; source: SourceDetail; reason: "stale" | "failed" };

export interface StaleResult { kind: "stale"; items: StaleItem[]; next_cursor: string | null; omissions: Omission[]; truncated: boolean }
export interface SourceResult { kind: "source"; source: SourceDetail }
export interface VersionsResult { kind: "versions"; document_id: string; versions: SourceVersion[]; next_before_sequence: number | null; truncated: boolean }
export interface DiffResult { kind: "diff"; document_id: string; path: string; from_version_id: string | null; to_version_id: string; semantic_changed: boolean; text: string; truncated: boolean; emitted_lines: number; max_bytes: number; max_lines: number }
export interface ExcerptResult { kind: "excerpt"; document_id: string; version_id: string; path: string; line: number; start: number; end: number; lines: Array<{ number: number; text: string }>; excerpt_sha256: string; truncated: boolean }
interface RecallRows { query: string | null; resolutions: Resolution[]; nodes: SearchNode[]; edges: Edge[]; evidence: Evidence[]; omissions: Omission[]; truncated: boolean }
export interface SearchResult extends RecallRows { kind: "search" }
export interface ContextResult extends RecallRows { kind: "context" }
export type RecallResult = SearchResult | ContextResult;

export type ApiResult =
  | StatusResult
  | VaultsResult
  | RootsResult
  | NodeResult
  | NeighborsResult
  | StaleResult
  | SourceResult
  | VersionsResult
  | DiffResult
  | ExcerptResult
  | RecallResult;

export type ApiRoute = ApiResult["kind"];

export interface ApiEnvelope<T extends ApiResult = ApiResult> {
  schema: "qlkg-api-response-v1";
  route: ApiRoute;
  status: "complete" | "partial";
  generation: Generation;
  registry_generation: Generation;
  vault_generations: VaultGeneration[];
  incomplete_vaults: IncompleteVault[];
  result: T;
}

export interface ApiErrorPayload {
  schema: "qlkg-api-error-v1";
  status: "error";
  route: ApiRoute | null;
  vault_id: string | null;
  error: { code: string; message: string };
  current_generation: Generation | null;
  retryable: boolean;
}

export interface RecallRequest {
  schema: "qlkg-recall-request-v1";
  operation: "search" | "context";
  vault_ids: string[];
  queries: string[];
  query: string | null;
  handle: null;
  handles: string[];
  scopes: string[];
  direction: "incoming" | "outgoing" | "both";
  edge_types: Relation[];
  max_depth: number;
  limit: number;
  token_budget: number;
  include_stale: boolean;
}
