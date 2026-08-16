import type { Edge, Evidence, NodeDetail, NodeSummary } from "../api/contracts";
import { append, badge, button, element, emptyState, link } from "../components/dom";
import { renderGraphList } from "../graph/graph-list";
import { layoutNeighborhood } from "../graph/neighborhood-layout";
import { renderSvgGraph } from "../graph/svg-graph";
import { routeHref } from "../state/router";
import { envelope, healthBadge, page, renderClientNotices, renderOmissions, type ViewContext } from "./common";

function collectGraph(context: ViewContext, detail: NodeDetail): { nodes: NodeSummary[]; edges: Edge[] } {
  const nodes = new Map<string, NodeSummary>([[detail.handle, detail]]);
  const edges = new Map<string, Edge>();
  const rows = context.state.response === null ? context.state.related : [context.state.response, ...context.state.related];
  for (const row of rows) {
    if (row.result.kind === "node") for (const edge of row.result.edges) edges.set(`${edge.source}\0${edge.relation}\0${edge.target}`, edge);
    if (row.result.kind === "neighbors" || row.result.kind === "context") {
      for (const node of row.result.nodes) nodes.set(node.handle, node);
      for (const edge of row.result.edges) edges.set(`${edge.source}\0${edge.relation}\0${edge.target}`, edge);
    }
  }
  return { nodes: [...nodes.values()], edges: [...edges.values()] };
}

function evidenceCard(row: Evidence): HTMLElement {
  const card = element("article", "evidence-card");
  const title = element("h3", undefined, row.kind === "concept" ? "Concept evidence" : `${row.relation ?? "Relation"} evidence`);
  const source = link(`${row.source_path}:${row.start_line}`, routeHref({ name: "source", vault: row.handle.split(":", 1)[0] ?? "", document: row.document_id }));
  const quote = element("blockquote");
  quote.dir = "auto";
  quote.textContent = row.excerpt;
  append(card, title, source, quote);
  return card;
}

export function renderConcept(context: ViewContext): HTMLElement {
  const response = envelope(context.state, "node");
  const title = response?.result.node.label ?? "Concept detail";
  const root = page(title, response?.result.node.handle ?? "Vault-qualified node");
  if (!response) return root;
  const node = response.result.node;
  const partialCoverage = context.state.routeNotices.length > 0 || response.result.truncated || context.state.related.some((row) => (row.result.kind === "neighbors" || row.result.kind === "context") && row.result.truncated);
  const meta = element("div", "badge-row");
  append(meta, badge(node.type), healthBadge(node.curation_status), healthBadge(node.source_status));
  root.append(meta);
  if (node.aliases.length) root.append(element("p", "muted", `Aliases: ${node.aliases.join(" · ")}`));
  if (node.text) {
    const description = element("p", "concept-text");
    description.dir = "auto";
    description.textContent = node.text;
    root.append(description);
  }
  if (node.parents.length) {
    const parents = element("section", "relation-strip");
    append(parents, element("h2", undefined, "Taxonomy parents"));
    for (const parent of node.parents) append(parents, link(parent, routeHref({ name: "node", handle: parent })));
    root.append(parents);
  }
  if (node.provenance && node.open_actions) {
    const provenance = element("section", "provenance-card");
    append(provenance, element("h2", undefined, "Native authority"), element("p", undefined, `${node.provenance.authority}:${node.provenance.line}`));
    const copy = button("Copy open target", () => {
      const target = `${node.open_actions?.authority ?? ""}:${node.open_actions?.line ?? 1}`;
      void navigator.clipboard?.writeText(target);
      copy.textContent = "Copied";
    });
    copy.dataset.authority = node.open_actions.authority;
    copy.dataset.line = String(node.open_actions.line);
    append(provenance, copy);
    root.append(provenance);
  }

  const graph = collectGraph(context, node);
  if (graph.nodes.length) {
    const section = element("section", "graph-section");
    append(section, element("h2", undefined, "Bounded two-hop neighborhood"));
    const layout = layoutNeighborhood(node.handle, graph.nodes, graph.edges);
    if (layout.ok) section.append(renderSvgGraph(layout, `${node.label} bounded two-hop neighborhood${partialCoverage ? ", partial coverage" : ""}`, (handle) => { location.hash = routeHref({ name: "node", handle }); }));
    else section.append(element("p", "notice notice-warn", `Diagram unavailable (${layout.reason}); use the complete list.`));
    section.append(renderGraphList(graph.nodes, graph.edges, (handle) => { location.hash = routeHref({ name: "node", handle }); }));
    root.append(section);
  } else root.append(emptyState("No current relations", "This node has no visible relation neighborhood."));

  if (response.result.evidence.length) {
    const evidence = element("section", "evidence-grid");
    append(evidence, element("h2", undefined, "Source evidence"));
    for (const row of response.result.evidence) evidence.append(evidenceCard(row));
    root.append(evidence);
  }
  const boundedRows = [response, ...context.state.related.filter((row) => row.result.kind === "neighbors" || row.result.kind === "context")];
  const serverOmissions = boundedRows.flatMap((row) => "omissions" in row.result ? row.result.omissions : []);
  const omissions = renderOmissions(serverOmissions, boundedRows.some((row) => "truncated" in row.result && row.result.truncated));
  if (omissions) root.append(omissions);
  const client = renderClientNotices(context.state.routeNotices);
  if (client) root.append(client);
  return root;
}
