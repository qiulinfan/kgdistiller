from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "smoke_multivault.py"


class MultiVaultSmokeTests(unittest.TestCase):
    def test_disposable_multivault_user_path_is_real_closed_and_path_free(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-smoke-test-") as temporary:
            root = Path(temporary).resolve()
            workspace = root / "workspace"
            workspace.mkdir()
            output = root / "summary.json"
            environment = os.environ.copy()
            environment.pop("KGDISTILLER_HOME", None)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--workspace",
                    str(workspace),
                    "--json-output",
                    str(output),
                ],
                cwd=str(ROOT),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=240,
                check=False,
            )
            stdout = completed.stdout.decode("utf-8", errors="strict")
            stderr = completed.stderr.decode("utf-8", errors="strict")
            self.assertEqual(0, completed.returncode, (stdout, stderr))
            self.assertEqual("", stderr)
            payload = json.loads(stdout)
            self.assertEqual(
                {
                    "schema",
                    "status",
                    "python_module_invocation",
                    "workspace_mode",
                    "formats",
                    "vault_count",
                    "api_route_count",
                    "steps",
                    "cleanup",
                    "error",
                },
                set(payload),
            )
            self.assertEqual("kgdistiller-multivault-smoke-v1", payload["schema"])
            self.assertEqual("passed", payload["status"])
            self.assertIs(payload["python_module_invocation"], True)
            self.assertEqual("supplied", payload["workspace_mode"])
            self.assertEqual(["markdown", "typst", "latex"], payload["formats"])
            self.assertEqual(2, payload["vault_count"])
            self.assertEqual(12, payload["api_route_count"])
            self.assertEqual("complete", payload["cleanup"])
            self.assertIsNone(payload["error"])
            self.assertEqual(
                [
                    "vault-setup",
                    "source-archive",
                    "transactional-ingest",
                    "native-notes",
                    "source-change",
                    "federated-recall",
                    "packaged-api",
                    "portable-snapshot",
                    "vault-move",
                    "incomplete-vault",
                ],
                [item["name"] for item in payload["steps"]],
            )
            for step in payload["steps"]:
                self.assertEqual(
                    {"name", "status", "duration_ms", "checks"}, set(step)
                )
                self.assertEqual("passed", step["status"])
                self.assertIsInstance(step["duration_ms"], int)
                self.assertGreaterEqual(step["duration_ms"], 0)
                self.assertGreater(step["checks"], 0)
            self.assertEqual(payload, json.loads(output.read_text(encoding="utf-8")))
            serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            self.assertNotIn(str(root), serialized)
            self.assertTrue(workspace.is_dir())
            self.assertEqual([], list(workspace.iterdir()))


if __name__ == "__main__":
    unittest.main()
