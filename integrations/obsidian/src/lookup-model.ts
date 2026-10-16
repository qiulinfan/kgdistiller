/**
 * The kgd results a lookup shows, typed as `kgd mcp` returns them
 * (docs/retrieval.md), and the pure shaping the lookup view needs. Nothing
 * here decides identity: a record with the queried name is a candidate, never
 * the same concept by that fact alone.
 */

import type { ElementDefinition } from "cytoscape";

import { entryId, KNOWLEDGE_DIRECTORY } from "./records";

export interface Lag {
  changed_files: number;
  unembedded: number;
  embedding_changed: boolean;
}

export interface RecordSummary {
  uid: string;
  label: string;
  kind: string;
  class: "node" | "relation";
  base: string;
  source: string;
  lines: string;
  /** Whether the source is a web address: knowledge outside the owner's files. */
  foreign?: boolean;
  gloss: string;
}

export interface ResolveResult {
  terms: Array<{ term: string; senses: RecordSummary[] }>;
  lag: Lag;
}

export interface SearchHit extends RecordSummary {
  epistemic: string | null;
  ranks: Record<string, number | null>;
}

export interface SearchResult {
  query: string;
  lanes: string[];
  results: SearchHit[];
  lag: Lag;
}

/** One out-link of a record, with whether its target exists. */
export interface OutLink {
  role: string;
  pos: number;
  uid: string;
  label: string | null;
  exists: boolean;
}

export interface InLink {
  uid: string;
  label: string;
  kind: string;
  role: string;
}

export interface FullRecord extends RecordSummary {
  id: string;
  aliases: string[];
  understanding: string;
  epistemic: string | null;
  body: string;
  search_terms: string;
  evidence: string[];
  out: OutLink[];
  in: InLink[];
}

export interface GetResult {
  records: FullRecord[];
  missing: string[];
  lag: Lag;
}

export interface NeighborsResult {
  edges: Array<{ from: string; role: string; to: string; depth: number }>;
  records: Record<string, { label: string | null; kind: string | null; class: string | null; exists: boolean }>;
  missing: string[];
  lag: Lag;
}

/** One source's records as `kg_browse base:path` returns them: grouped by kind, in line order within a kind. */
export interface SourceBrowse {
  base: string;
  source: string;
  kinds: Array<{ kind: string; records: Array<{ uid: string; label: string; class: "node" | "relation"; lines: string; gloss: string }> }>;
  lag: Lag;
}

/**
 * What the file open in the editor brings to the lookup, for a vault that is
 * the registered base `base`: an accepted record is put in focus, and a source
 * lists the source's records. Drafts are not indexed, and nothing else under
 * the knowledge folder is a source.
 */
export function openFileTarget(path: string, base: string): { uid: string } | { handle: string } | undefined {
  const id = entryId(path);
  if (id !== undefined) return { uid: `${base}:${id}` };
  if (path === KNOWLEDGE_DIRECTORY || path.startsWith(`${KNOWLEDGE_DIRECTORY}/`)) return undefined;
  return { handle: `${base}:${path}` };
}

/** The records a source listing shows, in kgd's order: kinds alphabetically, then lines. */
export function sourceRecords(browse: SourceBrowse): RecordSummary[] {
  return browse.kinds.flatMap((group) => group.records.map((record) => ({
    uid: record.uid, label: record.label, kind: group.kind, class: record.class,
    base: browse.base, source: browse.source, lines: record.lines, gloss: record.gloss,
  })));
}

/** Selections longer than this are cut before they become a query. */
export const MAX_QUERY_LENGTH = 500;

/** The query a selection becomes: runs of whitespace collapsed, capped at MAX_QUERY_LENGTH. */
export function lookupQuery(selection: string): string {
  return selection.split(/\s+/).filter(Boolean).join(" ").slice(0, MAX_QUERY_LENGTH);
}

/** A search hit's lane ranks, such as "lexical 3 · dense 1 · name –", in the order the lanes ran. */
export function rankText(lanes: readonly string[], ranks: Record<string, number | null>): string {
  return lanes.map((lane) => `${lane} ${ranks[lane] ?? "–"}`).join(" · ");
}

/** A record's out-links grouped by role, roles in kgd's order (`requires` last). */
export function linksByRole(out: readonly OutLink[]): Array<{ role: string; links: OutLink[] }> {
  const groups: Array<{ role: string; links: OutLink[] }> = [];
  for (const link of out) {
    const last = groups[groups.length - 1];
    if (last?.role === link.role) last.links.push(link);
    else groups.push({ role: link.role, links: [link] });
  }
  return groups;
}

export interface ChainNode {
  uid: string;
  label: string;
  kind?: string;
  exists: boolean;
  /** Expanded at an earlier place in the tree; shown here without children. */
  repeated: boolean;
  children: ChainNode[];
}

/**
 * The `requires` closure of `start` as a tree, from
 * `kg_neighbors --role requires --dir out`. Each record is expanded once, at
 * its first breadth-first occurrence; later occurrences and cycles are leaves
 * marked repeated. Missing records are leaves.
 */
export function chainTree(result: NeighborsResult, start: string): ChainNode {
  const outgoing = new Map<string, NeighborsResult["edges"]>();
  for (const edge of result.edges) {
    const edges = outgoing.get(edge.from) ?? [];
    edges.push(edge);
    outgoing.set(edge.from, edges);
  }
  const node = (uid: string): ChainNode => {
    const info = result.records[uid];
    return {
      uid,
      label: info?.label ?? uid,
      kind: info?.kind ?? undefined,
      exists: info?.exists ?? false,
      repeated: false,
      children: [],
    };
  };
  const root = node(start);
  const expanded = new Set([start]);
  const queue = [root];
  for (let current = queue.shift(); current; current = queue.shift()) {
    for (const edge of outgoing.get(current.uid) ?? []) {
      const child = node(edge.to);
      if (expanded.has(edge.to)) child.repeated = true;
      else {
        expanded.add(edge.to);
        if (child.exists) queue.push(child);
      }
      current.children.push(child);
    }
  }
  return root;
}

/** A record's inbound links as the lookup shows them. */
export interface InboundGroups {
  /** The relations it takes part in, grouped by kind in first-seen order, each with its role there. */
  about: Array<{ kind: string; links: InLink[] }>;
  /** The records whose `requires` lists it. */
  builtOn: InLink[];
}

export function inboundGroups(links: readonly InLink[]): InboundGroups {
  const about: InboundGroups["about"] = [];
  const builtOn: InLink[] = [];
  for (const link of links) {
    if (link.role === "requires") {
      builtOn.push(link);
      continue;
    }
    const group = about.find((candidate) => candidate.kind === link.kind);
    if (group) group.links.push(link);
    else about.push({ kind: link.kind, links: [link] });
  }
  return { about, builtOn };
}

/** What one drawn element of the neighbour graph stands for. */
export type GraphElementKind = "node" | "relation" | "dangling" | "relation-edge" | "role" | "requires";

export interface GraphElementData {
  id: string;
  /** The edge caption. */
  label?: string;
  /** The node caption. */
  display?: string;
  element: GraphElementKind;
  /** The record behind a node, a diamond or a relation edge. */
  uid?: string;
  color?: string;
  source?: string;
  target?: string;
}

/** A fixed palette; kinds take colours by their position among the drawn kinds sorted. */
const KIND_COLORS = [
  "#7c3aed", "#2563eb", "#0891b2", "#db2777", "#0f766e", "#c026d3",
  "#4338ca", "#0369a1", "#9d174d", "#65a30d", "#ea580c", "#475569",
];

/** A colour per non-empty kind, by the kind's position among the given kinds sorted. */
export function kindPalette(kinds: Iterable<string>): Map<string, string> {
  const sorted = [...new Set(kinds)].filter((kind) => kind !== "").sort();
  return new Map(sorted.map((kind, index) => [kind, KIND_COLORS[index % KIND_COLORS.length]!]));
}

/** The most records the neighbour graph draws. */
export const NEIGHBOUR_LIMIT = 40;
/** Concept steps the neighbour graph spans; a relation joining its participants is one step. */
export const NEIGHBOUR_STEPS = 3;
/** The `kg_neighbors` depth that covers them: through a relation, one step is two links. */
export const NEIGHBOUR_WALK_DEPTH = 2 * NEIGHBOUR_STEPS;

export interface NeighbourGraph {
  elements: ElementDefinition[];
  /** Records drawn as vertices, and those within reach. */
  shown: number;
  total: number;
}

/**
 * The records within `steps` concept steps of `focus`, as typed-graph
 * elements, from a `kg_neighbors --dir both --depth 2*steps` walk.
 *
 * Relations are edges. A relation with exactly two link values, both in roles,
 * is one edge between them labelled with its kind; one with more values, or
 * with `requires` links, is a diamond joining them, and its participants are
 * one step apart. A relation that another record links to, and the focus,
 * stay records in their own right: diamonds with their role edges. A relation
 * about a single record is no edge and is left out (the lookup lists it under
 * About it). Node records are circles coloured by
 * kind, missing records red, and `requires` links dashed, one step each. At
 * most `limit` vertices are drawn, the nearest first.
 */
export function neighbourGraph(
  result: NeighborsResult, focus: string, steps = NEIGHBOUR_STEPS, limit = NEIGHBOUR_LIMIT,
): NeighbourGraph {
  const info = (uid: string) => result.records[uid];
  const isRelation = (uid: string): boolean => info(uid)?.class === "relation";
  const missing = (uid: string): boolean => !(info(uid)?.exists ?? false);
  const links = result.edges.map((edge) => ({ from: edge.from, role: edge.role, to: edge.to }));
  const roleLinks = new Map<string, Array<{ role: string; to: string }>>();
  const requires = new Map<string, string[]>();
  const linkedTo = new Set<string>();
  for (const link of links) {
    linkedTo.add(link.to);
    if (link.role === "requires") requires.set(link.from, [...(requires.get(link.from) ?? []), link.to]);
    else roleLinks.set(link.from, [...(roleLinks.get(link.from) ?? []), { role: link.role, to: link.to }]);
  }
  const isVertex = (uid: string): boolean => !isRelation(uid) || linkedTo.has(uid) || uid === focus;

  const relationEdges: Array<{ uid: string; between: [string, string] }> = [];
  const connectors: Array<{ uid: string; values: Array<{ role: string; to: string }> }> = [];
  for (const uid of Object.keys(result.records)) {
    if (!isRelation(uid) || isVertex(uid)) continue;
    const values = [...(roleLinks.get(uid) ?? []), ...(requires.get(uid) ?? []).map((to) => ({ role: "requires", to }))];
    const inRoles = roleLinks.get(uid) ?? [];
    if (inRoles.length === 2 && values.length === 2) relationEdges.push({ uid, between: [inRoles[0]!.to, inRoles[1]!.to] });
    else if (new Set(values.map((value) => value.to)).size >= 2) connectors.push({ uid, values });
  }

  const adjacent = new Map<string, Set<string>>();
  const join = (a: string, b: string): void => {
    if (a === b) return;
    adjacent.set(a, (adjacent.get(a) ?? new Set()).add(b));
    adjacent.set(b, (adjacent.get(b) ?? new Set()).add(a));
  };
  for (const link of links) if (isVertex(link.from) && isVertex(link.to)) join(link.from, link.to);
  for (const edge of relationEdges) join(...edge.between);
  for (const connector of connectors) {
    const values = [...new Set(connector.values.map((value) => value.to))];
    values.forEach((a, index) => values.slice(index + 1).forEach((b) => join(a, b)));
  }
  const distance = new Map([[focus, 0]]);
  const queue = [focus];
  for (let uid = queue.shift(); uid !== undefined; uid = queue.shift()) {
    const next = (distance.get(uid) ?? 0) + 1;
    if (next > steps) continue;
    for (const neighbour of adjacent.get(uid) ?? []) {
      if (distance.has(neighbour)) continue;
      distance.set(neighbour, next);
      queue.push(neighbour);
    }
  }
  const reached = [...distance.keys()].sort((a, b) => (distance.get(a) ?? 0) - (distance.get(b) ?? 0) || (a < b ? -1 : a > b ? 1 : 0));
  const kept = new Set(reached.slice(0, limit));

  const drawnConnectors = connectors.filter((connector) => new Set(connector.values.map((value) => value.to).filter((to) => kept.has(to))).size >= 2);
  const drawnEdges = relationEdges.filter((edge) => kept.has(edge.between[0]) && kept.has(edge.between[1]));
  const colors = kindPalette([...kept, ...drawnConnectors.map((connector) => connector.uid), ...drawnEdges.map((edge) => edge.uid)]
    .map((uid) => info(uid)?.kind ?? ""));
  const color = (uid: string): string | undefined => {
    const kind = info(uid)?.kind;
    return kind ? colors.get(kind) : undefined;
  };
  const elements: ElementDefinition[] = [];
  const vertex = (uid: string, classes: string[]): void => {
    const relation = isRelation(uid);
    const data: GraphElementData = {
      id: uid,
      uid,
      display: info(uid)?.label ?? "(missing)",
      element: missing(uid) ? "dangling" : relation ? "relation" : "node",
      color: color(uid),
    };
    const all = [missing(uid) ? "kgd-dangling" : relation ? "kgd-relation" : "", ...classes];
    elements.push({ group: "nodes", data, classes: all.filter(Boolean).join(" ") });
  };
  const edge = (id: string, source: string, target: string, role: string, classes: string[], extra: Partial<GraphElementData> = {}): void => {
    const data: GraphElementData = { id, label: role === "requires" ? "" : role, element: role === "requires" ? "requires" : "role", source, target, ...extra };
    const all = [...classes, missing(source) || missing(target) ? "kgd-dangling" : ""];
    elements.push({ group: "edges", data, classes: all.filter(Boolean).join(" ") });
  };
  for (const uid of reached) if (kept.has(uid)) vertex(uid, uid === focus ? ["kgd-focus"] : []);
  for (const connector of drawnConnectors) vertex(connector.uid, []);
  links.forEach((link, index) => {
    if (!kept.has(link.from) || !kept.has(link.to)) return;
    edge(`link-${index}`, link.from, link.to, link.role, [link.role === "requires" ? "kgd-requires" : "kgd-role"]);
  });
  for (const { uid, between } of drawnEdges) {
    edge(`relation-${uid}`, between[0], between[1], info(uid)?.kind ?? "", ["kgd-relation-edge"], {
      label: info(uid)?.kind ?? "", element: "relation-edge", uid, color: color(uid),
    });
  }
  for (const connector of drawnConnectors) {
    connector.values.forEach((value, index) => {
      if (!kept.has(value.to)) return;
      edge(`connector-${connector.uid}-${index}`, connector.uid, value.to, value.role, [value.role === "requires" ? "kgd-requires" : "kgd-role"]);
    });
  }
  return { elements, shown: kept.size, total: reached.length };
}
