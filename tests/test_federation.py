from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import kgdistiller.federation as federation_module
import kgdistiller.native_compiler as native_compiler_module
import kgdistiller.source_archive as source_archive_module
from kgdistiller.cli import pretty_json
from kgdistiller.contracts import canonical_json, sha256_json
from kgdistiller.federation import capture_federation
from kgdistiller.native_compiler import sync_knowledge
from kgdistiller.source_archive import capture_source
from kgdistiller.vaults import init_vault


def _yaml_list(key: str, values: list[str]) -> list[str]:
    if not values:
        return [f"{key}: []"]
    return [f"{key}:", *(f"  - {json.dumps(value)}" for value in values)]


def _taxonomy(
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
            *_yaml_list("kgd_parents", parents or []),
            "---",
            "",
            f"# {label}",
            "",
            f"Curated {kind} entry for {label}.",
            "",
        ]
    )


def _concept(
    node_id: str,
    label: str,
    body: str,
    *,
    fields: list[str] | None = None,
    topics: list[str] | None = None,
    aliases: list[str] | None = None,
) -> str:
    return "\n".join(
        [
            "---",
            "kgd_schema: qlkg-concept-v1",
            f"kgd_id: {node_id}",
            *_yaml_list("aliases", aliases or []),
            "tags: [kgdistiller/concept]",
            *_yaml_list("kgd_fields", fields or []),
            *_yaml_list("kgd_topics", topics or []),
            "kgd_prerequisites: []",
            "kgd_implies: []",
            "kgd_generalizes: []",
            "kgd_contrasts_with: []",
            "kgd_derived_from: []",
            "---",
            "",
            f"# {label}",
            "",
            body,
            "",
        ]
    )


class FederationFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-federation-")
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "home"
        self.analysis = self.root / "Analysis"
        self.probability = self.root / "Probability"
        init_vault(
            self.analysis,
            vault_id="analysis",
            label="Analysis",
            home=self.home,
        )
        init_vault(
            self.probability,
            vault_id="probability",
            label="Probability",
            home=self.home,
        )
        (self.analysis / "Knowledge/Fields/Analysis.md").write_text(
            _taxonomy("analysis-field", "field", "Analysis"), encoding="utf-8"
        )
        (self.analysis / "Knowledge/Fields/Probability.md").write_text(
            _taxonomy("probability-field", "field", "Probability"), encoding="utf-8"
        )
        (self.analysis / "Knowledge/Topics/Measure.md").write_text(
            _taxonomy(
                "measure-topic",
                "topic",
                "Measure theory",
                parents=[
                    "[[Knowledge/Fields/Analysis]]",
                    "[[Knowledge/Fields/Probability]]",
                ],
            ),
            encoding="utf-8",
        )
        self.analysis_measure = self.analysis / "Knowledge/Concepts/Measure.md"
        self.analysis_measure.write_text(
            _concept(
                "measure",
                "Measure",
                "A countably additive set function on a sigma algebra.",
                topics=["[[Knowledge/Topics/Measure]]"],
                aliases=["测度"],
            ),
            encoding="utf-8",
        )
        (self.probability / "Knowledge/Fields/Probability.md").write_text(
            _taxonomy("probability", "field", "Probability"), encoding="utf-8"
        )
        (self.probability / "Knowledge/Concepts/Measure.md").write_text(
            _concept(
                "probability-measure",
                "Measure",
                "A probability measure assigns total mass one.",
                fields=["[[Knowledge/Fields/Probability]]"],
            ),
            encoding="utf-8",
        )
        sync_knowledge(home=self.home)
        with federation_module._INDEX_CACHE_LOCK:
            federation_module._INDEX_CACHE.clear()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_capture_is_coherent_portable_and_generation_cached(self) -> None:
        with mock.patch(
            "kgdistiller.federation._build_index",
            wraps=federation_module._build_index,
        ) as build:
            first = capture_federation(home=self.home)
            second = capture_federation(home=self.home)
        self.assertEqual(["analysis", "probability"], [item.vault.id for item in first.vaults])
        self.assertEqual(2, build.call_count)
        self.assertEqual(first.generation, second.generation)
        self.assertEqual((), first.incomplete_vaults)
        self.assertEqual(
            ("analysis-field", "probability-field"),
            first.by_id["analysis"].index.roots,
        )
        self.assertEqual(
            ("analysis-field", "probability-field"),
            first.by_id["analysis"].index.parents["measure-topic"],
        )
        cards = canonical_json([item.card for item in first.vaults])
        self.assertNotIn(str(self.root), cards)
        self.assertTrue(all(item.card["live_source_generation_sha256"] is None for item in first.vaults))
        with self.assertRaises(TypeError):
            first.by_id["analysis"].index.postings["poison"] = ("measure",)
        third = capture_federation(home=self.home)
        self.assertNotIn("poison", third.by_id["analysis"].index.postings)

    def test_invalid_and_stale_registered_vaults_are_explicitly_incomplete(self) -> None:
        self.analysis_measure.write_text(
            _concept(
                "measure",
                "Measure",
                "Uncompiled authority drift.",
                topics=["[[Knowledge/Topics/Measure]]"],
            ),
            encoding="utf-8",
        )
        moved = self.root / "Probability-missing"
        self.probability.rename(moved)
        snapshot = capture_federation(home=self.home)
        self.assertEqual((), snapshot.vaults)
        self.assertEqual(
            ["analysis", "probability"],
            [item["vault_id"] for item in snapshot.incomplete_vaults],
        )
        self.assertEqual("stale-native-graph", snapshot.incomplete_vaults[0]["code"])
        self.assertNotIn(str(self.root), canonical_json(snapshot.incomplete_vaults))

    def test_requested_missing_vault_is_not_silently_omitted(self) -> None:
        snapshot = capture_federation(home=self.home, vault_ids=["missing"])
        self.assertEqual((), snapshot.vaults)
        self.assertEqual("vault-not-registered", snapshot.incomplete_vaults[0]["code"])

    def test_deep_index_entry_fails_iteratively_without_recursion(self) -> None:
        captured = capture_federation(home=self.home, vault_ids=["analysis"])
        view = captured.vaults[0].view
        nested: object = "leaf"
        for _ in range(federation_module.MAX_INDEX_ENTRY_DEPTH + 2):
            nested = {"child": nested}
        view.nodes["measure"]["entry"] = nested
        with self.assertRaisesRegex(
            federation_module.FederationError, "structural limits"
        ):
            federation_module._build_index(view)

        with mock.patch.object(federation_module, "MAX_INDEX_ENTRY_VALUES", 10):
            with self.assertRaisesRegex(
                federation_module.FederationError, "structural limits"
            ):
                federation_module._body_terms("", ["x"] * 11)

        late_nested: object = "leaf"
        for _ in range(federation_module.MAX_INDEX_ENTRY_DEPTH + 2):
            late_nested = {"child": late_nested}
        view.nodes["measure"]["entry"] = [
            " ".join(f"term{index}" for index in range(256)),
            late_nested,
        ]
        with self.assertRaisesRegex(
            federation_module.FederationError, "structural limits"
        ):
            federation_module._build_index(view)

    def test_cjk_bigram_tokenization_is_shared_and_explicitly_bounded(self) -> None:
        phrase = "测度是满足可列可加性的集合函数"
        query = "可列可加性"
        indexed = federation_module._index_terms(phrase)
        queried = federation_module.query_terms(query)
        self.assertTrue(indexed & queried)
        self.assertIn("测测", federation_module._index_terms("测" * 300))
        self.assertIn("𠀁𠀂", federation_module.query_terms("𠀁𠀂"))
        self.assertTrue(
            federation_module._index_terms("𠀀𠀁𠀂")
            & federation_module.query_terms("𠀁𠀂")
        )
        with mock.patch.object(federation_module, "MAX_INDEX_TERMS_PER_FIELD", 4):
            with self.assertRaisesRegex(
                federation_module.FederationError, "representable term limit"
            ):
                federation_module._index_terms("甲乙丙丁戊己")

    def test_oversized_index_makes_only_that_vault_incomplete(self) -> None:
        self.analysis_measure.write_text(
            _concept(
                "measure",
                "Measure",
                "x" * 512,
                topics=["[[Knowledge/Topics/Measure]]"],
            ),
            encoding="utf-8",
        )
        sync_knowledge("analysis", home=self.home)
        with federation_module._INDEX_CACHE_LOCK:
            federation_module._INDEX_CACHE.clear()
        with mock.patch.object(
            federation_module, "MAX_INDEX_TEXT_BYTES_PER_NODE", 256
        ):
            snapshot = capture_federation(home=self.home)
        self.assertEqual(["probability"], [item.vault.id for item in snapshot.vaults])
        self.assertEqual("analysis", snapshot.incomplete_vaults[0]["vault_id"])
        self.assertEqual("recall-index-too-large", snapshot.incomplete_vaults[0]["code"])

    def test_recursive_failure_is_isolated_to_one_vault(self) -> None:
        original = federation_module._capture_one

        def fail_one(vault, **kwargs):
            if vault.id == "analysis":
                raise RecursionError("malicious nested entry")
            return original(vault, **kwargs)

        with mock.patch("kgdistiller.federation._capture_one", side_effect=fail_one):
            snapshot = capture_federation(home=self.home)
        self.assertEqual(["probability"], [item.vault.id for item in snapshot.vaults])
        self.assertEqual("invalid-vault-generation", snapshot.incomplete_vaults[0]["code"])

    def test_cache_covers_more_than_thirty_two_registered_vault_generations(self) -> None:
        captured = capture_federation(home=self.home, vault_ids=["analysis"])
        view = captured.vaults[0].view
        with federation_module._INDEX_CACHE_LOCK:
            federation_module._INDEX_CACHE.clear()
        with mock.patch(
            "kgdistiller.federation._build_index",
            wraps=federation_module._build_index,
        ) as build:
            for index in range(33):
                federation_module._cached_index(
                    f"vault-{index}", f"{index:064x}", view
                )
            for index in range(33):
                federation_module._cached_index(
                    f"vault-{index}", f"{index:064x}", view
                )
        self.assertEqual(33, build.call_count)

    def test_metadata_capture_never_reads_archived_blobs_and_preflights_row_budgets(self) -> None:
        source = self.analysis / "notes/source.md"
        source.parent.mkdir()
        source.write_text("Unreviewed archive body.\n", encoding="utf-8")
        capture_source(source, home=self.home)
        with mock.patch(
            "kgdistiller.source_archive._blob_bytes",
            side_effect=AssertionError("hot-path archive blob read"),
        ):
            snapshot = capture_federation(home=self.home)
        self.assertEqual(["analysis", "probability"], [item.vault.id for item in snapshot.vaults])

        with mock.patch.object(federation_module, "MAX_FEDERATION_DOCUMENTS", 0):
            bounded = capture_federation(home=self.home)
        self.assertEqual(["probability"], [item.vault.id for item in bounded.vaults])
        self.assertEqual("analysis", bounded.incomplete_vaults[0]["vault_id"])
        self.assertEqual(
            "federation-source-budget-exceeded",
            bounded.incomplete_vaults[0]["code"],
        )

    def test_aggregate_authority_and_retained_budgets_isolate_later_vault(self) -> None:
        analysis = capture_federation(home=self.home, vault_ids=["analysis"]).vaults[0]
        probability = capture_federation(home=self.home, vault_ids=["probability"]).vaults[0]
        authority_budget = analysis.authority_bytes + probability.authority_bytes - 1
        with mock.patch.object(
            federation_module, "MAX_FEDERATION_AUTHORITY_BYTES", authority_budget
        ):
            authority_bounded = capture_federation(home=self.home)
        self.assertEqual(["analysis"], [item.vault.id for item in authority_bounded.vaults])
        self.assertEqual("probability", authority_bounded.incomplete_vaults[0]["vault_id"])

        retained_budget = analysis.retained_weight + probability.retained_weight - 1
        with mock.patch.object(
            federation_module, "MAX_FEDERATION_RETAINED_WEIGHT_BYTES", retained_budget
        ):
            retained_bounded = capture_federation(home=self.home)
        self.assertEqual(["analysis"], [item.vault.id for item in retained_bounded.vaults])
        self.assertEqual("probability", retained_bounded.incomplete_vaults[0]["vault_id"])

    def test_authority_capture_is_streamed_in_two_stable_passes(self) -> None:
        with mock.patch(
            "kgdistiller.vaults.snapshot_managed_markdown",
            side_effect=AssertionError("full authority snapshot used"),
        ), mock.patch(
            "kgdistiller.federation.parse_native_markdown",
            wraps=federation_module.parse_native_markdown,
        ) as parsed:
            captured = capture_federation(home=self.home, vault_ids=["analysis"])
        self.assertEqual(1, len(captured.vaults))
        self.assertEqual(
            2 * captured.vaults[0].authority_files,
            parsed.call_count,
        )

    def test_declared_source_and_graph_sizes_bound_pinned_reads(self) -> None:
        source = self.analysis / "notes/source.md"
        source.parent.mkdir()
        source.write_text("Bounded metadata.\n", encoding="utf-8")
        capture_source(source, home=self.home)
        sources = self.analysis / ".kgdistiller/sources"
        manifest_path = sources / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        old_generation = manifest["generation_sha256"]
        manifest["artifacts"]["documents"]["bytes"] = 1
        new_generation = sha256_json(manifest["artifacts"])
        new_dir = sources / "generations" / new_generation
        new_dir.mkdir()
        for record in manifest["artifacts"].values():
            shutil.copyfile(
                sources / "generations" / old_generation / record["path"],
                new_dir / record["path"],
            )
        manifest["generation_sha256"] = new_generation
        manifest["generation_path"] = f"generations/{new_generation}"
        manifest_path.write_text(canonical_json(manifest), encoding="utf-8")
        with mock.patch(
            "kgdistiller.source_archive._read_regular",
            wraps=source_archive_module._read_regular,
        ) as source_read:
            bounded_source = capture_federation(home=self.home)
        self.assertEqual(["probability"], [item.vault.id for item in bounded_source.vaults])
        source_limits = [
            call.kwargs["maximum"]
            for call in source_read.call_args_list
            if call.kwargs.get("kind") == "source-documents"
        ]
        self.assertEqual([1], source_limits)

        graph_manifest_path = self.probability / ".kgdistiller/graph/manifest.json"
        graph_manifest = json.loads(graph_manifest_path.read_text(encoding="utf-8"))
        shard = graph_manifest["entry_store"]["shards"][0]
        shard_path = str(shard["path"])
        shard["bytes"] = 1
        graph_manifest_path.write_text(pretty_json(graph_manifest), encoding="utf-8")
        with mock.patch(
            "kgdistiller.native_compiler.read_vault_relative_regular",
            wraps=native_compiler_module.read_vault_relative_regular,
        ) as graph_read:
            bounded_graph = capture_federation(
                home=self.home, vault_ids=["probability"]
            )
        self.assertEqual((), bounded_graph.vaults)
        graph_limits = [
            call.kwargs["maximum"]
            for call in graph_read.call_args_list
            if len(call.args) > 1 and call.args[1] == f".kgdistiller/graph/{shard_path}"
        ]
        self.assertEqual([1], graph_limits)


if __name__ == "__main__":
    unittest.main()
