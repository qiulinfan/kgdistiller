from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import kgdistiller.codex_product as codex_product_module
from kgdistiller.codex_product import (
    RECOVERY_ROOT_NAME,
    STATE_NAME,
    CodexProductError,
    doctor_product,
    link_product,
    load_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def real_temporary_directory(*, prefix: str) -> tempfile.TemporaryDirectory[str]:
    """Place safety-boundary fixtures below a canonical non-symlink temp root."""
    return tempfile.TemporaryDirectory(
        prefix=prefix,
        dir=Path(tempfile.gettempdir()).resolve(),
    )


def copy_product_root(destination: Path) -> Path:
    destination.mkdir(parents=True)
    manifest = json.loads(
        (REPO_ROOT / "workflows" / "manifest.json").read_text(encoding="utf-8")
    )
    (destination / "workflows").mkdir()
    shutil.copy2(
        REPO_ROOT / "workflows" / "manifest.json",
        destination / "workflows" / "manifest.json",
    )
    for item in manifest["skills"]:
        source = REPO_ROOT / item["path"]
        shutil.copytree(source, destination / item["path"])
    for group in ("agents", "linkers"):
        for item in manifest[group]:
            source = REPO_ROOT / item["path"]
            target = destination / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    guide = manifest["workflow_guide"]
    (destination / guide).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / guide, destination / guide)
    return destination


def create_directory_link(source: Path, target: Path) -> None:
    if os.name == "nt":
        codex_product_module._create_junction(source, target)
    else:
        os.symlink(source, target, target_is_directory=True)


class CodexProductTests(unittest.TestCase):
    def test_manifest_assets_are_portable_and_complete(self) -> None:
        root, manifest = load_manifest(REPO_ROOT)
        self.assertEqual(REPO_ROOT, root)
        self.assertEqual(8, len(manifest["skills"]))
        self.assertEqual(4, len(manifest["agents"]))
        self.assertEqual(2, len(manifest["linkers"]))
        self.assertEqual(3, manifest["version"])
        self.assertEqual(8, len(manifest["workflows"]))
        self.assertEqual(
            {
                "curate-notes",
                "export-obsidian",
                "federate-paper",
                "import-paper",
                "manage-vaults",
                "portable-store",
                "publish-static",
                "trace-lineage",
            },
            {item["id"] for item in manifest["workflows"]},
        )
        result = doctor_product(source_only=True, source_root=REPO_ROOT)
        self.assertEqual("ok", result["status"])
        self.assertEqual("not-checked", result["installation"])

    def test_native_skill_metadata_workflows_and_packaging_are_current(self) -> None:
        native_skills = (
            "curate-kgdistiller-notes",
            "deploy-kgdistiller",
            "distill-paper-knowledge",
            "import-paper-knowledge",
            "ingest-kgdistiller",
            "query-kgdistiller",
        )
        for name in native_skills:
            with self.subTest(skill=name):
                skill = (REPO_ROOT / "skills" / name / "SKILL.md").read_text(
                    encoding="utf-8"
                )
                self.assertTrue(skill.startswith("---\n"))
                frontmatter = skill.split("---\n", 2)[1]
                self.assertEqual(
                    {"name", "description"},
                    {
                        line.split(":", 1)[0]
                        for line in frontmatter.splitlines()
                        if line.strip()
                    },
                )
                self.assertIn(
                    "Match user-facing explanations, prompts, and handoffs",
                    skill,
                )
                metadata = (
                    REPO_ROOT / "skills" / name / "agents" / "openai.yaml"
                ).read_text(encoding="utf-8")
                self.assertIn(f'default_prompt: "Use ${name} ', metadata)

        manifest = json.loads(
            (REPO_ROOT / "workflows" / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        by_id = {item["id"]: item for item in manifest["workflows"]}
        self.assertIn("qlkg-vault-store-v3", by_id["portable-store"]["description"])
        self.assertTrue(by_id["publish-static"]["description"].startswith("Legacy-only:"))
        self.assertTrue(by_id["export-obsidian"]["description"].startswith("Legacy-only:"))

        for name in (
            "product-workflows.md",
            "deployment.md",
            "obsidian.md",
            "transactional-ingest.md",
            "graph-contract.md",
            "release.md",
            "performance.md",
        ):
            self.assertTrue((REPO_ROOT / "docs" / name).is_file(), name)


    def test_wheel_and_sdist_have_distinct_closed_product_inventories(self) -> None:
        with real_temporary_directory(
            prefix="kgdistiller-product-archives-"
        ) as temporary:
            output = Path(temporary)
            completed = subprocess.run(
                ["uv", "build", "--out-dir", os.fspath(output)],
                cwd=REPO_ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            wheels = list(output.glob("*.whl"))
            sdists = list(output.glob("*.tar.gz"))
            self.assertEqual(1, len(wheels))
            self.assertEqual(1, len(sdists))

            with zipfile.ZipFile(wheels[0]) as archive:
                wheel_files = set(archive.namelist())
            self.assertIn("kgdistiller/static/v1/bundle.json", wheel_files)
            self.assertIn(
                "kgdistiller/product/skills/query-kgdistiller/SKILL.md",
                wheel_files,
            )
            self.assertIn(
                "kgdistiller/product/workflows/manifest.json", wheel_files
            )
            self.assertIn(
                "kgdistiller/product/.codex/agents/query-reviewer.toml",
                wheel_files,
            )
            self.assertIn(
                "kgdistiller/product/docs/obsidian.md", wheel_files
            )
            self.assertFalse(
                any(name.startswith("kgdistiller/product/frontend/") for name in wheel_files)
            )
            self.assertFalse(
                any(name.endswith("scripts/smoke_multivault.py") for name in wheel_files)
            )

            with tarfile.open(sdists[0], mode="r:gz") as archive:
                sdist_files = {item.name for item in archive.getmembers() if item.isfile()}
            roots = {name.split("/", 1)[0] for name in sdist_files if "/" in name}
            self.assertEqual(1, len(roots))
            prefix = next(iter(roots)) + "/"
            relative = {
                name[len(prefix) :]
                for name in sdist_files
                if name.startswith(prefix)
            }
            for expected in (
                "frontend/package-lock.json",
                "frontend/scripts/package-bundle.mjs",
                "frontend/src/main.ts",
                "frontend/tests/client.test.ts",
                "scripts/smoke_multivault.py",
                "src/kgdistiller/static/v1/bundle.json",
                "skills/query-kgdistiller/SKILL.md",
                "workflows/manifest.json",
                "docs/obsidian.md",
            ):
                self.assertIn(expected, relative)
            self.assertFalse(
                any(
                    name.startswith("frontend/node_modules/")
                    or name.startswith("frontend/dist/")
                    or name.endswith(".map")
                    for name in relative
                )
            )

    def test_posix_linker_is_lf_only_and_git_attributes_enforce_it(self) -> None:
        linker = REPO_ROOT / "scripts" / "link-codex-product.sh"
        content = linker.read_bytes()
        self.assertTrue(content.startswith(b"#!/usr/bin/env sh\n"))
        self.assertNotIn(b"\r", content)
        completed = subprocess.run(
            ["git", "check-attr", "text", "eol", "--", "scripts/link-codex-product.sh"],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("text: set", completed.stdout)
        self.assertIn("eol: lf", completed.stdout)

    def test_copy_link_and_doctor_preserve_global_configuration(self) -> None:
        with real_temporary_directory(prefix="kgdistiller-codex-") as temporary:
            home = Path(temporary) / ".codex"
            home.mkdir()
            agents_guidance = home / "AGENTS.md"
            config = home / "config.toml"
            unrelated_skill = home / "skills" / "unrelated-skill"
            unrelated_skill.mkdir(parents=True)
            agents_guidance.write_text("user guidance\n", encoding="utf-8")
            config.write_text("model = 'user-choice'\n", encoding="utf-8")
            (unrelated_skill / "SKILL.md").write_text("user skill\n", encoding="utf-8")

            linked = link_product(codex_home=home, mode="copy", source_root=REPO_ROOT)
            self.assertEqual("linked", linked["status"])
            self.assertEqual(8, linked["skills"])
            self.assertEqual(4, linked["agents"])
            self.assertEqual(
                "user guidance\n", agents_guidance.read_text(encoding="utf-8")
            )
            self.assertEqual(
                "model = 'user-choice'\n", config.read_text(encoding="utf-8")
            )
            self.assertEqual(
                "user skill\n",
                (unrelated_skill / "SKILL.md").read_text(encoding="utf-8"),
            )

            state = json.loads((home / STATE_NAME).read_text(encoding="utf-8"))
            self.assertEqual(13, len(state["assets"]))
            self.assertTrue(all(item["mode"] == "copy" for item in state["assets"]))
            self.assertTrue(
                all(
                    item["target"] not in {"AGENTS.md", "config.toml"}
                    for item in state["assets"]
                )
            )
            self.assertTrue(
                (home / "skills" / "query-kgdistiller" / "SKILL.md").is_file()
            )
            self.assertTrue(
                (home / "agents" / "kgdistiller-query-reviewer.toml").is_file()
            )

            checked = doctor_product(
                codex_home=home,
                source_root=REPO_ROOT,
            )
            self.assertEqual("ok", checked["status"])
            self.assertEqual("linked", checked["installation"])

    def test_home_ancestor_of_product_fails_before_any_write(self) -> None:
        with real_temporary_directory(
            prefix="kgdistiller-codex-overlap-"
        ) as temporary:
            ancestor = Path(temporary)
            source = copy_product_root(ancestor / "product")
            before = {path.name for path in ancestor.iterdir()}
            with (
                patch.dict(os.environ, {"CODEX_HOME": str(ancestor)}),
                self.assertRaisesRegex(CodexProductError, "cannot overlap"),
            ):
                link_product(source_root=source)
            self.assertEqual(before, {path.name for path in ancestor.iterdir()})
            self.assertFalse((ancestor / STATE_NAME).exists())
            self.assertFalse((ancestor / "agents").exists())
            self.assertFalse((ancestor / "workflow-products").exists())

    def test_state_path_must_be_an_ordinary_single_link_file(self) -> None:
        with real_temporary_directory(
            prefix="kgdistiller-codex-state-"
        ) as temporary:
            root = Path(temporary)
            home = root / "codex-home"
            state = home / STATE_NAME
            state.mkdir(parents=True)
            with self.assertRaisesRegex(CodexProductError, "ordinary, non-reparse"):
                link_product(codex_home=home, mode="copy", source_root=REPO_ROOT)
            self.assertFalse((home / "skills").exists())
            self.assertFalse((home / "agents").exists())

        with real_temporary_directory(
            prefix="kgdistiller-codex-state-link-"
        ) as temporary:
            home = Path(temporary) / "codex-home"
            link_product(codex_home=home, mode="copy", source_root=REPO_ROOT)
            state = home / STATE_NAME
            os.link(state, home / "state-hardlink-alias.json")
            with self.assertRaisesRegex(CodexProductError, "ordinary, non-reparse"):
                doctor_product(codex_home=home, source_root=REPO_ROOT)

    def test_reparse_destination_parents_fail_before_any_product_write(self) -> None:
        for namespace in ("skills", "agents", "workflow-products"):
            with (
                self.subTest(namespace=namespace),
                real_temporary_directory(
                    prefix="kgdistiller-codex-reparse-parent-"
                ) as temporary,
            ):
                root = Path(temporary)
                home = root / "codex-home"
                external = root / "external"
                home.mkdir()
                external.mkdir()
                sentinel = external / "sentinel.txt"
                sentinel.write_text("external\n", encoding="utf-8")
                linked_parent = home / namespace
                create_directory_link(external, linked_parent)
                try:
                    with self.assertRaisesRegex(
                        CodexProductError, "ordinary, non-reparse"
                    ):
                        link_product(
                            codex_home=home,
                            mode="copy",
                            source_root=REPO_ROOT,
                        )
                    self.assertEqual("external\n", sentinel.read_text(encoding="utf-8"))
                    self.assertEqual(
                        {"sentinel.txt"}, {item.name for item in external.iterdir()}
                    )
                    self.assertFalse((home / STATE_NAME).exists())
                    for other in {"skills", "agents", "workflow-products"} - {
                        namespace
                    }:
                        self.assertFalse((home / other).exists())
                finally:
                    codex_product_module._remove_exact(linked_parent)

        with real_temporary_directory(
            prefix="kgdistiller-codex-reparse-home-"
        ) as temporary:
            root = Path(temporary)
            external = root / "external-home"
            external.mkdir()
            sentinel = external / "sentinel.txt"
            sentinel.write_text("external\n", encoding="utf-8")
            linked_home = root / "codex-home"
            create_directory_link(external, linked_home)
            try:
                with self.assertRaisesRegex(CodexProductError, "ordinary, non-reparse"):
                    link_product(
                        codex_home=linked_home,
                        mode="copy",
                        source_root=REPO_ROOT,
                    )
                self.assertEqual(
                    {"sentinel.txt"}, {item.name for item in external.iterdir()}
                )
            finally:
                codex_product_module._remove_exact(linked_home)

    def test_state_sources_must_belong_to_the_active_product_manifest(self) -> None:
        with real_temporary_directory(
            prefix="kgdistiller-codex-state-source-"
        ) as temporary:
            root = Path(temporary)
            source = copy_product_root(root / "product")
            home = root / "codex-home"
            link_product(codex_home=home, mode="copy", source_root=source)
            state_path = home / STATE_NAME
            state = json.loads(state_path.read_text(encoding="utf-8"))
            record = next(item for item in state["assets"] if item["kind"] == "agent")
            target = home / record["target"]
            before = target.read_bytes()
            record["source"] = str((root / "foreign-product" / "agent.toml").resolve())
            state_path.write_text(
                json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CodexProductError, "manifest namespace"):
                link_product(codex_home=home, mode="copy", source_root=source)
            self.assertEqual(before, target.read_bytes())

    def test_link_refuses_an_unmanaged_name_collision(self) -> None:
        with real_temporary_directory(
            prefix="kgdistiller-codex-collision-"
        ) as temporary:
            home = Path(temporary) / ".codex"
            collision = home / "skills" / "query-kgdistiller"
            collision.mkdir(parents=True)
            sentinel = collision / "SKILL.md"
            sentinel.write_text("unmanaged\n", encoding="utf-8")
            with self.assertRaisesRegex(CodexProductError, "unmanaged Codex asset"):
                link_product(codex_home=home, mode="copy", source_root=REPO_ROOT)
            self.assertEqual("unmanaged\n", sentinel.read_text(encoding="utf-8"))
            self.assertFalse((home / STATE_NAME).exists())

    def test_doctor_detects_a_modified_managed_copy(self) -> None:
        with real_temporary_directory(
            prefix="kgdistiller-codex-modified-"
        ) as temporary:
            home = Path(temporary) / ".codex"
            link_product(codex_home=home, mode="copy", source_root=REPO_ROOT)
            target = home / "skills" / "query-kgdistiller" / "SKILL.md"
            target.write_text(
                target.read_text(encoding="utf-8") + "modified\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(CodexProductError, "modified managed copy"):
                doctor_product(codex_home=home, source_root=REPO_ROOT)

    def test_symbolic_link_mode_and_doctor_when_platform_allows_it(self) -> None:
        with real_temporary_directory(
            prefix="kgdistiller-codex-symlink-"
        ) as temporary:
            root = Path(temporary)
            probe_source = root / "probe-source"
            probe_target = root / "probe-target"
            probe_source.mkdir()
            try:
                os.symlink(probe_source, probe_target, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"directory symbolic links unavailable: {error}")
            probe_target.unlink()

            home = root / ".codex"
            result = link_product(
                codex_home=home, mode="symlink", source_root=REPO_ROOT
            )
            self.assertEqual({"symlink": 13}, result["modes"])
            self.assertTrue((home / "skills" / "query-kgdistiller").is_symlink())
            checked = doctor_product(codex_home=home, source_root=REPO_ROOT)
            self.assertEqual({"symlink": 13}, checked["modes"])

    def test_auto_mode_selects_only_supported_link_strategies(self) -> None:
        with real_temporary_directory(prefix="kgdistiller-codex-auto-") as temporary:
            home = Path(temporary) / ".codex"
            linked = link_product(codex_home=home, mode="auto", source_root=REPO_ROOT)
            self.assertEqual(13, sum(linked["modes"].values()))
            self.assertLessEqual(
                set(linked["modes"]), {"junction", "hardlink", "symlink"}
            )
            checked = doctor_product(codex_home=home, source_root=REPO_ROOT)
            self.assertEqual(linked["modes"], checked["modes"])

    @unittest.skipUnless(os.name == "nt", "Windows junction regression")
    def test_unicode_auto_install_uses_junction_fallback_and_remains_healthy(
        self,
    ) -> None:
        with real_temporary_directory(
            prefix="kgdistiller-codex-unicode-"
        ) as temporary:
            root = Path(temporary)
            source = copy_product_root(root / "产品版本")
            home = root / "用户配置" / ".codex"
            with patch.object(
                codex_product_module.os,
                "symlink",
                side_effect=OSError("symlinks disabled for test"),
            ):
                linked = link_product(
                    codex_home=home,
                    mode="auto",
                    source_root=source,
                )
            self.assertGreater(linked["modes"].get("junction", 0), 0)
            self.assertTrue(linked["real_time"])
            checked = doctor_product(codex_home=home, source_root=source)
            self.assertEqual(linked["modes"], checked["modes"])
            self.assertEqual(
                "kgdistiller-workflows-v1",
                json.loads(
                    (
                        home
                        / "workflow-products"
                        / "kgdistiller"
                        / "workflows"
                        / "manifest.json"
                    ).read_text(encoding="utf-8")
                )["schema"],
            )

    @unittest.skipUnless(os.name == "nt", "Windows junction regression")
    def test_junction_process_error_removes_exact_staging_path(self) -> None:
        with real_temporary_directory(
            prefix="kgdistiller-codex-junction-error-"
        ) as temporary:
            root = Path(temporary)
            source = root / "产品源"
            staging = root / ".产品链接.kgdistiller-staging"
            source.mkdir()

            def fail_after_staging(*args: object, **kwargs: object) -> None:
                staging.mkdir()
                raise OSError("injected process failure")

            with (
                patch(
                    "kgdistiller.codex_product.subprocess.run",
                    side_effect=fail_after_staging,
                ) as run,
                self.assertRaisesRegex(CodexProductError, "junction creation failed"),
            ):
                codex_product_module._create_junction(source, staging)
            self.assertFalse(staging.exists())
            self.assertEqual(subprocess.DEVNULL, run.call_args.kwargs["stdout"])
            self.assertEqual(subprocess.DEVNULL, run.call_args.kwargs["stderr"])
            self.assertIs(run.call_args.kwargs["text"], False)

    def test_live_install_exposes_edits_and_canonical_workflows_from_unrelated_cwd(
        self,
    ) -> None:
        with real_temporary_directory(prefix="kgdistiller-codex-live-") as temporary:
            root = Path(temporary)
            source = copy_product_root(root / "product")
            home = root / "codex-home"
            linked = link_product(codex_home=home, mode="auto", source_root=source)
            self.assertTrue(linked["real_time"])

            canonical_root = home / "workflow-products" / "kgdistiller"
            canonical_manifest = canonical_root / "workflows" / "manifest.json"
            self.assertEqual(
                "kgdistiller-workflows-v1",
                json.loads(canonical_manifest.read_text(encoding="utf-8"))["schema"],
            )
            skill_source = source / "skills" / "query-kgdistiller" / "SKILL.md"
            with skill_source.open("a", encoding="utf-8") as handle:
                handle.write("\n<!-- live-edit-visible -->\n")
            self.assertIn(
                "live-edit-visible",
                (home / "skills" / "query-kgdistiller" / "SKILL.md").read_text(
                    encoding="utf-8"
                ),
            )
            self.assertIn(
                "live-edit-visible",
                (
                    canonical_root / "skills" / "query-kgdistiller" / "SKILL.md"
                ).read_text(encoding="utf-8"),
            )

            agent_source = source / ".codex" / "agents" / "query-reviewer.toml"
            with agent_source.open("a", encoding="utf-8") as handle:
                handle.write("\n# live-agent-edit\n")
            self.assertIn(
                "live-agent-edit",
                (home / "agents" / "kgdistiller-query-reviewer.toml").read_text(
                    encoding="utf-8"
                ),
            )

            unrelated = root / "unrelated-cwd"
            unrelated.mkdir()
            previous_cwd = Path.cwd()
            try:
                os.chdir(unrelated)
                checked = doctor_product(codex_home=home, source_root=source)
            finally:
                os.chdir(previous_cwd)
            self.assertTrue(checked["real_time"])
            self.assertEqual(str(canonical_manifest), checked["canonical_manifest"])

    def test_skill_rename_removes_only_the_owned_retired_target(self) -> None:
        with real_temporary_directory(
            prefix="kgdistiller-codex-rename-"
        ) as temporary:
            root = Path(temporary)
            source = copy_product_root(root / "product")
            home = root / "codex-home"
            external = home / "skills" / "external-skill" / "SKILL.md"
            external.parent.mkdir(parents=True)
            external.write_text("external\n", encoding="utf-8")
            initial = link_product(codex_home=home, mode="auto", source_root=source)
            self.assertTrue(initial["real_time"])

            old_name = "trace-concept-lineage"
            new_name = "trace-technical-lineage"
            old_source = source / "skills" / old_name
            new_source = source / "skills" / new_name
            old_source.rename(new_source)
            for relative in (Path("SKILL.md"), Path("agents/openai.yaml")):
                path = new_source / relative
                path.write_text(
                    path.read_text(encoding="utf-8").replace(old_name, new_name),
                    encoding="utf-8",
                )
            manifest_path = source / "workflows" / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for item in manifest["skills"]:
                if item["name"] == old_name:
                    item["name"] = new_name
                    item["path"] = f"skills/{new_name}"
            for workflow in manifest["workflows"]:
                for step in workflow["steps"]:
                    if step["skill"] == old_name:
                        step["skill"] = new_name
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            linked = link_product(codex_home=home, mode="auto", source_root=source)
            self.assertEqual(1, linked["removed"])
            self.assertTrue(linked["real_time"])
            self.assertFalse((home / "skills" / old_name).exists())
            self.assertTrue((home / "skills" / new_name / "SKILL.md").is_file())
            self.assertEqual("external\n", external.read_text(encoding="utf-8"))
            self.assertEqual(
                new_name,
                next(
                    item["name"]
                    for item in json.loads(
                        (
                            home
                            / "workflow-products"
                            / "kgdistiller"
                            / "workflows"
                            / "manifest.json"
                        ).read_text(encoding="utf-8")
                    )["skills"]
                    if item["name"] == new_name
                ),
            )

    def test_wrong_owner_retired_copy_is_preserved_and_blocks_cleanup(self) -> None:
        with real_temporary_directory(
            prefix="kgdistiller-codex-wrong-owner-"
        ) as temporary:
            root = Path(temporary)
            source = copy_product_root(root / "product")
            home = root / "codex-home"
            link_product(codex_home=home, mode="copy", source_root=source)
            name = "trace-concept-lineage"
            target = home / "skills" / name / "SKILL.md"
            target.write_text("foreign replacement\n", encoding="utf-8")

            manifest_path = source / "workflows" / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["skills"] = [
                item for item in manifest["skills"] if item["name"] != name
            ]
            manifest["workflows"] = [
                item for item in manifest["workflows"] if item["id"] != "trace-lineage"
            ]
            manifest_path.write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
            shutil.rmtree(source / "skills" / name)

            with self.assertRaisesRegex(CodexProductError, "modified managed copy"):
                link_product(codex_home=home, mode="copy", source_root=source)
            self.assertEqual(
                "foreign replacement\n", target.read_text(encoding="utf-8")
            )

    def test_detached_agent_hardlink_doctor_fails_but_link_repairs(self) -> None:
        with real_temporary_directory(
            prefix="kgdistiller-codex-hardlink-"
        ) as temporary:
            root = Path(temporary)
            source = copy_product_root(root / "product")
            home = root / "codex-home"
            link_product(codex_home=home, mode="auto", source_root=source)
            state = json.loads((home / STATE_NAME).read_text(encoding="utf-8"))
            agent = next(item for item in state["assets"] if item["kind"] == "agent")
            if agent["mode"] != "hardlink":
                self.skipTest("platform installed agents with symbolic links")
            source_path = Path(agent["source"])
            replacement = source_path.with_name(f".{source_path.name}.replacement")
            replacement.write_text(
                source_path.read_text(encoding="utf-8") + "\n# atomic replacement\n",
                encoding="utf-8",
            )
            os.replace(replacement, source_path)
            target = home / agent["target"]
            before = target.read_bytes()
            with self.assertRaisesRegex(CodexProductError, "detached managed hardlink"):
                doctor_product(codex_home=home, source_root=source)
            self.assertEqual(before, target.read_bytes())

            linked = link_product(codex_home=home, mode="auto", source_root=source)
            self.assertTrue(linked["committed"])
            self.assertEqual("complete", linked["cleanup_status"])
            self.assertEqual([], linked["warnings"])
            self.assertTrue(os.path.samefile(source_path, target))
            self.assertIn("atomic replacement", target.read_text(encoding="utf-8"))
            checked = doctor_product(codex_home=home, source_root=source)
            self.assertEqual("complete", checked["cleanup_status"])

    def test_retired_detached_agent_hardlink_is_removed_by_recorded_digest(
        self,
    ) -> None:
        with real_temporary_directory(
            prefix="kgdistiller-codex-retired-hardlink-"
        ) as temporary:
            root = Path(temporary)
            source = copy_product_root(root / "product")
            home = root / "codex-home"
            link_product(codex_home=home, mode="auto", source_root=source)
            state = json.loads((home / STATE_NAME).read_text(encoding="utf-8"))
            agent = next(
                item for item in state["assets"] if item["name"] == "query-reviewer"
            )
            if agent["mode"] != "hardlink":
                self.skipTest("platform installed agents with symbolic links")

            old_source = source / ".codex" / "agents" / "query-reviewer.toml"
            new_source = source / ".codex" / "agents" / "query-auditor.toml"
            old_source.rename(new_source)
            manifest_path = source / "workflows" / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for item in manifest["agents"]:
                if item["name"] == "query-reviewer":
                    item["name"] = "query-auditor"
                    item["path"] = ".codex/agents/query-auditor.toml"
                    item["install_as"] = "kgdistiller-query-auditor.toml"
            for workflow in manifest["workflows"]:
                for step in workflow["steps"]:
                    if step["agent"] == "query-reviewer":
                        step["agent"] = "query-auditor"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            old_target = home / "agents" / "kgdistiller-query-reviewer.toml"
            old_digest = old_target.read_bytes()
            linked = link_product(codex_home=home, mode="auto", source_root=source)
            self.assertEqual(1, linked["removed"])
            self.assertEqual("complete", linked["cleanup_status"])
            self.assertFalse(old_target.exists())
            new_target = home / "agents" / "kgdistiller-query-auditor.toml"
            self.assertEqual(old_digest, new_target.read_bytes())
            self.assertTrue(os.path.samefile(new_source, new_target))
            doctor_product(codex_home=home, source_root=source)

    def test_detached_agent_hardlink_digest_mismatch_is_preserved(self) -> None:
        with real_temporary_directory(
            prefix="kgdistiller-codex-hardlink-owner-"
        ) as temporary:
            root = Path(temporary)
            source = copy_product_root(root / "product")
            home = root / "codex-home"
            link_product(codex_home=home, mode="auto", source_root=source)
            state = json.loads((home / STATE_NAME).read_text(encoding="utf-8"))
            agent = next(item for item in state["assets"] if item["kind"] == "agent")
            if agent["mode"] != "hardlink":
                self.skipTest("platform installed agents with symbolic links")
            source_path = Path(agent["source"])
            replacement = source_path.with_name(f".{source_path.name}.replacement")
            replacement.write_text(
                source_path.read_text(encoding="utf-8") + "\n# new source inode\n",
                encoding="utf-8",
            )
            os.replace(replacement, source_path)
            target = home / agent["target"]
            target.write_text("foreign owner content\n", encoding="utf-8")

            with self.assertRaisesRegex(CodexProductError, "detached managed hardlink"):
                link_product(codex_home=home, mode="auto", source_root=source)
            self.assertEqual(
                "foreign owner content\n", target.read_text(encoding="utf-8")
            )

    def test_link_cleanup_failure_reports_committed_state_and_recovers(self) -> None:
        with real_temporary_directory(
            prefix="kgdistiller-codex-commit-point-"
        ) as temporary:
            root = Path(temporary)
            source = copy_product_root(root / "product")
            home = root / "codex-home"
            link_product(codex_home=home, mode="auto", source_root=source)
            original_remove = codex_product_module._remove_exact
            injected = False

            def fail_first_owned_backup(path: Path) -> None:
                nonlocal injected
                if RECOVERY_ROOT_NAME in path.parts and not injected:
                    injected = True
                    raise OSError("injected committed cleanup failure")
                original_remove(path)

            with patch(
                "kgdistiller.codex_product._remove_exact",
                side_effect=fail_first_owned_backup,
            ):
                linked = link_product(
                    codex_home=home,
                    mode="auto",
                    source_root=source,
                )
            self.assertTrue(injected)
            self.assertTrue(linked["committed"])
            self.assertEqual("pending", linked["cleanup_status"])
            self.assertTrue(linked["warnings"])
            self.assertEqual(1, len(linked["recovery_paths"]))
            self.assertTrue(Path(linked["recovery_paths"][0]).exists())
            pending_state = json.loads((home / STATE_NAME).read_text(encoding="utf-8"))
            self.assertTrue(pending_state["cleanup"])
            checked = doctor_product(codex_home=home, source_root=source)
            self.assertEqual("pending", checked["cleanup_status"])

            recovered = link_product(
                codex_home=home,
                mode="auto",
                source_root=source,
            )
            self.assertEqual("complete", recovered["cleanup_status"])
            self.assertEqual([], recovered["recovery_paths"])
            final_state = json.loads((home / STATE_NAME).read_text(encoding="utf-8"))
            self.assertEqual([], final_state["cleanup"])
            self.assertFalse((home / RECOVERY_ROOT_NAME).exists())


if __name__ == "__main__":
    unittest.main()
