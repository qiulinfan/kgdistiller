export const GRAPH_SCHEMA = "kgdistiller-obsidian-graph-v1" as const;
export const PRIVATE_GRAPH_SCHEMA = "kgdistiller-graph-v1" as const;

export type CurationStatus = "current" | "pending" | "needs-review";

export interface GraphSourceGeneration {
  graph_schema: typeof PRIVATE_GRAPH_SCHEMA;
  graph_sha256: string;
  snapshot_sha256: string;
  source_hashes_sha256: string;
}

export interface GraphCounts {
  concepts: number;
  sources: number;
  semantic_edges: number;
  definitions: number;
  references: number;
}

export interface ConceptRecord {
  id: string;
  label: string;
  note_path: string;
  authority: string;
  curation_status: CurationStatus;
  aliases: string[];
  fields: string[];
}

export interface SourceRecord {
  authority: string;
  note_path: string;
}

export interface SemanticEdgeRecord {
  source: string;
  relation: string;
  target: string;
  evidence: string;
}

export interface DefinitionRecord {
  source_authority: string;
  target: string;
  line_start: number;
  line_end: number;
}

export interface ReferenceRecord {
  id: string;
  source_authority: string;
  target: string;
  label: string;
  line: number;
  context?: string;
}

export interface KgGraphContract {
  schema: typeof GRAPH_SCHEMA;
  source: GraphSourceGeneration;
  counts: GraphCounts;
  concepts: ConceptRecord[];
  sources: SourceRecord[];
  semantic_edges: SemanticEdgeRecord[];
  definitions: DefinitionRecord[];
  references: ReferenceRecord[];
  bundle_sha256: string;
}

type UnknownRecord = Record<string, unknown>;

const SHA256_RE = /^[0-9a-f]{64}$/;
const ID_RE = /^[a-z0-9][a-z0-9-]*$/;
const AUTHORITY_RE = /\.(?:md|typ|tex)$/i;

function fail(message: string): never {
  throw new Error(`Invalid ${GRAPH_SCHEMA}: ${message}`);
}

function asRecord(value: unknown, label: string): UnknownRecord {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    fail(`${label} must be an object`);
  }
  return value as UnknownRecord;
}

function asArray(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) {
    fail(`${label} must be an array`);
  }
  return value;
}

function asString(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    fail(`${label} must be a non-empty string`);
  }
  return value;
}

function asInteger(value: unknown, label: string): number {
  if (!Number.isInteger(value) || (value as number) < 0) {
    fail(`${label} must be a non-negative integer`);
  }
  return value as number;
}

function positiveInteger(value: unknown, label: string): number {
  const result = asInteger(value, label);
  if (result < 1) {
    fail(`${label} must be positive`);
  }
  return result;
}

function exactKeys(value: UnknownRecord, expected: readonly string[], label: string): void {
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  if (actual.length !== wanted.length || actual.some((key, index) => key !== wanted[index])) {
    fail(`${label} has unsupported or missing properties`);
  }
}

function keysWithOptional(
  value: UnknownRecord,
  required: readonly string[],
  optional: readonly string[],
  label: string,
): void {
  for (const key of required) {
    if (!(key in value)) {
      fail(`${label} is missing ${key}`);
    }
  }
  const allowed = new Set([...required, ...optional]);
  if (Object.keys(value).some((key) => !allowed.has(key))) {
    fail(`${label} has unsupported properties`);
  }
}

function uniqueStrings(values: unknown, label: string, pattern?: RegExp): string[] {
  const result = asArray(values, label).map((value, index) => {
    const item = asString(value, `${label}[${index}]`);
    if (pattern && !pattern.test(item)) {
      fail(`${label}[${index}] has an invalid value`);
    }
    return item;
  });
  if (new Set(result).size !== result.length) {
    fail(`${label} contains duplicates`);
  }
  return result;
}

export function isSafeVaultPath(value: string, suffix?: RegExp): boolean {
  if (
    value.startsWith("/") ||
    /^[A-Za-z]:[\\/]/.test(value) ||
    value.includes("\\") ||
    value.split("/").some((part) => part === "" || part === "." || part === "..")
  ) {
    return false;
  }
  return suffix ? suffix.test(value) : true;
}

function safePath(value: unknown, label: string, suffix: RegExp): string {
  const result = asString(value, label);
  if (!isSafeVaultPath(result, suffix)) {
    fail(`${label} must be a safe vault-relative path`);
  }
  return result;
}

function sha256(value: unknown, label: string): string {
  const result = asString(value, label);
  if (!SHA256_RE.test(result)) {
    fail(`${label} must be a lowercase SHA-256 digest`);
  }
  return result;
}

export function canonicalJson(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "string" || typeof value === "boolean") return JSON.stringify(value);
  if (typeof value === "number") {
    if (!Number.isFinite(value)) fail("canonical JSON contains a non-finite number");
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (typeof value === "object") {
    const record = value as UnknownRecord;
    return `{${Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
      .join(",")}}`;
  }
  fail("canonical JSON contains an unsupported value");
}

async function sha256Text(value: string): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(value),
  );
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function calculateBundleDigest(value: UnknownRecord): Promise<string> {
  const payload = { ...value };
  delete payload.bundle_sha256;
  return sha256Text(canonicalJson(payload));
}

export async function parseGraphContract(text: string): Promise<KgGraphContract> {
  let decoded: unknown;
  try {
    decoded = JSON.parse(text) as unknown;
  } catch (error) {
    fail(`malformed JSON: ${error instanceof Error ? error.message : String(error)}`);
  }
  const root = asRecord(decoded, "document");
  exactKeys(
    root,
    [
      "schema",
      "source",
      "counts",
      "concepts",
      "sources",
      "semantic_edges",
      "definitions",
      "references",
      "bundle_sha256",
    ],
    "document",
  );
  if (root.schema !== GRAPH_SCHEMA) fail(`schema must equal ${GRAPH_SCHEMA}`);

  const source = asRecord(root.source, "source");
  exactKeys(
    source,
    ["graph_schema", "graph_sha256", "snapshot_sha256", "source_hashes_sha256"],
    "source",
  );
  if (source.graph_schema !== PRIVATE_GRAPH_SCHEMA) {
    fail(`source.graph_schema must equal ${PRIVATE_GRAPH_SCHEMA}`);
  }
  sha256(source.graph_sha256, "source.graph_sha256");
  sha256(source.snapshot_sha256, "source.snapshot_sha256");
  sha256(source.source_hashes_sha256, "source.source_hashes_sha256");

  const counts = asRecord(root.counts, "counts");
  exactKeys(
    counts,
    ["concepts", "sources", "semantic_edges", "definitions", "references"],
    "counts",
  );
  for (const key of ["concepts", "sources", "semantic_edges", "definitions", "references"] as const) {
    asInteger(counts[key], `counts.${key}`);
  }

  const conceptIds = new Set<string>();
  const conceptPaths = new Set<string>();
  const conceptAuthorities = new Map<string, string>();
  for (const [index, raw] of asArray(root.concepts, "concepts").entries()) {
    const concept = asRecord(raw, `concepts[${index}]`);
    exactKeys(
      concept,
      ["id", "label", "note_path", "authority", "curation_status", "aliases", "fields"],
      `concepts[${index}]`,
    );
    const id = asString(concept.id, `concepts[${index}].id`);
    if (!ID_RE.test(id) || conceptIds.has(id)) fail(`concepts[${index}].id is invalid or duplicated`);
    conceptIds.add(id);
    asString(concept.label, `concepts[${index}].label`);
    const notePath = safePath(concept.note_path, `concepts[${index}].note_path`, /\.md$/);
    if (conceptPaths.has(notePath)) fail(`concepts[${index}].note_path is duplicated`);
    conceptPaths.add(notePath);
    const authority = safePath(concept.authority, `concepts[${index}].authority`, AUTHORITY_RE);
    conceptAuthorities.set(id, authority);
    if (!["current", "pending", "needs-review"].includes(String(concept.curation_status))) {
      fail(`concepts[${index}].curation_status is invalid`);
    }
    uniqueStrings(concept.aliases, `concepts[${index}].aliases`);
    uniqueStrings(concept.fields, `concepts[${index}].fields`, ID_RE);
  }

  const sourceAuthorities = new Set<string>();
  const allPaths = new Set(conceptPaths);
  for (const [index, raw] of asArray(root.sources, "sources").entries()) {
    const sourceRecord = asRecord(raw, `sources[${index}]`);
    exactKeys(sourceRecord, ["authority", "note_path"], `sources[${index}]`);
    const authority = safePath(sourceRecord.authority, `sources[${index}].authority`, AUTHORITY_RE);
    if (sourceAuthorities.has(authority)) fail(`sources[${index}].authority is duplicated`);
    sourceAuthorities.add(authority);
    const notePath = safePath(sourceRecord.note_path, `sources[${index}].note_path`, /\.md$/);
    if (allPaths.has(notePath)) fail(`sources[${index}].note_path is duplicated`);
    allPaths.add(notePath);
  }
  for (const authority of conceptAuthorities.values()) {
    if (!sourceAuthorities.has(authority)) fail("a concept has an unknown source authority");
  }

  const edgeKeys = new Set<string>();
  for (const [index, raw] of asArray(root.semantic_edges, "semantic_edges").entries()) {
    const edge = asRecord(raw, `semantic_edges[${index}]`);
    exactKeys(edge, ["source", "relation", "target", "evidence"], `semantic_edges[${index}]`);
    const from = asString(edge.source, `semantic_edges[${index}].source`);
    const relation = asString(edge.relation, `semantic_edges[${index}].relation`);
    const to = asString(edge.target, `semantic_edges[${index}].target`);
    asString(edge.evidence, `semantic_edges[${index}].evidence`);
    if (!conceptIds.has(from) || !conceptIds.has(to)) fail(`semantic_edges[${index}] has an unknown endpoint`);
    if (relation === "contains") fail(`semantic_edges[${index}] cannot contain a structural edge`);
    const key = `${from}\u0000${relation}\u0000${to}`;
    if (edgeKeys.has(key)) fail(`semantic_edges[${index}] is duplicated`);
    edgeKeys.add(key);
  }

  const definitionTargets = new Set<string>();
  for (const [index, raw] of asArray(root.definitions, "definitions").entries()) {
    const definition = asRecord(raw, `definitions[${index}]`);
    exactKeys(
      definition,
      ["source_authority", "target", "line_start", "line_end"],
      `definitions[${index}]`,
    );
    const authority = asString(definition.source_authority, `definitions[${index}].source_authority`);
    const target = asString(definition.target, `definitions[${index}].target`);
    const lineStart = positiveInteger(definition.line_start, `definitions[${index}].line_start`);
    const lineEnd = positiveInteger(definition.line_end, `definitions[${index}].line_end`);
    if (!sourceAuthorities.has(authority) || !conceptIds.has(target)) fail(`definitions[${index}] has an unknown endpoint`);
    if (lineEnd < lineStart) fail(`definitions[${index}] has a reversed line range`);
    if (definitionTargets.has(target)) fail(`definitions[${index}].target is duplicated`);
    definitionTargets.add(target);
  }
  if (definitionTargets.size !== conceptIds.size || [...conceptIds].some((id) => !definitionTargets.has(id))) {
    fail("each concept must have exactly one definition edge");
  }

  const referenceIds = new Set<string>();
  for (const [index, raw] of asArray(root.references, "references").entries()) {
    const reference = asRecord(raw, `references[${index}]`);
    keysWithOptional(
      reference,
      ["id", "source_authority", "target", "label", "line"],
      ["context"],
      `references[${index}]`,
    );
    const id = asString(reference.id, `references[${index}].id`);
    const authority = asString(reference.source_authority, `references[${index}].source_authority`);
    const target = asString(reference.target, `references[${index}].target`);
    asString(reference.label, `references[${index}].label`);
    positiveInteger(reference.line, `references[${index}].line`);
    if (reference.context !== undefined) asString(reference.context, `references[${index}].context`);
    if (!sourceAuthorities.has(authority) || !conceptIds.has(target)) fail(`references[${index}] has an unknown endpoint`);
    if (referenceIds.has(id)) fail(`references[${index}].id is duplicated`);
    referenceIds.add(id);
  }

  const arrays = {
    concepts: asArray(root.concepts, "concepts").length,
    sources: asArray(root.sources, "sources").length,
    semantic_edges: asArray(root.semantic_edges, "semantic_edges").length,
    definitions: asArray(root.definitions, "definitions").length,
    references: asArray(root.references, "references").length,
  };
  for (const [key, length] of Object.entries(arrays)) {
    if (counts[key] !== length) fail(`counts.${key} does not match its array`);
  }
  const claimed = sha256(root.bundle_sha256, "bundle_sha256");
  if ((await calculateBundleDigest(root)) !== claimed) fail("bundle_sha256 does not match canonical content");
  return root as unknown as KgGraphContract;
}
