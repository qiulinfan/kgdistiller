from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kgdistiller.cli import main  # noqa: E402
from kgdistiller.obsidian_plugin import (  # noqa: E402
    INSTALL_SCHEMA,
    PLUGIN_FILES,
    ObsidianPluginError,
    install_obsidian_plugin,
)
from kgdistiller.vault_registry import register_vault  # noqa: E402


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
        settings = b'{"showSources":false,"label":"\xe7\x9f\xa5\xe8\xaf\x86"}\n'
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

    def test_cli_installs_for_explicit_vault(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        arguments = [
            "kgdistiller",
            "--repo-root",
            str(self.vault),
            "obsidian",
            "install",
        ]
        with patch.object(sys, "argv", arguments), redirect_stdout(
            stdout
        ), redirect_stderr(stderr):
            status = main()

        self.assertEqual(0, status, stderr.getvalue())
        self.assertEqual("installed", json.loads(stdout.getvalue())["status"])
        self.assertEqual("", stderr.getvalue())

    def test_cli_installs_for_registered_vault_from_any_directory(self) -> None:
        home = Path(self.temporary.name) / "user-state"
        register_vault(self.vault, name="notes", home=home)
        stdout = io.StringIO()
        stderr = io.StringIO()
        arguments = [
            "kgdistiller",
            "--kgdistiller-home",
            str(home),
            "--vault",
            "notes",
            "obsidian",
            "install",
        ]
        with patch.object(sys, "argv", arguments), redirect_stdout(
            stdout
        ), redirect_stderr(stderr):
            status = main()

        self.assertEqual(0, status, stderr.getvalue())
        self.assertEqual(
            str(self.vault.resolve()), json.loads(stdout.getvalue())["vault"]
        )


if __name__ == "__main__":
    unittest.main()
