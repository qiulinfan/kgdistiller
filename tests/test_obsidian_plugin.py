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
from kgdistiller.home import Base, add_base, resolve_base
from kgdistiller.obsidian_plugin import (
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
        self.unrelated = Path(self.temporary.name).resolve()
        self.vault = self.unrelated / "vault"
        (self.vault / ".obsidian").mkdir(parents=True)
        self.plugin = self.vault / ".obsidian/plugins/kgdistiller"
        self.settings = self.plugin / "data.json"
        self.community = self.vault / ".obsidian/community-plugins.json"
        use_temporary_home(self)
        add_base(self.vault, "notes")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def base(self) -> Base:
        return resolve_base("notes", self.unrelated)

    def install(self) -> dict:
        return install_obsidian_plugin(self.base())

    def read(self, path: Path) -> object:
        return json.loads(path.read_text(encoding="utf-8"))

    def snapshot(self) -> dict[str, bytes]:
        paths = [self.plugin / name for name in (*PLUGIN_FILES, "data.json")] + [self.community]
        return {str(path): path.read_bytes() for path in paths if path.exists()}

    def test_fresh_install_enables_the_plugin_and_hidden_indexing(self) -> None:
        installed = self.install()

        self.assertEqual("installed", installed["status"])
        self.assertEqual("notes", installed["base"])
        self.assertEqual(str(self.vault), installed["vault"])
        self.assertEqual("enabled", installed["hidden_indexing"])
        self.assertEqual("updated", installed["community_plugins"])
        self.assertEqual([], installed["warnings"])
        self.assertTrue(installed["reload_required"])
        self.assertEqual(
            {"status", "plugin_id", "plugin_version", "base", "vault", "plugin_root", "files",
             "hidden_indexing", "community_plugins", "warnings", "reload_required"},
            set(installed),
        )
        self.assertEqual(list(PLUGIN_FILES), [item["path"] for item in installed["files"]])
        for name in PLUGIN_FILES:
            self.assertGreater((self.plugin / name).stat().st_size, 0)
        self.assertEqual(["kgdistiller"], self.read(self.community))
        self.assertEqual({"hiddenKnowledgeEnabled": True}, self.read(self.settings))

    def test_second_run_is_current(self) -> None:
        self.install()
        current = self.install()
        self.assertEqual("current", current["status"])
        self.assertEqual("current", current["hidden_indexing"])
        self.assertEqual("current", current["community_plugins"])
        self.assertFalse(current["reload_required"])

    def test_existing_settings_are_kept_and_hidden_indexing_is_enabled(self) -> None:
        self.install()
        self.settings.write_text(
            json.dumps({"hiddenKnowledgeEnabled": False, "showDrafts": False, "note": "测度"},
                       ensure_ascii=False),
            encoding="utf-8",
        )
        result = self.install()
        self.assertEqual("current", result["status"])
        self.assertEqual("enabled", result["hidden_indexing"])
        self.assertTrue(result["reload_required"])
        self.assertEqual(
            {"hiddenKnowledgeEnabled": True, "showDrafts": False, "note": "测度"},
            self.read(self.settings),
        )
        self.assertEqual(
            ["hiddenKnowledgeEnabled", "showDrafts", "note"], list(self.read(self.settings))
        )
        self.assertIn("测度", self.settings.read_text(encoding="utf-8"))

    def test_a_truthy_non_boolean_flag_is_rewritten_as_true(self) -> None:
        self.install()
        for value in (1, 1.0, "true"):
            with self.subTest(value=value):
                self.settings.write_text(
                    json.dumps({"showDrafts": False, "hiddenKnowledgeEnabled": value}),
                    encoding="utf-8",
                )
                result = self.install()
                self.assertEqual("enabled", result["hidden_indexing"])
                self.assertTrue(result["reload_required"])
                stored = self.read(self.settings)
                self.assertIs(True, stored["hiddenKnowledgeEnabled"])
                self.assertEqual({"showDrafts": False, "hiddenKnowledgeEnabled": True}, stored)

    def test_a_vault_never_opened_in_obsidian_is_refused_with_the_remedy(self) -> None:
        (self.vault / ".obsidian").rmdir()
        with self.assertRaisesRegex(ObsidianPluginError, "open this folder as a vault in Obsidian once"):
            self.install()
        self.assertFalse((self.vault / ".obsidian").exists())

    @unittest.skipIf(sys.platform == "win32", "directory symlinks need extra privileges on Windows")
    def test_a_symlinked_knowledge_tree_is_refused_without_writes(self) -> None:
        elsewhere = self.unrelated / "elsewhere"
        (self.vault / ".knowledge").rename(elsewhere)
        (self.vault / ".knowledge").symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaisesRegex(ObsidianPluginError, "must not be a symlink"):
            self.install()
        self.assertFalse((self.vault / ".obsidian/plugins").exists())
        self.assertFalse(self.community.exists())

    def test_invalid_or_non_object_settings_are_refused_without_writes(self) -> None:
        self.install()
        (self.plugin / "main.js").write_text("stale", encoding="utf-8")
        for content in (b"{not json", b"[1, 2]\n", b"\xff\xfe"):
            with self.subTest(content=content):
                self.settings.write_bytes(content)
                before = self.snapshot()
                with self.assertRaisesRegex(ObsidianPluginError, "data.json must be a JSON object"):
                    self.install()
                self.assertEqual(before, self.snapshot())

    def test_a_differing_bundle_is_updated(self) -> None:
        self.install()
        (self.plugin / "main.js").write_text("stale", encoding="utf-8")
        self.settings.write_text('{"showDrafts": false, "hiddenKnowledgeEnabled": true}\n',
                                 encoding="utf-8")
        updated = self.install()
        self.assertEqual("updated", updated["status"])
        self.assertEqual("current", updated["hidden_indexing"])
        self.assertTrue(updated["reload_required"])
        self.assertNotEqual(b"stale", (self.plugin / "main.js").read_bytes())
        self.assertEqual({"showDrafts": False, "hiddenKnowledgeEnabled": True}, self.read(self.settings))
        self.assertEqual(
            sorted([*PLUGIN_FILES, "data.json"]), sorted(path.name for path in self.plugin.iterdir())
        )
        self.assertEqual(["kgdistiller"], [path.name for path in self.plugin.parent.iterdir()])

    def test_relative_link_format_warns(self) -> None:
        app = self.vault / ".obsidian/app.json"
        cases = (
            (None, 0),
            ('{"newLinkFormat": "absolute"}', 0),
            ('{"newLinkFormat": "shortest", "other": 1}', 0),
            ('{"newLinkFormat": "relative"}', 1),
            ("{broken", 1),
            ('"relative"', 1),
        )
        for content, count in cases:
            with self.subTest(content=content):
                if content is None:
                    app.unlink(missing_ok=True)
                else:
                    app.write_text(content, encoding="utf-8")
                warnings = self.install()["warnings"]
                self.assertEqual(count, len(warnings), warnings)
                if content == '{"newLinkFormat": "relative"}':
                    self.assertIn("newLinkFormat", warnings[0])
                    self.assertIn("../", warnings[0])
                elif count:
                    self.assertEqual(["cannot read .obsidian/app.json to check newLinkFormat"], warnings)

    def test_rejects_unmanaged_plugin_files(self) -> None:
        self.plugin.mkdir(parents=True)
        (self.plugin / "user-note.txt").write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(ObsidianPluginError, "unmanaged files"):
            self.install()
        self.assertEqual(
            "keep", (self.plugin / "user-note.txt").read_text(encoding="utf-8")
        )
        self.assertEqual(["user-note.txt"], [path.name for path in self.plugin.iterdir()])

    def test_invalid_enable_configuration_rolls_back_new_install(self) -> None:
        self.community.write_text("{}\n", encoding="utf-8")

        with self.assertRaisesRegex(ObsidianPluginError, "unique JSON string list"):
            self.install()

        self.assertFalse(self.plugin.exists())
        self.assertEqual("{}\n", self.community.read_text(encoding="utf-8"))

    def test_invalid_enable_configuration_restores_existing_settings(self) -> None:
        self.install()
        original = b'{"hiddenKnowledgeEnabled": false}\n'
        self.settings.write_bytes(original)
        self.community.write_text("{}\n", encoding="utf-8")

        with self.assertRaisesRegex(ObsidianPluginError, "unique JSON string list"):
            self.install()

        self.assertEqual(original, self.settings.read_bytes())

    def cli(self, cwd: Path, *arguments: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["kgdistiller", *arguments]), chdir(cwd), redirect_stdout(
            stdout
        ), redirect_stderr(stderr):
            status = main()
        return status, stdout.getvalue(), stderr.getvalue()

    def test_cli_installs_for_a_named_base_from_an_unrelated_directory(self) -> None:
        status, stdout, stderr = self.cli(self.unrelated, "obsidian", "install", "--base", "notes")
        self.assertEqual(0, status, stderr)
        self.assertEqual("", stderr)
        result = json.loads(stdout)
        self.assertEqual("installed", result["status"])
        self.assertEqual("notes", result["base"])
        self.assertEqual(str(self.vault), result["vault"])
        self.assertEqual({"hiddenKnowledgeEnabled": True}, self.read(self.settings))

    def test_cli_installs_for_the_base_containing_the_working_directory(self) -> None:
        status, stdout, stderr = self.cli(self.vault / ".obsidian", "obsidian", "install")
        self.assertEqual(0, status, stderr)
        self.assertEqual(str(self.vault), json.loads(stdout)["vault"])

    def test_cli_refuses_an_unknown_base(self) -> None:
        status, stdout, stderr = self.cli(self.vault, "obsidian", "install", "--base", "missing")
        self.assertEqual(1, status)
        self.assertEqual("", stdout)
        self.assertIn("unknown base 'missing'", stderr)
        self.assertFalse((self.vault / ".obsidian/plugins").exists())

    def test_cli_refuses_outside_every_registered_base(self) -> None:
        status, stdout, stderr = self.cli(self.unrelated, "obsidian", "install")
        self.assertEqual(1, status)
        self.assertEqual("", stdout)
        self.assertIn("not inside any registered base root", stderr)
        self.assertFalse((self.vault / ".obsidian/plugins").exists())


if __name__ == "__main__":
    unittest.main()
