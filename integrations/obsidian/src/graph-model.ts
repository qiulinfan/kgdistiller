import type { ElementDefinition } from "cytoscape";

import {
  entryPath,
  firstLine,
  nameKey,
  parseValue,
  recordClass,
  recordLocation,
  resolveLocal,
  sheetPath,
  sourceOfSheet,
  understandingOf,
  FIXED_KEYS,
  type RecordClass,
  type Understanding,
  type ValueError,
} from "./records";

/** One file under `.knowledge/entries/` or `.knowledge/drafts/` with its metadata-cache frontmatter. */
export interface FrontmatterRecord {
  path: string;
  frontmatter: Readonly<Record<string, unknown>> | null | undefined;
}

/** A `requires` or role value after textual resolution. */
export interface ResolvedValue {
  raw: string;
  /** Vault path of the linked record. */
  path?: string;
  /** `base:id` of a foreign link; the plugin cannot see other bases, so it is a stub. */
  foreign?: string;
  /** A local link whose record does not exist (for a draft: in entries or drafts). */
  dangling?: string;
  /** A pending term: the source uses it without explaining it here. */
  term?: string;
  error?: ValueError | "nested-list";
}

export interface RoleValues {
  role: string;
  values: ResolvedValue[];
}

export interface KnowledgeRecord {
  path: string;
  id: string;
  draft: boolean;
  label: string;
  kind: string;
  recordClass: RecordClass;
  /** Undefined when the stored value is outside the understanding enum. */
  understanding?: Understanding;
  epistemic?: string;
  source?: string;
  lines?: string;
  roles: RoleValues[];
  requires: ResolvedValue[];
}

export interface KnowledgeModel {
  records: Map<string, KnowledgeRecord>;
}

const scalar = (value: unknown): string | undefined =>
  value === undefined || value === null ? undefined : String(value);

function resolveValue(raw: unknown, draft: boolean, exists: (path: string) => boolean): ResolvedValue {
  if (Array.isArray(raw)) return { raw: JSON.stringify(raw), error: "nested-list" };
  const text = scalar(raw) ?? "";
  const parsed = parseValue(text);
  switch (parsed.kind) {
    case "term":
      return { raw: text, term: parsed.term };
    case "error":
      return { raw: text, error: parsed.error };
    case "foreign":
      return { raw: text, foreign: `${parsed.base}:${parsed.id}` };
    case "local": {
      const path = resolveLocal(parsed.id, draft, exists);
      return path ? { raw: text, path } : { raw: text, dangling: parsed.id };
    }
  }
}

const valueList = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);

/**
 * Build the model from metadata-cache frontmatter. Pure: the same records always
 * give the same model. Links resolve textually, never through Obsidian's own
 * link resolution, so they match kgd.
 */
export function graphModel(input: readonly FrontmatterRecord[]): KnowledgeModel {
  const located = input
    .map((record) => ({ record, location: recordLocation(record.path) }))
    .filter((item) => item.location !== undefined)
    .sort((left, right) => left.record.path.localeCompare(right.record.path));
  const paths = new Set(located.map((item) => item.record.path));
  const exists = (path: string): boolean => paths.has(path);
  const records = new Map<string, KnowledgeRecord>();
  for (const { record, location } of located) {
    const frontmatter = record.frontmatter ?? {};
    const draft = location!.folder === "drafts";
    const roles: RoleValues[] = [];
    for (const [key, value] of Object.entries(frontmatter)) {
      if (FIXED_KEYS.has(key) || !Array.isArray(value) || value.length === 0) continue;
      roles.push({ role: key, values: value.map((item) => resolveValue(item, draft, exists)) });
    }
    records.set(record.path, {
      path: record.path,
      id: location!.id,
      draft,
      label: scalar(frontmatter.label) || location!.id,
      kind: scalar(frontmatter.kind) ?? "",
      recordClass: recordClass(frontmatter),
      understanding: understandingOf(frontmatter.understanding),
      epistemic: scalar(frontmatter.epistemic),
      source: scalar(frontmatter.source),
      lines: scalar(frontmatter.lines),
      roles,
      requires: valueList(frontmatter.requires).map((item) => resolveValue(item, draft, exists)),
    });
  }
  return { records };
}

export interface GraphFilters {
  /** Show only records of this kind (plus what they link to); empty shows every kind. */
  kind: string;
  recordClass: "" | RecordClass;
  understanding: "" | Understanding;
  /** Show only records whose `source` starts with this text; empty shows every source. */
  sourcePrefix: string;
  showDrafts: boolean;
  /** Draw pending terms as ghost nodes, one per name key. */
  showPending: boolean;
}

/** The toolbar's starting filters: everything shown, pending terms hidden. */
export function defaultFilters(showDrafts: boolean): GraphFilters {
  return { kind: "", recordClass: "", understanding: "", sourcePrefix: "", showDrafts, showPending: false };
}

export type GraphElementKind =
  | "node" | "relation" | "stub" | "dangling" | "pending" | "relation-edge" | "role" | "requires";

export interface GraphElementData {
  id: string;
  label: string;
  /** The node caption: the label, with a second line `draft` for drafts. */
  display?: string;
  element: GraphElementKind;
  /** The record behind a node, a diamond or a typed binary edge. */
  path?: string;
  /** The record that owns a role or requires edge. */
  owner?: string;
  kind?: string;
  draft?: boolean;
  understanding?: Understanding;
  role?: string;
  uid?: string;
  /** A pending ghost's name key. */
  key?: string;
  color?: string;
  source?: string;
  target?: string;
}

/** A fixed palette; kinds take colours by their position in the model's sorted kinds. */
const KIND_COLORS = [
  "#7c3aed", "#2563eb", "#0891b2", "#db2777", "#0f766e", "#c026d3",
  "#4338ca", "#0369a1", "#9d174d", "#65a30d", "#ea580c", "#475569",
];

/** Every non-empty kind of the model's records, drafts included, sorted. */
export function graphOptions(model: KnowledgeModel): string[] {
  return [...new Set([...model.records.values()].map((record) => record.kind).filter((kind) => kind !== ""))].sort();
}

/**
 * A deterministic colour per kind over all of the model's kinds, so a colour
 * never shifts when filters or the drafts toggle change what is drawn.
 */
export function kindColors(model: KnowledgeModel): Map<string, string> {
  return new Map(graphOptions(model).map((kind, index) => [kind, KIND_COLORS[index % KIND_COLORS.length]!]));
}

/** The records that pass every filter. */
export function selectRecords(model: KnowledgeModel, filters: GraphFilters): KnowledgeRecord[] {
  return [...model.records.values()].filter((record) =>
    (filters.showDrafts || !record.draft) &&
    (!filters.kind || record.kind === filters.kind) &&
    (!filters.recordClass || record.recordClass === filters.recordClass) &&
    (!filters.understanding || record.understanding === filters.understanding) &&
    (!filters.sourcePrefix || (record.source ?? "").startsWith(filters.sourcePrefix)));
}

const isLink = (value: ResolvedValue): boolean =>
  value.path !== undefined || value.foreign !== undefined || value.dangling !== undefined;

const recordElementId = (path: string): string => `record:${path}`;
const pendingElementId = (key: string): string => `pending:${key}`;

/** Every value of a record with its role, in model order: roles in frontmatter order, then `requires`. */
function ownedValues(record: KnowledgeRecord): Array<{ role: string; value: ResolvedValue }> {
  return [
    ...record.roles.flatMap((role) => role.values.map((value) => ({ role: role.role, value }))),
    ...record.requires.map((value) => ({ role: "requires", value })),
  ];
}

/**
 * Draw a relation as one typed edge only when it has exactly two link values,
 * both in roles. While pending ghosts are drawn, a relation with a term stays a
 * diamond so every term has its incidence edge.
 */
function binaryEndpoints(
  record: KnowledgeRecord,
  showPending: boolean,
): { values: [ResolvedValue, ResolvedValue]; directed: boolean } | undefined {
  if (record.requires.some(isLink)) return undefined;
  if (showPending && ownedValues(record).some(({ value }) => value.term !== undefined)) return undefined;
  const linked = record.roles.flatMap((role) => role.values.filter(isLink).map((value) => ({ role: role.role, value })));
  if (linked.length !== 2) return undefined;
  const [first, second] = linked as [typeof linked[0], typeof linked[0]];
  return { values: [first.value, second.value], directed: first.role !== second.role };
}

/** The first spelling of each pending term's name key, in model order. */
function pendingLabels(model: KnowledgeModel): Map<string, string> {
  const labels = new Map<string, string>();
  for (const record of model.records.values()) {
    for (const { value } of ownedValues(record)) {
      if (value.term === undefined) continue;
      const key = nameKey(value.term);
      if (key && !labels.has(key)) labels.set(key, value.term);
    }
  }
  return labels;
}

export function graphElements(model: KnowledgeModel, filters: GraphFilters): ElementDefinition[] {
  const visible = (record: KnowledgeRecord): boolean => filters.showDrafts || !record.draft;
  const colors = kindColors(model);
  const pending = filters.showPending ? pendingLabels(model) : new Map<string, string>();
  // A relation that some link targets must stay a node so edges can reach it.
  const targeted = new Set<string>();
  for (const record of model.records.values()) {
    if (!visible(record)) continue;
    for (const { value } of ownedValues(record)) {
      if (value.path) targeted.add(value.path);
    }
  }
  const nodes = new Map<string, ElementDefinition>();
  const edges: ElementDefinition[] = [];

  const addNode = (data: GraphElementData, classes: string): string => {
    if (!nodes.has(data.id)) nodes.set(data.id, { data: { display: data.label, ...data }, classes });
    return data.id;
  };

  const recordNode = (record: KnowledgeRecord): string => {
    const diamond = record.recordClass === "relation";
    const classes = [diamond ? "kgd-relation" : "kgd-node"];
    if (record.understanding) classes.push(`understanding-${record.understanding}`);
    if (record.draft) classes.push("kgd-draft");
    return addNode({
      id: recordElementId(record.path),
      label: record.label,
      display: record.draft ? `${record.label}\ndraft` : record.label,
      element: diamond ? "relation" : "node",
      path: record.path,
      kind: record.kind,
      draft: record.draft,
      understanding: record.understanding,
      color: colors.get(record.kind),
    }, classes.join(" "));
  };

  const endpoint = (value: ResolvedValue): string | undefined => {
    if (value.path) {
      const record = model.records.get(value.path);
      return record && visible(record) ? recordNode(record) : undefined;
    }
    if (value.foreign) {
      return addNode({ id: `stub:${value.foreign}`, label: value.foreign, element: "stub", uid: value.foreign }, "kgd-stub");
    }
    if (value.dangling) {
      return addNode({ id: `dangling:${value.dangling}`, label: value.dangling, element: "dangling" }, "kgd-dangling");
    }
    if (value.term !== undefined && filters.showPending) {
      const key = nameKey(value.term);
      if (!key) return undefined;
      return addNode({ id: pendingElementId(key), label: pending.get(key) ?? value.term, element: "pending", key }, "kgd-pending");
    }
    return undefined;
  };

  for (const record of selectRecords(model, filters)) {
    const binary = record.recordClass === "relation" && !targeted.has(record.path)
      ? binaryEndpoints(record, filters.showPending)
      : undefined;
    if (binary) {
      const source = endpoint(binary.values[0]);
      const target = endpoint(binary.values[1]);
      if (source && target) {
        const classes = ["kgd-relation-edge"];
        if (binary.directed) classes.push("kgd-directed");
        if (source === target) classes.push("kgd-loop");
        if (record.draft) classes.push("kgd-draft");
        edges.push({
          data: {
            id: recordElementId(record.path),
            label: record.kind,
            element: "relation-edge",
            path: record.path,
            kind: record.kind,
            draft: record.draft,
            color: colors.get(record.kind),
            source,
            target,
          } satisfies GraphElementData,
          classes: classes.join(" "),
        });
      }
      continue;
    }
    const own = recordNode(record);
    for (const role of record.roles) {
      for (const [index, value] of role.values.entries()) {
        const target = endpoint(value);
        if (!target) continue;
        edges.push({
          data: {
            id: `role:${record.path}:${role.role}:${index}`,
            label: role.role,
            element: "role",
            owner: record.path,
            role: role.role,
            draft: record.draft,
            color: colors.get(record.kind),
            source: own,
            target,
          } satisfies GraphElementData,
          classes: record.draft ? "kgd-role kgd-draft" : "kgd-role",
        });
      }
    }
    for (const [index, value] of record.requires.entries()) {
      const target = endpoint(value);
      if (!target) continue;
      edges.push({
        data: {
          id: `requires:${record.path}:${index}`,
          label: "requires",
          element: "requires",
          owner: record.path,
          draft: record.draft,
          source: own,
          target,
        } satisfies GraphElementData,
        classes: record.draft ? "kgd-requires kgd-draft" : "kgd-requires",
      });
    }
  }

  // Edges touching a missing record are marked so they draw in the dangling colour.
  for (const edge of edges) {
    if ([edge.data.source, edge.data.target].some((end) => String(end).startsWith("dangling:"))) {
      edge.classes = `${edge.classes ?? ""} kgd-dangling`.trim();
    }
  }
  return [...nodes.values(), ...edges];
}

/**
 * The records the active file focuses: the record itself, the records whose
 * source is the file, or, for a sheet, the records of the sheet's source.
 */
export function focusPaths(model: KnowledgeModel, activePath: string | undefined): string[] {
  if (!activePath) return [];
  if (recordLocation(activePath)) return model.records.has(activePath) ? [activePath] : [];
  const source = sourceOfSheet(activePath) ?? activePath;
  return [...model.records.values()].filter((record) => record.source === source).map((record) => record.path);
}

/**
 * The part of a drawn graph within `depth` hops of the seed records, following
 * every edge in both directions. A seed drawn as a typed edge seeds both of its
 * endpoints. Pure: returns a new element array.
 */
export function neighbourhood(elements: readonly ElementDefinition[], seeds: readonly string[], depth: number): ElementDefinition[] {
  const seedPaths = new Set(seeds);
  const nodeIds = new Set<string>();
  const edges: ElementDefinition[] = [];
  for (const element of elements) {
    if (element.data.source === undefined) nodeIds.add(String(element.data.id));
    else edges.push(element);
  }
  const start = new Set<string>();
  for (const path of seedPaths) {
    if (nodeIds.has(recordElementId(path))) start.add(recordElementId(path));
  }
  for (const edge of edges) {
    const data = edge.data as GraphElementData;
    if (data.element === "relation-edge" && data.path && seedPaths.has(data.path)) {
      start.add(String(data.source));
      start.add(String(data.target));
    }
  }
  const adjacent = new Map<string, string[]>();
  for (const edge of edges) {
    const source = String(edge.data.source);
    const target = String(edge.data.target);
    adjacent.set(source, [...(adjacent.get(source) ?? []), target]);
    adjacent.set(target, [...(adjacent.get(target) ?? []), source]);
  }
  const kept = new Set(start);
  let frontier = [...start];
  for (let hop = 0; hop < depth && frontier.length > 0; hop++) {
    const next: string[] = [];
    for (const id of frontier) {
      for (const neighbour of adjacent.get(id) ?? []) {
        if (!kept.has(neighbour)) {
          kept.add(neighbour);
          next.push(neighbour);
        }
      }
    }
    frontier = next;
  }
  return elements.filter((element) => element.data.source === undefined
    ? kept.has(String(element.data.id))
    : kept.has(String(element.data.source)) && kept.has(String(element.data.target)));
}

export interface RecordDetails {
  element: "record";
  label: string;
  kind: string;
  recordClass: RecordClass;
  draft: boolean;
  /** Undefined when the stored value is outside the understanding enum. */
  understanding?: Understanding;
  epistemic?: string;
  roles: Array<{ role: string; values: string[] }>;
  requires: string[];
  recordPath: string;
  /** The source with its `lines` and the 0-based first line for `eState.line`. */
  source?: { path: string; lines?: string; line?: number };
  /** The source's sheet, present only when the sheet file exists. */
  sheet?: string;
}

export interface PendingOwner {
  label: string;
  path: string;
  role: string;
}

export type ElementDetails =
  | RecordDetails
  | { element: "pending"; term: string; key: string; owners: PendingOwner[] }
  | { element: "stub"; uid: string }
  | { element: "dangling"; id: string; path: string };

/**
 * What the details pane shows for a selected element. Records, diamonds and
 * typed edges show their record; role and requires edges show their owner.
 */
export function detailsModel(
  model: KnowledgeModel,
  data: GraphElementData,
  exists: (path: string) => boolean,
): ElementDetails | undefined {
  if (data.element === "pending" && data.key !== undefined) {
    const key = data.key;
    const owners: PendingOwner[] = [];
    for (const record of model.records.values()) {
      for (const { role, value } of ownedValues(record)) {
        if (value.term !== undefined && nameKey(value.term) === key) {
          owners.push({ label: record.label, path: record.path, role });
        }
      }
    }
    return { element: "pending", term: data.label, key, owners };
  }
  if (data.element === "stub") return { element: "stub", uid: data.uid ?? data.label };
  if (data.element === "dangling") return { element: "dangling", id: data.label, path: entryPath(data.label) };
  const record = model.records.get(data.path ?? data.owner ?? "");
  if (!record) return undefined;
  const details: RecordDetails = {
    element: "record",
    label: record.label,
    kind: record.kind,
    recordClass: record.recordClass,
    draft: record.draft,
    understanding: record.understanding,
    epistemic: record.epistemic,
    roles: record.roles.map((role) => ({ role: role.role, values: role.values.map((value) => valueLabel(model, value)) })),
    requires: record.requires.map((value) => valueLabel(model, value)),
    recordPath: record.path,
  };
  if (record.source) {
    const first = firstLine(record.lines);
    details.source = { path: record.source, lines: record.lines, line: first === undefined ? undefined : first - 1 };
    if (exists(sheetPath(record.source))) details.sheet = sheetPath(record.source);
  }
  return details;
}

/** Labels for a record's values in the details pane: the linked record's label, the uid, or the term. */
export function valueLabel(model: KnowledgeModel, value: ResolvedValue): string {
  if (value.path) return model.records.get(value.path)?.label ?? value.path;
  if (value.foreign) return value.foreign;
  if (value.dangling) return `${value.dangling} (missing)`;
  if (value.term) return `${value.term} (pending)`;
  return `${value.raw} (invalid: ${value.error})`;
}
