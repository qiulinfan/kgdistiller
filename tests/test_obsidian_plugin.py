from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import chdir, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kgdistiller.cli import main
from kgdistiller.obsidian_plugin import (
    INSTALL_SCHEMA,
    PLUGIN_FILES,
    ObsidianPluginError,
    install_obsidian_plugin,
)
from tests.knowledge_fixture import use_temporary_home


class ObsidianPluginInstallTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="kgdistiller-obsidian-plugin-"
        )
        self.vault = Path(self.temporary.name) / "vault"
        (self.vault / ".obsidian").mkdir(parents=True)
        self.plugin = self.vault / ".obsidian/plugins/kgdistiller"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_installs_enables_and_is_idempotent(self) -> None:
        installed = install_obsidian_plugin(self.vault)

        self.assertEqual(INSTALL_SCHEMA, installed["schema"])
        self.assertEqual("installed", installed["status"])
        self.assertEqual("updated", installed["enabled_configuration"])
        self.assertTrue(installed["reload_required"])
        self.assertEqual(list(PLUGIN_FILES), [item["path"] for item in installed["files"]])
        self.assertEqual(
            ["kgdistiller"],
            json.loads(
                (self.vault / ".obsidian/community-plugins.json").read_text(
                    encoding="utf-8"
                )
            ),
        )
        for name in PLUGIN_FILES:
            self.assertGreater((self.plugin / name).stat().st_size, 0)

        current = install_obsidian_plugin(self.vault)
        self.assertEqual("current", current["status"])
        self.assertEqual("current", current["enabled_configuration"])

    def test_replace_preserves_plugin_settings(self) -> None:
        install_obsidian_plugin(self.vault)
        settings = b'{"showDrafts":false,"label":"\xe7\x9f\xa5\xe8\xaf\x86"}\n'
        (self.plugin / "data.json").write_bytes(settings)
        (self.plugin / "main.js").write_text("stale", encoding="utf-8")

        with self.assertRaisesRegex(ObsidianPluginError, "use --replace"):
            install_obsidian_plugin(self.vault)

        updated = install_obsidian_plugin(self.vault, replace=True)
        self.assertEqual("updated", updated["status"])
        self.assertEqual(settings, (self.plugin / "data.json").read_bytes())
        self.assertNotEqual(b"stale", (self.plugin / "main.js").read_bytes())

    def test_install_without_enable_does_not_edit_community_configuration(self) -> None:
        result = install_obsidian_plugin(self.vault, enable=False)
        self.assertEqual("unchanged", result["enabled_configuration"])
        self.assertFalse(
            (self.vault / ".obsidian/community-plugins.json").exists()
        )

    def test_rejects_unmanaged_plugin_files(self) -> None:
        self.plugin.mkdir(parents=True)
        (self.plugin / "user-note.txt").write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(ObsidianPluginError, "unmanaged files"):
            install_obsidian_plugin(self.vault, replace=True)
        self.assertEqual(
            "keep", (self.plugin / "user-note.txt").read_text(encoding="utf-8")
        )

    def test_invalid_enable_configuration_rolls_back_new_install(self) -> None:
        configuration = self.vault / ".obsidian/community-plugins.json"
        configuration.write_text("{}\n", encoding="utf-8")

        with self.assertRaisesRegex(ObsidianPluginError, "unique JSON string list"):
            install_obsidian_plugin(self.vault)

        self.assertFalse(self.plugin.exists())
        self.assertEqual("{}\n", configuration.read_text(encoding="utf-8"))

    def cli(self, cwd: Path, *arguments: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["kgdistiller", *arguments]), chdir(cwd), redirect_stdout(
            stdout
        ), redirect_stderr(stderr):
            status = main()
        return status, stdout.getvalue(), stderr.getvalue()

    def register(self) -> Path:
        use_temporary_home(self)
        unrelated = Path(self.temporary.name).resolve()
        status, _, stderr = self.cli(unrelated, "base", "add", str(self.vault), "--name", "notes")
        self.assertEqual(0, status, stderr)
        return unrelated

    def test_cli_installs_for_a_named_base_from_an_unrelated_directory(self) -> None:
        unrelated = self.register()
        status, stdout, stderr = self.cli(unrelated, "obsidian", "install", "--base", "notes")
        self.assertEqual(0, status, stderr)
        self.assertEqual("", stderr)
        result = json.loads(stdout)
        self.assertEqual("installed", result["status"])
        self.assertEqual(str(self.vault.resolve()), result["vault"])

    def test_cli_installs_for_the_base_containing_the_working_directory(self) -> None:
        self.register()
        status, stdout, stderr = self.cli(self.vault / ".obsidian", "obsidian", "install")
        self.assertEqual(0, status, stderr)
        self.assertEqual(str(self.vault.resolve()), json.loads(stdout)["vault"])

    def test_cli_refuses_outside_every_registered_base(self) -> None:
        unrelated = self.register()
        status, stdout, stderr = self.cli(unrelated, "obsidian", "install")
        self.assertEqual(1, status)
        self.assertEqual("", stdout)
        self.assertIn("not inside any registered base root", stderr)
        self.assertFalse(self.plugin.exists())


if __name__ == "__main__":
    unittest.main()
