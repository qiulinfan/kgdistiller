from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kgdistiller import cli, vaults as vault_module
from kgdistiller.contracts import canonical_json, sha256_json, validate_contract
from kgdistiller.vaults import (
    REGISTRY_SCHEMA,
    REPORT_SCHEMA,
    VAULT_SCHEMA,
    VaultError,
    add_vault,
    doctor_vaults,
    init_vault,
    kgdistiller_home,
    list_vaults,
    load_registry,
    locate_file,
    remove_vault,
)


class VaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-vault-test-")
        # The contract requires canonical paths. Hosted runners may expose the
        # same temporary directory through macOS /var or a Windows 8.3 alias.
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "machine-home"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _manifest(self, vault_id: str, label: str = "Test Vault") -> dict[str, object]:
        return {
            "schema": VAULT_SCHEMA,
            "id": vault_id,
            "label": label,
            "description": "",
            "concept_root": "Knowledge/Concepts",
            "field_root": "Knowledge/Fields",
            "topic_root": "Knowledge/Topics",
            "source_include": ["**/*.md", "**/*.typ", "**/*.tex"],
            "source_exclude": ["Knowledge/**", ".kgdistiller/**", "private/**"],
        }

    def _make_vault(self, name: str, vault_id: str, label: str = "Test Vault") -> Path:
        root = self.root / name
        for relative in (
            ".kgdistiller/sources",
            ".kgdistiller/graph",
            ".kgdistiller/build",
            "Knowledge/Concepts",
            "Knowledge/Fields",
            "Knowledge/Topics",
        ):
            (root / relative).mkdir(parents=True, exist_ok=True)
        (root / ".kgdistiller/vault.json").write_text(
            json.dumps(self._manifest(vault_id, label), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        return root

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

    def _cli(self, *arguments: str) -> tuple[int, dict[str, object], str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.dict(
                os.environ, {"KGDISTILLER_HOME": str(self.home)}, clear=False
            ),
            mock.patch.object(sys, "argv", ["kgdistiller", *arguments]),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            result = cli.main()
        output = stdout.getvalue()
        return result, json.loads(output) if output else {}, stderr.getvalue()

    def test_init_writes_portable_layout_and_canonical_registry(self) -> None:
        vault_root = self.root / "Mathematics"
        report = init_vault(
            vault_root, vault_id="math", label="Mathematics", home=self.home
        )

        self.assertEqual(report["schema"], REPORT_SCHEMA)
        self.assertEqual(report["action"], "init")
        self.assertEqual(report["status"], "ok")
        manifest = json.loads(
            (vault_root / ".kgdistiller/vault.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["schema"], VAULT_SCHEMA)
        self.assertEqual(manifest["id"], "math")
        for relative in (
            ".kgdistiller/sources",
            ".kgdistiller/graph",
            ".kgdistiller/build",
            "Knowledge/Concepts",
            "Knowledge/Fields",
            "Knowledge/Topics",
        ):
            self.assertTrue((vault_root / relative).is_dir(), relative)

        registry_path = self.home / "vaults.json"
        registry_bytes = registry_path.read_bytes()
        registry = json.loads(registry_bytes.decode("utf-8"))
        self.assertEqual(registry_bytes, canonical_json(registry).encode("utf-8"))
        self.assertEqual(registry["schema"], REGISTRY_SCHEMA)
        self.assertEqual(report["registry_generation"], sha256_json(registry))
        self.assertEqual(validate_contract(registry), registry)

        listed = list_vaults(home=self.home)
        self.assertEqual(listed["registry_generation"], report["registry_generation"])
        self.assertEqual(
            listed["result"]["vaults"],
            [{"id": "math", "label": "Mathematics", "path": str(vault_root)}],
        )

    def test_add_sorts_registry_and_remove_never_deletes_vault(self) -> None:
        zeta = self._make_vault("zeta", "zeta", "Zeta")
        alpha = self._make_vault("alpha", "alpha", "Alpha")
        add_vault(zeta, home=self.home)
        add_vault(alpha, home=self.home)

        listed = list_vaults(home=self.home)
        self.assertEqual(
            [item["id"] for item in listed["result"]["vaults"]],
            ["alpha", "zeta"],
        )
        original_manifest = (alpha / ".kgdistiller/vault.json").read_bytes()
        removed = remove_vault("alpha", home=self.home)
        self.assertEqual(removed["result"], {"kind": "removed-vault", "id": "alpha"})
        self.assertEqual((alpha / ".kgdistiller/vault.json").read_bytes(), original_manifest)
        self.assertTrue(alpha.is_dir())
        self.assertEqual(
            [item.id for item in load_registry(self.home).registrations], ["zeta"]
        )

    def test_remove_allows_cleanup_of_missing_registered_vault(self) -> None:
        vault = self._make_vault("temporary", "temporary")
        add_vault(vault, home=self.home)
        (vault / ".kgdistiller/vault.json").unlink()

        with self.assertRaisesRegex(VaultError, "missing vault manifest"):
            list_vaults(home=self.home)
        report = remove_vault("temporary", home=self.home)
        self.assertEqual(report["status"], "ok")
        self.assertTrue(vault.is_dir())

    def test_init_rejects_user_content_and_existing_manifest_without_overwrite(self) -> None:
        occupied = self.root / "occupied"
        occupied.mkdir()
        user_file = occupied / "notes.md"
        user_file.write_text("keep me", encoding="utf-8")
        with self.assertRaisesRegex(VaultError, "empty or compatible"):
            init_vault(occupied, vault_id="occupied", label="Occupied", home=self.home)
        self.assertEqual(user_file.read_text(encoding="utf-8"), "keep me")

        existing = self._make_vault("existing", "existing")
        manifest_path = existing / ".kgdistiller/vault.json"
        before = manifest_path.read_bytes()
        with self.assertRaisesRegex(VaultError, "already exists"):
            init_vault(existing, vault_id="existing", label="Changed", home=self.home)
        self.assertEqual(manifest_path.read_bytes(), before)

    def test_registry_rejects_duplicate_overlapping_and_mismatched_vaults(self) -> None:
        outer = self._make_vault("outer", "outer")
        inner = self._make_vault("outer/nested", "inner")
        self.home.mkdir(parents=True)
        registry_path = self.home / "vaults.json"
        registry_path.write_text(
            json.dumps(
                {
                    "schema": REGISTRY_SCHEMA,
                    "vaults": [
                        {"id": "outer", "path": str(outer)},
                        {"id": "inner", "path": str(inner)},
                    ],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(VaultError, "overlap"):
            load_registry(self.home)

        registry_path.write_text(
            json.dumps(
                {
                    "schema": REGISTRY_SCHEMA,
                    "vaults": [{"id": "wrong", "path": str(outer)}],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(VaultError, "does not match manifest"):
            load_registry(self.home)

    def test_locate_requires_real_included_unmanaged_file(self) -> None:
        vault = self._make_vault("routing", "routing", "Routing")
        included = vault / "notes" / "chapter.md"
        included.parent.mkdir()
        included.write_text("source", encoding="utf-8")
        private = vault / "private" / "secret.md"
        private.parent.mkdir()
        private.write_text("secret", encoding="utf-8")
        managed = vault / "Knowledge/Concepts/Concept.md"
        managed.write_text("managed", encoding="utf-8")
        unsupported = vault / "notes/data.txt"
        unsupported.write_text("data", encoding="utf-8")
        add_vault(vault, home=self.home)

        report = locate_file(included, home=self.home)
        self.assertEqual(report["result"]["relative_path"], "notes/chapter.md")
        self.assertEqual(report["result"]["vault"]["id"], "routing")
        with self.assertRaisesRegex(VaultError, "source_exclude"):
            locate_file(private, home=self.home)
        with self.assertRaisesRegex(VaultError, "managed Vault content"):
            locate_file(managed, home=self.home)
        with self.assertRaisesRegex(VaultError, "source_include"):
            locate_file(unsupported, home=self.home)
        outside = self.root / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        with self.assertRaisesRegex(VaultError, "not inside a registered Vault"):
            locate_file(outside, home=self.home)

    def test_locate_rejects_symlink_escape_when_supported(self) -> None:
        vault = self._make_vault("linked", "linked")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "escaped.md").write_text("outside", encoding="utf-8")
        link = vault / "linked-sources"
        if not self._make_directory_link(link, outside):
            self.skipTest("directory symlinks and junctions are unavailable")
        add_vault(vault, home=self.home)
        with self.assertRaisesRegex(VaultError, "symlink or reparse"):
            locate_file(link / "escaped.md", home=self.home)

    def test_creation_rejects_unsafe_ancestor_without_writing_through_it(self) -> None:
        outside = self.root / "outside-ancestor"
        outside.mkdir()
        linked_parent = self.root / "linked-parent"
        if not self._make_directory_link(linked_parent, outside):
            self.skipTest("directory symlinks or junctions are unavailable")

        with self.assertRaisesRegex(VaultError, "non-reparse directory components"):
            kgdistiller_home(linked_parent / "new-home", create=True)
        self.assertFalse((outside / "new-home").exists())

        with self.assertRaisesRegex(VaultError, "non-reparse directory components"):
            init_vault(
                linked_parent / "new-vault",
                vault_id="unsafe",
                label="Unsafe",
                home=self.home,
            )
        self.assertFalse((outside / "new-vault").exists())
        self.assertFalse(self.home.exists())

    def test_registry_lock_link_is_rejected_without_modifying_target(self) -> None:
        kgdistiller_home(self.home, create=True)
        target = self.root / "lock-target.txt"
        original = b"do not modify this target"
        target.write_bytes(original)
        lock_path = self.home / "vaults.lock"
        try:
            lock_path.symlink_to(target)
        except (OSError, NotImplementedError):
            try:
                os.link(target, lock_path)
            except OSError:
                self.skipTest("file links are unavailable")

        with self.assertRaisesRegex(VaultError, "registry lock"):
            with vault_module._registry_lock(self.home):
                self.fail("unsafe lock was acquired")
        self.assertEqual(target.read_bytes(), original)

    def test_custom_managed_roots_do_not_reserve_all_of_knowledge(self) -> None:
        vault = self._make_vault("custom-roots", "custom-roots", "Custom Roots")
        manifest_path = vault / ".kgdistiller/vault.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(
            {
                "concept_root": "Managed/Concepts",
                "field_root": "Managed/Fields",
                "topic_root": "Managed/Topics",
                "source_exclude": [".kgdistiller/**"],
            }
        )
        for relative in ("Managed/Concepts", "Managed/Fields", "Managed/Topics"):
            (vault / relative).mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        ordinary_knowledge = vault / "Knowledge" / "ordinary.md"
        ordinary_knowledge.write_text("ordinary source", encoding="utf-8")
        managed = vault / "Managed/Concepts/managed.md"
        managed.write_text("managed", encoding="utf-8")
        add_vault(vault, home=self.home)

        located = locate_file(ordinary_knowledge, home=self.home)
        self.assertEqual(located["result"]["relative_path"], "Knowledge/ordinary.md")
        with self.assertRaisesRegex(VaultError, "managed Vault content"):
            locate_file(managed, home=self.home)

    def test_windows_glob_matching_case_normalizes_path_and_pattern(self) -> None:
        with mock.patch(
            "kgdistiller.vaults._windows_path_semantics", return_value=True
        ):
            self.assertTrue(
                vault_module._glob_matches("NOTES/Chapter.MD", "notes/**/*.md")
            )
            self.assertTrue(
                vault_module._glob_matches(
                    "notes/PRIVATE/Secret.TEX", "Notes/Private/**/*.tex"
                )
            )
        with mock.patch(
            "kgdistiller.vaults._windows_path_semantics", return_value=False
        ):
            self.assertFalse(
                vault_module._glob_matches("NOTES/Chapter.MD", "notes/**/*.md")
            )

    def test_doctor_reports_bad_manifest_and_cli_returns_nonzero(self) -> None:
        vault = self._make_vault("doctor", "doctor", "Doctor")
        add_vault(vault, home=self.home)
        (vault / ".kgdistiller/vault.json").write_text("{}", encoding="utf-8")

        report = doctor_vaults(home=self.home)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["result"]["counts"], {"checked": 1, "healthy": 0, "failed": 1})
        self.assertEqual(report["result"]["vaults"][0]["status"], "error")
        self.assertEqual(validate_contract(report), report)

        code, output, error = self._cli("vault", "doctor")
        self.assertEqual(code, 1)
        self.assertEqual(output["status"], "failed")
        self.assertEqual(error, "")

    def test_registry_replace_failure_preserves_previous_bytes(self) -> None:
        vault = self._make_vault("atomic", "atomic")
        add_vault(vault, home=self.home)
        registry_path = self.home / "vaults.json"
        before = registry_path.read_bytes()
        with mock.patch("kgdistiller.vaults.os.replace", side_effect=OSError("injected")):
            with self.assertRaisesRegex(OSError, "injected"):
                remove_vault("atomic", home=self.home)
        self.assertEqual(registry_path.read_bytes(), before)

    def test_cli_vault_commands_work_from_arbitrary_cwd(self) -> None:
        vault = self.root / "cli-vault"
        unrelated = self.root / "unrelated-cwd"
        unrelated.mkdir()
        previous = Path.cwd()
        try:
            os.chdir(unrelated)
            code, output, error = self._cli(
                "vault",
                "init",
                str(vault),
                "--id",
                "cli",
                "--label",
                "CLI Vault",
            )
            self.assertEqual((code, error), (0, ""))
            self.assertEqual(output["action"], "init")
            source = vault / "source.typ"
            source.write_text("source", encoding="utf-8")
            code, output, error = self._cli("vault", "locate", str(source))
            self.assertEqual((code, error), (0, ""))
            self.assertEqual(output["result"]["relative_path"], "source.typ")
        finally:
            os.chdir(previous)

    def test_registry_and_manifest_are_closed_and_bounded(self) -> None:
        vault = self._make_vault("closed", "closed")
        manifest_path = vault / ".kgdistiller/vault.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["unknown"] = True
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(VaultError, "unknown property"):
            add_vault(vault, home=self.home)

        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "vaults.json").write_bytes(b" " * (1024 * 1024 + 1))
        with self.assertRaisesRegex(VaultError, "exceeds 1048576 bytes"):
            load_registry(self.home)


if __name__ == "__main__":
    unittest.main()
