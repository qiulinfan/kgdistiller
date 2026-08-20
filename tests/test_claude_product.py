from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import kgdistiller.codex_product as codex_product_module
from kgdistiller.claude_product import (
    ClaudeProductError,
    doctor_claude_product,
    link_claude_product,
    load_claude_manifest,
)
from kgdistiller.codex_product import STATE_NAME, load_manifest

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
        (REPO_ROOT / "workflows" / "claude-manifest.json").read_text(encoding="utf-8")
    )
    (destination / "workflows").mkdir()
    shutil.copy2(
        REPO_ROOT / "workflows" / "claude-manifest.json",
        destination / "workflows" / "claude-manifest.json",
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


class ClaudeProductTests(unittest.TestCase):
    def test_manifest_assets_are_portable_and_complete(self) -> None:
        root, manifest = load_claude_manifest(REPO_ROOT)
        self.assertEqual(REPO_ROOT, root)
        self.assertEqual(8, len(manifest["skills"]))
        self.assertEqual(4, len(manifest["agents"]))
        self.assertEqual(2, len(manifest["linkers"]))
        self.assertEqual(7, len(manifest["workflows"]))
        for agent in manifest["agents"]:
            self.assertTrue(agent["install_as"].endswith(".md"))
        result = doctor_claude_product(source_only=True, source_root=REPO_ROOT)
        self.assertEqual("ok", result["status"])
        self.assertEqual("not-checked", result["installation"])
        self.assertEqual("kgdistiller-claude-doctor-v1", result["schema"])

    def test_claude_and_codex_manifests_stay_aligned(self) -> None:
        _, claude_manifest = load_claude_manifest(REPO_ROOT)
        _, codex_manifest = load_manifest(REPO_ROOT)
        self.assertEqual(codex_manifest["skills"], claude_manifest["skills"])
        self.assertEqual(codex_manifest["workflows"], claude_manifest["workflows"])
        self.assertEqual(
            codex_manifest["installation"], claude_manifest["installation"]
        )
        self.assertEqual(
            [item["name"] for item in codex_manifest["agents"]],
            [item["name"] for item in claude_manifest["agents"]],
        )

    def test_posix_linker_is_lf_only_and_git_attributes_enforce_it(self) -> None:
        linker = REPO_ROOT / "scripts" / "link-claude-product.sh"
        content = linker.read_bytes()
        self.assertTrue(content.startswith(b"#!/usr/bin/env sh\n"))
        self.assertNotIn(b"\r", content)
        completed = subprocess.run(
            [
                "git",
                "check-attr",
                "text",
                "eol",
                "--",
                "scripts/link-claude-product.sh",
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("text: set", completed.stdout)
        self.assertIn("eol: lf", completed.stdout)

    def test_copy_link_and_doctor_preserve_global_configuration(self) -> None:
        with real_temporary_directory(prefix="kgdistiller-claude-") as temporary:
            home = Path(temporary) / ".claude"
            home.mkdir()
            guidance = home / "CLAUDE.md"
            settings = home / "settings.json"
            unrelated_skill = home / "skills" / "unrelated-skill"
            unrelated_skill.mkdir(parents=True)
            guidance.write_text("user guidance\n", encoding="utf-8")
            settings.write_text("{}\n", encoding="utf-8")
            (unrelated_skill / "SKILL.md").write_text("user skill\n", encoding="utf-8")

            linked = link_claude_product(
                claude_home=home, mode="copy", source_root=REPO_ROOT
            )
            self.assertEqual("linked", linked["status"])
            self.assertEqual(8, linked["skills"])
            self.assertEqual(4, linked["agents"])
            self.assertEqual([], linked["adopted"])
            self.assertEqual(str(home), linked["claude_home"])
            self.assertEqual(["CLAUDE.md", "settings.json"], linked["protected"])
            self.assertEqual(
                "user guidance\n", guidance.read_text(encoding="utf-8")
            )
            self.assertEqual("{}\n", settings.read_text(encoding="utf-8"))
            self.assertEqual(
                "user skill\n",
                (unrelated_skill / "SKILL.md").read_text(encoding="utf-8"),
            )

            state = json.loads((home / STATE_NAME).read_text(encoding="utf-8"))
            self.assertEqual("kgdistiller-claude-links-v1", state["schema"])
            installed_agents = sorted(
                record["target"]
                for record in state["assets"]
                if record["kind"] == "agent"
            )
            self.assertEqual(
                [
                    "agents/kgdistiller-note-curator.md",
                    "agents/kgdistiller-paper-distiller.md",
                    "agents/kgdistiller-query-reviewer.md",
                    "agents/kgdistiller-transaction-reviewer.md",
                ],
                installed_agents,
            )
            for target in installed_agents:
                installed = home.joinpath(*target.split("/"))
                self.assertTrue(installed.is_file(), installed)
                self.assertIn(
                    "name: kgdistiller-", installed.read_text(encoding="utf-8")
                )
            canonical_manifest = (
                home
                / "workflow-products"
                / "kgdistiller"
                / "workflows"
                / "claude-manifest.json"
            )
            self.assertTrue(canonical_manifest.is_file())
            canonical_guide = (
                home
                / "workflow-products"
                / "kgdistiller"
                / "docs"
                / "product-workflows.md"
            )
            self.assertTrue(canonical_guide.is_file())

            checked = doctor_claude_product(claude_home=home, source_root=REPO_ROOT)
            self.assertEqual("ok", checked["status"])
            self.assertEqual({"copy": 13}, checked["modes"])
            self.assertFalse(checked["real_time"])

    def test_symlink_link_adopts_existing_skills_only_links(self) -> None:
        with real_temporary_directory(prefix="kgdistiller-claude-") as temporary:
            root = Path(temporary)
            source = copy_product_root(root / "product")
            home = root / ".claude"
            (home / "skills").mkdir(parents=True)
            for name in ("query-kgdistiller", "ingest-kgdistiller"):
                try:
                    create_directory_link(
                        source / "skills" / name, home / "skills" / name
                    )
                except OSError as error:
                    self.skipTest(f"platform cannot create directory links: {error}")

            linked = link_claude_product(
                claude_home=home, mode="symlink", source_root=source
            )
            self.assertEqual("linked", linked["status"])
            self.assertEqual(
                ["skills/ingest-kgdistiller", "skills/query-kgdistiller"],
                linked["adopted"],
            )
            self.assertTrue(linked["real_time"])

            checked = doctor_claude_product(claude_home=home, source_root=source)
            self.assertEqual("ok", checked["status"])

            relinked = link_claude_product(
                claude_home=home, mode="symlink", source_root=source
            )
            self.assertEqual([], relinked["adopted"])
            self.assertEqual("linked", relinked["status"])

    def test_link_refuses_unmanaged_real_directories_and_foreign_links(self) -> None:
        with real_temporary_directory(prefix="kgdistiller-claude-") as temporary:
            root = Path(temporary)
            source = copy_product_root(root / "product")
            home = root / ".claude"
            occupied = home / "skills" / "query-kgdistiller"
            occupied.mkdir(parents=True)
            (occupied / "SKILL.md").write_text("user-owned\n", encoding="utf-8")
            with self.assertRaisesRegex(
                ClaudeProductError, "unmanaged Claude Code asset"
            ):
                link_claude_product(
                    claude_home=home, mode="symlink", source_root=source
                )
            self.assertEqual(
                "user-owned\n",
                (occupied / "SKILL.md").read_text(encoding="utf-8"),
            )

            shutil.rmtree(occupied)
            foreign = root / "foreign-skill"
            foreign.mkdir()
            (foreign / "SKILL.md").write_text("foreign\n", encoding="utf-8")
            try:
                create_directory_link(foreign, occupied)
            except OSError as error:
                self.skipTest(f"platform cannot create directory links: {error}")
            with self.assertRaisesRegex(
                ClaudeProductError, "unmanaged Claude Code asset"
            ):
                link_claude_product(
                    claude_home=home, mode="symlink", source_root=source
                )

    def test_agent_preset_frontmatter_is_validated(self) -> None:
        with real_temporary_directory(prefix="kgdistiller-claude-") as temporary:
            source = copy_product_root(Path(temporary) / "product")
            preset = source / ".claude" / "agents" / "note-curator.md"
            text = preset.read_text(encoding="utf-8")
            preset.write_text(
                text.replace("name: kgdistiller-note-curator", "name: rogue-agent"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ClaudeProductError, "kgdistiller-note-curator"
            ):
                load_claude_manifest(source)


if __name__ == "__main__":
    unittest.main()
