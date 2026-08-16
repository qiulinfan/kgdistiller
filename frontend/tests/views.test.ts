import { describe, expect, it } from "vitest";
import type { ApiEnvelope, ApiResult, NodeDetail } from "../src/api/contracts";
import type { WorkspaceState } from "../src/state/model";
import { renderBrowse } from "../src/views/browse";
import { renderConcept } from "../src/views/concept";
import { renderSource } from "../src/views/source";
import { envelope, node, sha256, source } from "./fixtures";

function state(primary: ApiEnvelope<ApiResult>, related: ApiEnvelope<ApiResult>[], route: WorkspaceState["route"], notices: string[] = []): WorkspaceState {
  return {
    phase: "ready",
    route,
    generation: primary.generation,
    epoch: 1,
    navigation: 1,
    status: null,
    vaults: [],
    incompleteVaults: [],
    response: primary,
    related,
    routeNotices: notices,
    message: null
  };
}

describe("bounded workspace views", () => {
  it("surfaces client/server taxonomy bounds and filters relations consistently", async () => {
    const rootNode = node("alpha:root", "field");
    const child = node("alpha:child", "knowledge");
    const roots = await envelope({ kind: "roots" as const, nodes: [rootNode], omissions: [], truncated: false });
    const neighbors = await envelope({
      kind: "neighbors" as const,
      center: rootNode.handle,
      nodes: [rootNode, child],
      edges: [
        { source: rootNode.handle, relation: "contains" as const, target: child.handle, evidence: null, curation_status: "not-applicable" as const },
        { source: rootNode.handle, relation: "implies" as const, target: child.handle, evidence: null, curation_status: "current" as const }
      ],
      omissions: [{ kind: "edge" as const, id: rootNode.handle, reason: "limit" as const }],
      truncated: true
    });
    const view = renderBrowse({ state: state(roots, [neighbors], { name: "browse", vault: "alpha" }, ["Taxonomy expansion reached the client bound."]), go: () => undefined });
    expect(view.textContent).toContain("Client display bound");
    expect(view.textContent).toContain("Partial result");
    expect(view.querySelector("svg")?.getAttribute("aria-label")).toContain("partial coverage");
    const selector = view.querySelector<HTMLSelectElement>('select[aria-label="Filter by relation type"]');
    expect(selector).not.toBeNull();
    if (selector) {
      selector.value = "implies";
      selector.dispatchEvent(new Event("change"));
    }
    const graphText = view.querySelector(".graph-host")?.textContent ?? "";
    expect(graphText).toContain("implies");
    expect(graphText).not.toContain("— contains →");
  });

  it("labels a client-bounded two-hop neighborhood as partial", async () => {
    const summary = node("alpha:root", "knowledge");
    const detail: NodeDetail = { ...summary, aliases: [], text: null, authority: null, provenance: null, open_actions: null };
    const primary = await envelope({ kind: "node" as const, node: detail, edges: [], evidence: [], omissions: [], truncated: false });
    const neighbors = await envelope({ kind: "neighbors" as const, center: summary.handle, nodes: [summary], edges: [], omissions: [], truncated: false });
    const view = renderConcept({ state: state(primary, [neighbors], { name: "node", handle: summary.handle }, ["Second-hop expansion reached the client bound."]), go: () => undefined });
    expect(view.querySelector("svg")?.getAttribute("aria-label")).toContain("partial coverage");
    expect(view.textContent).toContain("Client display bound");
  });

  it("shows version and excerpt truncation instead of implying completeness", async () => {
    const sourceRow = source();
    const primary = await envelope({ kind: "source" as const, source: sourceRow });
    const versions = await envelope({
      kind: "versions" as const,
      document_id: sourceRow.document_id,
      versions: [{
        version_id: sourceRow.current_version_id, sequence: 1, captured_at: "2026-01-01T00:00:00Z", captured_path: sourceRow.path,
        format: "markdown" as const, predecessor_version_id: null, raw_sha256: "a".repeat(64), normalized_text_sha256: "b".repeat(64), byte_count: 1, derivation_status: null
      }],
      next_before_sequence: 1,
      truncated: true
    });
    const excerpt = await envelope({
      kind: "excerpt" as const,
      document_id: sourceRow.document_id,
      version_id: sourceRow.current_version_id,
      path: sourceRow.path,
      line: 1,
      start: 1,
      end: 1,
      lines: [{ number: 1, text: "focus" }],
      excerpt_sha256: await sha256("focus"),
      truncated: true
    });
    const view = renderSource({ state: state(primary, [versions, excerpt], { name: "source", vault: "alpha", document: sourceRow.document_id }), go: () => undefined });
    expect(view.textContent).toContain("History is paginated");
    expect(view.textContent).toContain("focus line is retained");
  });

  it("renders hostile labels, bodies, and excerpts only as text", async () => {
    const hostile = '<img src=x onerror="globalThis.pwned=true"><script>pwned()</script>';
    const summary = node("alpha:hostile", "knowledge", hostile);
    const detail: NodeDetail = {
      ...summary,
      aliases: [hostile],
      text: hostile,
      authority: null,
      provenance: null,
      open_actions: null
    };
    const primary = await envelope({
      kind: "node" as const,
      node: detail,
      edges: [],
      evidence: [{
        kind: "concept" as const,
        handle: summary.handle,
        source: null,
        relation: null,
        target: null,
        document_id: "11111111-1111-1111-1111-111111111111",
        version_id: "doc:11111111-1111-1111-1111-111111111111:v00000001",
        source_path: "Sources/note.md",
        format: "markdown" as const,
        start_line: 1,
        end_line: 1,
        start_column: null,
        end_column: null,
        excerpt: hostile,
        excerpt_sha256: await sha256(hostile)
      }],
      omissions: [],
      truncated: false
    });
    const view = renderConcept({ state: state(primary, [], { name: "node", handle: summary.handle }), go: () => undefined });
    expect(view.querySelector("img")).toBeNull();
    expect(view.querySelector("script")).toBeNull();
    expect(view.textContent).toContain(hostile);
    expect(view.querySelector("blockquote")?.textContent).toBe(hostile);
  });
});
