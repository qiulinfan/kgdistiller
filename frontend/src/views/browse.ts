import type { Edge, NodeSummary } from "../api/contracts";
import { append, element, emptyState } from "../components/dom";
import { renderGraphList } from "../graph/graph-list";
import { renderSvgGraph } from "../graph/svg-graph";
import { layoutTaxonomy } from "../graph/taxonomy-layout";
import { routeHref } from "../state/router";
import { envelope, page, renderClientNotices, renderOmissions, type ViewContext } from "./common";

function collected(context: ViewContext): { nodes: NodeSummary[]; edges: Edge[] } {
  const nodes = new Map<string, NodeSummary>();
  const edges = new Map<string, Edge>();
  const rows = context.state.response === null ? context.state.related : [context.state.response, ...context.state.related];
  for (const row of rows) {
    if (row.result.kind === "roots" || row.result.kind === "neighbors") {
      for (const node of row.result.nodes) nodes.set(node.handle, node);
      if (row.result.kind === "neighbors") for (const edge of row.result.edges) edges.set(`${edge.source}\0${edge.relation}\0${edge.target}`, edge);
    }
  }
  return {
    nodes: [...nodes.values()].sort((left, right) => left.handle < right.handle ? -1 : left.handle > right.handle ? 1 : 0),
    edges: [...edges.values()].sort((left, right) => `${left.source}\0${left.relation}\0${left.target}` < `${right.source}\0${right.relation}\0${right.target}` ? -1 : 1)
  };
}

export function renderBrowse(context: ViewContext): HTMLElement {
  const { state } = context;
  const root = page("Browse taxonomy", "Multi-parent concepts stay multi-parent");
  if (state.route.name !== "browse" || state.route.vault === null) {
    root.append(emptyState("Choose a Vault", "The taxonomy view never merges similarly named nodes across Vaults."));
    return root;
  }
  const response = envelope(state, "roots");
  if (!response) return root;
  const graph = collected(context);
  const partialCoverage = state.routeNotices.length > 0 || response.result.truncated || state.related.some((row) => row.result.kind === "neighbors" && row.result.truncated);
  if (!graph.nodes.length) {
    root.append(emptyState("No taxonomy roots", "This Vault has no current field or topic roots."));
    return root;
  }
  const controls = element("div", "filter-row");
  const type = element("select");
  type.setAttribute("aria-label", "Filter by node type");
  for (const value of ["all", "field", "topic", "knowledge"]) {
    const option = element("option", undefined, value === "all" ? "All node types" : value);
    option.value = value;
    type.append(option);
  }
  const status = element("select");
  status.setAttribute("aria-label", "Filter by curation status");
  for (const value of ["all", "current", "needs-review", "pending"]) {
    const option = element("option", undefined, value === "all" ? "All curation states" : value);
    option.value = value;
    status.append(option);
  }
  const relation = element("select");
  relation.setAttribute("aria-label", "Filter by relation type");
  const relationValues = ["all", ...new Set(graph.edges.map((edge) => edge.relation))];
  for (const value of relationValues) {
    const option = element("option", undefined, value === "all" ? "All relation types" : value);
    option.value = value;
    relation.append(option);
  }
  append(controls, type, relation, status);
  root.append(controls);
  const graphHost = element("div", "graph-host");
  const draw = (): void => {
    graphHost.replaceChildren();
    const visibleNodes = graph.nodes.filter((node) => (type.value === "all" || node.type === type.value) && (status.value === "all" || node.curation_status === status.value));
    const handles = new Set(visibleNodes.map((node) => node.handle));
    const visibleEdges = graph.edges.filter((edge) => handles.has(edge.source) && handles.has(edge.target) && (relation.value === "all" || edge.relation === relation.value));
    const layout = layoutTaxonomy(visibleNodes, visibleEdges);
    if (relation.value !== "all" && relation.value !== "contains") {
      graphHost.append(element("p", "notice", "The taxonomy diagram renders contains edges; the selected typed relations remain in the list."));
    } else if (layout.ok) graphHost.append(renderSvgGraph(layout, `${state.route.name === "browse" ? state.route.vault : "Vault"} bounded taxonomy${partialCoverage ? ", partial coverage" : ""}`, (handle) => { location.hash = routeHref({ name: "node", handle }); }));
    else graphHost.append(element("p", "notice notice-warn", `Diagram unavailable (${layout.reason}); the complete bounded list remains below.`));
    graphHost.append(renderGraphList(visibleNodes, visibleEdges, (handle) => { location.hash = routeHref({ name: "node", handle }); }));
  };
  type.addEventListener("change", draw);
  relation.addEventListener("change", draw);
  status.addEventListener("change", draw);
  draw();
  root.append(graphHost);
  const boundedRows = [response, ...state.related.filter((row) => row.result.kind === "neighbors")];
  const serverOmissions = boundedRows.flatMap((row) => "omissions" in row.result ? row.result.omissions : []);
  const omissions = renderOmissions(serverOmissions, boundedRows.some((row) => "truncated" in row.result && row.result.truncated));
  if (omissions) root.append(omissions);
  const client = renderClientNotices(state.routeNotices);
  if (client) root.append(client);
  return root;
}
