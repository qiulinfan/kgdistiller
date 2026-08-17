from __future__ import annotations

import copy
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from kgdistiller.cli import (
    GraphState,
    make_artifacts,
    sha256_authority_file,
    sha256_file,
    source_registry_sha256,
    write_artifacts,
)
from kgdistiller.contracts import ContractError, finalize_self_digest, validate_contract
from kgdistiller.static_export import (
    StaticExportError,
    _export_recovery_root,
    _install_export,
    _run_product_git,
    _run_source_git,
    _source_checkout_revision,
    _source_inputs,
    export_site_bundle,
    resolve_product_commit,
)
from kgdistiller.static_export_verifier import ExportVerificationError, verify_export


class StaticSiteExportTests(unittest.TestCase):
    SOURCE_REVISION = "e" * 40

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-export-")
        self.repo = Path(self.temporary.name)
        self.public_source = self.repo / "notes/public/public.md"
        self.private_source = self.repo / "notes/private/private.md"
        self.public_source.parent.mkdir(parents=True)
        self.private_source.parent.mkdir(parents=True)
        self.public_source.write_text("--[[Public concept]]--\n", encoding="utf-8")
        self.private_source.write_text("--[[Private concept]]--\n", encoding="utf-8")
        self.registry = self.repo / "knowledge/sources.json"
        self.identities = self.repo / "config/custom-identities.json"
        self.registry.parent.mkdir(parents=True)
        self.registry.write_text(
            json.dumps(
                {
                    "schema": "kgdistiller-sources-v1",
                    "fields": [
                        {
                            "id": "shared-field",
                            "label": "Shared Field",
                            "text": "A shared field.",
                        }
                    ],
                    "sources": [
                        {
                            "id": "notes:public",
                            "subject": "notes",
                            "course": "public",
                            "root": "notes/public",
                            "files": ["*.md"],
                            "fields": ["shared-field"],
                            "knowledge_origin": "personal-note",
                            "publish": True,
                            "web": "https://example.test/public",
                        },
                        {
                            "id": "notes:private",
                            "subject": "notes",
                            "course": "private",
                            "root": "notes/private",
                            "files": ["*.md"],
                            "fields": ["shared-field"],
                            "knowledge_origin": "research",
                            "publish": False,
                            "web": "https://example.test/private",
                        },
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        state = GraphState(
            nodes={
                "shared-field": {
                    "id": "shared-field",
                    "type": "field",
                    "label": "Shared Field",
                    "text": "A shared field.",
                    "properties": {
                        "origin": "registry-taxonomy",
                        "source_status": "meta",
                    },
                },
                "public-concept": {
                    "id": "public-concept",
                    "type": "knowledge",
                    "label": "Public concept",
                    "text": "A hydrated public entry.",
                    "properties": {
                        "source_status": "active",
                        "curation_status": "needs-review",
                        "entry_origin": "agent-extracted",
                        "fields": ["shared-field"],
                        "knowledge_origin": "personal-note",
                    },
                    "provenance": {
                        "active": True,
                        "authority": "notes/public/public.md",
                        "line": 1,
                        "web": "https://example.test/public/#kn-public-concept",
                    },
                },
                "private-concept": {
                    "id": "private-concept",
                    "type": "knowledge",
                    "label": "Private concept",
                    "text": "A secret research entry.",
                    "properties": {
                        "source_status": "active",
                        "curation_status": "needs-review",
                        "entry_origin": "agent-extracted",
                        "fields": ["shared-field"],
                        "knowledge_origin": "research",
                    },
                    "provenance": {
                        "active": True,
                        "authority": "notes/private/private.md",
                        "line": 1,
                        "web": "https://example.test/private/#kn-private-concept",
                    },
                },
            },
            edges={
                ("shared-field", "contains", "public-concept"): {
                    "source": "shared-field",
                    "relation": "contains",
                    "target": "public-concept",
                },
                ("shared-field", "contains", "private-concept"): {
                    "source": "shared-field",
                    "relation": "contains",
                    "target": "private-concept",
                },
                ("private-concept", "contrasts-with", "public-concept"): {
                    "source": "private-concept",
                    "relation": "contrasts-with",
                    "target": "public-concept",
                    "origin": "agent",
                    "confidence": "high",
                    "evidence": "The fixture contrasts public and private concepts.",
                },
            },
            references=[
                {
                    "id": "public-ref",
                    "target": "public-concept",
                    "authority": "notes/public/public.md",
                    "line": 1,
                    "source_format": "markdown",
                },
                {
                    "id": "hidden-target-ref",
                    "target": "private-concept",
                    "authority": "notes/public/public.md",
                    "line": 1,
                    "source_format": "markdown",
                },
                {
                    "id": "private-ref",
                    "target": "private-concept",
                    "authority": "notes/private/private.md",
                    "line": 1,
                    "source_format": "markdown",
                },
            ],
            manifest={},
        )
        source_hashes = {
            "notes/public/public.md": sha256_authority_file(self.public_source),
            "notes/private/private.md": sha256_authority_file(self.private_source),
        }
        self.state = state
        self.source_hashes = source_hashes
        self.graph = self.repo / "knowledge/graph"
        write_artifacts(
            self.graph,
            make_artifacts(
                state,
                source_hashes,
                registry_sha256=source_registry_sha256(self.registry),
                git_revision="c" * 40,
            ),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def export(
        self,
        name: str = "site",
        *,
        product_commit: str = "b" * 40,
        replace: bool = False,
    ) -> Path:
        output = self.repo / "knowledge/export" / name
        with (
            patch(
                "kgdistiller.static_export._source_checkout_commit", return_value=None
            ),
            patch("kgdistiller.static_export._distribution_commit", return_value=None),
            patch(
                "kgdistiller.static_export._source_checkout_revision",
                return_value=self.SOURCE_REVISION,
            ),
        ):
            self.last_export_result = export_site_bundle(
                self.repo,
                output,
                registry=self.registry,
                graph_dir=self.graph,
                identities=self.identities,
                product_commit=product_commit,
                source_repository="https://github.com/example/notes",
                replace=replace,
            )
        return output

    def rewrite_graph_bundle(self, output: Path, mutate) -> dict:
        graph_path = output / "graph.json"
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        mutate(graph)
        graph = finalize_self_digest(graph, "graph_sha256")
        graph_path.write_text(
            json.dumps(graph, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        content = graph_path.read_text(encoding="utf-8").encode("utf-8")
        manifest_path = output / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["graph"]["public_sha256"] = graph["graph_sha256"]
        manifest["graph"]["public_counts"] = graph["counts"]
        record = next(
            item for item in manifest["artifacts"] if item["kind"] == "site-graph"
        )
        record["bytes"] = len(content)
        record["sha256"] = hashlib.sha256(content).hexdigest()
        manifest = finalize_self_digest(manifest, "export_sha256")
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return graph

    def rewrite_manifest_bundle(self, output: Path, mutate) -> dict:
        manifest_path = output / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        mutate(manifest)
        manifest = finalize_self_digest(manifest, "export_sha256")
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return manifest

    def test_product_commit_must_match_discovered_provenance(self) -> None:
        discovered = "a" * 40
        with (
            patch(
                "kgdistiller.static_export._source_checkout_commit",
                return_value=discovered,
            ),
            patch("kgdistiller.static_export._distribution_commit", return_value=None),
            self.assertRaisesRegex(StaticExportError, "does not match"),
        ):
            self.assertEqual(discovered, resolve_product_commit(discovered.upper()))
            resolve_product_commit("b" * 40)

        with (
            patch(
                "kgdistiller.static_export._source_checkout_commit", return_value=None
            ),
            patch(
                "kgdistiller.static_export._distribution_commit",
                return_value=discovered,
            ),
            self.assertRaisesRegex(StaticExportError, "does not match"),
        ):
            resolve_product_commit("b" * 40)

    def test_source_checkout_and_direct_url_provenance_cannot_disagree(self) -> None:
        with (
            patch(
                "kgdistiller.static_export._source_checkout_commit",
                return_value="a" * 40,
            ),
            patch(
                "kgdistiller.static_export._distribution_commit",
                return_value="b" * 40,
            ),
            self.assertRaisesRegex(StaticExportError, "commits disagree"),
        ):
            resolve_product_commit("a" * 40)

    def test_dirty_source_checkout_is_rejected_before_export_provenance(self) -> None:
        dirty = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=0,
            stdout=b"?? untracked-product-file\n",
            stderr=b"",
        )
        with (
            patch(
                "kgdistiller.static_export._source_checkout_root",
                return_value=self.repo,
            ),
            patch(
                "kgdistiller.static_export.subprocess.run", return_value=dirty
            ) as run,
            self.assertRaisesRegex(StaticExportError, "source checkout is dirty"),
        ):
            resolve_product_commit("a" * 40)
        self.assertEqual(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            run.call_args.args[0],
        )

    def test_dirty_instance_checkout_is_rejected_for_source_revision(self) -> None:
        top_level = subprocess.CompletedProcess(
            args=["git", "rev-parse"],
            returncode=0,
            stdout=f"{self.repo}\n".encode(),
            stderr=b"",
        )
        dirty = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=0,
            stdout=b" M knowledge/graph/nodes.jsonl\n",
            stderr=b"",
        )
        with (
            patch(
                "kgdistiller.static_export.subprocess.run",
                side_effect=[top_level, dirty],
            ) as run,
            self.assertRaisesRegex(StaticExportError, "repository checkout is dirty"),
        ):
            _source_checkout_revision(self.repo, [self.registry])
        self.assertEqual(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            run.call_args_list[1].args[0],
        )

    def test_source_graph_hash_must_match_current_authority_text(self) -> None:
        self.public_source.write_text(
            "changed after graph generation\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(
            StaticExportError, "does not match the committed graph hash"
        ):
            self.export()

    def test_private_graph_digest_is_recomputed_before_export(self) -> None:
        nodes_path = self.graph / "nodes.jsonl"
        records = [
            json.loads(line)
            for line in nodes_path.read_text(encoding="utf-8").splitlines()
            if line
        ]
        records[0]["label"] = "Tampered after graph generation"
        nodes_path.write_text(
            "".join(
                json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
                for record in records
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(StaticExportError, "graph digest"):
            self.export("stale-private-graph")

    def test_crlf_checkout_matches_canonical_sources_graph_and_bundle(self) -> None:
        expected_source_hash = self.source_hashes["notes/public/public.md"]
        public_text = self.public_source.read_text(encoding="utf-8")
        self.public_source.write_bytes(public_text.replace("\n", "\r\n").encode())
        self.assertEqual(
            expected_source_hash, sha256_authority_file(self.public_source)
        )
        self.assertNotEqual(expected_source_hash, sha256_file(self.public_source))

        for path in sorted(self.graph.rglob("*")):
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))

        graph_manifest = self.graph_manifest
        inputs = _source_inputs(
            self.repo,
            self.registry,
            self.graph,
            graph_manifest,
            self.source_hashes,
        )
        declared_shards = {
            self.graph / str(shard["path"])
            for shard in graph_manifest["entry_store"]["shards"]
        }
        self.assertTrue(declared_shards)
        self.assertTrue(declared_shards.issubset(set(inputs)))

        output = self.export("crlf")
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(
            expected_source_hash,
            manifest["source"]["published_hashes"]["notes/public/public.md"],
        )

        for path in sorted(output.iterdir()):
            text = path.read_text(encoding="utf-8")
            path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
        self.assertEqual("ok", verify_export(output)["status"])
        completed = subprocess.run(
            [sys.executable, str(output / "verify_export.py"), str(output)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_source_revision_requires_every_export_input_to_be_tracked(self) -> None:
        completed = [
            subprocess.CompletedProcess([], 0, f"{self.repo}\n".encode(), b""),
            subprocess.CompletedProcess([], 0, b"", b""),
            subprocess.CompletedProcess(
                [], 0, f"{self.SOURCE_REVISION}\n".encode(), b""
            ),
            subprocess.CompletedProcess([], 0, b"", b""),
        ]
        with (
            patch("kgdistiller.static_export.subprocess.run", side_effect=completed),
            self.assertRaisesRegex(StaticExportError, "not tracked by source HEAD"),
        ):
            _source_checkout_revision(self.repo, [self.registry])

        tracked = self.registry.relative_to(self.repo).as_posix()
        completed[-1] = subprocess.CompletedProcess([], 0, tracked.encode(), b"")
        with (
            patch("kgdistiller.static_export.subprocess.run", side_effect=completed),
            self.assertRaisesRegex(StaticExportError, "not NUL-terminated"),
        ):
            _source_checkout_revision(self.repo, [self.registry])

        completed[-1] = subprocess.CompletedProcess(
            [], 0, f"{tracked}\0\0".encode(), b""
        )
        with (
            patch("kgdistiller.static_export.subprocess.run", side_effect=completed),
            self.assertRaisesRegex(StaticExportError, "empty record"),
        ):
            _source_checkout_revision(self.repo, [self.registry])

        completed[-1] = subprocess.CompletedProcess([], 0, f"{tracked}\0".encode(), b"")
        with patch("kgdistiller.static_export.subprocess.run", side_effect=completed):
            self.assertEqual(
                self.SOURCE_REVISION,
                _source_checkout_revision(self.repo, [self.registry]),
            )

    def test_source_revision_reads_real_git_paths_as_utf8(self) -> None:
        authority = self.repo / "notes/公开/测度论.md"
        authority.parent.mkdir(parents=True)
        authority.write_text("--[[可测空间]]--\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "tests@example.test"],
            cwd=self.repo,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "kgdistiller tests"],
            cwd=self.repo,
            check=True,
        )
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "commit", "-qm", "中文 authority"], cwd=self.repo, check=True
        )
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=False,
        ).stdout.decode("ascii").strip()

        self.assertEqual(
            revision,
            _source_checkout_revision(self.repo, [self.registry, authority]),
        )

    def test_git_machine_output_decode_failures_are_structured(self) -> None:
        invalid = subprocess.CompletedProcess([], 0, b"\xff", b"")
        for helper in (_run_product_git, _run_source_git):
            stderr = io.StringIO()
            with (
                patch(
                    "kgdistiller.static_export.subprocess.run", return_value=invalid
                ) as run,
                redirect_stderr(stderr),
                self.assertRaisesRegex(StaticExportError, "not valid UTF-8"),
            ):
                helper(self.repo, ["status"], "fixture")
            self.assertEqual("", stderr.getvalue())
            self.assertIs(run.call_args.kwargs["text"], False)

    def test_site_bundle_is_hydrated_filtered_and_self_verifying(self) -> None:
        output = self.export()
        self.assertEqual(
            {
                "manifest.json",
                "graph.json",
                "knowledge-registry.typ",
                "verify_export.py",
            },
            {path.name for path in output.iterdir()},
        )
        graph = json.loads((output / "graph.json").read_text(encoding="utf-8"))
        self.assertEqual(
            {"shared-field", "public-concept"}, {node["id"] for node in graph["nodes"]}
        )
        public = next(node for node in graph["nodes"] if node["id"] == "public-concept")
        self.assertEqual("A hydrated public entry.", public["text"])
        self.assertNotIn("entry_path", public["properties"])
        self.assertEqual(1, len(graph["edges"]))
        self.assertEqual("contains", graph["edges"][0]["relation"])
        self.assertEqual(1, len(graph["references"]))
        self.assertEqual([], graph["diagnostics"]["errors"])
        self.assertEqual([], graph["diagnostics"]["info"])
        self.assertEqual(1, len(graph["diagnostics"]["warnings"]))
        self.assertEqual("public-concept", graph["diagnostics"]["warnings"][0]["node"])
        self.assertEqual(
            "notes/public/public.md",
            graph["diagnostics"]["warnings"][0]["source"],
        )

        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("b" * 40, manifest["producer"]["commit"])
        self.assertEqual(self.SOURCE_REVISION, manifest["source"]["revision"])
        self.assertEqual(
            self.graph_manifest["graph_sha256"], manifest["graph"]["private_sha256"]
        )
        self.assertEqual(["notes:public"], manifest["visibility"]["published_sources"])
        self.assertEqual(1, manifest["visibility"]["excluded_sources"])
        self.assertEqual(
            {"notes/public/public.md": sha256_authority_file(self.public_source)},
            manifest["source"]["published_hashes"],
        )
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                output / "manifest.json",
                output / "graph.json",
                output / "knowledge-registry.typ",
            )
        )
        self.assertNotIn("Private concept", combined)
        self.assertNotIn("notes/private/private.md", combined)
        self.assertEqual("ok", verify_export(output)["status"])
        completed = subprocess.run(
            [sys.executable, str(output / "verify_export.py"), str(output)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn('"status": "ok"', completed.stdout)

    def test_unsynchronized_registry_generations_preserve_existing_export(self) -> None:
        output = self.export()
        baseline = {
            path.relative_to(output).as_posix(): path.read_bytes()
            for path in output.rglob("*")
            if path.is_file()
        }
        original_registry = self.registry.read_bytes()

        def assert_unchanged() -> None:
            current = {
                path.relative_to(output).as_posix(): path.read_bytes()
                for path in output.rglob("*")
                if path.is_file()
            }
            self.assertEqual(baseline, current)

        try:
            registry = json.loads(original_registry)
            registry["sources"][0]["subject"] = "changed-without-sync"
            self.registry.write_text(json.dumps(registry), encoding="utf-8")
            with self.assertRaisesRegex(StaticExportError, "source registry is out of sync"):
                self.export(replace=True)
            assert_unchanged()
            self.registry.write_bytes(original_registry)

            self.identities.parent.mkdir(parents=True)
            self.identities.write_text(
                json.dumps(
                    {
                        "schema": "kgdistiller-identities-v1",
                        "identities": [
                            {
                                "id": "unsynchronized-identity",
                                "canonical_name": "Unsynchronized identity",
                                "aliases": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(StaticExportError, "identity registry is out of sync"):
                self.export(replace=True)
            assert_unchanged()
        finally:
            self.registry.write_bytes(original_registry)
            self.identities.unlink(missing_ok=True)

    def test_published_topic_roots_are_visible_without_knowledge_descendants(
        self,
    ) -> None:
        payload = json.loads(self.registry.read_text(encoding="utf-8"))
        public = next(item for item in payload["sources"] if item["publish"])
        private = next(item for item in payload["sources"] if not item["publish"])
        public["topics"] = [
            {
                "glob": "*.md",
                "id": topic_id,
                "label": topic_id.replace("-", " ").title(),
                "fields": ["shared-field"] if index < 2 else [],
            }
            for index, topic_id in enumerate(
                (
                    "cpp-programming",
                    "programming-languages",
                    "data-structures-algorithms",
                    "data-structures-and-algorithms",
                )
            )
        ]
        private["topics"] = [
            {
                "glob": "*.md",
                "id": topic_id,
                "label": topic_id.replace("-", " ").title(),
                "fields": ["shared-field"],
            }
            for topic_id in ("computer-organization", "computer-architecture")
        ]
        private["fields"].append("private-only-field")
        payload["fields"].append(
            {
                "id": "private-only-field",
                "label": "Private Only Field",
                "text": "",
            }
        )
        self.registry.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        public_topics = {item["id"] for item in public["topics"]}
        private_topics = {item["id"] for item in private["topics"]}
        for topic_id in sorted(public_topics | private_topics):
            self.state.nodes[topic_id] = {
                "id": topic_id,
                "type": "topic",
                "label": topic_id.replace("-", " ").title(),
                "text": "",
                "properties": {
                    "origin": "registry-taxonomy",
                    "source_status": "meta",
                },
            }
        self.state.nodes["private-only-field"] = {
            "id": "private-only-field",
            "type": "field",
            "label": "Private Only Field",
            "text": "",
            "properties": {
                "origin": "registry-taxonomy",
                "source_status": "meta",
            },
        }
        for item in public["topics"]:
            if not item["fields"]:
                continue
            topic_id = item["id"]
            self.state.edges[("shared-field", "contains", topic_id)] = {
                "source": "shared-field",
                "relation": "contains",
                "target": topic_id,
                "origin": "registry-taxonomy",
            }
        for topic_id in private_topics:
            self.state.edges[("shared-field", "contains", topic_id)] = {
                "source": "shared-field",
                "relation": "contains",
                "target": topic_id,
                "origin": "registry-taxonomy",
            }
        self.state.edges[("private-only-field", "contains", "cpp-programming")] = {
            "source": "private-only-field",
            "relation": "contains",
            "target": "cpp-programming",
            "origin": "registry-taxonomy",
        }
        write_artifacts(
            self.graph,
            make_artifacts(
                self.state,
                self.source_hashes,
                registry_sha256=source_registry_sha256(self.registry),
                git_revision="c" * 40,
            ),
        )

        output = self.export("published-topic-roots")
        graph = json.loads((output / "graph.json").read_text(encoding="utf-8"))
        visible = {node["id"] for node in graph["nodes"]}
        self.assertTrue(public_topics.issubset(visible))
        self.assertTrue(private_topics.isdisjoint(visible))
        self.assertIn("shared-field", visible)
        self.assertEqual(6, graph["counts"]["nodes"])
        self.assertEqual(3, graph["counts"]["edges"])
        self.assertEqual(1, graph["counts"]["references"])
        bundle = "\n".join(
            path.read_text(encoding="utf-8") for path in output.iterdir()
        )
        self.assertNotIn("computer-organization", bundle)
        self.assertNotIn("computer-architecture", bundle)
        self.assertNotIn("private-only-field", bundle)
        self.assertEqual("ok", verify_export(output)["status"])

    def test_published_source_and_topic_fields_are_visibility_seeds(self) -> None:
        payload = json.loads(self.registry.read_text(encoding="utf-8"))
        public = next(item for item in payload["sources"] if item["publish"])
        private = next(item for item in payload["sources"] if not item["publish"])
        public["fields"].append("published-source-field")
        public["topics"] = [
            {
                "glob": "*.md",
                "id": "published-topic-root",
                "label": "Published Topic Root",
                "fields": ["published-topic-field"],
            }
        ]
        private["fields"].append("private-only-field")
        payload["fields"].extend(
            {
                "id": field_id,
                "label": field_id.replace("-", " ").title(),
                "text": "",
            }
            for field_id in (
                "published-source-field",
                "published-topic-field",
                "private-only-field",
            )
        )
        self.registry.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        for field_id in (
            "published-source-field",
            "published-topic-field",
            "private-only-field",
        ):
            self.state.nodes[field_id] = {
                "id": field_id,
                "type": "field",
                "label": field_id.replace("-", " ").title(),
                "text": "",
                "properties": {
                    "origin": "registry-taxonomy",
                    "source_status": "meta",
                },
            }
        self.state.nodes["published-topic-root"] = {
            "id": "published-topic-root",
            "type": "topic",
            "label": "Published Topic Root",
            "text": "",
            "properties": {
                "origin": "registry-taxonomy",
                "source_status": "meta",
            },
        }
        write_artifacts(
            self.graph,
            make_artifacts(
                self.state,
                self.source_hashes,
                registry_sha256=source_registry_sha256(self.registry),
                git_revision="c" * 40,
            ),
        )

        output = self.export("published-field-roots")
        graph = json.loads((output / "graph.json").read_text(encoding="utf-8"))
        visible = {node["id"] for node in graph["nodes"]}
        self.assertTrue(
            {
                "published-source-field",
                "published-topic-field",
                "published-topic-root",
            }.issubset(visible)
        )
        self.assertNotIn("private-only-field", visible)

    def test_published_taxonomy_seeds_must_match_registry_owned_graph_nodes(
        self,
    ) -> None:
        original_registry = json.loads(self.registry.read_text(encoding="utf-8"))

        def add_field_seed(
            payload: dict,
            state: GraphState,
            field_id: str,
            *,
            node_type: str = "field",
            origin: str = "registry-taxonomy",
        ) -> None:
            payload["fields"].append(
                {"id": field_id, "label": field_id.replace("-", " ").title()}
            )
            payload["sources"][0]["fields"].append(field_id)
            state.nodes[field_id] = {
                "id": field_id,
                "type": node_type,
                "label": field_id.replace("-", " ").title(),
                "text": "",
                "properties": {
                    "origin": origin,
                    "source_status": "meta",
                },
            }

        cases = {
            "missing": (
                "missing from the graph",
                lambda payload, state: payload["sources"][0].setdefault(
                    "topics", []
                ).append(
                    {
                        "glob": "*.md",
                        "id": "missing-public-topic",
                        "label": "Missing Public Topic",
                        "fields": [],
                    }
                ),
            ),
            "wrong-type": (
                "must have type field",
                lambda payload, state: add_field_seed(
                    payload,
                    state,
                    "wrong-type-field",
                    node_type="topic",
                ),
            ),
            "wrong-origin": (
                "not registry-owned",
                lambda payload, state: add_field_seed(
                    payload,
                    state,
                    "wrong-origin-field",
                    origin="private-taxonomy",
                ),
            ),
        }
        for name, (message, mutate) in cases.items():
            with self.subTest(name=name):
                payload = copy.deepcopy(original_registry)
                state = copy.deepcopy(self.state)
                mutate(payload, state)
                self.registry.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                write_artifacts(
                    self.graph,
                    make_artifacts(
                        state,
                        self.source_hashes,
                        registry_sha256=source_registry_sha256(self.registry),
                        git_revision="c" * 40,
                    ),
                )
                with self.assertRaisesRegex(StaticExportError, message):
                    self.export(f"bad-taxonomy-{name}")

    @property
    def graph_manifest(self) -> dict:
        return json.loads((self.graph / "manifest.json").read_text(encoding="utf-8"))

    def test_static_verifier_rejects_tampering(self) -> None:
        output = self.export()
        with (output / "graph.json").open("a", encoding="utf-8") as handle:
            handle.write(" ")
        with self.assertRaisesRegex(
            ExportVerificationError, "(?:byte count|digest) mismatch"
        ):
            verify_export(output)

    def test_static_verifier_refuses_pre_namespace_manifest(self) -> None:
        output = self.export()
        manifest_path = output / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["schema"] = "legacy-static-export-v0"
        manifest = finalize_self_digest(manifest, "export_sha256")
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ExportVerificationError, "unsupported schema"):
            verify_export(output)

    def test_site_graph_schema_and_verifier_reject_the_same_bad_value_types(
        self,
    ) -> None:
        def public_node(graph: dict) -> dict:
            return next(
                node for node in graph["nodes"] if node["id"] == "public-concept"
            )

        mutations = {
            "node-text": lambda graph: public_node(graph).__setitem__("text", []),
            "node-entry": lambda graph: public_node(graph).__setitem__("entry", 7),
            "node-properties": lambda graph: public_node(graph).__setitem__(
                "properties", 7
            ),
            "node-provenance": lambda graph: public_node(graph).__setitem__(
                "provenance", []
            ),
            "node-id-bound": lambda graph: public_node(graph).__setitem__(
                "id", "n" * 257
            ),
            "diagnostic-code-bound": lambda graph: graph["diagnostics"]["warnings"][
                0
            ].__setitem__("code", "c" * 257),
            "diagnostic-source-type": lambda graph: graph["diagnostics"]["warnings"][
                0
            ].__setitem__("source", []),
            "count-bound": lambda graph: graph["counts"].__setitem__(
                "nodes", 1_000_001
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                output = self.export(f"bad-graph-{name}")
                graph = self.rewrite_graph_bundle(output, mutate)
                with self.assertRaises(ContractError):
                    validate_contract(graph)
                with self.assertRaises(ExportVerificationError):
                    verify_export(output)

        output = self.export("bad-graph-standalone")
        graph = self.rewrite_graph_bundle(
            output,
            lambda value: public_node(value).__setitem__("properties", 7),
        )
        with self.assertRaises(ContractError):
            validate_contract(graph)
        completed = subprocess.run(
            [sys.executable, str(output / "verify_export.py"), str(output)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(1, completed.returncode)
        self.assertIn("verify_export:", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)

    def test_manifest_schema_and_verifier_share_exact_artifact_and_bounds(self) -> None:
        def add_fourth_artifact(manifest: dict) -> None:
            manifest["artifacts"].append(copy.deepcopy(manifest["artifacts"][0]))

        def exceed_artifact_size(manifest: dict) -> None:
            manifest["artifacts"][0]["bytes"] = 134_217_729

        def mismatch_artifact_pair(manifest: dict) -> None:
            manifest["artifacts"][0]["path"] = "wrong-graph.json"

        mutations = {
            "four-artifacts": add_fourth_artifact,
            "source-count-bound": lambda manifest: manifest["source"].__setitem__(
                "files", 1_000_001
            ),
            "source-count-type": lambda manifest: manifest["source"].__setitem__(
                "files", True
            ),
            "visibility-count-bound": lambda manifest: manifest[
                "visibility"
            ].__setitem__("excluded_sources", 100_001),
            "visibility-id-bound": lambda manifest: manifest["visibility"].__setitem__(
                "published_sources", ["s" * 257]
            ),
            "artifact-size-bound": exceed_artifact_size,
            "artifact-kind-path-pair": mismatch_artifact_pair,
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                output = self.export(f"bad-manifest-{name}")
                manifest = self.rewrite_manifest_bundle(output, mutate)
                with self.assertRaises(ContractError):
                    validate_contract(manifest)
                with self.assertRaises(ExportVerificationError):
                    verify_export(output)

    def test_private_edge_evidence_between_public_nodes_is_never_exported(self) -> None:
        secret = "PRIVATE-EDGE-EVIDENCE-7f621db6"
        peer = copy.deepcopy(self.state.nodes["public-concept"])
        peer["id"] = "public-peer"
        peer["label"] = "Public peer"
        peer["text"] = "Another public entry."
        self.state.nodes["public-peer"] = peer
        self.state.edges[("shared-field", "contains", "public-peer")] = {
            "source": "shared-field",
            "relation": "contains",
            "target": "public-peer",
        }
        self.state.edges[("public-concept", "derived-from", "public-peer")] = {
            "source": "public-concept",
            "relation": "derived-from",
            "target": "public-peer",
            "origin": "private-research",
            "authority": "notes/private/private.md",
            "evidence": secret,
            "evidence_fingerprints": {
                "notes/private/private.md": "f" * 64,
            },
        }
        write_artifacts(
            self.graph,
            make_artifacts(
                self.state,
                self.source_hashes,
                registry_sha256=source_registry_sha256(self.registry),
                git_revision="c" * 40,
            ),
        )

        output = self.export("private-edge")
        graph = json.loads((output / "graph.json").read_text(encoding="utf-8"))
        semantic = next(
            edge for edge in graph["edges"] if edge["relation"] == "derived-from"
        )
        self.assertEqual(
            {
                "source": "public-concept",
                "relation": "derived-from",
                "target": "public-peer",
            },
            semantic,
        )
        bundle_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in output.iterdir()
            if path.suffix in {".json", ".typ", ".py"}
        )
        self.assertNotIn(secret, bundle_text)
        self.assertNotIn("notes/private/private.md", bundle_text)
        self.assertEqual("ok", verify_export(output)["status"])

    def test_export_rejects_existing_destination_and_credential_url(self) -> None:
        output = self.export()
        with self.assertRaisesRegex(StaticExportError, "already exists"):
            export_site_bundle(
                self.repo,
                output,
                registry=self.registry,
                graph_dir=self.graph,
                product_commit="b" * 40,
                source_repository="https://github.com/example/notes",
            )
        with self.assertRaisesRegex(StaticExportError, "credential-free HTTPS"):
            export_site_bundle(
                self.repo,
                self.repo / "knowledge/export/bad-url",
                registry=self.registry,
                graph_dir=self.graph,
                product_commit="b" * 40,
                source_repository="https://user:secret@example.test/notes",
            )

    def test_replace_advances_a_verified_bundle_and_records_the_previous_export(
        self,
    ) -> None:
        output = self.export()
        previous = json.loads((output / "manifest.json").read_text(encoding="utf-8"))

        self.export(product_commit="d" * 40, replace=True)

        current = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("d" * 40, current["producer"]["commit"])
        self.assertEqual(previous["export_sha256"], current["replaces_export_sha256"])
        self.assertNotEqual(previous["export_sha256"], current["export_sha256"])
        verification = verify_export(output)
        self.assertEqual(
            previous["export_sha256"], verification["replaces_export_sha256"]
        )
        self.assertEqual(
            {
                "manifest.json",
                "graph.json",
                "knowledge-registry.typ",
                "verify_export.py",
            },
            {path.name for path in output.iterdir()},
        )

    def test_replace_validation_failure_preserves_every_old_bundle_byte(self) -> None:
        output = self.export()
        before = {path.name: path.read_bytes() for path in output.iterdir()}
        real_verify = verify_export

        def verify_old_but_reject_staging(path: Path) -> dict:
            if Path(path).resolve() == output.resolve():
                return real_verify(path)
            raise ExportVerificationError("injected staging verification failure")

        with (
            patch(
                "kgdistiller.static_export.verify_export",
                side_effect=verify_old_but_reject_staging,
            ),
            self.assertRaisesRegex(ExportVerificationError, "injected"),
        ):
            self.export(product_commit="d" * 40, replace=True)

        after = {path.name: path.read_bytes() for path in output.iterdir()}
        self.assertEqual(before, after)
        self.assertEqual("ok", verify_export(output)["status"])

    def test_replace_refuses_an_unverified_directory_without_deleting_it(self) -> None:
        output = self.repo / "knowledge/export/unmanaged"
        output.mkdir(parents=True)
        sentinel = output / "sentinel.txt"
        sentinel.write_text("keep\n", encoding="utf-8")
        with self.assertRaisesRegex(StaticExportError, "exactly a four-file"):
            export_site_bundle(
                self.repo,
                output,
                registry=self.registry,
                graph_dir=self.graph,
                product_commit="d" * 40,
                source_repository="https://github.com/example/notes",
                replace=True,
            )
        self.assertEqual("keep\n", sentinel.read_text(encoding="utf-8"))

    def test_directory_swap_failure_restores_the_previous_bundle(self) -> None:
        output = self.repo / "knowledge/export/swap-target"
        staging = self.repo / "knowledge/export/swap-staging"
        output.mkdir(parents=True)
        staging.mkdir()
        (output / "sentinel.txt").write_text("old\n", encoding="utf-8")
        (staging / "sentinel.txt").write_text("new\n", encoding="utf-8")
        real_replace = __import__("os").replace
        failed = False

        def fail_new_install(source: Path, target: Path) -> None:
            nonlocal failed
            if Path(source) == staging and Path(target) == output and not failed:
                failed = True
                raise OSError("injected directory swap failure")
            real_replace(source, target)

        with (
            patch("kgdistiller.static_export.os.replace", side_effect=fail_new_install),
            self.assertRaisesRegex(StaticExportError, "previous bundle was restored"),
        ):
            _install_export(
                staging,
                output,
                True,
                recovery_root=self.repo / "knowledge/build/direct-swap-recovery",
                previous_export_sha256="a" * 64,
                current_export_sha256="b" * 64,
            )
        self.assertEqual("old\n", (output / "sentinel.txt").read_text(encoding="utf-8"))
        self.assertEqual(
            "new\n", (staging / "sentinel.txt").read_text(encoding="utf-8")
        )

    def test_post_commit_cleanup_failure_returns_committed_and_next_run_recovers(
        self,
    ) -> None:
        output = self.export()
        previous = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        with patch(
            "kgdistiller.static_export._remove_export_backup",
            side_effect=OSError("injected post-commit cleanup failure"),
        ):
            self.export(product_commit="d" * 40, replace=True)

        committed = self.last_export_result
        self.assertTrue(committed["committed"])
        self.assertEqual("pending", committed["cleanup_status"])
        self.assertTrue(committed["warnings"])
        self.assertEqual(1, len(committed["recovery_paths"]))
        recovery_path = Path(committed["recovery_paths"][0])
        self.assertTrue(recovery_path.is_dir())
        self.assertIn("knowledge", recovery_path.parts)
        self.assertIn("build", recovery_path.parts)
        current = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("d" * 40, current["producer"]["commit"])
        self.assertEqual(previous["export_sha256"], current["replaces_export_sha256"])
        self.assertEqual("ok", verify_export(output)["status"])

        self.export(product_commit="f" * 40, replace=True)
        recovered = self.last_export_result
        self.assertTrue(recovered["committed"])
        self.assertEqual("complete", recovered["cleanup_status"])
        self.assertEqual([], recovered["warnings"])
        self.assertEqual([], recovered["recovery_paths"])
        final = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("f" * 40, final["producer"]["commit"])
        self.assertEqual(current["export_sha256"], final["replaces_export_sha256"])
        self.assertFalse(_export_recovery_root(self.repo, output).exists())


if __name__ == "__main__":
    unittest.main()
