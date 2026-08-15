from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from kgdistiller import cli
from kgdistiller import native_compiler as compiler
from kgdistiller import source_archive as archive
from kgdistiller.contracts import validate_contract
from kgdistiller.native_compiler import (
    NativeCompilerError,
    check_knowledge,
    compile_vault,
    sync_knowledge,
)
from kgdistiller.source_archive import (
    SourceArchiveError,
    SourceEvidenceView,
    SourceLedger,
    current_evidence_view,
)
from kgdistiller.vaults import init_vault, load_vault, remove_vault


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

    def test_registry_change_after_stage_fails_before_graph_install(self) -> None:
        stage_and_validate = compiler._stage_and_validate

        def change_registry(compilation):
            stage_and_validate(compilation)
            remove_vault("math", home=self.home)

        with self.patched_evidence(), mock.patch.object(
            compiler, "_stage_and_validate", side_effect=change_registry
        ):
            with self.assertRaises(NativeCompilerError) as raised:
                sync_knowledge(home=self.home)
        self.assertEqual("stale-vault-selection", raised.exception.code)
        self.assertEqual([], list((self.vault_root / ".kgdistiller/graph").iterdir()))

    def test_manifest_change_after_stage_fails_before_graph_install(self) -> None:
        stage_and_validate = compiler._stage_and_validate

        def change_manifest(compilation):
            stage_and_validate(compilation)
            path = self.vault_root / ".kgdistiller/vault.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["description"] = "changed during sync"
            path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

        with self.patched_evidence(), mock.patch.object(
            compiler, "_stage_and_validate", side_effect=change_manifest
        ):
            with self.assertRaises(NativeCompilerError) as raised:
                sync_knowledge(home=self.home)
        self.assertEqual("stale-vault-selection", raised.exception.code)
        self.assertEqual([], list((self.vault_root / ".kgdistiller/graph").iterdir()))

    def test_cli_requires_explicit_selection_for_multiple_vaults(self) -> None:
        second = self.root / "Physics"
        init_vault(second, vault_id="physics", label="Physics", home=self.home)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.dict(
                os.environ, {"KGDISTILLER_HOME": str(self.home)}, clear=False
            ),
            mock.patch.object(sys, "argv", ["kgdistiller", "knowledge", "check"]),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = cli.main()
        self.assertEqual(1, code)
        self.assertEqual("vault-selection-required", json.loads(stderr.getvalue())["code"])
        with self.patched_evidence(), mock.patch.dict(
            os.environ, {"KGDISTILLER_HOME": str(self.home)}, clear=False
        ), mock.patch.object(
            sys,
            "argv",
            ["kgdistiller", "knowledge", "sync", "--vault", "math"],
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main()
        self.assertEqual(0, code)

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
