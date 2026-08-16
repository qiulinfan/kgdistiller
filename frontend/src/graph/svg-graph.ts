import type { PositionedEdge, PositionedNode } from "./taxonomy-layout";

const SVG = "http://www.w3.org/2000/svg";

export function truncateGraphLabel(value: string, maximum = 22): string {
  const Segmenter = (Intl as unknown as {
    Segmenter?: new (locale?: string | string[], options?: { granularity: "grapheme" }) => {
      segment: (input: string) => Iterable<{ segment: string }>;
    };
  }).Segmenter;
  const graphemes = Segmenter ? [...new Segmenter(undefined, { granularity: "grapheme" }).segment(value)].map((item) => item.segment) : Array.from(value);
  return graphemes.length > maximum ? `${graphemes.slice(0, maximum - 1).join("")}…` : value;
}

export function renderSvgGraph(
  layout: { nodes: PositionedNode[]; edges: PositionedEdge[]; width: number; height: number },
  label: string,
  onOpen: (handle: string) => void
): SVGSVGElement {
  const svg = document.createElementNS(SVG, "svg");
  svg.classList.add("graph-svg");
  svg.setAttribute("viewBox", `0 0 ${layout.width} ${layout.height}`);
  svg.setAttribute("role", "group");
  svg.setAttribute("aria-label", label);
  const edgeLayer = document.createElementNS(SVG, "g");
  edgeLayer.classList.add("graph-edges");
  for (const item of layout.edges) {
    const path = document.createElementNS(SVG, "path");
    path.setAttribute("d", item.path);
    path.setAttribute("data-relation", item.edge.relation);
    const title = document.createElementNS(SVG, "title");
    title.textContent = `${item.edge.source} ${item.edge.relation} ${item.edge.target}`;
    path.append(title);
    edgeLayer.append(path);
  }
  svg.append(edgeLayer);
  const nodeLayer = document.createElementNS(SVG, "g");
  const focusables: SVGGElement[] = [];
  for (const item of layout.nodes) {
    const group = document.createElementNS(SVG, "g");
    group.classList.add("graph-node", `node-${item.node.type}`);
    group.setAttribute("transform", `translate(${item.x} ${item.y})`);
    group.setAttribute("tabindex", "0");
    group.setAttribute("role", "button");
    group.setAttribute("aria-label", `${item.node.label}, ${item.node.type}, ${item.node.vault_id}`);
    group.dataset.handle = item.node.handle;
    const rect = document.createElementNS(SVG, "rect");
    rect.setAttribute("x", "-72");
    rect.setAttribute("y", "-25");
    rect.setAttribute("width", "144");
    rect.setAttribute("height", "50");
    rect.setAttribute("rx", "12");
    const text = document.createElementNS(SVG, "text");
    text.setAttribute("text-anchor", "middle");
    text.setAttribute("dominant-baseline", "middle");
    text.textContent = truncateGraphLabel(item.node.label);
    group.append(rect, text);
    const activate = (): void => onOpen(item.node.handle);
    group.addEventListener("click", activate);
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        activate();
      } else if (["ArrowLeft", "ArrowUp", "ArrowRight", "ArrowDown"].includes(event.key)) {
        event.preventDefault();
        const index = focusables.indexOf(group);
        const delta = event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 1;
        focusables[(index + delta + focusables.length) % focusables.length]?.focus();
      }
    });
    focusables.push(group);
    nodeLayer.append(group);
  }
  svg.append(nodeLayer);
  return svg;
}
