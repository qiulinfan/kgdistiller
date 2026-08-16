import { describe, expect, it, vi } from "vitest";
import type { Edge } from "../src/api/contracts";
import { renderGraphList } from "../src/graph/graph-list";
import { layoutNeighborhood } from "../src/graph/neighborhood-layout";
import { renderSvgGraph, truncateGraphLabel } from "../src/graph/svg-graph";
import { layoutTaxonomy } from "../src/graph/taxonomy-layout";
import { node } from "./fixtures";

const contains = (source: string, target: string): Edge => ({ source, relation: "contains", target, evidence: null, curation_status: "not-applicable" });

describe("bounded deterministic graph layouts", () => {
  it("preserves a multi-parent DAG and rejects cycles/dangling edges", () => {
    const nodes = [node("alpha:a"), node("alpha:b"), node("alpha:c", "knowledge")];
    const edges = [contains("alpha:a", "alpha:c"), contains("alpha:b", "alpha:c")];
    const layout = layoutTaxonomy(nodes, edges);
    expect(layout.ok).toBe(true);
    if (layout.ok) expect(layout.edges).toHaveLength(2);
    expect(layoutTaxonomy(nodes, [...edges, contains("alpha:c", "alpha:a")])).toMatchObject({ ok: false, reason: "cycle" });
    expect(layoutTaxonomy(nodes, [contains("alpha:a", "alpha:missing")])).toMatchObject({ ok: false, reason: "dangling" });
  });

  it("rejects nodes outside a two-hop neighborhood", () => {
    const nodes = [node("alpha:a"), node("alpha:b"), node("alpha:c"), node("alpha:d")];
    expect(layoutNeighborhood("alpha:a", nodes, [contains("alpha:a", "alpha:b"), contains("alpha:b", "alpha:c"), contains("alpha:c", "alpha:d")])).toMatchObject({ ok: false, reason: "outside-two-hops" });
  });

  it("truncates grapheme clusters without breaking emoji or combining marks", () => {
    const label = `${"a".repeat(20)}👩🏽‍🔬e\u0301tail`;
    const truncated = truncateGraphLabel(label, 22);
    expect(truncated).not.toContain("�");
    expect(truncated.endsWith("…")).toBe(true);
    expect(truncated).toContain("👩🏽‍🔬");
  });

  it("opens SVG nodes from Enter and Space and always exposes a list fallback", () => {
    const nodes = [node("alpha:a"), node("alpha:b", "knowledge")];
    const edges = [contains("alpha:a", "alpha:b")];
    const layout = layoutTaxonomy(nodes, edges);
    expect(layout.ok).toBe(true);
    if (!layout.ok) return;
    const opened = vi.fn();
    const svg = renderSvgGraph(layout, "Taxonomy", opened);
    const target = svg.querySelector<SVGGElement>('[role="button"]');
    expect(target).not.toBeNull();
    target?.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    target?.dispatchEvent(new KeyboardEvent("keydown", { key: " ", bubbles: true }));
    expect(opened).toHaveBeenCalledTimes(2);
    expect(opened).toHaveBeenNthCalledWith(1, "alpha:a");

    const fallback = renderGraphList(nodes, edges, opened);
    expect(fallback.querySelector("h3")?.textContent).toBe("Graph as a list");
    expect(fallback.querySelectorAll("button")).toHaveLength(2);
    expect(fallback.textContent).toContain("alpha:a — contains → alpha:b");
  });
});
