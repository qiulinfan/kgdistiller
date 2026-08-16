import type { Edge, NodeSummary } from "../api/contracts";
import type { PositionedEdge, PositionedNode } from "./taxonomy-layout";

export const MAX_NEIGHBORHOOD_NODES = 81;
export const MAX_NEIGHBORHOOD_EDGES = 180;

export type NeighborhoodLayout =
  | { ok: true; nodes: PositionedNode[]; edges: PositionedEdge[]; width: number; height: number }
  | { ok: false; reason: "too-large" | "duplicate" | "missing-center" | "dangling" | "outside-two-hops" };

function compareText(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

export function layoutNeighborhood(center: string, nodes: NodeSummary[], edges: Edge[]): NeighborhoodLayout {
  if (nodes.length > MAX_NEIGHBORHOOD_NODES || edges.length > MAX_NEIGHBORHOOD_EDGES) return { ok: false, reason: "too-large" };
  const byHandle = new Map(nodes.map((node) => [node.handle, node]));
  if (byHandle.size !== nodes.length || new Set(edges.map((edge) => `${edge.source}\0${edge.relation}\0${edge.target}`)).size !== edges.length) {
    return { ok: false, reason: "duplicate" };
  }
  if (!byHandle.has(center)) return { ok: false, reason: "missing-center" };
  if (edges.some((edge) => !byHandle.has(edge.source) || !byHandle.has(edge.target))) return { ok: false, reason: "dangling" };
  const incident = new Map<string, Array<{ neighbor: string; edge: Edge }>>();
  for (const handle of byHandle.keys()) incident.set(handle, []);
  for (const edge of edges) {
    incident.get(edge.source)?.push({ neighbor: edge.target, edge });
    incident.get(edge.target)?.push({ neighbor: edge.source, edge });
  }
  const distance = new Map([[center, 0]]);
  const queue = [center];
  while (queue.length) {
    const handle = queue.shift();
    if (handle === undefined) break;
    const depth = distance.get(handle) ?? 0;
    if (depth === 2) continue;
    for (const item of incident.get(handle) ?? []) {
      if (!distance.has(item.neighbor)) {
        distance.set(item.neighbor, depth + 1);
        queue.push(item.neighbor);
      }
    }
  }
  if (distance.size !== nodes.length) return { ok: false, reason: "outside-two-hops" };

  const rings = [[], [], []] as string[][];
  for (const handle of byHandle.keys()) rings[distance.get(handle) ?? 2]?.push(handle);
  for (const ring of rings) {
    ring.sort((left, right) => {
      const leftNode = byHandle.get(left) as NodeSummary;
      const rightNode = byHandle.get(right) as NodeSummary;
      return compareText(leftNode.vault_id, rightNode.vault_id) || compareText(left, right);
    });
  }
  const width = 720;
  const height = 560;
  const positioned: PositionedNode[] = [];
  const positions = new Map<string, PositionedNode>();
  rings.forEach((ring, ringIndex) => {
    const radius = ringIndex === 0 ? 0 : ringIndex === 1 ? 150 : 250;
    ring.forEach((handle, index) => {
      const angle = ring.length === 1 ? -Math.PI / 2 : -Math.PI / 2 + (2 * Math.PI * index) / ring.length;
      const item = {
        node: byHandle.get(handle) as NodeSummary,
        x: width / 2 + Math.cos(angle) * radius,
        y: height / 2 + Math.sin(angle) * radius,
        layer: ringIndex
      };
      positioned.push(item);
      positions.set(handle, item);
    });
  });
  const groups = new Map<string, Edge[]>();
  for (const edge of edges) {
    const key = [edge.source, edge.target].sort(compareText).join("\0");
    const group = groups.get(key) ?? [];
    group.push(edge);
    groups.set(key, group);
  }
  for (const group of groups.values()) group.sort((left, right) => compareText(`${left.relation}\0${left.source}`, `${right.relation}\0${right.source}`));
  const positionedEdges = edges.map((edge) => {
    const source = positions.get(edge.source) as PositionedNode;
    const target = positions.get(edge.target) as PositionedNode;
    const group = groups.get([edge.source, edge.target].sort(compareText).join("\0")) ?? [edge];
    const offset = group.indexOf(edge) - (group.length - 1) / 2;
    const middleX = (source.x + target.x) / 2 + (target.y - source.y) * offset * 0.08;
    const middleY = (source.y + target.y) / 2 - (target.x - source.x) * offset * 0.08;
    return { edge, source, target, path: `M ${source.x} ${source.y} Q ${middleX} ${middleY}, ${target.x} ${target.y}` };
  });
  return { ok: true, nodes: positioned, edges: positionedEdges, width, height };
}
