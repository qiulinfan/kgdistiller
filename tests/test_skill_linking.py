"""Native Skill linking stays product-owned and does not install agents."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
RUNTIMES = ("claude", "codex", "opencode", "omp")


def powershell_ready():
    if not shutil.which("pwsh"):
        return False
    try:
        result = subprocess.run(["pwsh", "-NoLogo", "-NoProfile", "-Command", 'Write-Output "ready"'],
                                check=False, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        # This optional capability probe must not abort discovery of all tests.
        # Ready runtimes still execute the real PowerShell linker test class.
        return False
    return result.returncode == 0 and "ready" in result.stdout


PWSH_READY = powershell_ready()


class PowerShellReadinessTests(unittest.TestCase):
    def test_unavailable_startup_does_not_abort_test_discovery(self):
        for error in (OSError("runtime unavailable"), subprocess.TimeoutExpired("pwsh", 15)):
            with self.subTest(error=type(error).__name__), patch.object(shutil, "which", return_value="pwsh"), \
                    patch.object(subprocess, "run", side_effect=error):
                self.assertFalse(powershell_ready())

    def test_successful_startup_keeps_real_linker_tests_enabled(self):
        with patch.object(shutil, "which", return_value="pwsh"), \
                patch.object(subprocess, "run", return_value=subprocess.CompletedProcess("pwsh", 0, "ready\n", "")):
            self.assertTrue(powershell_ready())


class SkillLinkingTests(unittest.TestCase):
    linkers = ("sh",)

    def fixture(self, base):
        base = base.resolve()
        repo = base / "product checkout"
        scripts = repo / "scripts"
        scripts.mkdir(parents=True)
        for stem in ("link-skills", "link-claude-skills"):
            for suffix in ("sh", "ps1"):
                shutil.copy2(ROOT / "scripts" / f"{stem}.{suffix}", scripts)
        self.skill(repo / "skills/alpha")
        self.skill(repo / "skills/zeta")
        homes = {runtime: base / "homes" / runtime for runtime in RUNTIMES}
        homes["opencode"] = base / "config home" / "opencode"
        env = dict(os.environ, CLAUDE_CONFIG_DIR=str(homes["claude"]),
                   CODEX_HOME=str(homes["codex"]),
                   XDG_CONFIG_HOME=str(homes["opencode"].parent),
                   PI_CODING_AGENT_DIR=str(homes["omp"]))
        return repo, homes, env

    @staticmethod
    def skill(directory):
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            f"---\nname: {directory.name}\ndescription: Fixture.\n---\nOriginal.\n")

    def run_linker(self, kind, repo, env, runtime, legacy=False, success=True):
        stem = "link-claude-skills" if legacy else "link-skills"
        command = (["sh", str(repo / "scripts" / f"{stem}.sh")] if kind == "sh" else
                   ["pwsh", "-NoLogo", "-NoProfile", "-File", str(repo / "scripts" / f"{stem}.ps1")])
        if not legacy:
            command += [runtime] if kind == "sh" else ["-Runtime", runtime]
        result = subprocess.run(command, cwd=repo, env=env, text=True,
                                check=False, capture_output=True, timeout=30)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_four_native_homes_updates_and_legacy_claude_entry(self):
        for kind in self.linkers:
            with self.subTest(linker=kind), tempfile.TemporaryDirectory() as tmp:
                repo, homes, env = self.fixture(Path(tmp))
                for runtime in RUNTIMES:
                    self.run_linker(kind, repo, env, runtime)
                # A copied directory can pass the initial install, but cannot
                # track source updates or survive a repeated install.
                for home in homes.values():
                    self.assertEqual((home / "skills/alpha").resolve(), (repo / "skills/alpha").resolve())
                    if os.name == "nt" and hasattr(Path, "is_junction"):
                        self.assertTrue((home / "skills/alpha").is_junction())
                self.run_linker(kind, repo, env, "claude", legacy=True)
                manifest = repo / "skills/alpha/SKILL.md"
                manifest.write_text(manifest.read_text().replace("Original", "Updated"))
                for home in homes.values():
                    self.assertIn("Updated", (home / "skills/alpha/SKILL.md").read_text())
                    self.assertEqual({p.name for p in home.iterdir()}, {"skills"})
                shutil.rmtree(repo / "skills/alpha")
                self.skill(repo / "skills/beta")
                foreign = Path(tmp) / "foreign"
                foreign.mkdir()
                for runtime, home in homes.items():
                    (home / "skills/foreign").symlink_to(foreign, target_is_directory=True)
                    self.run_linker(kind, repo, env, runtime)
                    self.run_linker(kind, repo, env, runtime)
                    self.assertFalse(os.path.lexists(home / "skills/alpha"))
                    self.assertTrue((home / "skills/beta/SKILL.md").is_file())
                    self.assertEqual((home / "skills/foreign").resolve(), foreign.resolve())

    def test_conflict_preserves_foreign_entries_and_stale_links(self):
        for kind in self.linkers:
            with self.subTest(linker=kind), tempfile.TemporaryDirectory() as tmp:
                repo, homes, env = self.fixture(Path(tmp))
                skills = homes["omp"] / "skills"
                (skills / "zeta").mkdir(parents=True)
                marker = skills / "zeta/keep.txt"
                marker.write_text("preserve")
                (skills / "stale").symlink_to(repo / "skills/deleted", target_is_directory=True)
                self.run_linker(kind, repo, env, "omp", success=False)
                self.assertEqual(marker.read_text(), "preserve")
                self.assertTrue((skills / "stale").is_symlink())
                self.assertFalse((skills / "alpha").exists())
                shutil.rmtree(skills / "zeta")
                foreign = Path(tmp) / "foreign"
                foreign.mkdir()
                (skills / "zeta").symlink_to(foreign, target_is_directory=True)
                self.run_linker(kind, repo, env, "omp", success=False)
                self.assertEqual((skills / "zeta").resolve(), foreign.resolve())
                self.assertTrue((skills / "stale").is_symlink())

    def test_foreign_relative_links_survive_owned_stale_cleanup(self):
        for kind in self.linkers:
            with self.subTest(linker=kind), tempfile.TemporaryDirectory() as tmp:
                repo, homes, env = self.fixture(Path(tmp))
                skills = homes["omp"] / "skills"
                foreign_target = skills / "skills/deleted"
                foreign_target.mkdir(parents=True)
                marker = foreign_target / "keep.txt"
                marker.write_text("preserve")
                foreign = skills / "foreign"
                foreign.symlink_to(Path("skills/deleted"), target_is_directory=True)
                owned_stale = skills / "owned-stale"
                owned_stale.symlink_to(repo / "skills/deleted", target_is_directory=True)
                self.run_linker(kind, repo, env, "omp")
                self.assertTrue(foreign.is_symlink())
                self.assertEqual(foreign.resolve(), foreign_target.resolve())
                self.assertEqual(marker.read_text(), "preserve")
                self.assertFalse(os.path.lexists(owned_stale))

    def test_wanted_relative_foreign_link_is_rejected_before_stale_cleanup(self):
        for kind in self.linkers:
            with self.subTest(linker=kind), tempfile.TemporaryDirectory() as tmp:
                repo, homes, env = self.fixture(Path(tmp))
                skills = homes["omp"] / "skills"
                foreign_target = skills / "skills/alpha"
                foreign_target.mkdir(parents=True)
                destination = skills / "alpha"
                destination.symlink_to(Path("skills/alpha"), target_is_directory=True)
                stale = skills / "stale"
                stale.symlink_to(repo / "skills/deleted", target_is_directory=True)
                self.run_linker(kind, repo, env, "omp", success=False)
                self.assertEqual(destination.resolve(), foreign_target.resolve())
                self.assertTrue(stale.is_symlink())

    @unittest.skipIf(os.name == "nt", "Native Windows is covered by the real linker lifecycle tests")
    def test_windows_shell_dispatch_uses_native_paths_and_preserves_exit_status(self):
        for system in ("MINGW64_NT-10.0", "MSYS_NT-10.0", "CYGWIN_NT-10.0"):
            with self.subTest(system=system), tempfile.TemporaryDirectory() as tmp:
                repo, homes, env = self.fixture(Path(tmp))
                bin_dir = Path(tmp) / "dispatch-bin"
                bin_dir.mkdir()
                log = Path(tmp) / "dispatch.json"
                self.stub(bin_dir / "uname", '#!/bin/sh\nprintf "%s\\n" "$KG_TEST_SYSTEM"\n')
                self.stub(bin_dir / "cygpath", '#!/bin/sh\n[ "$1" = "-aw" ] || exit 9\nprintf "native:%s\\n" "$2"\n')
                self.stub(bin_dir / "pwsh", '#!/bin/sh\nexec "$KG_TEST_PYTHON" -c '
                          + '\'import json, os, sys; assert os.environ["MSYS2_ARG_CONV_EXCL"] == "*"; open(os.environ["KG_TEST_LOG"], "w").write(json.dumps(sys.argv[1:])); sys.exit(17)\' "$@"\n')
                env.update(PATH=str(bin_dir) + os.pathsep + env["PATH"], KG_TEST_SYSTEM=system,
                           KG_TEST_LOG=str(log), KG_TEST_PYTHON=sys.executable)
                result = subprocess.run(["sh", str(repo / "scripts/link-skills.sh"), "opencode"],
                                        cwd=repo, env=env, text=True, check=False, capture_output=True, timeout=30)
                self.assertEqual(17, result.returncode, result.stdout + result.stderr)
                self.assertEqual(["-NoLogo", "-NoProfile", "-File",
                                  "native:" + str(repo / "scripts/link-skills.ps1"),
                                  "-Runtime", "opencode", "-RuntimeHome", "native:" + str(homes["opencode"])],
                                 json.loads(log.read_text()))
                self.assertFalse(homes["opencode"].exists())

    @unittest.skipIf(os.name == "nt", "Dependency injection uses POSIX executable stubs")
    def test_windows_missing_powershell_fails_before_creating_runtime_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, homes, env = self.fixture(Path(tmp))
            bin_dir = Path(tmp) / "dispatch-bin"
            bin_dir.mkdir()
            self.stub(bin_dir / "uname", '#!/bin/sh\nprintf "MINGW64_NT-10.0\\n"\n')
            (bin_dir / "dirname").symlink_to(shutil.which("dirname"))
            env["PATH"] = str(bin_dir)
            result = subprocess.run([shutil.which("sh"), str(repo / "scripts/link-skills.sh"), "codex"],
                                    cwd=repo, env=env, text=True, check=False, capture_output=True, timeout=30)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("PowerShell 7", result.stderr)
            self.assertFalse(homes["codex"].exists())

    @staticmethod
    def stub(path, content):
        path.write_text(content)
        path.chmod(0o755)


@unittest.skipUnless(PWSH_READY, "PowerShell 7 is absent or its runtime cannot initialize")
class PowerShellSkillLinkingTests(SkillLinkingTests):
    linkers = ("ps1",)


if __name__ == "__main__":
    unittest.main()
