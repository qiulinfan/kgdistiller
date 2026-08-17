import type { ElementDefinition } from "cytoscape";

import type { KgGraphContract } from "./contract";

export interface GraphFilters {
  relation: string;
  field: string;
  showSources: boolean;
  showDefinitions: boolean;
  showReferences: boolean;
}

export interface GraphElementData {
  id: string;
  label: string;
  kind: "concept" | "source" | "semantic" | "definition" | "reference";
  notePath?: string;
  authority?: string;
  conceptId?: string;
  status?: string;
  fields?: string;
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

export function projectionPath(graphPath: string, notePath: string): string {
  const slash = graphPath.lastIndexOf("/");
  return slash < 0 ? notePath : `${graphPath.slice(0, slash + 1)}${notePath}`;
}

export function relationColor(relation: string): string {
  let hash = 0;
  for (const character of relation) hash = (hash * 31 + character.codePointAt(0)!) >>> 0;
  return RELATION_COLORS[hash % RELATION_COLORS.length] ?? RELATION_COLORS[0]!;
}

export function relationOptions(graph: KgGraphContract): string[] {
  return [...new Set(graph.semantic_edges.map((edge) => edge.relation))].sort();
}

export function fieldOptions(graph: KgGraphContract): string[] {
  return [...new Set(graph.concepts.flatMap((concept) => concept.fields))].sort();
}

export function graphElements(
  graph: KgGraphContract,
  graphPath: string,
  filters: GraphFilters,
): ElementDefinition[] {
  const sourceNotes = new Map(
    graph.sources.map((source) => [
      source.authority,
      projectionPath(graphPath, source.note_path),
    ]),
  );
  const selectedConcepts = new Set(
    graph.concepts
      .filter((concept) => !filters.field || concept.fields.includes(filters.field))
      .map((concept) => concept.id),
  );
  const elements: ElementDefinition[] = graph.concepts
    .filter((concept) => selectedConcepts.has(concept.id))
    .map((concept) => ({
      data: {
        id: `concept:${concept.id}`,
        label: concept.label,
        kind: "concept",
        notePath: projectionPath(graphPath, concept.note_path),
        authority: concept.authority,
        conceptId: concept.id,
        status: concept.curation_status,
        fields: concept.fields.join(", "),
      } satisfies GraphElementData,
    }));

  if (filters.showSources) {
    const usedAuthorities = new Set<string>();
    for (const concept of graph.concepts) {
      if (selectedConcepts.has(concept.id)) usedAuthorities.add(concept.authority);
    }
    if (filters.showReferences) {
      for (const reference of graph.references) {
        if (selectedConcepts.has(reference.target)) usedAuthorities.add(reference.source_authority);
      }
    }
    elements.push(
      ...graph.sources
        .filter((source) => usedAuthorities.has(source.authority))
        .map((source) => ({
          data: {
            id: `source:${source.authority}`,
            label: source.authority,
            kind: "source",
            notePath: projectionPath(graphPath, source.note_path),
            authority: source.authority,
          } satisfies GraphElementData,
        })),
    );
  }

  for (const [index, edge] of graph.semantic_edges.entries()) {
    if (
      !selectedConcepts.has(edge.source) ||
      !selectedConcepts.has(edge.target) ||
      (filters.relation && edge.relation !== filters.relation)
    ) {
      continue;
    }
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
      if (!selectedConcepts.has(definition.target)) continue;
      elements.push({
        data: {
          id: `definition:${definition.target}`,
          source: `source:${definition.source_authority}`,
          target: `concept:${definition.target}`,
          label: "defines",
          kind: "definition",
          authority: definition.source_authority,
          notePath: sourceNotes.get(definition.source_authority),
          line: definition.line_start,
          lineEnd: definition.line_end,
        } satisfies GraphElementData,
      });
    }
  }

  if (filters.showSources && filters.showReferences) {
    for (const reference of graph.references) {
      if (!selectedConcepts.has(reference.target)) continue;
      elements.push({
        data: {
          id: `reference:${reference.id}`,
          source: `source:${reference.source_authority}`,
          target: `concept:${reference.target}`,
          label: "references",
          kind: "reference",
          authority: reference.source_authority,
          notePath: sourceNotes.get(reference.source_authority),
          line: reference.line,
          evidence: reference.context,
        } satisfies GraphElementData,
      });
    }
  }
  return elements;
}
