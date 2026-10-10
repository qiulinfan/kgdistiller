export const GRAPH_SCHEMA = "kgdistiller-obsidian-graph-v1" as const;
/** The product's single knowledge tree at a base root; entries live in its `entries/` folder. */
export const KNOWLEDGE_DIRECTORY = ".knowledge";

export const UNDERSTANDING_STATES = ["unknown", "not-yet-understood", "understood"] as const;
export type Understanding = (typeof UNDERSTANDING_STATES)[number];

export interface GraphCounts {
  concepts: number;
  sources: number;
  semantic_edges: number;
  definitions: number;
}

export interface ConceptRecord {
  id: string;
  label: string;
  kind: string;
  aliases: string[];
  /** The concept's entry file, relative to the base root. */
  authority: string;
  understanding: Understanding;
}

export interface SourceRecord {
  authority: string;
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

export interface KgGraphContract {
  schema: typeof GRAPH_SCHEMA;
  counts: GraphCounts;
  concepts: ConceptRecord[];
  sources: SourceRecord[];
  semantic_edges: SemanticEdgeRecord[];
  definitions: DefinitionRecord[];
}

type UnknownRecord = Record<string, unknown>;

const ID_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

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

function uniqueStrings(values: unknown, label: string): string[] {
  const result = asArray(values, label).map((value, index) => asString(value, `${label}[${index}]`));
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

/** Any safe relative path: sources are plain text documents of any format. */
function safePath(value: unknown, label: string): string {
  const result = asString(value, label);
  if (!isSafeVaultPath(result)) {
    fail(`${label} must be a safe vault-relative path`);
  }
  return result;
}

const COUNT_KEYS = ["concepts", "sources", "semantic_edges", "definitions"] as const;

export function parseGraphContract(text: string): KgGraphContract {
  let decoded: unknown;
  try {
    decoded = JSON.parse(text) as unknown;
  } catch (error) {
    fail(`malformed JSON: ${error instanceof Error ? error.message : String(error)}`);
  }
  const root = asRecord(decoded, "document");
  exactKeys(root, ["schema", "counts", ...COUNT_KEYS], "document");
  if (root.schema !== GRAPH_SCHEMA) fail(`schema must equal ${GRAPH_SCHEMA}`);

  const counts = asRecord(root.counts, "counts");
  exactKeys(counts, COUNT_KEYS, "counts");
  for (const key of COUNT_KEYS) asInteger(counts[key], `counts.${key}`);

  const conceptIds = new Set<string>();
  for (const [index, raw] of asArray(root.concepts, "concepts").entries()) {
    const label = `concepts[${index}]`;
    const concept = asRecord(raw, label);
    exactKeys(concept, ["id", "label", "kind", "aliases", "authority", "understanding"], label);
    const id = asString(concept.id, `${label}.id`);
    if (!ID_RE.test(id) || conceptIds.has(id)) fail(`${label}.id is invalid or duplicated`);
    conceptIds.add(id);
    asString(concept.label, `${label}.label`);
    asString(concept.kind, `${label}.kind`);
    uniqueStrings(concept.aliases, `${label}.aliases`);
    safePath(concept.authority, `${label}.authority`);
    if (!(UNDERSTANDING_STATES as readonly unknown[]).includes(concept.understanding)) {
      fail(`${label}.understanding is invalid`);
    }
  }

  const sourceAuthorities = new Set<string>();
  for (const [index, raw] of asArray(root.sources, "sources").entries()) {
    const sourceRecord = asRecord(raw, `sources[${index}]`);
    exactKeys(sourceRecord, ["authority"], `sources[${index}]`);
    const authority = safePath(sourceRecord.authority, `sources[${index}].authority`);
    if (sourceAuthorities.has(authority)) fail(`sources[${index}].authority is duplicated`);
    sourceAuthorities.add(authority);
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
  if (definitionTargets.size !== conceptIds.size) {
    fail("each concept must have exactly one definition edge");
  }

  for (const key of COUNT_KEYS) {
    if (counts[key] !== asArray(root[key], key).length) fail(`counts.${key} does not match its array`);
  }
  return root as unknown as KgGraphContract;
}
