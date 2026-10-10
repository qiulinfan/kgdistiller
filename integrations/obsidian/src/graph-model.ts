import type { ElementDefinition } from "cytoscape";

import {
  parseValue,
  recordClass,
  recordLocation,
  resolveLocal,
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
  showDrafts: boolean;
}

export type GraphElementKind =
  | "node" | "relation" | "stub" | "dangling" | "relation-edge" | "role" | "requires";

export interface GraphElementData {
  id: string;
  label: string;
  element: GraphElementKind;
  /** The record behind a node, a diamond or a typed binary edge. */
  path?: string;
  kind?: string;
  draft?: boolean;
  understanding?: Understanding;
  role?: string;
  uid?: string;
  color?: string;
  source?: string;
  target?: string;
}

const KIND_COLORS = [
  "#7c3aed",
  "#2563eb",
  "#0891b2",
  "#059669",
  "#ca8a04",
  "#dc2626",
  "#db2777",
];

/** A deterministic colour per kind: the palette indexed by the kind's position in sorted order. */
export function kindColors(kinds: Iterable<string>): Map<string, string> {
  const sorted = [...new Set(kinds)].sort();
  return new Map(sorted.map((kind, index) => [kind, KIND_COLORS[index % KIND_COLORS.length]!]));
}

export function kindOptions(model: KnowledgeModel, showDrafts = true): string[] {
  return [...new Set([...model.records.values()]
    .filter((record) => showDrafts || !record.draft)
    .map((record) => record.kind)
    .filter((kind) => kind !== ""))].sort();
}

const isLink = (value: ResolvedValue): boolean =>
  value.path !== undefined || value.foreign !== undefined || value.dangling !== undefined;

const recordElementId = (path: string): string => `record:${path}`;

/** Draw a relation as one typed edge only when it has exactly two link values, both in roles. */
function binaryEndpoints(record: KnowledgeRecord): { values: [ResolvedValue, ResolvedValue]; directed: boolean } | undefined {
  if (record.requires.some(isLink)) return undefined;
  const linked = record.roles.flatMap((role) => role.values.filter(isLink).map((value) => ({ role: role.role, value })));
  if (linked.length !== 2) return undefined;
  const [first, second] = linked as [typeof linked[0], typeof linked[0]];
  return { values: [first.value, second.value], directed: first.role !== second.role };
}

export function graphElements(model: KnowledgeModel, filters: GraphFilters): ElementDefinition[] {
  const visible = [...model.records.values()].filter((record) => filters.showDrafts || !record.draft);
  const colors = kindColors(visible.filter((record) => record.recordClass === "relation").map((record) => record.kind));
  // A relation that some link targets must stay a node so edges can reach it.
  const targeted = new Set<string>();
  for (const record of visible) {
    for (const value of [...record.requires, ...record.roles.flatMap((role) => role.values)]) {
      if (value.path) targeted.add(value.path);
    }
  }
  const selected = visible.filter((record) => !filters.kind || record.kind === filters.kind);
  const nodes = new Map<string, ElementDefinition>();
  const edges: ElementDefinition[] = [];

  const recordNode = (record: KnowledgeRecord): string => {
    const id = recordElementId(record.path);
    if (!nodes.has(id)) {
      const diamond = record.recordClass === "relation";
      const classes = [diamond ? "kgd-relation" : "kgd-node"];
      if (record.draft) classes.push("kgd-draft");
      if (!diamond && record.understanding) classes.push(`understanding-${record.understanding}`);
      nodes.set(id, {
        data: {
          id,
          label: record.label,
          element: diamond ? "relation" : "node",
          path: record.path,
          kind: record.kind,
          draft: record.draft,
          understanding: record.understanding,
          color: diamond ? colors.get(record.kind) : undefined,
        } satisfies GraphElementData,
        classes: classes.join(" "),
      });
    }
    return id;
  };

  const endpoint = (value: ResolvedValue): string | undefined => {
    if (value.path) {
      const record = model.records.get(value.path);
      return record && (filters.showDrafts || !record.draft) ? recordNode(record) : undefined;
    }
    if (value.foreign) {
      const id = `stub:${value.foreign}`;
      if (!nodes.has(id)) {
        nodes.set(id, {
          data: { id, label: value.foreign, element: "stub", uid: value.foreign } satisfies GraphElementData,
          classes: "kgd-stub",
        });
      }
      return id;
    }
    if (value.dangling) {
      const id = `dangling:${value.dangling}`;
      if (!nodes.has(id)) {
        nodes.set(id, {
          data: { id, label: value.dangling, element: "dangling" } satisfies GraphElementData,
          classes: "kgd-dangling",
        });
      }
      return id;
    }
    return undefined;
  };

  for (const record of selected) {
    const binary = record.recordClass === "relation" && !targeted.has(record.path)
      ? binaryEndpoints(record)
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

export interface OpenTarget {
  path: string;
}

/** Records, diamonds and typed binary edges open their own record file. */
export function openTarget(data: GraphElementData): OpenTarget | undefined {
  return data.path ? { path: data.path } : undefined;
}

/** Labels for a record's values in the details pane: the linked record's label, the uid, or the term. */
export function valueLabel(model: KnowledgeModel, value: ResolvedValue): string {
  if (value.path) return model.records.get(value.path)?.label ?? value.path;
  if (value.foreign) return value.foreign;
  if (value.dangling) return `${value.dangling} (missing)`;
  if (value.term) return `${value.term} (pending)`;
  return `${value.raw} (invalid: ${value.error})`;
}
