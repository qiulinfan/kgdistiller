import { describe, expect, it } from "vitest";
import type { ApiResult, SearchNode } from "../src/api/contracts";
import { assertApiResponse } from "../src/api/validators";
import { envelope, node, sha256, source } from "./fixtures";

async function rejected(result: ApiResult, vaults = ["alpha"]): Promise<void> {
  await expect(assertApiResponse(await envelope(result, vaults))).rejects.toThrow();
}

describe("frontend API semantic closure", () => {
  it("rejects portable-path and unavailable-center forgeries", async () => {
    await rejected({ kind: "source", source: source("C:/secret.md") });
    await rejected({ kind: "neighbors", center: "beta:x", nodes: [], edges: [], omissions: [], truncated: false });
  });

  it("rejects cross-Vault stale rows and incomplete relation evidence", async () => {
    await rejected({
      kind: "stale",
      items: [{ kind: "node", node: { ...node("beta:x", "knowledge"), curation_status: "needs-review" }, reason: "needs-review" }],
      next_cursor: null,
      omissions: [],
      truncated: false
    });
    const document = "11111111-1111-1111-1111-111111111111";
    await rejected({
      kind: "context",
      query: null,
      resolutions: [],
      nodes: [],
      edges: [],
      evidence: [{
        kind: "relation", handle: "alpha:root", source: "alpha:root", relation: null, target: "alpha:child",
        document_id: document, version_id: `doc:${document}:v00000001`, source_path: "Sources/note.md", format: "markdown",
        start_line: 1, end_line: 1, start_column: null, end_column: null, excerpt: "evidence", excerpt_sha256: await sha256("evidence")
      }],
      omissions: [],
      truncated: false
    });
  });

  it("rejects retrieval-control and lane path forgeries", async () => {
    await rejected({
      kind: "search", query: "root",
      resolutions: [{ query: "root", status: "missing", match_kind: "id", matches: ["alpha:root"], overflow: false }],
      nodes: [], edges: [], evidence: [], omissions: [], truncated: false
    });
    const searchNode: SearchNode = {
      ...node("alpha:root", "knowledge"),
      score: 1,
      lane_evidence: [{
        lane: "graph", rank: 1, score: 1, reason: "trusted-edge", match_kind: null,
        matched_fields: [], matched_terms: [], scope: null, seed: "beta:seed",
        path: [{ source: "beta:seed", relation: "implies", target: "alpha:root" }]
      }]
    };
    await rejected({ kind: "search", query: "root", resolutions: [], nodes: [searchNode], edges: [], evidence: [], omissions: [], truncated: false }, ["alpha", "beta"]);
    await rejected({ kind: "roots", nodes: [], omissions: [{ kind: "node", id: "alpha:root", reason: "limit" }], truncated: false });
  });

  it("binds version pagination, Unicode diff lines, and empty excerpts", async () => {
    const document = "11111111-1111-1111-1111-111111111111";
    await rejected({
      kind: "versions", document_id: document,
      versions: [{
        version_id: `doc:${document}:v00000002`, sequence: 2, captured_at: "2026-01-01T00:00:00Z", captured_path: "Sources/note.md",
        format: "markdown", predecessor_version_id: null, raw_sha256: "a".repeat(64), normalized_text_sha256: "b".repeat(64), byte_count: 1, derivation_status: "committed"
      }],
      next_before_sequence: null, truncated: false
    });
    await expect(assertApiResponse(await envelope({
      kind: "diff", document_id: document, path: "Sources/note.md", from_version_id: null,
      to_version_id: `doc:${document}:v00000001`, semantic_changed: true, text: "a\u2028b", truncated: false,
      emitted_lines: 2, max_bytes: 1024 * 1024, max_lines: 10_000
    }))).resolves.toBeTruthy();
    await rejected({
      kind: "excerpt", document_id: document, version_id: `doc:${document}:v00000001`, path: "Sources/note.md",
      line: 9, start: 1, end: 0, lines: [], excerpt_sha256: await sha256(""), truncated: false
    });
  });
});
