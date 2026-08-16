import type { Edge, NodeSummary } from "../api/contracts";

export function renderGraphList(nodes: NodeSummary[], edges: Edge[], onOpen: (handle: string) => void): HTMLElement {
  const section = document.createElement("section");
  section.className = "graph-list";
  const heading = document.createElement("h3");
  heading.textContent = "Graph as a list";
  section.append(heading);
  const nodeList = document.createElement("ul");
  for (const node of nodes) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "text-link";
    button.textContent = `${node.label} · ${node.type} · ${node.vault_id}`;
    button.addEventListener("click", () => onOpen(node.handle));
    item.append(button);
    nodeList.append(item);
  }
  section.append(nodeList);
  if (edges.length) {
    const edgeHeading = document.createElement("h4");
    edgeHeading.textContent = "Typed relations";
    section.append(edgeHeading);
    const edgeList = document.createElement("ul");
    for (const edge of edges) {
      const item = document.createElement("li");
      item.textContent = `${edge.source} — ${edge.relation} → ${edge.target}`;
      edgeList.append(item);
    }
    section.append(edgeList);
  }
  return section;
}
