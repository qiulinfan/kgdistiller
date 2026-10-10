import type { ElementDefinition } from "cytoscape";

import { KNOWLEDGE_DIRECTORY, type KgGraphContract, type Understanding } from "./contract";

export interface GraphFilters {
  relation: string;
  showSources: boolean;
  showDefinitions: boolean;
}

export interface GraphElementData {
  id: string;
  label: string;
  kind: "concept" | "source" | "semantic" | "definition";
  authority?: string;
  conceptId?: string;
  conceptKind?: string;
  understanding?: Understanding;
  relation?: string;
  evidence?: string;
  line?: number;
  lineEnd?: number;
  color?: string;
  source?: string;
  target?: string;
}

const RELATION_COLORS = [
  "#7c3aed",
  "#2563eb",
  "#0891b2",
  "#059669",
  "#ca8a04",
  "#dc2626",
  "#db2777",
];

export interface OpenTarget {
  path: string;
  line?: number;
}

/**
 * Concepts open their own entry file; sources and definitions open the source
 * document, definitions at their cited line range. Targets exist only for a
 * graph inside the vault-root `.knowledge/` tree, whose entry and source paths
 * are vault-relative.
 */
export function openTarget(data: GraphElementData, graphPath: string): OpenTarget | undefined {
  if (!graphPath.startsWith(`${KNOWLEDGE_DIRECTORY}/`)) return undefined;
  if (data.kind === "semantic" || !data.authority) return undefined;
  return data.line ? { path: data.authority, line: data.line } : { path: data.authority };
}

/** Concept nodes carry the reader's understanding state as their style class. */
export function understandingClass(understanding: Understanding): string {
  return `understanding-${understanding}`;
}

export function relationColor(relation: string): string {
  let hash = 0;
  for (const character of relation) hash = (hash * 31 + character.codePointAt(0)!) >>> 0;
  return RELATION_COLORS[hash % RELATION_COLORS.length] ?? RELATION_COLORS[0]!;
}

export function relationOptions(graph: KgGraphContract): string[] {
  return [...new Set(graph.semantic_edges.map((edge) => edge.relation))].sort();
}

export function graphElements(
  graph: KgGraphContract,
  filters: GraphFilters,
): ElementDefinition[] {
  const elements: ElementDefinition[] = graph.concepts.map((concept) => ({
    data: {
      id: `concept:${concept.id}`,
      label: concept.label,
      kind: "concept",
      authority: concept.authority,
      conceptId: concept.id,
      conceptKind: concept.kind,
      understanding: concept.understanding,
    } satisfies GraphElementData,
    classes: understandingClass(concept.understanding),
  }));

  if (filters.showSources) {
    elements.push(
      ...graph.sources.map((source) => ({
        data: {
          id: `source:${source.authority}`,
          label: source.authority,
          kind: "source",
          authority: source.authority,
        } satisfies GraphElementData,
      })),
    );
  }

  for (const [index, edge] of graph.semantic_edges.entries()) {
    if (filters.relation && edge.relation !== filters.relation) continue;
    elements.push({
      data: {
        id: `semantic:${index}:${edge.source}:${edge.relation}:${edge.target}`,
        source: `concept:${edge.source}`,
        target: `concept:${edge.target}`,
        label: edge.relation,
        kind: "semantic",
        relation: edge.relation,
        evidence: edge.evidence,
        color: relationColor(edge.relation),
      } satisfies GraphElementData,
    });
  }

  if (filters.showSources && filters.showDefinitions) {
    for (const definition of graph.definitions) {
      elements.push({
        data: {
          id: `definition:${definition.target}`,
          source: `source:${definition.source_authority}`,
          target: `concept:${definition.target}`,
          label: "defines",
          kind: "definition",
          authority: definition.source_authority,
          line: definition.line_start,
          lineEnd: definition.line_end,
        } satisfies GraphElementData,
      });
    }
  }

  return elements;
}
