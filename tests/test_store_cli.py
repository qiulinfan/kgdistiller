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
from types import SimpleNamespace
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kgdistiller.cli import main  # noqa: E402
from kgdistiller.embedding import EmbeddingError  # noqa: E402
from kgdistiller.store import StoreError  # noqa: E402


class StoreCliContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-store-cli-")
        self.root = Path(self.temporary.name).resolve()
        self.database = self.root / "local/knowledge.sqlite"
        self.portable = self.root / "portable"
        self.provider_profiles = {
            "primary": {
                "adapter": "deterministic-fixture",
                "model": "fixture-v1",
                "dimensions": 3,
                "base_url": "https://fixture.invalid/v1",
                "credential_env": "STORE_CLI_SECRET",
            },
            "secondary": {
                "adapter": "deterministic-fixture",
                "model": "fixture-v2",
                "dimensions": 4,
                "base_url": "https://fixture.invalid/v1",
                "credential_env": "STORE_CLI_OTHER_SECRET",
            },
        }
        self.runtime = SimpleNamespace(
            database=self.database,
            portable_store=self.portable,
            provider_profiles=self.provider_profiles,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def invoke(
        self,
        arguments: list[str],
        *,
        snapshot_result: dict | None = None,
        snapshot_error: StoreError | None = None,
        policy_result: dict | None = None,
        policy_error: EmbeddingError | None = None,
    ) -> tuple[int, str, str, mock.Mock, mock.Mock]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        snapshot = mock.Mock(return_value=snapshot_result or {"operation": "snapshot"})
        if snapshot_error is not None:
            snapshot.side_effect = snapshot_error
        policy = mock.Mock(return_value=policy_result or {"schema": "qlkg-embedding-policy-v1"})
        if policy_error is not None:
            policy.side_effect = policy_error
        argv = ["kgdistiller", "--repo-root", str(self.root), *arguments]
        with mock.patch.object(sys, "argv", argv), mock.patch(
            "kgdistiller.cli.resolve_runtime_config", return_value=self.runtime
        ), mock.patch("kgdistiller.cli.synchronize", return_value=(None, {}, None)), mock.patch(
            "kgdistiller.cli.load_state", return_value=object()
        ), mock.patch("kgdistiller.cli.ensure_database"), mock.patch(
            "kgdistiller.embedding.load_embedding_policy", policy
        ), mock.patch("kgdistiller.store.snapshot_store", snapshot), contextlib.redirect_stdout(
            stdout
        ), contextlib.redirect_stderr(stderr):
            status = main()
        return status, stdout.getvalue(), stderr.getvalue(), snapshot, policy

    def test_snapshot_default_missing_policy_is_unmanaged_compatible(self) -> None:
        status, stdout, stderr, snapshot, policy = self.invoke(
            ["store", "snapshot"],
            policy_error=EmbeddingError(
                "embedding-policy-not-found", "embedding policy does not exist"
            ),
        )

        self.assertEqual(0, status)
        self.assertEqual("", stderr)
        self.assertEqual("snapshot", json.loads(stdout)["operation"])
        policy.assert_called_once_with(
            (self.root / "knowledge/embedding-policy.json").resolve()
        )
        call = snapshot.call_args
        self.assertIsNone(call.kwargs["policy"])
        self.assertIsNone(call.kwargs["policy_path"])
        self.assertEqual(self.provider_profiles, call.kwargs["provider_configs"])
        self.assertFalse(call.kwargs["require_ready"])
        self.assertFalse(call.kwargs["allow_partial"])

    def test_explicit_missing_policy_is_a_structured_error(self) -> None:
        status, stdout, stderr, snapshot, policy = self.invoke(
            ["--embedding-policy", "config/missing.json", "store", "snapshot"],
            policy_error=EmbeddingError(
                "embedding-policy-not-found", "embedding policy does not exist"
            ),
        )

        self.assertEqual(1, status)
        self.assertEqual("", stdout)
        self.assertEqual("embedding-policy-not-found", json.loads(stderr)["code"])
        policy.assert_called_once_with((self.root / "config/missing.json").resolve())
        snapshot.assert_not_called()

    def test_snapshot_passes_full_policy_registry_and_readiness_mode(self) -> None:
        policy_payload = {
            "schema": "qlkg-embedding-policy-v1",
            "profiles": [{"name": "primary"}, {"name": "secondary"}],
        }
        status, _, _, snapshot, _ = self.invoke(
            ["--embedding-policy", "config/policy.json", "store", "snapshot", "--allow-partial"],
            policy_result=policy_payload,
        )

        self.assertEqual(0, status)
        call = snapshot.call_args
        self.assertEqual(policy_payload, call.kwargs["policy"])
        self.assertEqual((self.root / "config/policy.json").resolve(), call.kwargs["policy_path"])
        self.assertEqual(self.provider_profiles, call.kwargs["provider_configs"])
        self.assertFalse(call.kwargs["require_ready"])
        self.assertTrue(call.kwargs["allow_partial"])

    def test_readiness_block_is_receipt_on_stdout_with_exit_three(self) -> None:
        receipt = {
            "schema": "qlkg-store-operation-receipt-v1",
            "operation": "snapshot",
            "portable_status": "partial",
        }
        error = StoreError(
            "required embedding coverage is incomplete",
            code="coverage-blocked",
            receipt=receipt,
        )
        status, stdout, stderr, snapshot, _ = self.invoke(
            ["store", "snapshot", "--require-ready"],
            snapshot_error=error,
            policy_error=EmbeddingError(
                "embedding-policy-not-found", "embedding policy does not exist"
            ),
        )

        self.assertEqual(3, status)
        self.assertEqual(receipt, json.loads(stdout))
        self.assertEqual("", stderr)
        self.assertTrue(snapshot.call_args.kwargs["require_ready"])

    def test_unexpected_store_failure_is_bounded_and_secret_safe(self) -> None:
        sentinel = "store-cli-credential-sentinel"
        status, stdout, stderr, snapshot, _ = self.invoke(
            ["store", "snapshot"],
            policy_result={"schema": "qlkg-embedding-policy-v1"},
        )
        snapshot.side_effect = None
        self.assertEqual(0, status)
        self.assertNotIn(sentinel, stdout + stderr)

        with mock.patch.object(
            sys,
            "argv",
            ["kgdistiller", "--repo-root", str(self.root), "store", "snapshot"],
        ), mock.patch(
            "kgdistiller.cli.resolve_runtime_config", return_value=self.runtime
        ), mock.patch(
            "kgdistiller.embedding.load_embedding_policy",
            return_value={"schema": "qlkg-embedding-policy-v1"},
        ), mock.patch(
            "kgdistiller.cli.synchronize", side_effect=ValueError(sentinel)
        ), contextlib.redirect_stdout(
            io.StringIO()
        ), contextlib.redirect_stderr(
            error_output := io.StringIO()
        ):
            self.assertEqual(1, main())
        self.assertEqual("store-command-failed", json.loads(error_output.getvalue())["code"])
        self.assertNotIn(sentinel, error_output.getvalue())

    def test_verify_flag_and_materialize_registry_reach_the_core(self) -> None:
        verify_result = {
            "schema": "qlkg-store-operation-receipt-v1",
            "operation": "verify",
        }
        materialize_result = {
            "schema": "qlkg-store-operation-receipt-v1",
            "operation": "materialize",
        }

        for arguments, patch_target, result in (
            (
                ["store", "verify", "--require-ready"],
                "kgdistiller.store.verify_store",
                verify_result,
            ),
            (
                ["store", "materialize"],
                "kgdistiller.store.materialize_store",
                materialize_result,
            ),
        ):
            with self.subTest(command=arguments[-1]):
                output = io.StringIO()
                argv = ["kgdistiller", "--repo-root", str(self.root), *arguments]
                with mock.patch.object(sys, "argv", argv), mock.patch(
                    "kgdistiller.cli.resolve_runtime_config", return_value=self.runtime
                ), mock.patch(
                    patch_target, return_value=result
                ) as operation, contextlib.redirect_stdout(output):
                    self.assertEqual(0, main())
                self.assertEqual(result, json.loads(output.getvalue()))
                if arguments[1] == "verify":
                    operation.assert_called_once_with(self.portable, require_ready=True)
                else:
                    operation.assert_called_once_with(
                        self.portable,
                        self.database,
                        require_ready=False,
                        provider_configs=self.provider_profiles,
                    )

    def test_verify_readiness_miss_is_receipt_exit_three_but_integrity_failure_is_one(
        self,
    ) -> None:
        receipt = {
            "schema": "qlkg-store-operation-receipt-v1",
            "operation": "verify",
            "portable_status": "unmanaged",
            "retrieval_status": "retrieval-not-ready",
        }
        cases = (
            (
                StoreError(
                    "portable store is not retrieval-ready",
                    code="coverage-blocked",
                    receipt=receipt,
                ),
                3,
                receipt,
                None,
            ),
            (
                StoreError("portable store manifest digest mismatch"),
                1,
                None,
                "store-command-failed",
            ),
        )
        for error, expected_status, expected_stdout, expected_code in cases:
            with self.subTest(status=expected_status):
                stdout = io.StringIO()
                stderr = io.StringIO()
                argv = [
                    "kgdistiller",
                    "--repo-root",
                    str(self.root),
                    "store",
                    "verify",
                    "--require-ready",
                ]
                with mock.patch.object(sys, "argv", argv), mock.patch(
                    "kgdistiller.cli.resolve_runtime_config", return_value=self.runtime
                ), mock.patch(
                    "kgdistiller.store.verify_store", side_effect=error
                ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    self.assertEqual(expected_status, main())
                if expected_stdout is not None:
                    self.assertEqual(expected_stdout, json.loads(stdout.getvalue()))
                    self.assertEqual("", stderr.getvalue())
                else:
                    self.assertEqual("", stdout.getvalue())
                    self.assertEqual(expected_code, json.loads(stderr.getvalue())["code"])

    def test_materialize_require_ready_reaches_the_core(self) -> None:
        result = {
            "schema": "qlkg-store-operation-receipt-v1",
            "operation": "materialize",
        }
        output = io.StringIO()
        argv = [
            "kgdistiller",
            "--repo-root",
            str(self.root),
            "store",
            "materialize",
            "--require-ready",
        ]
        with mock.patch.object(sys, "argv", argv), mock.patch(
            "kgdistiller.cli.resolve_runtime_config", return_value=self.runtime
        ), mock.patch(
            "kgdistiller.store.materialize_store", return_value=result
        ) as materialize, contextlib.redirect_stdout(output):
            self.assertEqual(0, main())

        self.assertEqual(result, json.loads(output.getvalue()))
        materialize.assert_called_once_with(
            self.portable,
            self.database,
            require_ready=True,
            provider_configs=self.provider_profiles,
        )

    def test_mutually_exclusive_snapshot_flags_are_usage_error_without_writes(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join(
            value
            for value in (str(REPO_ROOT / "src"), environment.get("PYTHONPATH", ""))
            if value
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "kgdistiller",
                "--repo-root",
                str(self.root),
                "store",
                "snapshot",
                "--require-ready",
                "--allow-partial",
            ],
            cwd=self.root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(2, completed.returncode)
        self.assertEqual([], list(self.root.iterdir()))


if __name__ == "__main__":
    unittest.main()
