from __future__ import annotations

import contextlib
import io
import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from kgdistiller import cli
from kgdistiller import native_compiler as compiler
from kgdistiller import source_archive as archive
from kgdistiller import vaults as vault_module
from kgdistiller.contracts import validate_contract
from kgdistiller.native_compiler import (
    NativeCompilerError,
    check_knowledge,
    compile_vault,
    sync_knowledge,
)
from kgdistiller.query import GraphView, QueryError
from kgdistiller.source_archive import (
    SourceArchiveError,
    SourceEvidenceView,
    SourceLedger,
    current_evidence_view,
)
from kgdistiller.vaults import VaultError, init_vault, load_vault, remove_vault


def yaml_list(key: str, values: list[str]) -> list[str]:
    if not values:
        return [f"{key}: []"]
    return [f"{key}:", *(f"  - {json.dumps(value)}" for value in values)]


def taxonomy_note(
    node_id: str,
    kind: str,
    label: str,
    *,
    parents: list[str] | None = None,
) -> str:
    return "\n".join(
        [
            "---",
            "kgd_schema: qlkg-taxonomy-v1",
            f"kgd_id: {node_id}",
            f"kgd_kind: {kind}",
            "aliases: []",
            *yaml_list("kgd_parents", parents or []),
            "---",
            "",
            f"# {label}",
            "",
            f"Curated {kind} entry.",
            "",
        ]
    )


def concept_note(
    node_id: str,
    label: str,
    *,
    fields: list[str] | None = None,
    topics: list[str] | None = None,
    prerequisites: list[str] | None = None,
    implies: list[str] | None = None,
    generalizes: list[str] | None = None,
    contrasts: list[str] | None = None,
    derived: list[str] | None = None,
    body: str = "Curated concept entry.",
) -> str:
    return "\n".join(
        [
            "---",
            "kgd_schema: qlkg-concept-v1",
            f"kgd_id: {node_id}",
            "aliases: []",
            "tags: [kgdistiller/concept]",
            *yaml_list("kgd_fields", fields or []),
            *yaml_list("kgd_topics", topics or []),
            *yaml_list("kgd_prerequisites", prerequisites or []),
            *yaml_list("kgd_implies", implies or []),
            *yaml_list("kgd_generalizes", generalizes or []),
            *yaml_list("kgd_contrasts_with", contrasts or []),
            *yaml_list("kgd_derived_from", derived or []),
            "---",
            "",
            f"# {label}",
            "",
            body,
            "",
        ]
    )


def _spawned_native_reader(graph: str, connection) -> None:
    from kgdistiller import native_compiler as spawned_compiler
    from kgdistiller.query import GraphView as SpawnedGraphView

    paused = False

    def pause_after_manifest(label: str, path: str) -> None:
        nonlocal paused
        if paused or label != "after-manifest":
            return
        paused = True
        connection.send(("paused", None))
        if connection.recv() != "release":
            raise RuntimeError("invalid reader release command")

    spawned_compiler._native_reader_hook = pause_after_manifest
    try:
        view = SpawnedGraphView.load(Path(graph), max_attempts=10)
        connection.send(
            ("done", view.snapshot["graph"]["sha256"], view.snapshot["namespace"])
        )
    except BaseException as error:
        connection.send(("error", type(error).__name__, str(error)))
    finally:
        connection.close()


def _spawned_native_writer(home: str, connection) -> None:
    from kgdistiller.native_compiler import sync_knowledge as spawned_sync

    try:
        report = spawned_sync(home=Path(home))
        connection.send(("done", report["vaults"][0]["graph_sha256"]))
    except BaseException as error:
        connection.send(("error", type(error).__name__, str(error)))
    finally:
        connection.close()


def _spawned_paused_writer(home: str, connection) -> None:
    from kgdistiller import native_compiler as spawned_compiler

    paused = False

    def pause_before_publish(label: str, path: str) -> None:
        nonlocal paused
        if paused or label != "after-final-preconditions":
            return
        paused = True
        connection.send(("paused", None))
        if connection.recv() != "release":
            raise RuntimeError("invalid writer release command")

    spawned_compiler._native_transaction_hook = pause_before_publish
    try:
        report = spawned_compiler.sync_knowledge(home=Path(home))
        connection.send(("done", report["vaults"][0]["graph_sha256"]))
    except BaseException as error:
        connection.send(("error", type(error).__name__, str(error)))
    finally:
        connection.close()


def _spawned_graph_reader(graph: str, connection) -> None:
    from kgdistiller.query import GraphView as SpawnedGraphView

    try:
        view = SpawnedGraphView.load(Path(graph))
        connection.send(
            ("done", view.snapshot["graph"]["sha256"], view.snapshot["namespace"])
        )
    except BaseException as error:
        connection.send(("error", type(error).__name__, str(error)))
    finally:
        connection.close()


class NativeCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-native-test-")
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "machine-home"
        self.vault_root = self.root / "Math"
        init_vault(
            self.vault_root,
            vault_id="math",
            label="Mathematics",
            home=self.home,
        )
        self.field = self.vault_root / "Knowledge/Fields/Measure Theory.md"
        self.topic = self.vault_root / "Knowledge/Topics/Measure.md"
        self.sigma = self.vault_root / "Knowledge/Concepts/Sigma Algebra.md"
        self.measure = self.vault_root / "Knowledge/Concepts/Measure Space.md"
        self.field.write_text(
            taxonomy_note("measure-theory", "field", "Measure theory"),
            encoding="utf-8",
        )
        self.topic.write_text(
            taxonomy_note(
                "measure",
                "topic",
                "Measure",
                parents=["[[Knowledge/Fields/Measure Theory]]"],
            ),
            encoding="utf-8",
        )
        self.sigma.write_text(
            concept_note(
                "sigma-algebra",
                "Sigma algebra",
                fields=["[[Knowledge/Fields/Measure Theory]]"],
            ),
            encoding="utf-8",
        )
        self.measure.write_text(
            concept_note(
                "measure-space",
                "Measure space",
                topics=["[[Knowledge/Topics/Measure]]"],
                prerequisites=["[[Knowledge/Concepts/Sigma Algebra]]"],
                implies=["[[Knowledge/Concepts/Sigma Algebra]]"],
                generalizes=["[[Knowledge/Concepts/Sigma Algebra]]"],
                contrasts=["[[Knowledge/Concepts/Sigma Algebra]]"],
                derived=["[[Knowledge/Concepts/Sigma Algebra]]"],
                body="Curated entry with #kn[ghost], --[[ghost]]--, and \\kn{ghost}.",
            ),
            encoding="utf-8",
        )
        self.ledger = SourceLedger(
            self.vault_root / ".kgdistiller/sources",
            None,
            None,
            (),
            (),
            (),
        )
        self.evidence = SourceEvidenceView(
            generation_sha256=None,
            concept_ids=frozenset({"measure-space", "sigma-algebra"}),
            relations=frozenset(
                {
                    ("sigma-algebra", "prerequisite-for", "measure-space"),
                    ("measure-space", "contrasts-with", "sigma-algebra"),
                }
            ),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @contextlib.contextmanager
    def patched_evidence(self):
        with mock.patch.object(
            compiler, "load_source_ledger", return_value=self.ledger
        ), mock.patch.object(
            compiler, "current_evidence_view", return_value=self.evidence
        ):
            yield

    def _compile(self):
        with self.patched_evidence():
            return compile_vault(load_vault(self.vault_root))

    def _make_directory_link(self, link: Path, target: Path) -> bool:
        try:
            link.symlink_to(target, target_is_directory=True)
            return True
        except (OSError, NotImplementedError):
            pass
        if os.name != "nt":
            return False
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            check=False,
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return completed.returncode == 0 and os.path.lexists(link)

    def _remove_directory_link(self, link: Path) -> None:
        if os.name == "nt":
            os.rmdir(link)
        else:
            link.unlink()

    def test_exact_qlkg_v3_mapping_directions_evidence_and_registry(self) -> None:
        compilation = self._compile()
        state = compilation.state
        self.assertEqual("qlkg-v3", state.manifest["schema"])
        self.assertEqual(
            {"measure-theory"},
            set(state.nodes["measure-space"]["properties"]["fields"]),
        )
        self.assertEqual(
            ["measure-theory"], state.nodes["measure"]["properties"]["fields"]
        )
        self.assertEqual(
            self.measure.relative_to(self.vault_root).as_posix(),
            state.nodes["measure-space"]["provenance"]["authority"],
        )
        self.assertIn("#kn[ghost]", state.nodes["measure-space"]["text"])
        self.assertNotIn("ghost", state.nodes)

        prerequisite = state.edges[
            ("sigma-algebra", "prerequisite-for", "measure-space")
        ]
        self.assertEqual("current", prerequisite["curation_status"])
        self.assertEqual(
            "Declared by kgd_prerequisites in Knowledge/Concepts/Measure Space.md",
            prerequisite["evidence"],
        )
        self.assertEqual(
            "needs-review",
            state.edges[("measure-space", "implies", "sigma-algebra")][
                "curation_status"
            ],
        )
        self.assertIn(
            ("measure-space", "contrasts-with", "sigma-algebra"), state.edges
        )
        self.assertIn(("measure-theory", "contains", "measure"), state.edges)
        self.assertIn(("measure", "contains", "measure-space"), state.edges)
        self.assertEqual([], state.references)

        registry = compilation.source_registry
        self.assertEqual("qlkg-sources-v3", registry["schema"])
        self.assertEqual(
            ["Knowledge/Concepts", "Knowledge/Fields", "Knowledge/Topics"],
            [item["root"] for item in registry["sources"]],
        )
        self.assertEqual(
            [{
                "glob": "Measure.md",
                "id": "measure",
                "label": "Measure",
                "fields": ["measure-theory"],
            }],
            registry["sources"][2]["topics"],
        )
        self.assertEqual(
            set(compilation.state.manifest["source_hashes"]),
            {
                "Knowledge/Fields/Measure Theory.md",
                "Knowledge/Topics/Measure.md",
                "Knowledge/Concepts/Sigma Algebra.md",
                "Knowledge/Concepts/Measure Space.md",
            },
        )
        self.assertEqual(
            cli.sha256_text(cli.json_text(registry)),
            compilation.state.manifest["registry_sha256"],
        )
        normalized_measure = self.measure.read_bytes().decode("utf-8").replace(
            "\r\n", "\n"
        ).replace("\r", "\n")
        self.assertEqual(
            cli.sha256_text(normalized_measure),
            compilation.state.manifest["source_hashes"][
                "Knowledge/Concepts/Measure Space.md"
            ],
        )

    def test_f2_evidence_view_uses_only_current_effective_committed_mapping(self) -> None:
        ledger = SourceLedger(
            self.vault_root / ".kgdistiller/sources",
            {},
            "a" * 64,
            ({"document_id": "doc", "current_version_id": "v2"},),
            (
                {"version_id": "v1"},
                {"version_id": "v2"},
            ),
            (
                {
                    "version_id": "v1",
                    "status": "committed",
                    "concept_evidence": [{"concept_id": "measure-space"}],
                    "relation_evidence": [
                        {
                            "source": "sigma-algebra",
                            "relation": "contrasts-with",
                            "target": "measure-space",
                        }
                    ],
                },
                {
                    "version_id": "v2",
                    "status": "carried-forward",
                    "inherited_from_version_id": "v1",
                },
            ),
        )
        view = current_evidence_view(ledger)
        self.assertTrue(view.has_concept("measure-space"))
        self.assertTrue(
            view.has_relation("sigma-algebra", "contrasts-with", "measure-space")
        )
        self.assertFalse(view.has_concept("sigma-algebra"))

    def test_global_ids_link_case_kind_and_self_relations_fail_closed(self) -> None:
        original_topic = self.topic.read_text(encoding="utf-8")
        self.topic.write_text(
            original_topic.replace("kgd_id: measure", "kgd_id: measure-theory"),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(NativeCompilerError, "occurs more than once"):
            self._compile()
        self.topic.write_text(original_topic, encoding="utf-8")

        original_measure = self.measure.read_text(encoding="utf-8")
        self.measure.write_text(
            original_measure.replace(
                "[[Knowledge/Topics/Measure]]", "[[knowledge/Topics/Measure]]"
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(NativeCompilerError, "exact path case"):
            self._compile()
        self.measure.write_text(original_measure, encoding="utf-8")

        self.measure.write_text(
            original_measure.replace(
                "[[Knowledge/Topics/Measure]]", "[[Knowledge/Fields/Measure Theory]]"
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(NativeCompilerError, "topic note"):
            self._compile()
        self.measure.write_text(original_measure, encoding="utf-8")

        relation_keys = (
            "kgd_prerequisites",
            "kgd_implies",
            "kgd_generalizes",
            "kgd_contrasts_with",
            "kgd_derived_from",
        )
        for key in relation_keys:
            lines = original_measure.splitlines()
            result: list[str] = []
            index = 0
            while index < len(lines):
                if lines[index] == f"{key}:":
                    result.extend(
                        [f"{key}:", '  - "[[Knowledge/Concepts/Measure Space]]"']
                    )
                    index += 2
                else:
                    result.append(lines[index])
                    index += 1
            self.measure.write_text("\n".join(result) + "\n", encoding="utf-8")
            with self.subTest(property=key):
                with self.assertRaisesRegex(NativeCompilerError, "same concept"):
                    self._compile()
            self.measure.write_text(original_measure, encoding="utf-8")

    def test_edge_limit_fails_during_contains_and_semantic_insertion(self) -> None:
        for limit, phase in ((2, "contains"), (3, "semantic")):
            with self.subTest(phase=phase), mock.patch.object(
                compiler, "MAX_NATIVE_EDGES", limit
            ):
                with self.assertRaises(NativeCompilerError) as raised:
                    self._compile()
            self.assertEqual("native-graph-too-large", raised.exception.code)
            self.assertIn(f"exceeds {limit} edges", raised.exception.message)

    def test_sync_check_are_staged_deterministic_and_byte_exact(self) -> None:
        with self.patched_evidence():
            first = sync_knowledge(home=self.home)
            self.assertEqual(first, validate_contract(first))
            self.assertTrue(first["vaults"][0]["changed"])
            current = check_knowledge(home=self.home)
            self.assertEqual("ok", current["status"])
            second = sync_knowledge(home=self.home)
            self.assertFalse(second["vaults"][0]["changed"])
        graph = self.vault_root / ".kgdistiller/graph"
        nodes = graph / "nodes.jsonl"
        nodes.write_bytes(nodes.read_bytes() + b"tampered\n")
        with self.patched_evidence():
            drift = check_knowledge(home=self.home)
            self.assertEqual("failed", drift["status"])
            self.assertIn("nodes.jsonl", drift["vaults"][0]["mismatches"])
            repaired = sync_knowledge(home=self.home)
            self.assertTrue(repaired["vaults"][0]["changed"])
            self.assertEqual("ok", check_knowledge(home=self.home)["status"])
        self.assertEqual(
            [],
            list(
                (self.vault_root / ".kgdistiller/build").glob(
                    ".stage-knowledge-*"
                )
            ),
        )

    def test_stage_hydrates_artifact_layout_and_rejects_bad_shard_metadata(self) -> None:
        compilation = self._compile()
        artifacts = dict(compilation.artifacts)
        manifest = json.loads(artifacts["manifest.json"])
        self.assertTrue(manifest["entry_store"]["shards"])
        manifest["entry_store"]["shards"][0]["sha256"] = "0" * 64
        artifacts["manifest.json"] = cli.pretty_json(manifest)
        corrupted = replace(compilation, artifacts=artifacts)
        with self.assertRaisesRegex(NativeCompilerError, "cannot hydrate staged"):
            compiler._stage_and_validate(corrupted)

    def test_authority_snapshot_detects_same_path_and_cross_file_mixed_reads(self) -> None:
        original = self.measure.read_bytes()
        changed = original.replace(b"Curated entry with", b"Changed entry with")
        capture = vault_module._capture_managed_markdown_once
        calls = 0

        def change_after_first_collection(vault, roots):
            nonlocal calls
            result = capture(vault, roots)
            calls += 1
            if calls == 1:
                self.measure.write_bytes(changed)
            return result

        try:
            with mock.patch.object(
                vault_module,
                "_capture_managed_markdown_once",
                side_effect=change_after_first_collection,
            ):
                with self.assertRaisesRegex(VaultError, "paths or contents changed"):
                    self._compile()
        finally:
            self.measure.write_bytes(original)

        mixed = False

        def change_previous_file(label: str, parent: Path, leaf: str) -> None:
            nonlocal mixed
            if (
                not mixed
                and label == "before-leaf-open"
                and parent == self.sigma.parent
                and leaf == self.sigma.name
            ):
                mixed = True
                self.measure.write_bytes(changed)

        try:
            with mock.patch.object(
                archive, "_anchored_test_hook", side_effect=change_previous_file
            ):
                with self.assertRaisesRegex(VaultError, "paths or contents changed"):
                    self._compile()
        finally:
            self.measure.write_bytes(original)
        self.assertTrue(mixed)

    def test_stage_to_publish_authority_cas_rejects_change_but_allows_aba(self) -> None:
        original = self.measure.read_bytes()
        changed = original.replace(b"Curated entry with", b"Changed entry with")
        prepare = compiler._prepare_vault_stage

        def change_after_stage(compilation):
            stage = prepare(compilation)
            self.measure.write_bytes(changed)
            return stage

        with self.patched_evidence(), mock.patch.object(
            compiler, "_prepare_vault_stage", side_effect=change_after_stage
        ):
            with self.assertRaises(NativeCompilerError) as raised:
                sync_knowledge(home=self.home)
        self.assertEqual("stale-native-authority", raised.exception.code)
        self.assertEqual([], list((self.vault_root / ".kgdistiller/graph").iterdir()))

        self.measure.write_bytes(original)

        def change_and_restore_after_stage(compilation):
            stage = prepare(compilation)
            self.measure.write_bytes(changed)
            self.measure.write_bytes(original)
            return stage

        with self.patched_evidence(), mock.patch.object(
            compiler,
            "_prepare_vault_stage",
            side_effect=change_and_restore_after_stage,
        ):
            report = sync_knowledge(home=self.home)
        self.assertTrue(report["vaults"][0]["changed"])

    def test_identity_index_rejects_normalized_label_alias_conflicts(self) -> None:
        original = self.measure.read_text(encoding="utf-8")
        self.measure.write_text(
            original.replace(
                "aliases: []",
                'aliases: ["Ｓｉｇｍａ　Ａｌｇｅｂｒａ"]',
                1,
            ),
            encoding="utf-8",
        )
        with self.assertRaises(NativeCompilerError) as raised:
            self._compile()
        self.assertEqual("conflicting-native-identity", raised.exception.code)
        self.assertEqual(
            {
                "Knowledge/Concepts/Measure Space.md",
                "Knowledge/Concepts/Sigma Algebra.md",
            },
            {
                raised.exception.details["first_authority"],
                raised.exception.details["second_authority"],
            },
        )
        self.measure.write_text(original, encoding="utf-8")
        original_topic = self.topic.read_text(encoding="utf-8")
        self.topic.write_text(
            original_topic.replace(
                "aliases: []", 'aliases: ["Ｍｅａｓｕｒｅ　ｓｐａｃｅ"]', 1
            ),
            encoding="utf-8",
        )
        with self.assertRaises(NativeCompilerError) as taxonomy_collision:
            self._compile()
        self.assertEqual(
            "conflicting-native-identity", taxonomy_collision.exception.code
        )
        self.assertEqual(
            {
                "Knowledge/Concepts/Measure Space.md",
                "Knowledge/Topics/Measure.md",
            },
            {
                taxonomy_collision.exception.details["first_authority"],
                taxonomy_collision.exception.details["second_authority"],
            },
        )

    def test_publication_failure_matrix_restores_exact_old_graph(self) -> None:
        with self.patched_evidence():
            sync_knowledge(home=self.home)
        stale = self.vault_root / ".kgdistiller/graph/stale.json"
        stale.write_bytes(b"stale-before-image")
        vault = load_vault(self.vault_root)
        old = compiler._capture_live_graph(vault)
        self.measure.write_text(
            self.measure.read_text(encoding="utf-8").replace(
                "Curated entry with", "Revised curated entry with"
            ),
            encoding="utf-8",
        )
        labels = (
            "after-journal",
            "after-final-preconditions",
            "before-live-delete",
            "after-live-delete",
            "before-live-write",
            "after-live-write",
            "before-manifest",
            "after-manifest",
            "before-final-verify",
            "after-final-verify",
            "before-commit",
        )
        for target in labels:
            fired = False

            def fail(label: str, path: str) -> None:
                nonlocal fired
                if not fired and label == target:
                    fired = True
                    raise OSError(f"injected {target}")

            with self.subTest(step=target), self.patched_evidence(), mock.patch.object(
                compiler, "_native_transaction_hook", side_effect=fail
            ):
                with self.assertRaises(OSError):
                    sync_knowledge(home=self.home)
            self.assertTrue(fired)
            self.assertEqual(old, compiler._capture_live_graph(vault))
            self.assertFalse(
                os.path.lexists(self.vault_root / compiler.GRAPH_TRANSACTION_PATH)
            )

    def test_prepared_and_committed_journals_recover_exactly(self) -> None:
        with self.patched_evidence():
            sync_knowledge(home=self.home)
        vault = load_vault(self.vault_root)
        old = compiler._capture_live_graph(vault)
        self.measure.write_text(
            self.measure.read_text(encoding="utf-8").replace(
                "Curated entry with", "Revised curated entry with"
            ),
            encoding="utf-8",
        )
        fired = False

        def fail_before_commit(label: str, path: str) -> None:
            nonlocal fired
            if not fired and label == "before-commit":
                fired = True
                raise OSError("simulated crash before commit")
            if label == "before-rollback-state":
                raise SystemExit("simulated crash before rollback state")

        with self.patched_evidence(), mock.patch.object(
            compiler, "_native_transaction_hook", side_effect=fail_before_commit
        ):
            with self.assertRaisesRegex(NativeCompilerError, "rollback could not complete"):
                sync_knowledge(home=self.home)
        self.assertTrue(
            os.path.lexists(self.vault_root / compiler.GRAPH_TRANSACTION_PATH)
        )
        prepared = json.loads(
            (self.vault_root / compiler.GRAPH_TRANSACTION_PATH).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual("prepared", prepared["state"])
        self.assertEqual(compiler._EMPTY_LEDGER_SHA256, prepared["ledger_generation_sha256"])
        vault_manifest = self.vault_root / ".kgdistiller/vault.json"
        vault_manifest_bytes = vault_manifest.read_bytes()
        vault_manifest.unlink()
        try:
            with self.assertRaisesRegex(QueryError, "native Vault manifest"):
                GraphView.load(self.vault_root / ".kgdistiller/graph")
        finally:
            vault_manifest.write_bytes(vault_manifest_bytes)
        vault_manifest.write_bytes(b"{}\n")
        try:
            with self.assertRaisesRegex(QueryError, "manifest is unavailable or invalid"):
                GraphView.load(self.vault_root / ".kgdistiller/graph")
        finally:
            vault_manifest.write_bytes(vault_manifest_bytes)
        prepared_live = compiler._capture_live_graph(vault)
        nodes_path = self.vault_root / ".kgdistiller/graph/nodes.jsonl"
        nodes_path.write_bytes(b"third-state\n")
        third_state = compiler._capture_live_graph(vault)
        with self.patched_evidence():
            with self.assertRaises(NativeCompilerError) as stale:
                check_knowledge(home=self.home)
        self.assertEqual("stale-graph-transaction", stale.exception.code)
        self.assertEqual(third_state, compiler._capture_live_graph(vault))
        nodes_path.write_bytes(prepared_live["nodes.jsonl"])
        self.measure.write_text(
            self.measure.read_text(encoding="utf-8").replace(
                "Revised curated entry", "Post-crash revised entry"
            ),
            encoding="utf-8",
        )
        init_vault(
            self.root / "RecoveryOnly",
            vault_id="recovery-only",
            label="Recovery only",
            home=self.home,
        )
        with self.patched_evidence():
            drift = check_knowledge(vault_id="math", home=self.home)
        self.assertEqual("failed", drift["status"])
        self.assertEqual(old, compiler._capture_live_graph(vault))
        self.assertFalse(
            os.path.lexists(self.vault_root / compiler.GRAPH_TRANSACTION_PATH)
        )

        with self.patched_evidence(), mock.patch.object(
            compiler,
            "_native_transaction_hook",
            side_effect=lambda label, path: (
                (_ for _ in ()).throw(OSError("simulated crash after commit"))
                if label == "after-commit"
                else None
            ),
        ):
            with self.assertRaisesRegex(OSError, "after commit"):
                sync_knowledge(vault_id="math", home=self.home)
        self.assertTrue(
            os.path.lexists(self.vault_root / compiler.GRAPH_TRANSACTION_PATH)
        )
        committed = json.loads(
            (self.vault_root / compiler.GRAPH_TRANSACTION_PATH).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual("committed", committed["state"])
        self.assertEqual(compiler._EMPTY_LEDGER_SHA256, committed["ledger_generation_sha256"])
        view = GraphView.load(self.vault_root / ".kgdistiller/graph")
        self.assertEqual("math", view.snapshot["namespace"])
        self.assertIn("Post-crash revised entry", view.nodes["measure-space"]["text"])
        self.assertFalse(
            os.path.lexists(self.vault_root / compiler.GRAPH_TRANSACTION_PATH)
        )

    def test_rollback_clears_journal_before_best_effort_backup_cleanup(self) -> None:
        with self.patched_evidence():
            sync_knowledge(home=self.home)
        vault = load_vault(self.vault_root)
        old = compiler._capture_live_graph(vault)
        self.measure.write_text(
            self.measure.read_text(encoding="utf-8").replace(
                "Curated entry with", "Revised curated entry with"
            ),
            encoding="utf-8",
        )
        publication_failed = False

        def crash_during_cleanup(label: str, path: str) -> None:
            nonlocal publication_failed
            if not publication_failed and label == "after-live-write":
                publication_failed = True
                raise OSError("force prepared rollback")
            if label == "after-journal-clear":
                raise SystemExit("crash after durable abort")

        with self.patched_evidence(), mock.patch.object(
            compiler, "_native_transaction_hook", side_effect=crash_during_cleanup
        ):
            with self.assertRaisesRegex(NativeCompilerError, "rollback could not complete"):
                sync_knowledge(home=self.home)
        self.assertTrue(publication_failed)
        self.assertFalse(
            os.path.lexists(self.vault_root / compiler.GRAPH_TRANSACTION_PATH)
        )
        self.assertEqual(old, compiler._capture_live_graph(vault))
        self.assertTrue(
            list(
                (self.vault_root / ".kgdistiller/build").glob(
                    ".stage-knowledge-*"
                )
            )
        )
        with self.patched_evidence():
            report = check_knowledge(home=self.home)
        self.assertEqual("failed", report["status"])
        self.assertEqual(old, compiler._capture_live_graph(vault))

    def test_mid_rollback_crashes_resume_from_rolling_back_journal(self) -> None:
        with self.patched_evidence():
            sync_knowledge(home=self.home)
        vault = load_vault(self.vault_root)
        old = compiler._capture_live_graph(vault)
        self.measure.write_text(
            self.measure.read_text(encoding="utf-8").replace(
                "Curated entry with", "Revised curated entry with"
            ),
            encoding="utf-8",
        )
        for target in (
            "rollback-after-manifest-remove",
            "rollback-after-restore",
        ):
            publication_failed = False
            rollback_crashed = False

            def crash_mid_rollback(label: str, path: str) -> None:
                nonlocal publication_failed, rollback_crashed
                if not publication_failed and label == "after-manifest":
                    publication_failed = True
                    raise OSError("force rollback after complete new graph")
                if not rollback_crashed and label == target:
                    rollback_crashed = True
                    raise SystemExit(f"crash at {target}")

            with self.subTest(step=target), self.patched_evidence(), mock.patch.object(
                compiler,
                "_native_transaction_hook",
                side_effect=crash_mid_rollback,
            ):
                with self.assertRaisesRegex(
                    NativeCompilerError, "rollback could not complete"
                ):
                    sync_knowledge(home=self.home)
            self.assertTrue(publication_failed)
            self.assertTrue(rollback_crashed)
            journal = json.loads(
                (self.vault_root / compiler.GRAPH_TRANSACTION_PATH).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual("rolling-back", journal["state"])
            with self.patched_evidence():
                drift = check_knowledge(home=self.home)
            self.assertEqual("failed", drift["status"])
            self.assertEqual(old, compiler._capture_live_graph(vault))
            self.assertFalse(
                os.path.lexists(self.vault_root / compiler.GRAPH_TRANSACTION_PATH)
            )

    def test_same_manifest_stale_only_transactions_recover_exact_old(self) -> None:
        with self.patched_evidence():
            sync_knowledge(home=self.home)
        vault = load_vault(self.vault_root)
        graph = self.vault_root / ".kgdistiller/graph"
        (graph / "stale-a.json").write_bytes(b"stale-a-before-image")
        (graph / "stale-b.json").write_bytes(b"stale-b-before-image")
        old = compiler._capture_live_graph(vault)

        for target in ("after-journal", "after-live-delete"):
            fired = False

            def crash_with_prepared_journal(label: str, path: str) -> None:
                nonlocal fired
                if not fired and label == target:
                    fired = True
                    raise OSError(f"crash at {target}")
                if fired and label == "before-rollback-state":
                    raise SystemExit("crash before rollback state")

            with self.subTest(step=target), self.patched_evidence(), mock.patch.object(
                compiler,
                "_native_transaction_hook",
                side_effect=crash_with_prepared_journal,
            ):
                with self.assertRaisesRegex(
                    NativeCompilerError, "rollback could not complete"
                ):
                    sync_knowledge(home=self.home)

            self.assertTrue(fired)
            journal = json.loads(
                (self.vault_root / compiler.GRAPH_TRANSACTION_PATH).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual("prepared", journal["state"])
            self.assertEqual(
                journal["old_manifest_sha256"], journal["new_manifest_sha256"]
            )
            if target == "after-journal":
                self.assertEqual(old, compiler._capture_live_graph(vault))
            else:
                partial = compiler._capture_live_graph(vault)
                self.assertNotEqual(old, partial)
                self.assertEqual(
                    1,
                    sum(
                        name in partial for name in ("stale-a.json", "stale-b.json")
                    ),
                )

            with self.patched_evidence():
                report = check_knowledge(home=self.home)
            self.assertEqual("failed", report["status"])
            self.assertEqual(old, compiler._capture_live_graph(vault))
            self.assertFalse(
                os.path.lexists(self.vault_root / compiler.GRAPH_TRANSACTION_PATH)
            )

    def test_native_reader_guard_exposes_complete_old_then_new_generation(self) -> None:
        with self.patched_evidence():
            first = sync_knowledge(home=self.home)
        old_digest = first["vaults"][0]["graph_sha256"]
        self.measure.write_text(
            self.measure.read_text(encoding="utf-8").replace(
                "Curated entry with", "Revised curated entry with"
            ),
            encoding="utf-8",
        )
        reader_paused = threading.Event()
        release_reader = threading.Event()
        reader_result: list[GraphView] = []
        writer_result: list[dict] = []
        errors: list[BaseException] = []
        paused_once = False

        def pause_reader(label: str, path: str) -> None:
            nonlocal paused_once
            if not paused_once and label == "after-manifest":
                paused_once = True
                reader_paused.set()
                if not release_reader.wait(2):
                    raise AssertionError("reader release timed out")

        def read_graph() -> None:
            try:
                reader_result.append(
                    GraphView.load(
                        self.vault_root / ".kgdistiller/graph", max_attempts=10
                    )
                )
            except BaseException as error:
                errors.append(error)

        def write_graph() -> None:
            try:
                with self.patched_evidence():
                    writer_result.append(sync_knowledge(home=self.home))
            except BaseException as error:
                errors.append(error)

        with mock.patch.object(
            compiler, "_native_reader_hook", side_effect=pause_reader
        ):
            reader = threading.Thread(target=read_graph)
            reader.start()
            self.assertTrue(reader_paused.wait(2))
            writer = threading.Thread(target=write_graph)
            writer.start()
            time.sleep(0.05)
            self.assertTrue(writer.is_alive())
            release_reader.set()
            reader.join(3)
            writer.join(3)
        self.assertFalse(reader.is_alive())
        self.assertFalse(writer.is_alive())
        self.assertEqual([], errors)
        self.assertEqual(old_digest, reader_result[0].snapshot["graph"]["sha256"])
        new_digest = writer_result[0]["vaults"][0]["graph_sha256"]
        self.assertNotEqual(old_digest, new_digest)
        latest = GraphView.load(self.vault_root / ".kgdistiller/graph")
        self.assertEqual(new_digest, latest.snapshot["graph"]["sha256"])

    def test_spawned_reader_guard_blocks_writer_until_complete_old_read(self) -> None:
        with self.patched_evidence():
            first = sync_knowledge(home=self.home)
        old_digest = first["vaults"][0]["graph_sha256"]
        self.measure.write_text(
            self.measure.read_text(encoding="utf-8").replace(
                "Curated entry with", "Spawned writer revised entry with"
            ),
            encoding="utf-8",
        )
        context = multiprocessing.get_context("spawn")
        reader_parent, reader_child = context.Pipe()
        writer_parent, writer_child = context.Pipe()
        reader = context.Process(
            target=_spawned_native_reader,
            args=(str(self.vault_root / ".kgdistiller/graph"), reader_child),
        )
        writer = context.Process(
            target=_spawned_native_writer,
            args=(str(self.home), writer_child),
        )
        reader.start()
        reader_child.close()
        writer_started = False
        try:
            self.assertTrue(reader_parent.poll(5), "spawned reader did not reach M0")
            self.assertEqual(("paused", None), reader_parent.recv())
            writer.start()
            writer_started = True
            writer_child.close()
            time.sleep(0.15)
            self.assertTrue(writer.is_alive())
            self.assertFalse(writer_parent.poll())
            live_manifest = json.loads(
                (self.vault_root / ".kgdistiller/graph/manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(old_digest, live_manifest["graph_sha256"])
            reader_parent.send("release")
            self.assertTrue(reader_parent.poll(5), "spawned reader did not finish")
            reader_message = reader_parent.recv()
            self.assertEqual(("done", old_digest, "math"), reader_message)
            self.assertTrue(writer_parent.poll(5), "spawned writer did not finish")
            writer_message = writer_parent.recv()
            self.assertEqual("done", writer_message[0], writer_message)
            new_digest = writer_message[1]
            self.assertNotEqual(old_digest, new_digest)
        finally:
            reader.join(3)
            if reader.is_alive():
                reader.terminate()
                reader.join(3)
            if writer_started:
                writer.join(3)
                if writer.is_alive():
                    writer.terminate()
                    writer.join(3)
            reader_parent.close()
            writer_parent.close()
        self.assertEqual(0, reader.exitcode)
        self.assertEqual(0, writer.exitcode)
        latest = GraphView.load(self.vault_root / ".kgdistiller/graph")
        self.assertEqual(new_digest, latest.snapshot["graph"]["sha256"])

    def test_spawned_writer_publication_makes_reader_wait_for_complete_new(self) -> None:
        with self.patched_evidence():
            first = sync_knowledge(home=self.home)
        old_digest = first["vaults"][0]["graph_sha256"]
        self.measure.write_text(
            self.measure.read_text(encoding="utf-8").replace(
                "Curated entry with", "Writer-first revised entry with"
            ),
            encoding="utf-8",
        )
        context = multiprocessing.get_context("spawn")
        writer_parent, writer_child = context.Pipe()
        reader_parent, reader_child = context.Pipe()
        writer = context.Process(
            target=_spawned_paused_writer,
            args=(str(self.home), writer_child),
        )
        reader = context.Process(
            target=_spawned_graph_reader,
            args=(str(self.vault_root / ".kgdistiller/graph"), reader_child),
        )
        writer.start()
        writer_child.close()
        reader_started = False
        try:
            self.assertTrue(writer_parent.poll(5), "writer did not reach publication")
            self.assertEqual(("paused", None), writer_parent.recv())
            reader.start()
            reader_started = True
            reader_child.close()
            time.sleep(0.15)
            self.assertTrue(reader.is_alive())
            self.assertFalse(reader_parent.poll())
            writer_parent.send("release")
            self.assertTrue(writer_parent.poll(5), "writer did not finish")
            writer_message = writer_parent.recv()
            self.assertEqual("done", writer_message[0], writer_message)
            new_digest = writer_message[1]
            self.assertNotEqual(old_digest, new_digest)
            self.assertTrue(reader_parent.poll(5), "reader did not finish")
            self.assertEqual(
                ("done", new_digest, "math"), reader_parent.recv()
            )
        finally:
            writer.join(3)
            if writer.is_alive():
                writer.terminate()
                writer.join(3)
            if reader_started:
                reader.join(3)
                if reader.is_alive():
                    reader.terminate()
                    reader.join(3)
            writer_parent.close()
            reader_parent.close()
        self.assertEqual(0, writer.exitcode)
        self.assertEqual(0, reader.exitcode)

    def test_registry_change_after_stage_fails_before_graph_install(self) -> None:
        prepare_stage = compiler._prepare_vault_stage

        def change_registry(compilation):
            stage = prepare_stage(compilation)
            remove_vault("math", home=self.home)
            return stage

        with self.patched_evidence(), mock.patch.object(
            compiler, "_prepare_vault_stage", side_effect=change_registry
        ):
            with self.assertRaises(NativeCompilerError) as raised:
                sync_knowledge(home=self.home)
        self.assertEqual("stale-vault-selection", raised.exception.code)
        self.assertEqual([], list((self.vault_root / ".kgdistiller/graph").iterdir()))

    def test_manifest_change_after_stage_fails_before_graph_install(self) -> None:
        prepare_stage = compiler._prepare_vault_stage

        def change_manifest(compilation):
            stage = prepare_stage(compilation)
            path = self.vault_root / ".kgdistiller/vault.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["description"] = "changed during sync"
            path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            return stage

        with self.patched_evidence(), mock.patch.object(
            compiler, "_prepare_vault_stage", side_effect=change_manifest
        ):
            with self.assertRaises(NativeCompilerError) as raised:
                sync_knowledge(home=self.home)
        self.assertEqual("stale-vault-selection", raised.exception.code)
        self.assertEqual([], list((self.vault_root / ".kgdistiller/graph").iterdir()))

    def test_final_check_to_publish_manifest_race_rolls_back_before_return(self) -> None:
        with self.patched_evidence():
            sync_knowledge(home=self.home)
        vault = load_vault(self.vault_root)
        old = compiler._capture_live_graph(vault)
        self.measure.write_text(
            self.measure.read_text(encoding="utf-8").replace(
                "Curated entry with", "Revised curated entry with"
            ),
            encoding="utf-8",
        )
        manifest_path = self.vault_root / ".kgdistiller/vault.json"
        original_manifest = manifest_path.read_bytes()
        fired = False

        def change_manifest(label: str, path: str) -> None:
            nonlocal fired
            if fired or label != "after-final-preconditions":
                return
            fired = True
            payload = json.loads(original_manifest.decode("utf-8"))
            payload["description"] = "changed in final-check race"
            manifest_path.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )

        try:
            with self.patched_evidence(), mock.patch.object(
                compiler, "_native_transaction_hook", side_effect=change_manifest
            ):
                with self.assertRaises(NativeCompilerError) as raised:
                    sync_knowledge(home=self.home)
            self.assertEqual("stale-vault-selection", raised.exception.code)
            self.assertTrue(fired)
            self.assertEqual(old, compiler._capture_live_graph(vault))
        finally:
            manifest_path.write_bytes(original_manifest)

    def test_cli_selects_all_vaults_in_id_order_or_one_explicit_id(self) -> None:
        second = self.root / "Physics"
        init_vault(second, vault_id="physics", label="Physics", home=self.home)
        (second / "Knowledge/Fields/Physics.md").write_text(
            taxonomy_note("physics", "field", "Physics"), encoding="utf-8"
        )
        (second / "Knowledge/Concepts/State.md").write_text(
            concept_note(
                "physical-state",
                "Physical state",
                fields=["[[Knowledge/Fields/Physics]]"],
            ),
            encoding="utf-8",
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            self.patched_evidence(),
            mock.patch.dict(
                os.environ, {"KGDISTILLER_HOME": str(self.home)}, clear=False
            ),
            mock.patch.object(sys, "argv", ["kgdistiller", "knowledge", "sync"]),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = cli.main()
        self.assertEqual(0, code, stderr.getvalue())
        report = json.loads(stdout.getvalue())
        self.assertEqual(["math", "physics"], [row["id"] for row in report["vaults"]])
        stdout = io.StringIO()
        stderr = io.StringIO()
        with self.patched_evidence(), mock.patch.dict(
            os.environ, {"KGDISTILLER_HOME": str(self.home)}, clear=False
        ), mock.patch.object(
            sys,
            "argv",
            ["kgdistiller", "knowledge", "check", "--vault", "math"],
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main()
        self.assertEqual(0, code)
        self.assertEqual(["math"], [row["id"] for row in json.loads(stdout.getvalue())["vaults"]])

    def test_managed_note_ancestor_swap_never_reads_outside(self) -> None:
        outside = self.root / "outside-concepts"
        outside.mkdir()
        outside_note = outside / self.measure.name
        outside_note.write_text(
            concept_note("outside", "Outside", fields=["[[Knowledge/Fields/Measure Theory]]"]),
            encoding="utf-8",
        )
        parent = self.measure.parent
        backup = parent.with_name("Concepts-pinned-backup")
        attempted = False

        def swap(label: str, pinned_parent: Path, leaf: str) -> None:
            nonlocal attempted
            if (
                attempted
                or label != "before-leaf-open"
                or pinned_parent != parent
                or leaf != self.measure.name
            ):
                return
            attempted = True
            try:
                parent.rename(backup)
            except OSError as error:
                raise SourceArchiveError(
                    "injected-ancestor-swap", "pinned note parent rejected replacement"
                ) from error
            if not self._make_directory_link(parent, outside):
                backup.rename(parent)
                raise SourceArchiveError(
                    "injected-ancestor-swap", "could not install note-parent test link"
                )

        try:
            with mock.patch.object(archive, "_anchored_test_hook", side_effect=swap):
                with self.assertRaises((SourceArchiveError, OSError)):
                    self._compile()
        finally:
            if os.path.lexists(parent) and backup.exists():
                self._remove_directory_link(parent)
            if backup.exists():
                backup.rename(parent)
        self.assertTrue(attempted)
        self.assertIn("kgd_id: outside", outside_note.read_text(encoding="utf-8"))

    def test_managed_note_hardlink_is_rejected(self) -> None:
        outside = self.root / "outside-hardlink.md"
        outside.write_bytes(self.measure.read_bytes())
        self.measure.unlink()
        try:
            os.link(outside, self.measure)
        except OSError as error:
            self.skipTest(f"hard links are unavailable: {error}")
        with self.assertRaises(SourceArchiveError) as raised:
            self._compile()
        self.assertEqual("invalid-vault-file", raised.exception.code)

    def test_graph_publication_ancestor_swap_never_writes_outside(self) -> None:
        with self.patched_evidence():
            sync_knowledge(home=self.home)
        self.measure.write_text(
            self.measure.read_text(encoding="utf-8").replace(
                "Curated entry with", "Revised curated entry with"
            ),
            encoding="utf-8",
        )
        graph = self.vault_root / ".kgdistiller/graph"
        backup = graph.with_name("graph-pinned-backup")
        outside = self.root / "outside-graph"
        outside.mkdir()
        sentinel = outside / "sources.json"
        sentinel.write_bytes(b"outside-sentinel")
        attempted = False

        def swap(label: str, parent: Path, leaf: str) -> None:
            nonlocal attempted
            if attempted or label != "before-leaf-create" or parent != graph:
                return
            attempted = True
            try:
                graph.rename(backup)
            except OSError as error:
                raise SourceArchiveError(
                    "injected-ancestor-swap", "pinned graph parent rejected replacement"
                ) from error
            if not self._make_directory_link(graph, outside):
                backup.rename(graph)
                raise SourceArchiveError(
                    "injected-ancestor-swap", "could not install graph-parent test link"
                )

        try:
            with self.patched_evidence(), mock.patch.object(
                archive, "_anchored_test_hook", side_effect=swap
            ):
                with self.assertRaises((SourceArchiveError, OSError)):
                    sync_knowledge(home=self.home)
        finally:
            if os.path.lexists(graph) and backup.exists():
                self._remove_directory_link(graph)
            if backup.exists():
                backup.rename(graph)
        self.assertTrue(attempted)
        self.assertEqual(b"outside-sentinel", sentinel.read_bytes())
        self.assertEqual(["sources.json"], sorted(item.name for item in outside.iterdir()))


if __name__ == "__main__":
    unittest.main()
