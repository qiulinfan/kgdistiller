from __future__ import annotations

import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kgdistiller.cli import main  # noqa: E402
from kgdistiller.vault_registry import (  # noqa: E402
    HOME_ENVIRONMENT,
    REGISTRY_SCHEMA,
    VAULT_ENVIRONMENT,
    VAULT_MANIFEST,
    VAULT_SCHEMA,
    VaultRegistryError,
    doctor_vaults,
    ensure_vault_manifest,
    kgdistiller_home,
    list_vaults,
    load_registry,
    register_vault,
    resolve_registered_vault,
    resolve_repo_root,
    set_default_vault,
    unregister_vault,
)
from tests.test_query import write_fixture_graph  # noqa: E402


class VaultRegistryTest(unittest.TestCase):
    def test_register_resolve_and_unregister_unicode_vault(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-vault-registry-") as raw:
            base = Path(raw)
            home = base / "用户状态"
            vault = base / "数学 笔记"
            vault.mkdir()

            result = register_vault(vault, name="数学", home=home)
            manifest = json.loads(
                (vault / VAULT_MANIFEST).read_text(encoding="utf-8")
            )
            registry = load_registry(home)

            self.assertEqual("registered", result["status"])
            self.assertEqual(VAULT_SCHEMA, manifest["schema"])
            self.assertEqual(REGISTRY_SCHEMA, registry["schema"])
            self.assertEqual(manifest["vault_id"], registry["default_vault_id"])
            self.assertEqual(vault.resolve(), resolve_registered_vault("数学", home))
            self.assertEqual(
                vault.resolve(),
                resolve_registered_vault(manifest["vault_id"], home),
            )
            if os.name != "nt":
                self.assertEqual(0o700, stat.S_IMODE(home.stat().st_mode))
                self.assertEqual(
                    0o600,
                    stat.S_IMODE((home / "vaults.json").stat().st_mode),
                )
                self.assertEqual(
                    0o600,
                    stat.S_IMODE((home / "registry.lock").stat().st_mode),
                )

            removed = unregister_vault("数学", home)
            self.assertEqual("unregistered", removed["status"])
            self.assertTrue((vault / VAULT_MANIFEST).is_file())
            self.assertEqual([], load_registry(home)["vaults"])
            self.assertIsNone(load_registry(home)["default_vault_id"])

    def test_resolution_is_independent_of_current_working_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-vault-resolution-") as raw:
            base = Path(raw)
            home = base / "home"
            first = base / "first"
            second = base / "second"
            outside = base / "outside"
            nested = second / "notes/chapter"
            for path in (first, second, outside, nested):
                path.mkdir(parents=True, exist_ok=True)
            first_record = register_vault(first, name="first", home=home)["vault"]
            register_vault(second, name="second", home=home)

            self.assertEqual(
                first.resolve(),
                resolve_repo_root(
                    explicit_repo_root=None,
                    explicit_vault=None,
                    cwd=outside,
                    home=home,
                ),
            )
            self.assertEqual(
                second.resolve(),
                resolve_repo_root(
                    explicit_repo_root=None,
                    explicit_vault="second",
                    cwd=outside,
                    home=home,
                ),
            )
            self.assertEqual(
                second.resolve(),
                resolve_repo_root(
                    explicit_repo_root=None,
                    explicit_vault=None,
                    cwd=nested,
                    home=home,
                ),
            )

            set_default_vault("second", home)
            self.assertEqual(
                second.resolve(),
                resolve_repo_root(
                    explicit_repo_root=None,
                    explicit_vault=None,
                    cwd=outside,
                    home=home,
                ),
            )
            self.assertEqual(
                outside.resolve(),
                resolve_repo_root(
                    explicit_repo_root=None,
                    explicit_vault=None,
                    cwd=outside,
                    home=home,
                    use_default=False,
                ),
            )
            set_default_vault(None, home)
            self.assertEqual(
                outside.resolve(),
                resolve_repo_root(
                    explicit_repo_root=None,
                    explicit_vault=None,
                    cwd=outside,
                    home=home,
                ),
            )
            self.assertEqual(first_record["id"], load_registry(home)["vaults"][0]["id"])

    def test_environment_selectors_and_explicit_repo_root_precedence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-vault-environment-") as raw:
            base = Path(raw)
            home = base / "home"
            vault = base / "vault"
            explicit = base / "explicit"
            vault.mkdir()
            explicit.mkdir()
            register_vault(vault, name="research", home=home)

            with patch.dict(
                os.environ,
                {
                    HOME_ENVIRONMENT: str(home),
                    VAULT_ENVIRONMENT: "research",
                },
                clear=False,
            ):
                self.assertEqual(home.resolve(), kgdistiller_home())
                self.assertEqual(
                    vault.resolve(),
                    resolve_repo_root(
                        explicit_repo_root=None,
                        explicit_vault=None,
                        cwd=explicit,
                    ),
                )
                self.assertEqual(
                    explicit.resolve(),
                    resolve_repo_root(
                        explicit_repo_root=explicit,
                        explicit_vault=None,
                        cwd=vault,
                    ),
                )
            with self.assertRaisesRegex(VaultRegistryError, "absolute path"):
                kgdistiller_home(Path("relative-state"))
            with self.assertRaisesRegex(VaultRegistryError, "cannot be combined"):
                resolve_repo_root(
                    explicit_repo_root=explicit,
                    explicit_vault="research",
                    home=home,
                )

    def test_registration_conflicts_and_explicit_relocation_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-vault-conflict-") as raw:
            base = Path(raw)
            home = base / "home"
            original = base / "original"
            copied = base / "copied"
            other = base / "other"
            for path in (original, copied, other):
                path.mkdir()
            register_vault(original, name="main", home=home)
            (copied / "knowledge").mkdir()
            shutil.copyfile(
                original / VAULT_MANIFEST,
                copied / VAULT_MANIFEST,
            )

            with self.assertRaisesRegex(VaultRegistryError, "another existing path"):
                register_vault(copied, name="main", home=home)
            relocated = register_vault(
                copied,
                name="main",
                home=home,
                replace=True,
            )
            self.assertEqual("updated", relocated["status"])
            self.assertEqual(copied.resolve(), resolve_registered_vault("main", home))

            ensure_vault_manifest(other)
            with self.assertRaisesRegex(VaultRegistryError, "belongs to another vault"):
                register_vault(other, name="main", home=home)
            self.assertEqual(1, len(load_registry(home)["vaults"]))

    def test_invalid_registry_and_identity_mismatch_are_reported_without_repair(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-vault-invalid-") as raw:
            base = Path(raw)
            home = base / "home"
            vault = base / "vault"
            vault.mkdir()
            record = register_vault(vault, name="main", home=home)["vault"]
            manifest_path = vault / VAULT_MANIFEST
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["vault_id"] = "00000000-0000-4000-8000-000000000000"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            report = doctor_vaults("main", home)
            self.assertEqual("error", report["status"])
            self.assertEqual(1, report["errors"])
            self.assertEqual(record["id"], load_registry(home)["vaults"][0]["id"])

            (home / "vaults.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(VaultRegistryError, "unsupported fields"):
                list_vaults(home)


class VaultRegistryCliTest(unittest.TestCase):
    def run_cli(self, *arguments: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["kgdistiller", *arguments]), redirect_stdout(
            stdout
        ), redirect_stderr(stderr):
            status = main()
        return status, stdout.getvalue(), stderr.getvalue()

    def test_global_cli_registers_and_queries_from_an_unrelated_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-vault-cli-") as raw:
            base = Path(raw)
            home = base / "state"
            vault = base / "知识库"
            vault.mkdir()
            write_fixture_graph(vault)

            status, output, error = self.run_cli(
                "--kgdistiller-home",
                str(home),
                "vault",
                "register",
                str(vault),
                "--name",
                "math",
            )
            self.assertEqual(0, status, error)
            self.assertEqual("registered", json.loads(output)["status"])

            status, output, error = self.run_cli(
                "--kgdistiller-home",
                str(home),
                "--vault",
                "math",
                "agent",
                "status",
            )
            self.assertEqual(0, status, error)
            self.assertEqual("json-memory", json.loads(output)["backend"])

            status, output, error = self.run_cli(
                "--kgdistiller-home",
                str(home),
                "vault",
                "list",
            )
            self.assertEqual(0, status, error)
            self.assertEqual("math", json.loads(output)["vaults"][0]["name"])

            status, output, error = self.run_cli(
                "--kgdistiller-home",
                str(home),
                "vault",
                "unregister",
                "math",
            )
            self.assertEqual(0, status, error)
            self.assertEqual("unregistered", json.loads(output)["status"])
            self.assertTrue((vault / VAULT_MANIFEST).is_file())


if __name__ == "__main__":
    unittest.main()
