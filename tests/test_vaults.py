from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kgdistiller import cli
from kgdistiller.contracts import canonical_json, sha256_json, validate_contract
from kgdistiller.vaults import (
    REGISTRY_SCHEMA,
    REPORT_SCHEMA,
    VAULT_SCHEMA,
    VaultError,
    add_vault,
    doctor_vaults,
    init_vault,
    list_vaults,
    load_registry,
    locate_file,
    remove_vault,
)


class VaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-vault-test-")
        self.root = Path(self.temporary.name)
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
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlinks are unavailable")
        add_vault(vault, home=self.home)
        with self.assertRaisesRegex(VaultError, "symlink or reparse"):
            locate_file(link / "escaped.md", home=self.home)

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
