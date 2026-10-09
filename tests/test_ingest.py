from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from kgdistiller.alignment import empty_alignment_set
from kgdistiller.cli import (
    DELTA_SCHEMA,
    GRAPH_SCHEMA,
    SOURCE_SCHEMA,
    apply_delta,
    curation_report,
    load_state,
    make_agent_snapshot,
    sha256_authority_file,
    sha256_file,
    sha256_text,
    synchronize,
)
from kgdistiller.contracts import sha256_json
from kgdistiller.ingest import (
    CAPABILITY,
    JOURNAL_SCHEMA,
    REQUEST_SCHEMA,
    IngestError,
    IngestPaths,
    _backup_target,
    _remove_target,
    _restore_journal,
    apply_ingest,
    finalize_request,
    plan_ingest,
    recover_ingest,
)
from kgdistiller.query import (
    COMPARISON_SCHEMA,
    SNAPSHOT_SCHEMA,
    GraphView,
    resolve_concepts,
    search,
)


class TransactionalIngestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-ingest-test-")
        self.repo = Path(self.temporary.name)
        self.authority = self.repo / "notes/demo/chapter.md"
        self.authority.parent.mkdir(parents=True)
        self.authority.write_text(
            "> **Definition: --[[Alpha]]--**\n>\n"
            "> Alpha is the baseline concept.\n",
            encoding="utf-8",
        )
        self.registry = self.repo / ".knowledge/sources.json"
        self.registry.parent.mkdir(parents=True)
        self.registry.write_text(
            json.dumps(
                {
                    "schema": SOURCE_SCHEMA,
                    "sources": [
                        {
                            "id": "notes:demo",
                            "root": "notes/demo",
                            "files": ["*.md", "*.typ", "*.tex"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.graph = self.repo / ".knowledge/graph"
        self.identities = self.repo / ".knowledge/identities.json"
        self.alignments = self.repo / ".knowledge/alignments.json"
        self.alignments.write_text(
            json.dumps(empty_alignment_set()), encoding="utf-8"
        )
        self.paths = IngestPaths(
            repo_root=self.repo,
            registry=self.registry,
            graph_dir=self.graph,
            identities=self.identities,
            alignments=self.alignments,
        )
        synchronize(
            self.repo,
            self.registry,
            self.graph,
            identities=self.identities,
            alignments=self.alignments,
            files=[],
            write=True,
        )
        baseline_delta = self.repo / ".knowledge/build/baseline.delta.json"
        baseline_delta.parent.mkdir(parents=True, exist_ok=True)
        baseline_delta.write_text(
            json.dumps(
                {
                    "schema": DELTA_SCHEMA,
                    "remove_nodes": [],
                    "nodes": [
                        {"id": "alpha", "text": "Alpha is the baseline concept."}
                    ],
                    "edges": [],
                    "remove_edges": [],
                }
            ),
            encoding="utf-8",
        )
        apply_delta(self.graph, baseline_delta)
        synchronize(
            self.repo,
            self.registry,
            self.graph,
            identities=self.identities,
            alignments=self.alignments,
            files=[self.authority.relative_to(self.repo)],
            write=True,
        )

        self.candidate = self._candidate_snapshot()
        self.candidate_path = self.repo / ".knowledge/build/beta.snapshot.json"
        self.candidate_path.write_text(json.dumps(self.candidate), encoding="utf-8")
        target = make_agent_snapshot(load_state(self.graph))
        self.query_report = {
            "schema": COMPARISON_SCHEMA,
            "alignment_sha256": sha256_json(empty_alignment_set()),
            "candidate": {
                "namespace": self.candidate["namespace"],
                "snapshot_sha256": self.candidate["snapshot_sha256"],
                "graph_sha256": self.candidate["graph"]["sha256"],
            },
            "target": {
                "namespace": "personal",
                "snapshot_sha256": target["snapshot_sha256"],
                "graph_sha256": target["graph"]["sha256"],
            },
            "results": [
                {
                    "candidate": {"namespace": "paper:beta", "id": "beta"},
                    "status": "unmatched",
                    "identity_target_id": None,
                    "candidates": [],
                    "registry_evidence": [],
                    "rejected_target_ids": [],
                }
            ],
            "summary": {
                "matched": 0,
                "ambiguous": 0,
                "unmatched": 1,
                "present_edges": 0,
                "missing_edges": 0,
            },
            "alignment_report_sha256": "1" * 64,
        }
        self.query_path = self.repo / ".knowledge/build/beta.comparison.json"
        self.query_path.write_text(json.dumps(self.query_report), encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _candidate_snapshot() -> dict:
        snapshot = {
            "schema": SNAPSHOT_SCHEMA,
            "namespace": "paper:beta",
            "graph": {
                "schema": GRAPH_SCHEMA,
                "sha256": "b" * 64,
                "counts": {"nodes": 1, "edges": 0, "references": 0},
            },
            "nodes": [
                {
                    "id": "beta",
                    "type": "knowledge",
                    "label": "Beta",
                    "text": "Beta is a source-backed candidate concept.",
                    "properties": {"aliases": []},
                    "provenance": {
                        "authority": "paper.md",
                        "line": 7,
                        "source_format": "markdown",
                    },
                }
            ],
            "edges": [],
            "references": [],
            "diagnostics": {"errors": [], "warnings": []},
        }
        snapshot["snapshot_sha256"] = sha256_json(snapshot)
        return snapshot

    def request(self, mode: str, *, request_id: str = "add-beta") -> dict:
        content = self.authority.read_text(encoding="utf-8") + (
            "\n> **Definition: --[[Beta]]--**\n>\n"
            "> Beta is a source-backed candidate concept.\n"
        )
        state = load_state(self.graph)
        return finalize_request(
            {
                "schema": REQUEST_SCHEMA,
                "request_id": request_id,
                "mode": mode,
                "capabilities": [CAPABILITY],
                "base_graph_sha256": state.manifest["graph_sha256"],
                "base_alignment_sha256": sha256_json(empty_alignment_set()),
                "candidate_snapshot": {
                    "path": self.candidate_path.relative_to(self.repo).as_posix(),
                    "sha256": self.candidate["snapshot_sha256"],
                },
                "query_report": {
                    "path": self.query_path.relative_to(self.repo).as_posix(),
                    "sha256": sha256_json(self.query_report),
                },
                "authority_patches": [
                    {
                        "path": self.authority.relative_to(self.repo).as_posix(),
                        "operation": "write",
                        "expected_sha256": sha256_authority_file(self.authority),
                        "content": content,
                        "content_sha256": sha256_text(content),
                        "expected_markers": {
                            "definitions": ["alpha", "beta"],
                            "references": [],
                        },
                    }
                ],
                "decisions": [
                    {
                        "candidate_id": "beta",
                        "action": "add",
                        "target_id": "beta",
                        "evidence": "The reviewed source explicitly defines Beta.",
                    }
                ],
                "delta": {
                    "schema": DELTA_SCHEMA,
                    "remove_nodes": [],
                    "nodes": [
                        {
                            "id": "beta",
                            "text": "Beta is a source-backed candidate concept.",
                        }
                    ],
                    "edges": [],
                    "remove_edges": [],
                },
                "alignment_decisions": [],
                "review": {
                    "status": "reviewed",
                    "reviewer": "fixture-reviewer",
                    "evidence": ["Beta has an explicit reviewed authority marker."],
                    "provenance": [
                        {
                            "path": self.authority.relative_to(self.repo).as_posix(),
                            "line": 5,
                            "kind": "authority",
                        }
                    ],
                },
            }
        )

    def material_hashes(self) -> dict[str, str | None]:
        values: dict[str, str | None] = {
            "authority": sha256_file(self.authority),
            "alignments": sha256_file(self.alignments),
        }
        for path in sorted(self.graph.rglob("*")):
            if path.is_file():
                values[f"graph:{path.relative_to(self.graph).as_posix()}"] = (
                    sha256_file(path)
                )
        return values

    def write_recovery_journal(
        self,
        *,
        request_sha256: str,
        status: str = "installing",
        backup_root: Path | None = None,
        targets: list[dict] | None = None,
    ) -> tuple[Path, Path]:
        state_dir = self.repo / ".knowledge/build/kgdistiller-ingest"
        expected_backup_root = state_dir / "backups" / request_sha256
        journal_path = state_dir / "journal.json"
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        journal_path.write_text(
            json.dumps(
                {
                    "schema": JOURNAL_SCHEMA,
                    "request_sha256": request_sha256,
                    "status": status,
                    "backup_root": str(
                        backup_root
                        if backup_root is not None
                        else expected_backup_root
                    ),
                    "targets": [] if targets is None else targets,
                }
            ),
            encoding="utf-8",
        )
        return journal_path, expected_backup_root

    def test_plan_is_read_only_and_predicts_json_graph_change(self) -> None:
        before = self.material_hashes()

        plan = plan_ingest(self.paths, self.request("plan"))

        self.assertEqual("kgdistiller-ingest-plan-v1", plan["schema"])
        self.assertEqual("planned", plan["status"])
        self.assertEqual(["beta"], plan["changes"]["nodes"]["added"])
        self.assertNotEqual(plan["before"]["graph_sha256"], plan["after"]["graph_sha256"])
        self.assertEqual(before, self.material_hashes())
        self.assertFalse(any(self.repo.rglob("*.sqlite")))

    def partial_request(self, suffix: str = ".md") -> dict:
        """Start with two authored, uncurated definitions and review only Alpha."""
        sources = {
            ".md": (
                "> **Definition: --[[Alpha]]--**\n> Alpha is the first concept.\n\n"
                "> **Definition: --[[Beta]]--**\n> Beta is the second concept.\n"
            ),
            ".typ": (
                "#definition(title: [#kn[Alpha]])[Alpha is the first concept.]\n\n"
                "#definition(title: [#kn[Beta]])[Beta is the second concept.]\n"
            ),
            ".tex": (
                "\\begin{definition}\\kn{Alpha} Alpha is the first concept.\\end{definition}\n\n"
                "\\begin{definition}\\kn{Beta} Beta is the second concept.\\end{definition}\n"
            ),
        }
        old_authority = self.authority
        self.authority = old_authority.with_suffix(suffix)
        if old_authority != self.authority:
            old_authority.unlink()
        content = sources[suffix]
        self.authority.write_text(content, encoding="utf-8")
        shutil.rmtree(self.graph)
        shutil.rmtree(self.repo / ".knowledge/entries")
        if suffix != ".md":
            evidence = (
                self.repo / ".knowledge/derived/by-source"
                / self.authority.relative_to(self.repo).with_suffix(suffix + ".md")
            )
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence.write_text(
                "Alpha is the first concept.\n\nBeta is the second concept.\n",
                encoding="utf-8",
            )
        synchronize(
            self.repo, self.registry, self.graph,
            identities=self.identities, alignments=self.alignments,
            files=[], write=True,
        )
        node = self.candidate["nodes"][0]
        node.update({"id": "alpha", "label": "Alpha", "text": "Alpha is the first concept."})
        self.candidate.pop("snapshot_sha256")
        self.candidate["snapshot_sha256"] = sha256_json(self.candidate)
        self.candidate_path.write_text(json.dumps(self.candidate), encoding="utf-8")
        snapshot = make_agent_snapshot(load_state(self.graph))
        self.query_report["candidate"]["snapshot_sha256"] = self.candidate["snapshot_sha256"]
        self.query_report["target"].update({
            "snapshot_sha256": snapshot["snapshot_sha256"],
            "graph_sha256": snapshot["graph"]["sha256"],
        })
        result = self.query_report["results"][0]
        result["candidate"]["id"] = "alpha"
        result["status"] = "matched"
        result["identity_target_id"] = "alpha"
        self.query_report["summary"].update({"matched": 1, "unmatched": 0})
        self.query_path.write_text(json.dumps(self.query_report), encoding="utf-8")
        request = self.request("apply", request_id="curate-alpha")
        # Moving an unchanged statement must not pull Beta into curation scope.
        request["authority_patches"][0].update({
            "content": "\n" + content,
            "content_sha256": sha256_text("\n" + content),
        })
        request["decisions"] = [{
            "candidate_id": "alpha", "action": "reuse", "target_id": "alpha",
            "evidence": "Review the existing Alpha definition only.",
        }]
        request["delta"]["nodes"] = [{"id": "alpha", "text": "Alpha is the first concept."}]
        return finalize_request(request)

    def assert_partial_ingest(self, suffix: str) -> None:
        request = self.partial_request(suffix)
        receipt = apply_ingest(self.paths, request)
        self.assertEqual("committed", receipt["status"])
        state = load_state(self.graph)
        self.assertEqual("current", state.nodes["alpha"]["properties"]["curation_status"])
        self.assertEqual("pending", state.nodes["beta"]["properties"]["curation_status"])
        self.assertFalse((self.repo / ".knowledge/entries/beta.md").exists())
        # The normal source-wide check must still report its coverage gap.
        report = curation_report(state, {self.authority.relative_to(self.repo).as_posix()})
        self.assertEqual(["beta"], [item["node"] for item in report["errors"]])

    def test_partial_ingest_curates_only_selected_markdown_node(self) -> None:
        self.assert_partial_ingest(".md")

    def test_partial_ingest_curates_only_selected_typst_node(self) -> None:
        self.assert_partial_ingest(".typ")

    def test_partial_ingest_curates_only_selected_latex_node(self) -> None:
        self.assert_partial_ingest(".tex")

    def test_partial_ingest_does_not_require_an_authority_patch(self) -> None:
        request = self.partial_request()
        request["authority_patches"] = []
        receipt = apply_ingest(self.paths, finalize_request(request))
        self.assertEqual("committed", receipt["status"])
        state = load_state(self.graph)
        self.assertEqual("current", state.nodes["alpha"]["properties"]["curation_status"])
        self.assertEqual("pending", state.nodes["beta"]["properties"]["curation_status"])

    def test_partial_ingest_rejects_changed_sibling_without_reviewed_entry(self) -> None:
        request = self.partial_request()
        patch = request["authority_patches"][0]
        patch["content"] = patch["content"].replace("Beta is the second concept.", "Beta has changed.")
        patch["content_sha256"] = sha256_text(patch["content"])
        before = self.material_hashes()
        with self.assertRaises(IngestError) as rejected:
            apply_ingest(self.paths, finalize_request(request))
        self.assertEqual("curation-failed", rejected.exception.code)
        self.assertEqual("beta", rejected.exception.diagnostics[0]["node"])
        self.assertEqual(before, self.material_hashes())

    def test_partial_ingest_rejects_changed_curated_definition_without_replacement(self) -> None:
        request = self.request("apply")
        patch = request["authority_patches"][0]
        patch["content"] = patch["content"].replace("Alpha is the baseline concept.", "Alpha has changed.")
        patch["content_sha256"] = sha256_text(patch["content"])
        before = self.material_hashes()
        with self.assertRaises(IngestError) as rejected:
            apply_ingest(self.paths, finalize_request(request))
        self.assertEqual("curation-failed", rejected.exception.code)
        self.assertIn("stale-node-entry", {item["code"] for item in rejected.exception.diagnostics})
        self.assertEqual(before, self.material_hashes())

    def test_partial_ingest_preserves_unchanged_entry_when_source_lines_move(self) -> None:
        request = self.request("apply")
        patch = request["authority_patches"][0]
        patch["content"] = "# Reading notes\n\n" + patch["content"]
        patch["content_sha256"] = sha256_text(patch["content"])
        entry_path = self.repo / ".knowledge/entries/alpha.md"
        entry_before = entry_path.read_bytes()
        apply_ingest(self.paths, finalize_request(request))
        alpha = load_state(self.graph).nodes["alpha"]
        self.assertEqual(3, alpha["provenance"]["line"])
        self.assertEqual("current", alpha["properties"]["curation_status"])
        self.assertEqual(entry_before, entry_path.read_bytes())

    def test_partial_ingest_rejects_new_definition_without_entry(self) -> None:
        request = self.request("apply")
        request["delta"]["nodes"] = []
        with self.assertRaises(IngestError) as rejected:
            apply_ingest(self.paths, finalize_request(request))
        self.assertEqual("curation-failed", rejected.exception.code)
        self.assertEqual("beta", rejected.exception.diagnostics[0]["node"])

    def test_partial_ingest_checks_explicit_node_update_without_authority_patch(self) -> None:
        request = self.partial_request()
        request["authority_patches"] = []
        request["delta"]["nodes"] = [{"id": "alpha", "properties": {"aliases": ["A"]}}]
        with self.assertRaises(IngestError) as rejected:
            apply_ingest(self.paths, finalize_request(request))
        self.assertEqual("curation-failed", rejected.exception.code)
        self.assertEqual("alpha", rejected.exception.diagnostics[0]["node"])

    def external_dependency_request(
        self, suffix: str, *, include_reference: bool, existing_edge: bool,
    ) -> dict:
        request = self.partial_request(suffix)
        gamma = self.authority.parent / "gamma.md"
        gamma.write_text(
            "> **Definition: --[[Gamma]]--**\n> Gamma is an external prerequisite.\n",
            encoding="utf-8",
        )
        if include_reference:
            reference = {".md": "[[Gamma]]", ".typ": "#ref[Gamma]", ".tex": r"\knref{Gamma}"}[suffix]
            self.authority.write_text(
                self.authority.read_text(encoding="utf-8") + "\n" + reference + "\n",
                encoding="utf-8",
            )
        synchronize(
            self.repo, self.registry, self.graph,
            identities=self.identities, alignments=self.alignments,
            files=[], write=True,
        )
        edge = {
            "source": "gamma", "relation": "prerequisite-for", "target": "beta",
            "evidence": "Beta directly uses Gamma in its source explanation.",
        }
        if existing_edge:
            delta_path = self.repo / ".knowledge/build/dependency.delta.json"
            delta_path.write_text(json.dumps({
                "schema": DELTA_SCHEMA, "nodes": [], "edges": [edge],
                "remove_nodes": [], "remove_edges": [],
            }), encoding="utf-8")
            apply_delta(self.graph, delta_path)
        else:
            request["delta"]["edges"] = [edge]
        state = load_state(self.graph)
        snapshot = make_agent_snapshot(state)
        self.query_report["target"].update({
            "snapshot_sha256": snapshot["snapshot_sha256"],
            "graph_sha256": snapshot["graph"]["sha256"],
        })
        self.query_path.write_text(json.dumps(self.query_report), encoding="utf-8")
        request["base_graph_sha256"] = state.manifest["graph_sha256"]
        request["query_report"]["sha256"] = sha256_json(self.query_report)
        request["authority_patches"][0]["expected_sha256"] = sha256_authority_file(self.authority)
        return finalize_request(request)

    def assert_partial_ingest_rejects_removed_sibling_reference(self, suffix: str) -> None:
        request = self.external_dependency_request(
            suffix, include_reference=True, existing_edge=True,
        )
        # Only Alpha is curated; Beta's definition stays unchanged while the
        # authority patch removes its last file-level reference to Gamma.
        before = self.material_hashes()
        with self.assertRaises(IngestError) as rejected:
            apply_ingest(self.paths, request)
        self.assertEqual("curation-failed", rejected.exception.code)
        self.assertEqual(
            {"missing-cross-file-ref"},
            {item["code"] for item in rejected.exception.diagnostics},
        )
        self.assertEqual(before, self.material_hashes())

    def test_partial_ingest_rejects_removed_sibling_markdown_reference(self) -> None:
        self.assert_partial_ingest_rejects_removed_sibling_reference(".md")

    def test_partial_ingest_rejects_removed_sibling_typst_reference(self) -> None:
        self.assert_partial_ingest_rejects_removed_sibling_reference(".typ")

    def test_partial_ingest_rejects_removed_sibling_latex_reference(self) -> None:
        self.assert_partial_ingest_rejects_removed_sibling_reference(".tex")

    def test_relation_only_ingest_requires_consumer_reference(self) -> None:
        request = self.external_dependency_request(
            ".md", include_reference=False, existing_edge=False,
        )
        request["authority_patches"] = []
        request["delta"]["nodes"] = []
        before = self.material_hashes()
        with self.assertRaises(IngestError) as rejected:
            apply_ingest(self.paths, finalize_request(request))
        self.assertEqual("curation-failed", rejected.exception.code)
        self.assertEqual(
            {"missing-cross-file-ref"},
            {item["code"] for item in rejected.exception.diagnostics},
        )
        self.assertEqual(before, self.material_hashes())

    def test_relation_only_ingest_does_not_require_pending_consumer_entry(self) -> None:
        request = self.external_dependency_request(
            ".md", include_reference=True, existing_edge=False,
        )
        request["authority_patches"] = []
        request["delta"]["nodes"] = []
        receipt = apply_ingest(self.paths, finalize_request(request))
        self.assertEqual("committed", receipt["status"])
        state = load_state(self.graph)
        self.assertEqual("pending", state.nodes["beta"]["properties"]["curation_status"])
        self.assertEqual("pending", state.nodes["gamma"]["properties"]["curation_status"])
        self.assertIn(("gamma", "prerequisite-for", "beta"), state.edges)

    def test_ingest_request_v1_refuses_unknown_request_and_delta_contracts(
        self,
    ) -> None:
        cases = (
            ("request", lambda value: value.__setitem__("schema", "legacy-ingest-request-v0")),
            (
                "delta",
                lambda value: value["delta"].__setitem__(
                    "schema", "legacy-agent-delta-v0"
                ),
            ),
        )
        for name, mutate in cases:
            with self.subTest(name=name):
                request = self.request("plan", request_id=f"legacy-{name}")
                mutate(request)
                request = finalize_request(request)

                with self.assertRaises(IngestError) as rejected:
                    plan_ingest(self.paths, request)

                self.assertEqual("invalid-request", rejected.exception.code)

    def test_plan_accepts_reviewed_hash_across_crlf_checkout(self) -> None:
        request = self.request("plan")
        expected = request["authority_patches"][0]["expected_sha256"]
        text = self.authority.read_text(encoding="utf-8")
        self.authority.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))

        self.assertEqual(expected, sha256_authority_file(self.authority))
        self.assertNotEqual(expected, sha256_file(self.authority))
        plan = plan_ingest(self.paths, request)
        self.assertEqual(
            expected, plan["before"]["source_hashes"]["notes/demo/chapter.md"]
        )

    def test_apply_is_idempotent_and_immediately_queryable_from_json(self) -> None:
        request = self.request("apply")

        first = apply_ingest(self.paths, request)
        second = apply_ingest(self.paths, request)

        self.assertEqual(first, second)
        self.assertEqual("kgdistiller-ingest-receipt-v1", first["schema"])
        self.assertEqual("committed", first["status"])
        self.assertEqual("json-memory", first["engine"]["query_backend"])
        self.assertNotIn("index_schema", first["engine"])
        self.assertNotIn(
            "index-rebuild", {item["stage"] for item in first["validations"]}
        )
        self.assertEqual(["beta"], first["changes"]["nodes"]["added"])
        receipt_path = (
            self.repo
            / ".knowledge/build/kgdistiller-ingest/receipts"
            / f"{request['request_sha256']}.json"
        )
        self.assertTrue(receipt_path.is_file())

        view = GraphView.load(self.graph, self.alignments)
        resolved = resolve_concepts(view, ["Beta"])[0]
        searched = search(view, "source-backed candidate", limit=5)
        self.assertEqual("exact", resolved["status"])
        self.assertEqual("beta", resolved["matches"][0]["id"])
        self.assertEqual("beta", searched[0]["node"]["id"])
        self.assertFalse(any(self.repo.rglob("*.sqlite")))

    def test_idempotent_apply_rejects_a_tampered_or_legacy_receipt(self) -> None:
        request = self.request("apply")
        receipt = apply_ingest(self.paths, request)
        receipt_path = (
            self.repo
            / ".knowledge/build/kgdistiller-ingest/receipts"
            / f"{request['request_sha256']}.json"
        )

        tampered = copy.deepcopy(receipt)
        tampered["warnings"] = ["forged"]
        receipt_path.write_text(json.dumps(tampered), encoding="utf-8")
        with self.assertRaisesRegex(IngestError, "receipt digest"):
            apply_ingest(self.paths, request)

        legacy = copy.deepcopy(receipt)
        legacy["schema"] = "legacy-ingest-receipt-v0"
        receipt_path.write_text(json.dumps(legacy), encoding="utf-8")
        with self.assertRaisesRegex(IngestError, "expected stored"):
            apply_ingest(self.paths, request)

    def test_stale_source_and_stale_graph_reject_without_transaction_writes(self) -> None:
        request = self.request("apply")
        self.authority.write_text(
            self.authority.read_text(encoding="utf-8") + "\nUser changed this.\n",
            encoding="utf-8",
        )
        changed = self.material_hashes()
        with self.assertRaises(IngestError) as source_error:
            apply_ingest(self.paths, request)
        self.assertEqual("stale-source", source_error.exception.code)
        self.assertEqual(changed, self.material_hashes())

        self.authority.write_text(
            "> **Definition: --[[Alpha]]--**\n>\n"
            "> Alpha is the baseline concept.\n",
            encoding="utf-8",
        )
        before = self.material_hashes()
        stale = copy.deepcopy(request)
        stale["base_graph_sha256"] = "f" * 64
        stale = finalize_request(stale)
        with self.assertRaises(IngestError) as graph_error:
            apply_ingest(self.paths, stale)
        self.assertEqual("stale-base-graph", graph_error.exception.code)
        self.assertEqual(before, self.material_hashes())

    def test_plan_and_apply_reject_query_report_from_another_alignment_generation(
        self,
    ) -> None:
        baseline_report = copy.deepcopy(self.query_report)
        for mode in ("plan", "apply"):
            with self.subTest(mode=mode):
                self.query_report = copy.deepcopy(baseline_report)
                self.query_report["alignment_sha256"] = "f" * 64
                self.query_path.write_text(
                    json.dumps(self.query_report), encoding="utf-8"
                )
                request = self.request(mode, request_id=f"stale-alignment-{mode}")
                before = self.material_hashes()

                with self.assertRaises(IngestError) as rejected:
                    if mode == "plan":
                        plan_ingest(self.paths, request)
                    else:
                        apply_ingest(self.paths, request)

                self.assertEqual("stale-query-report", rejected.exception.code)
                self.assertEqual(before, self.material_hashes())

    def test_query_report_without_alignment_generation_fails_closed(self) -> None:
        self.query_report.pop("alignment_sha256")
        self.query_path.write_text(json.dumps(self.query_report), encoding="utf-8")
        request = self.request("plan", request_id="missing-alignment-binding")

        with self.assertRaises(IngestError) as rejected:
            plan_ingest(self.paths, request)

        self.assertEqual("stale-query-report", rejected.exception.code)

    def test_v1_comparison_rejects_legacy_identity_statuses(self) -> None:
        self.query_report["results"][0]["status"] = "new"
        self.query_path.write_text(json.dumps(self.query_report), encoding="utf-8")
        request = self.request("plan", request_id="legacy-comparison-status")

        with self.assertRaises(IngestError) as rejected:
            plan_ingest(self.paths, request)

        self.assertEqual("invalid-query-report", rejected.exception.code)

    def test_install_failures_restore_authority_graph_and_alignments(self) -> None:
        stages = [
            "prepared-install",
            "installed-authorities",
            "installed-alignments",
            "installed-graph",
            "receipt-written",
        ]
        baseline = self.material_hashes()
        for index, failure_stage in enumerate(stages):
            request = self.request("apply", request_id=f"failure-{index}")

            def inject(stage: str, expected: str = failure_stage) -> None:
                if stage == expected:
                    raise IngestError("injected-failure", stage, stage=stage)

            with self.assertRaises(IngestError, msg=failure_stage):
                apply_ingest(self.paths, request, failure_injector=inject)
            recover_ingest(self.paths)
            self.assertEqual(baseline, self.material_hashes(), failure_stage)
            self.assertNotIn("beta", load_state(self.graph).nodes)

    def prepare_first_ingest(self, mode: str) -> dict:
        self.authority.write_text("# Fresh notes\n", encoding="utf-8")
        shutil.rmtree(self.graph)
        shutil.rmtree(self.repo / ".knowledge/entries")
        synchronize(
            self.repo,
            self.registry,
            self.graph,
            identities=self.identities,
            alignments=self.alignments,
            files=[],
            write=True,
        )
        self.assertFalse((self.repo / ".knowledge/entries").exists())
        snapshot = make_agent_snapshot(load_state(self.graph))
        self.query_report["target"] = {
            "namespace": "personal",
            "snapshot_sha256": snapshot["snapshot_sha256"],
            "graph_sha256": snapshot["graph"]["sha256"],
        }
        self.query_path.write_text(json.dumps(self.query_report), encoding="utf-8")
        request = self.request(mode, request_id="first-beta")
        request["authority_patches"][0]["expected_markers"]["definitions"] = ["beta"]
        return finalize_request(request)

    def test_first_ingest_creates_entry_directory_and_valid_receipt(self) -> None:
        request = self.prepare_first_ingest("apply")
        receipt = apply_ingest(self.paths, request)

        self.assertEqual("committed", receipt["status"])
        self.assertTrue((self.repo / ".knowledge/entries/beta.md").is_file())
        recover_ingest(self.paths)
        view = GraphView.load(self.graph, self.alignments)
        self.assertEqual(
            "beta", resolve_concepts(view, ["Beta"])[0]["matches"][0]["id"]
        )
        self.assertEqual(receipt, apply_ingest(self.paths, request))

    def test_first_ingest_failure_restores_absent_entry_directory(self) -> None:
        request = self.prepare_first_ingest("apply")
        before = self.material_hashes()

        def inject(stage: str) -> None:
            if stage == "installed-graph":
                raise IngestError("injected-failure", stage, stage=stage)

        with self.assertRaises(IngestError) as failure:
            apply_ingest(self.paths, request, failure_injector=inject)

        self.assertEqual("injected-failure", failure.exception.code)
        recover_ingest(self.paths)
        self.assertEqual(before, self.material_hashes())
        self.assertFalse((self.repo / ".knowledge/entries").exists())
        self.assertNotIn("beta", load_state(self.graph).nodes)

    def test_recovery_restores_only_declared_repository_targets(self) -> None:
        request_sha256 = "a" * 64
        backup_root = (
            self.repo
            / ".knowledge/build/kgdistiller-ingest/backups"
            / request_sha256
        )
        records = [
            _backup_target(self.repo, self.authority, backup_root),
            _backup_target(self.repo, self.graph, backup_root),
            _backup_target(self.repo, self.alignments, backup_root),
        ]
        baseline = self.material_hashes()
        self.authority.write_text("mutated\n", encoding="utf-8")
        _remove_target(self.graph)
        self.alignments.write_text("{}", encoding="utf-8")

        _restore_journal(
            self.paths,
            {
                "schema": JOURNAL_SCHEMA,
                "backup_root": str(backup_root),
                "request_sha256": request_sha256,
                "status": "installing",
                "targets": records,
            },
        )

        self.assertEqual(baseline, self.material_hashes())
        shutil.rmtree(backup_root, ignore_errors=True)

    def test_recovery_rejects_untrusted_backup_root_without_cleanup(self) -> None:
        request_sha256 = "b" * 64
        authority_before = self.authority.read_bytes()
        with tempfile.TemporaryDirectory(prefix="kgdistiller-external-backup-") as raw:
            external = Path(raw)
            sentinel = external / "sentinel.txt"
            sentinel.write_text("keep\n", encoding="utf-8")
            journal_path, _ = self.write_recovery_journal(
                request_sha256=request_sha256,
                status="committed",
                backup_root=external,
            )

            with self.assertRaises(IngestError) as rejected:
                recover_ingest(self.paths)

            self.assertEqual("recovery", rejected.exception.stage)
            self.assertEqual("keep\n", sentinel.read_text(encoding="utf-8"))
            self.assertEqual(authority_before, self.authority.read_bytes())
            self.assertTrue(journal_path.is_file())

    def test_recovery_rejects_invalid_digest_or_status(self) -> None:
        authority_before = self.authority.read_bytes()
        cases = (
            ("invalid-digest", "not-a-sha256", "installing"),
            ("invalid-status", "9" * 64, "unknown"),
            ("non-string-status", "8" * 64, []),
        )
        for name, request_sha256, status in cases:
            with self.subTest(name=name):
                journal_path, _ = self.write_recovery_journal(
                    request_sha256=request_sha256,
                    status=status,  # type: ignore[arg-type]
                )

                with self.assertRaises(IngestError) as rejected:
                    recover_ingest(self.paths)

                self.assertEqual("recovery", rejected.exception.stage)
                self.assertEqual(authority_before, self.authority.read_bytes())
                self.assertTrue(journal_path.is_file())

    def test_recovery_rejects_parent_target_without_touching_it(self) -> None:
        request_sha256 = "c" * 64
        authority_before = self.authority.read_bytes()
        with tempfile.TemporaryDirectory(prefix="kgdistiller-external-target-") as raw:
            sentinel = Path(raw) / "sentinel.md"
            sentinel.write_text("keep\n", encoding="utf-8")
            journal_path, _ = self.write_recovery_journal(
                request_sha256=request_sha256,
                targets=[
                    {
                        "path": f"../{Path(raw).name}/sentinel.md",
                        "existed": False,
                        "kind": "file",
                    }
                ],
            )

            with self.assertRaises(IngestError) as rejected:
                recover_ingest(self.paths)

            self.assertEqual("recovery", rejected.exception.stage)
            self.assertEqual("keep\n", sentinel.read_text(encoding="utf-8"))
            self.assertEqual(authority_before, self.authority.read_bytes())
            self.assertTrue(journal_path.is_file())

    def test_recovery_rejects_target_symlink_escape(self) -> None:
        request_sha256 = "d" * 64
        authority_before = self.authority.read_bytes()
        with tempfile.TemporaryDirectory(prefix="kgdistiller-symlink-target-") as raw:
            external = Path(raw)
            sentinel = external / "sentinel.md"
            sentinel.write_text("keep\n", encoding="utf-8")
            link = self.repo / "notes/escape-link"
            try:
                link.symlink_to(external, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"directory symlinks unavailable: {error}")
            journal_path, _ = self.write_recovery_journal(
                request_sha256=request_sha256,
                targets=[
                    {
                        "path": "notes/escape-link/sentinel.md",
                        "existed": False,
                        "kind": "file",
                    }
                ],
            )

            with self.assertRaises(IngestError) as rejected:
                recover_ingest(self.paths)

            self.assertEqual("recovery", rejected.exception.stage)
            self.assertEqual("keep\n", sentinel.read_text(encoding="utf-8"))
            self.assertEqual(authority_before, self.authority.read_bytes())
            self.assertTrue(journal_path.is_file())

    def test_recovery_rejects_symlinked_expected_backup_root(self) -> None:
        request_sha256 = "e" * 64
        authority_before = self.authority.read_bytes()
        with tempfile.TemporaryDirectory(prefix="kgdistiller-symlink-backup-") as raw:
            external = Path(raw)
            sentinel = external / "sentinel.txt"
            sentinel.write_text("keep\n", encoding="utf-8")
            journal_path, expected_backup_root = self.write_recovery_journal(
                request_sha256=request_sha256,
                status="committed",
            )
            expected_backup_root.parent.mkdir(parents=True, exist_ok=True)
            try:
                expected_backup_root.symlink_to(external, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"directory symlinks unavailable: {error}")

            with self.assertRaises(IngestError) as rejected:
                recover_ingest(self.paths)

            self.assertEqual("recovery", rejected.exception.stage)
            self.assertEqual("keep\n", sentinel.read_text(encoding="utf-8"))
            self.assertEqual(authority_before, self.authority.read_bytes())
            self.assertTrue(journal_path.is_file())

    def test_recovery_rejects_unmanaged_repository_target(self) -> None:
        request_sha256 = "f" * 64
        unmanaged = self.repo / "unmanaged.md"
        unmanaged.write_text("keep\n", encoding="utf-8")
        authority_before = self.authority.read_bytes()
        journal_path, _ = self.write_recovery_journal(
            request_sha256=request_sha256,
            targets=[
                {"path": "unmanaged.md", "existed": False, "kind": "file"}
            ],
        )

        with self.assertRaises(IngestError) as rejected:
            recover_ingest(self.paths)

        self.assertEqual("recovery", rejected.exception.stage)
        self.assertEqual("keep\n", unmanaged.read_text(encoding="utf-8"))
        self.assertEqual(authority_before, self.authority.read_bytes())
        self.assertTrue(journal_path.is_file())

    def test_request_cannot_patch_outside_the_repository(self) -> None:
        request = self.request("plan")
        request["authority_patches"][0]["path"] = "../escape.md"
        request = finalize_request(request)

        with self.assertRaises(IngestError) as rejected:
            plan_ingest(self.paths, request)

        self.assertIn(
            rejected.exception.code,
            {"invalid-path", "invalid-request", "unsafe-source-path"},
        )
        self.assertFalse((self.repo.parent / "escape.md").exists())


if __name__ == "__main__":
    unittest.main()
