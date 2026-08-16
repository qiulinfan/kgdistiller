import type { Edge, NodeSummary } from "../api/contracts";

export const MAX_TAXONOMY_NODES = 100;
export const MAX_TAXONOMY_EDGES = 180;

export interface PositionedNode {
  node: NodeSummary;
  x: number;
  y: number;
  layer: number;
}

export interface PositionedEdge {
  edge: Edge;
  source: PositionedNode;
  target: PositionedNode;
  path: string;
}

export type TaxonomyLayout =
  | { ok: true; nodes: PositionedNode[]; edges: PositionedEdge[]; width: number; height: number }
  | { ok: false; reason: "too-large" | "duplicate" | "dangling" | "cycle" };

function compareText(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

function barycenter(handles: string[], positions: Map<string, number>): number {
  const values = handles.map((handle) => positions.get(handle)).filter((value): value is number => value !== undefined);
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : Number.POSITIVE_INFINITY;
}

export function layoutTaxonomy(allNodes: NodeSummary[], allEdges: Edge[]): TaxonomyLayout {
  const edges = allEdges.filter((edge) => edge.relation === "contains");
  if (allNodes.length > MAX_TAXONOMY_NODES || edges.length > MAX_TAXONOMY_EDGES) return { ok: false, reason: "too-large" };
  const byHandle = new Map(allNodes.map((node) => [node.handle, node]));
  if (byHandle.size !== allNodes.length) return { ok: false, reason: "duplicate" };
  const edgeKeys = edges.map((edge) => `${edge.source}\0${edge.relation}\0${edge.target}`);
  if (new Set(edgeKeys).size !== edgeKeys.length) return { ok: false, reason: "duplicate" };
  if (edges.some((edge) => !byHandle.has(edge.source) || !byHandle.has(edge.target))) return { ok: false, reason: "dangling" };

  const outgoing = new Map<string, string[]>();
  const incoming = new Map<string, string[]>();
  const indegree = new Map<string, number>();
  const layer = new Map<string, number>();
  for (const handle of byHandle.keys()) {
    outgoing.set(handle, []);
    incoming.set(handle, []);
    indegree.set(handle, 0);
    layer.set(handle, 0);
  }
  for (const edge of edges) {
    outgoing.get(edge.source)?.push(edge.target);
    incoming.get(edge.target)?.push(edge.source);
    indegree.set(edge.target, (indegree.get(edge.target) ?? 0) + 1);
  }
  for (const values of [...outgoing.values(), ...incoming.values()]) values.sort(compareText);
  const ready = [...byHandle.keys()].filter((handle) => indegree.get(handle) === 0).sort(compareText);
  const ordered: string[] = [];
  while (ready.length) {
    const handle = ready.shift();
    if (handle === undefined) break;
    ordered.push(handle);
    for (const target of outgoing.get(handle) ?? []) {
      layer.set(target, Math.max(layer.get(target) ?? 0, (layer.get(handle) ?? 0) + 1));
      const remaining = (indegree.get(target) ?? 0) - 1;
      indegree.set(target, remaining);
      if (remaining === 0) {
        ready.push(target);
        ready.sort(compareText);
      }
    }
  }
  if (ordered.length !== byHandle.size) return { ok: false, reason: "cycle" };

  const layers: string[][] = [];
  for (const handle of ordered) {
    const index = layer.get(handle) ?? 0;
    (layers[index] ??= []).push(handle);
  }
  for (const values of layers) values.sort(compareText);
  for (let sweep = 0; sweep < 2; sweep += 1) {
    for (let index = 1; index < layers.length; index += 1) {
      const previous = new Map((layers[index - 1] ?? []).map((handle, position) => [handle, position]));
      layers[index]?.sort((left, right) => barycenter(incoming.get(left) ?? [], previous) - barycenter(incoming.get(right) ?? [], previous) || compareText(left, right));
    }
    for (let index = layers.length - 2; index >= 0; index -= 1) {
      const following = new Map((layers[index + 1] ?? []).map((handle, position) => [handle, position]));
      layers[index]?.sort((left, right) => barycenter(outgoing.get(left) ?? [], following) - barycenter(outgoing.get(right) ?? [], following) || compareText(left, right));
    }
  }

  const maximumRows = Math.max(1, ...layers.map((values) => values.length));
  const positioned: PositionedNode[] = [];
  const positions = new Map<string, PositionedNode>();
  layers.forEach((values, layerIndex) => {
    const offset = (maximumRows - values.length) * 46;
    values.forEach((handle, rowIndex) => {
      const node = byHandle.get(handle);
      if (!node) return;
      const item = { node, x: 110 + layerIndex * 240, y: 70 + offset + rowIndex * 92, layer: layerIndex };
      positioned.push(item);
      positions.set(handle, item);
    });
  });
  positioned.sort((left, right) => left.layer - right.layer || left.y - right.y || compareText(left.node.handle, right.node.handle));
  const positionedEdges = edges.map((edge) => {
    const source = positions.get(edge.source) as PositionedNode;
    const target = positions.get(edge.target) as PositionedNode;
    const middle = (source.x + target.x) / 2;
    return { edge, source, target, path: `M ${source.x + 72} ${source.y} C ${middle} ${source.y}, ${middle} ${target.y}, ${target.x - 72} ${target.y}` };
  });
  return {
    ok: true,
    nodes: positioned,
    edges: positionedEdges,
    width: Math.max(360, layers.length * 240 + 80),
    height: Math.max(240, maximumRows * 92 + 80)
  };
}
