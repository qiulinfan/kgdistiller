from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kgdistiller.cli import apply_delta, synchronize  # noqa: E402
from kgdistiller.obsidian_export import (  # noqa: E402
    ObsidianExportError,
    _concept_relatives,
    build_obsidian_projection,
    verify_obsidian_projection,
)
from kgdistiller.project import initialize_project  # noqa: E402


class ObsidianExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-obsidian-")
        self.repo = Path(self.temporary.name)
        self.registry = self.repo / "knowledge/sources.json"
        self.graph = self.repo / "knowledge/graph"
        self.identities = self.repo / "knowledge/identities.json"
        self.alignments = self.repo / "knowledge/alignments.json"
        self.typst_registry = self.repo / "knowledge/build/knowledge-registry.typ"
        self.output = self.repo / "knowledge/build/obsidian"
        initialize_project(
            self.repo,
            self.registry,
            source_root=Path("notes"),
            alignments=self.alignments,
        )
        self.authority = self.repo / "notes/chapter.md"
        self.authority.write_text(
            "> **Definition: --[[Sigma algebra]]--**\n>\n"
            "> A collection closed under complement and countable union.\n\n"
            "> **Definition: --[[Measure]]--**\n>\n"
            "> A countably additive function on a [[Sigma algebra]].\n",
            encoding="utf-8",
        )
        synchronize(
            self.repo,
            self.registry,
            self.graph,
            self.typst_registry,
            identities=self.identities,
            alignments=self.alignments,
            files=[],
            course=None,
            subject=None,
            write=True,
        )
        delta = self.repo / "relation.json"
        delta.write_text(
            json.dumps(
                {
                    "schema": "kgdistiller-agent-delta-v1",
                    "nodes": [],
                    "edges": [
                        {
                            "source": "sigma-algebra",
                            "relation": "prerequisite-for",
                            "target": "measure",
                            "evidence": "A measure is defined on a sigma algebra.",
                            "curation_status": "current",
                        }
                    ],
                    "remove_nodes": [],
                    "remove_edges": [],
                }
            ),
            encoding="utf-8",
        )
        apply_delta(self.graph, self.typst_registry, delta)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def build(self, *, output: Path | None = None, replace: bool = False):
        return build_obsidian_projection(
            self.repo,
            output or self.output,
            registry=self.registry,
            graph_dir=self.graph,
            identities=self.identities,
            replace=replace,
        )

    def add_identity_definitions(self, definitions: dict[str, str]) -> None:
        identities = (
            json.loads(self.identities.read_text(encoding="utf-8"))
            if self.identities.is_file()
            else {"schema": "kgdistiller-identities-v1", "identities": []}
        )
        identities["identities"].extend(
            {
                "id": node_id,
                "canonical_name": label,
                "aliases": [],
            }
            for node_id, label in definitions.items()
        )
        self.identities.write_text(
            json.dumps(identities, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        additions = "\n".join(
            f"> **Definition: --[[{label}]]--**\n>\n> Portability fixture for {label}.\n"
            for label in definitions.values()
        )
        self.authority.write_text(
            self.authority.read_text(encoding="utf-8") + "\n" + additions,
            encoding="utf-8",
        )
        synchronize(
            self.repo,
            self.registry,
            self.graph,
            self.typst_registry,
            identities=self.identities,
            alignments=self.alignments,
            files=[],
            course=None,
            subject=None,
            write=True,
        )

    @staticmethod
    def hashed_concept_relative(node_id: str) -> Path:
        digest = hashlib.sha256(node_id.encode("utf-8")).hexdigest()
        return Path("concepts") / f"_kgd-{node_id[:48]}-{digest}.md"

    def test_projection_has_stable_concepts_sources_and_lossy_wikilinks(self) -> None:
        delta = self.repo / "structured-entry.json"
        delta.write_text(
            json.dumps(
                {
                    "schema": "kgdistiller-agent-delta-v1",
                    "nodes": [
                        {
                            "id": "measure",
                            "entry": {
                                "common_confusions": [
                                    "A measure is not the same thing as its sigma algebra."
                                ]
                            },
                        }
                    ],
                    "edges": [],
                    "remove_nodes": [],
                    "remove_edges": [],
                }
            ),
            encoding="utf-8",
        )
        apply_delta(self.graph, self.typst_registry, delta)
        created = self.build()
        verified = verify_obsidian_projection(self.output)

        self.assertEqual("kgdistiller-obsidian-export-report-v1", created["schema"])
        self.assertEqual("kgdistiller-obsidian-projection-v1", created["artifact_schema"])
        self.assertEqual(created["projection_sha256"], verified["projection_sha256"])
        self.assertEqual(2, created["counts"]["concepts"])
        sigma = (self.output / "concepts/Sigma algebra.md").read_text(encoding="utf-8")
        measure = (self.output / "concepts/Measure.md").read_text(encoding="utf-8")
        proxy = (self.output / "sources/notes/chapter.md.md").read_text(encoding="utf-8")
        self.assertIn('kgd_id: "sigma-algebra"', sigma)
        self.assertIn("`prerequisite-for`", sigma)
        self.assertIn("[[Measure|Measure]]", sigma)
        self.assertIn("[[../sources/notes/chapter.md|notes/chapter.md:", measure)
        self.assertIn("### Common confusions", measure)
        self.assertIn("A measure is not the same thing as its sigma algebra.", measure)
        self.assertIn("[[notes/chapter|Open registered Markdown authority]]", proxy)
        self.assertIn("[[../../concepts/Sigma algebra|Sigma algebra]]", proxy)

    def test_chinese_canonical_label_is_the_raw_authority_wikilink_target(self) -> None:
        self.add_identity_definitions({"chinese-concept-id": "中文名"})
        self.authority.write_text(
            self.authority.read_text(encoding="utf-8")
            + "\n> A second occurrence references [[中文名]].\n",
            encoding="utf-8",
        )
        synchronize(
            self.repo,
            self.registry,
            self.graph,
            self.typst_registry,
            identities=self.identities,
            alignments=self.alignments,
            files=[],
            course=None,
            subject=None,
            write=True,
        )

        self.build()

        authority = self.authority.read_text(encoding="utf-8")
        concept_path = self.output / "concepts/中文名.md"
        concept = concept_path.read_text(encoding="utf-8")
        proxy = (self.output / "sources/notes/chapter.md.md").read_text(encoding="utf-8")
        self.assertIn("--[[中文名]]--", authority)
        self.assertIn("references [[中文名]]", authority)
        self.assertEqual([concept_path], list(self.repo.rglob("中文名.md")))
        self.assertIn('kgd_id: "chinese-concept-id"', concept)
        self.assertIn("[[../../concepts/中文名|中文名]]", proxy)

    def test_markdown_raw_target_survives_semantic_label_cleanup_and_md_suffix(self) -> None:
        self.authority.write_text(
            self.authority.read_text(encoding="utf-8")
            + "\n> **Definition: --[[snake_case]]--**\n>\n> Keeps its literal target.\n"
            + "\n> **Definition: --[[Guide.md]]--**\n>\n> Uses Obsidian's explicit extension.\n"
            + "\n> **Definition: --[[100%% rule]]--**\n>\n> Requires a plugin-safe fallback.\n",
            encoding="utf-8",
        )
        synchronize(
            self.repo,
            self.registry,
            self.graph,
            self.typst_registry,
            identities=self.identities,
            alignments=self.alignments,
            files=[],
            course=None,
            subject=None,
            write=True,
        )

        self.build()

        snake = (self.output / "concepts/snake_case.md").read_text(encoding="utf-8")
        guide = (self.output / "concepts/Guide.md").read_text(encoding="utf-8")
        percent = self.hashed_concept_relative("100-rule")
        self.assertIn('kgd_id: "snakecase"', snake)
        self.assertIn('kgd_id: "guide-md"', guide)
        self.assertFalse((self.output / "concepts/Guide.md.md").exists())
        self.assertTrue((self.output / percent).is_file())

    def test_registered_markdown_basename_conflict_fails_closed(self) -> None:
        collision = self.repo / "notes/Measure.md"
        collision.write_text(
            "This filename shadows the raw [[Measure]] target.\n",
            encoding="utf-8",
        )
        synchronize(
            self.repo,
            self.registry,
            self.graph,
            self.typst_registry,
            identities=self.identities,
            alignments=self.alignments,
            files=[],
            course=None,
            subject=None,
            write=True,
        )

        with self.assertRaisesRegex(
            ObsidianExportError,
            "conflicts with a registered Markdown authority basename",
        ):
            self.build()
        self.assertFalse(self.output.exists())

    def test_same_generation_is_a_byte_stable_noop(self) -> None:
        first = self.build()
        before = {
            path.relative_to(self.output).as_posix(): path.read_bytes()
            for path in self.output.rglob("*")
            if path.is_file()
        }
        second = self.build()
        after = {
            path.relative_to(self.output).as_posix(): path.read_bytes()
            for path in self.output.rglob("*")
            if path.is_file()
        }
        self.assertTrue(first["changed"])
        self.assertFalse(second["changed"])
        self.assertEqual(before, after)

    def test_external_projection_uses_file_uri_for_authority(self) -> None:
        external = self.repo.parent / f"{self.repo.name}-external-vault"
        self.addCleanup(shutil.rmtree, external, True)
        created = self.build(output=external)
        proxy = (external / "sources/notes/chapter.md.md").read_text(encoding="utf-8")
        self.assertEqual("file-uri", json.loads((external / "manifest.json").read_text(encoding="utf-8"))["policy"]["authority_links"])
        self.assertIn("[Open registered Markdown authority](file:", proxy)
        self.assertEqual("kgdistiller-obsidian-export-report-v1", created["schema"])

    def test_hash_filename_uses_safe_proxy_and_file_uri(self) -> None:
        hash_authority = self.repo / "notes/C#-notes.md"
        hash_authority.write_text(
            "> **Definition: --[[C sharp type]]--**\n>\n"
            "> A type authored in a filename containing a hash.\n",
            encoding="utf-8",
        )
        synchronize(
            self.repo,
            self.registry,
            self.graph,
            self.typst_registry,
            identities=self.identities,
            alignments=self.alignments,
            files=[],
            course=None,
            subject=None,
            write=True,
        )

        self.build()

        proxies = list((self.output / "sources/by-authority").glob("*.md"))
        self.assertEqual(1, len(proxies))
        proxy = proxies[0]
        proxy_relative = proxy.relative_to(self.output).as_posix()
        self.assertNotIn("#", proxy_relative)
        self.assertRegex(proxy.name, r"^notes-c-notes-md-[0-9a-f]{64}\.md$")
        proxy_text = proxy.read_text(encoding="utf-8")
        concept = (self.output / "concepts/C sharp type.md").read_text(encoding="utf-8")
        proxy_target = Path(proxy_relative).with_suffix("").as_posix()
        self.assertIn(
            f"[[../{proxy_target}|notes/C#-notes.md:",
            concept,
        )
        self.assertIn("[Open registered Markdown authority](file:", proxy_text)
        self.assertIn("C%23-notes.md)", proxy_text)
        self.assertNotIn("[[notes/C#-notes", proxy_text)
        self.assertIn("[[../../concepts/C sharp type|C sharp type]]", proxy_text)

    def test_253_through_256_character_labels_use_hashed_paths_and_keep_links(self) -> None:
        definitions = {
            f"long-label-{length}": character * length
            for length, character in zip(range(253, 257), "abcd")
        }
        self.add_identity_definitions(definitions)
        delta = self.repo / "long-id-relations.json"
        delta.write_text(
            json.dumps(
                {
                    "schema": "kgdistiller-agent-delta-v1",
                    "nodes": [],
                    "edges": [
                        {
                            "source": "measure",
                            "relation": "prerequisite-for",
                            "target": node_id,
                            "evidence": "Portability regression link.",
                            "curation_status": "current",
                        }
                        for node_id in definitions
                    ],
                    "remove_nodes": [],
                    "remove_edges": [],
                }
            ),
            encoding="utf-8",
        )
        apply_delta(self.graph, self.typst_registry, delta)

        self.build()
        verified = verify_obsidian_projection(self.output)
        measure = (self.output / "concepts/Measure.md").read_text(encoding="utf-8")
        proxy = (self.output / "sources/notes/chapter.md.md").read_text(encoding="utf-8")
        concept_paths = {
            artifact["path"]
            for artifact in json.loads(
                (self.output / "manifest.json").read_text(encoding="utf-8")
            )["artifacts"]
            if artifact["kind"] == "concept"
        }

        self.assertEqual(6, verified["counts"]["concepts"])
        for node_id, label in definitions.items():
            with self.subTest(length=len(label)):
                relative = self.hashed_concept_relative(node_id)
                self.assertLessEqual(len(relative.name.encode("utf-8")), 255)
                self.assertTrue(relative.name.isascii())
                self.assertIn(relative.as_posix(), concept_paths)
                self.assertNotIn(f"concepts/{label}.md", concept_paths)
                concept = (self.output / relative).read_text(encoding="utf-8")
                self.assertIn(f'kgd_id: "{node_id}"', concept)
                self.assertIn(f"[[{relative.stem}|{label}]]", measure)
                self.assertIn(f"[[../../concepts/{relative.stem}|{label}]]", proxy)

    def test_windows_reserved_labels_use_hashed_paths_and_keep_proxy_backlinks(self) -> None:
        reserved_labels = [
            "con",
            "nul",
            "aux",
            "prn",
            *(f"com{number}" for number in range(1, 10)),
            *(f"lpt{number}" for number in range(1, 10)),
        ]
        definitions = {
            f"windows-{label}": label.upper() for label in reserved_labels
        }
        self.add_identity_definitions(definitions)

        self.build()
        verify_obsidian_projection(self.output)
        proxy = (self.output / "sources/notes/chapter.md.md").read_text(encoding="utf-8")
        concept_paths = {
            artifact["path"]
            for artifact in json.loads(
                (self.output / "manifest.json").read_text(encoding="utf-8")
            )["artifacts"]
            if artifact["kind"] == "concept"
        }

        for node_id, label in definitions.items():
            with self.subTest(node_id=node_id):
                relative = self.hashed_concept_relative(node_id)
                self.assertIn(relative.as_posix(), concept_paths)
                self.assertNotIn(f"concepts/{label}.md", concept_paths)
                concept = (self.output / relative).read_text(encoding="utf-8")
                self.assertIn(f'kgd_id: "{node_id}"', concept)
                self.assertIn(f"[[../../concepts/{relative.stem}|{label}]]", proxy)

    def test_casefold_and_unicode_filename_collisions_use_stable_fallbacks(self) -> None:
        nodes = {
            "case-upper": {"id": "case-upper", "label": "Same"},
            "case-lower": {"id": "case-lower", "label": "same"},
            "unicode-composed": {"id": "unicode-composed", "label": "Café"},
            "unicode-decomposed": {
                "id": "unicode-decomposed",
                "label": "Cafe\N{COMBINING ACUTE ACCENT}",
            },
        }

        planned = _concept_relatives(nodes)

        self.assertEqual(
            {node_id: self.hashed_concept_relative(node_id) for node_id in nodes},
            planned,
        )

    def test_changed_projection_requires_explicit_replace(self) -> None:
        self.build()
        self.authority.write_text(
            self.authority.read_text(encoding="utf-8")
            + "\n> **Definition: --[[Outer measure]]--**\n>\n> A covering construction.\n",
            encoding="utf-8",
        )
        synchronize(
            self.repo,
            self.registry,
            self.graph,
            self.typst_registry,
            identities=self.identities,
            alignments=self.alignments,
            files=[],
            course=None,
            subject=None,
            write=True,
        )
        with self.assertRaisesRegex(ObsidianExportError, "pass --replace"):
            self.build()
        replaced = self.build(replace=True)
        self.assertTrue(replaced["changed"])
        self.assertTrue((self.output / "concepts/Outer measure.md").is_file())

    def test_projection_cannot_overlap_authority_or_obsidian_config(self) -> None:
        with self.assertRaisesRegex(ObsidianExportError, "overlaps registered authority"):
            self.build(output=self.repo / "notes/projection")
        with self.assertRaisesRegex(ObsidianExportError, "cannot be the project root or .obsidian"):
            self.build(output=self.repo / ".obsidian/plugins/kgdistiller")

    def test_symlinked_managed_build_cannot_redirect_output_into_authority(self) -> None:
        authority_before = self.authority.read_bytes()
        build_root = self.repo / "knowledge/build"
        self.typst_registry.unlink()
        build_root.rmdir()
        try:
            build_root.symlink_to(self.repo / "notes", target_is_directory=True)
        except (NotImplementedError, OSError):
            build_root.mkdir()
            self.skipTest("directory symlink creation is unavailable")
        try:
            with self.assertRaisesRegex(ObsidianExportError, "overlaps registered authority"):
                self.build(output=build_root / "obsidian")
            self.assertEqual(authority_before, self.authority.read_bytes())
            self.assertFalse((self.repo / "notes/obsidian").exists())
        finally:
            build_root.unlink()
            build_root.mkdir()

    def test_verifier_rejects_tampering_and_unmanaged_files(self) -> None:
        self.build()
        concept = self.output / "concepts/Measure.md"
        original = concept.read_bytes()
        concept.write_text(concept.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(ObsidianExportError, "artifact digest mismatch"):
            verify_obsidian_projection(self.output)

        concept.write_bytes(original)
        (self.output / "extra.md").write_text("unmanaged", encoding="utf-8")
        with self.assertRaisesRegex(ObsidianExportError, "managed-file mismatch"):
            verify_obsidian_projection(self.output)
        (self.output / "extra.md").unlink()

        link = self.output / "extra-link"
        try:
            link.symlink_to(self.output / "concepts/Measure.md")
        except (NotImplementedError, OSError):
            pass
        else:
            with self.assertRaisesRegex(ObsidianExportError, "managed-file mismatch"):
                verify_obsidian_projection(self.output)
            link.unlink()

        if hasattr(os, "mkfifo"):
            fifo = self.output / "extra-fifo"
            os.mkfifo(fifo)
            try:
                with self.assertRaisesRegex(ObsidianExportError, "managed-file mismatch"):
                    verify_obsidian_projection(self.output)
            finally:
                fifo.unlink()

    def test_verifier_rejects_symlink_manifest_before_reading_it(self) -> None:
        self.build()
        manifest = self.output / "manifest.json"
        original = manifest.read_bytes()
        external = self.repo / "external-manifest.json"
        external.write_bytes(original)
        manifest.unlink()
        try:
            manifest.symlink_to(external)
        except (NotImplementedError, OSError):
            manifest.write_bytes(original)
            self.skipTest("symlink creation is unavailable")
        try:
            with self.assertRaisesRegex(ObsidianExportError, "manifest.*ordinary file"):
                verify_obsidian_projection(self.output)
        finally:
            manifest.unlink()
            manifest.write_bytes(original)

    def test_unsynchronized_authority_inventory_fails_closed(self) -> None:
        self.build()
        baseline = {
            path.relative_to(self.output).as_posix(): path.read_bytes()
            for path in self.output.rglob("*")
            if path.is_file()
        }
        original_authority = self.authority.read_bytes()
        original_registry = self.registry.read_bytes()

        def assert_rejected() -> None:
            with self.assertRaisesRegex(
                ObsidianExportError,
                "out of sync|ownership is not unique",
            ):
                self.build(replace=True)
            current = {
                path.relative_to(self.output).as_posix(): path.read_bytes()
                for path in self.output.rglob("*")
                if path.is_file()
            }
            self.assertEqual(baseline, current)

        with self.subTest(change="modified"):
            self.authority.write_text(
                self.authority.read_text(encoding="utf-8") + "\nunsynchronized\n",
                encoding="utf-8",
            )
            assert_rejected()
            self.authority.write_bytes(original_authority)

        with self.subTest(change="added"):
            added = self.repo / "notes/added.md"
            added.write_text("> **Definition: --[[Added]]--**\n", encoding="utf-8")
            assert_rejected()
            added.unlink()

        with self.subTest(change="deleted"):
            self.authority.unlink()
            assert_rejected()
            self.authority.write_bytes(original_authority)

        with self.subTest(change="overlapping-ownership"):
            registry = json.loads(original_registry)
            overlap = dict(registry["sources"][0])
            overlap["id"] = "overlapping-owner"
            registry["sources"].append(overlap)
            self.registry.write_text(json.dumps(registry), encoding="utf-8")
            assert_rejected()
            self.registry.write_bytes(original_registry)

    def test_unsynchronized_registry_generations_fail_without_replacing_projection(self) -> None:
        self.build()
        baseline = {
            path.relative_to(self.output).as_posix(): path.read_bytes()
            for path in self.output.rglob("*")
            if path.is_file()
        }
        original_registry = self.registry.read_bytes()
        original_identities = (
            self.identities.read_bytes() if self.identities.is_file() else None
        )

        def assert_unchanged() -> None:
            current = {
                path.relative_to(self.output).as_posix(): path.read_bytes()
                for path in self.output.rglob("*")
                if path.is_file()
            }
            self.assertEqual(baseline, current)

        try:
            registry = json.loads(original_registry)
            registry["sources"][0]["subject"] = "changed-without-sync"
            self.registry.write_text(json.dumps(registry), encoding="utf-8")
            with self.assertRaisesRegex(ObsidianExportError, "source registry is out of sync"):
                self.build(replace=True)
            assert_unchanged()
            self.registry.write_bytes(original_registry)

            identities = (
                json.loads(original_identities)
                if original_identities is not None
                else {"schema": "kgdistiller-identities-v1", "identities": []}
            )
            identities["identities"].append(
                {
                    "id": "unsynchronized-identity",
                    "canonical_name": "Unsynchronized identity",
                    "aliases": [],
                }
            )
            self.identities.write_text(json.dumps(identities), encoding="utf-8")
            with self.assertRaisesRegex(ObsidianExportError, "identity registry is out of sync"):
                self.build(replace=True)
            assert_unchanged()
        finally:
            self.registry.write_bytes(original_registry)
            if original_identities is None:
                self.identities.unlink(missing_ok=True)
            else:
                self.identities.write_bytes(original_identities)

    def test_authority_change_during_render_fails_before_install(self) -> None:
        self.build()
        baseline = {
            path.relative_to(self.output).as_posix(): path.read_bytes()
            for path in self.output.rglob("*")
            if path.is_file()
        }
        original_authority = self.authority.read_bytes()

        from kgdistiller import obsidian_export

        original_render = obsidian_export._render_source_proxy
        changed = False

        def render_then_change(*args: object, **kwargs: object) -> str:
            nonlocal changed
            rendered = original_render(*args, **kwargs)
            if not changed:
                self.authority.write_text(
                    self.authority.read_text(encoding="utf-8") + "\nchanged mid-build\n",
                    encoding="utf-8",
                )
                changed = True
            return rendered

        try:
            with patch.object(
                obsidian_export,
                "_render_source_proxy",
                render_then_change,
            ):
                with self.assertRaisesRegex(ObsidianExportError, "out of sync"):
                    self.build(replace=True)
            current = {
                path.relative_to(self.output).as_posix(): path.read_bytes()
                for path in self.output.rglob("*")
                if path.is_file()
            }
            self.assertEqual(baseline, current)
        finally:
            self.authority.write_bytes(original_authority)

    def test_stage_render_failure_preserves_existing_projection(self) -> None:
        self.build()
        baseline = {
            path.relative_to(self.output).as_posix(): path.read_bytes()
            for path in self.output.rglob("*")
            if path.is_file()
        }
        self.add_identity_definitions({"stage-failure": "Stage failure"})

        from kgdistiller import obsidian_export

        original_render = obsidian_export._render_concept

        def fail_during_stage(
            node: dict[str, object],
            **kwargs: object,
        ) -> str:
            if node["id"] == "stage-failure":
                raise OSError("injected stage render failure")
            return original_render(node, **kwargs)  # type: ignore[arg-type]

        with patch.object(obsidian_export, "_render_concept", fail_during_stage):
            with self.assertRaisesRegex(OSError, "injected stage render failure"):
                self.build(replace=True)

        current = {
            path.relative_to(self.output).as_posix(): path.read_bytes()
            for path in self.output.rglob("*")
            if path.is_file()
        }
        self.assertEqual(baseline, current)
        self.assertEqual(
            [],
            list(self.output.parent.glob(f".{self.output.name}.stage-*")),
        )


if __name__ == "__main__":
    unittest.main()
