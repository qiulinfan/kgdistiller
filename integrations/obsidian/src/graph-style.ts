/**
 * The typed-graph look shared by the graph view and the lookup's neighbour
 * graph: one Cytoscape stylesheet in the theme's colours and one fcose layout.
 * The fcose extension is registered here, once.
 */

import cytoscape from "cytoscape";
import fcose from "cytoscape-fcose";

cytoscape.use(fcose);

export const FCOSE_LAYOUT: cytoscape.BaseLayoutOptions & Record<string, unknown> = {
  name: "fcose",
  animate: false,
  fit: true,
  padding: 32,
  quality: "default",
  randomize: true,
  nodeRepulsion: () => 9000,
  idealEdgeLength: () => 110,
  edgeElasticity: () => 0.45,
  nodeSeparation: 90,
  gravity: 0.25,
  numIter: 2500,
};

/** The stylesheet in the theme colours of `element`; `.kgd-focus` marks the record a lookup is about. */
export function graphStylesheet(element: HTMLElement): cytoscape.StylesheetJson {
  const computedStyle = getComputedStyle(element);
  const themeColor = (name: string, fallback: string): string =>
    computedStyle.getPropertyValue(name).trim() || fallback;
  const textNormal = themeColor("--text-normal", "#1f2937");
  const textMuted = themeColor("--text-muted", "#64748b");
  const backgroundPrimary = themeColor("--background-primary", "#ffffff");
  const accent = themeColor("--interactive-accent", "#7c3aed");
  return [
    {
      selector: "node",
      style: {
        label: "data(display)",
        color: textNormal,
        "font-size": 11,
        "text-wrap": "wrap",
        "text-max-width": "120px",
        "text-valign": "bottom",
        "text-margin-y": 7,
        "background-color": accent,
        "border-width": 2,
        "border-color": backgroundPrimary,
        width: 30,
        height: 30,
      },
    },
    {
      selector: "node[color]",
      style: { "background-color": "data(color)" },
    },
    {
      selector: "node.kgd-relation",
      style: { shape: "diamond", width: 24, height: 24, "font-size": 9 },
    },
    {
      selector: "node.understanding-unknown",
      style: { "border-style": "dashed", "border-color": textMuted, "border-width": 2 },
    },
    {
      selector: "node.understanding-not-yet-understood",
      style: { "border-color": "#ca8a04", "border-width": 4 },
    },
    {
      selector: "node.understanding-understood",
      style: { "border-color": "#059669", "border-width": 4 },
    },
    {
      selector: "node.kgd-draft",
      style: { "border-style": "dashed", "background-opacity": 0.45 },
    },
    {
      selector: "node.kgd-stub",
      style: { "background-color": "#9ca3af", width: 20, height: 20, color: textMuted },
    },
    {
      selector: "node.kgd-dangling",
      style: { "background-color": "#dc2626", width: 18, height: 18, color: "#dc2626" },
    },
    {
      selector: "node.kgd-pending",
      style: {
        shape: "ellipse",
        width: 14,
        height: 14,
        "background-color": "#9ca3af",
        "background-opacity": 0,
        "border-style": "dotted",
        "border-color": textMuted,
        "border-width": 2,
        color: textMuted,
        "font-style": "italic",
        "font-size": 9,
      },
    },
    {
      selector: "edge",
      style: {
        width: 2,
        "curve-style": "bezier",
        "line-color": textMuted,
        "target-arrow-color": textMuted,
        "arrow-scale": 0.8,
        label: "data(label)",
        "font-size": 9,
        color: textMuted,
        "text-background-color": backgroundPrimary,
        "text-background-opacity": 0.85,
        "text-background-padding": "2px",
      },
    },
    {
      selector: "edge[color]",
      style: { "line-color": "data(color)", "target-arrow-color": "data(color)" },
    },
    {
      selector: "edge.kgd-relation-edge",
      style: { width: 3 },
    },
    {
      selector: "edge.kgd-directed, edge.kgd-role",
      style: { "target-arrow-shape": "triangle" },
    },
    {
      selector: "edge.kgd-requires",
      style: { "line-style": "dashed", "target-arrow-shape": "vee" },
    },
    {
      selector: "edge.kgd-draft",
      style: { "line-style": "dashed", opacity: 0.7 },
    },
    {
      selector: "edge.kgd-dangling",
      style: { "line-color": "#dc2626", "target-arrow-color": "#dc2626" },
    },
    {
      selector: ":selected",
      style: { "overlay-color": accent, "overlay-opacity": 0.18 },
    },
    {
      selector: "node.kgd-focus",
      style: { "border-color": accent, "border-width": 4, width: 38, height: 38 },
    },
  ];
}
