from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from kgdistiller.claude_product import doctor_claude_product, link_claude_product
from kgdistiller.codex_product import (
    CodexProductError,
    _validate_workflows,
    doctor_product,
    link_product,
)

ROOT = Path(__file__).resolve().parents[1]
PAPER_COMMANDS = {
    "harvest-paper", "paper-related-work",
}
RETIRED_PAPER_COMMANDS = {
    "federate-paper-knowledge", "trace-concept-lineage", "import-paper-knowledge",
}


class PaperCommandTests(unittest.TestCase):
    def test_commands_run_without_a_specialized_agent(self) -> None:
        workflow = [{
            "id": "prepare", "description": "Prepare a paper",
            "steps": [{"id": "prepare", "skill": "harvest-paper", "agent": None,
                       "mode": "author"}],
        }]
        _validate_workflows(workflow, {"harvest-paper"}, set())
        for invalid in ("unknown", "", False, []):
            with self.subTest(agent=invalid):
                workflow[0]["steps"][0]["agent"] = invalid
                with self.assertRaises(CodexProductError):
                    _validate_workflows(workflow, {"harvest-paper"}, set())

    def test_both_runtime_installs_preserve_per_skill_invocation_policies(self) -> None:
        for runtime in ("codex", "claude"):
            with self.subTest(runtime=runtime), tempfile.TemporaryDirectory() as temp:
                home = Path(temp).resolve() / runtime
                if runtime == "codex":
                    link_product(codex_home=home, mode="copy", source_root=ROOT)
                else:
                    link_claude_product(claude_home=home, mode="copy", source_root=ROOT)
                for name in PAPER_COMMANDS:
                    folder = home / "skills" / name
                    header = (folder / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
                    self.assertIn("disable-model-invocation: true", header.splitlines())
                    metadata = (folder / "agents/openai.yaml").read_text(encoding="utf-8")
                    self.assertIn("policy:\n  allow_implicit_invocation: false", metadata)
                related = home / "skills/paper-related-work"
                self.assertTrue((related / "references/citation-discovery.md").is_file())
                self.assertFalse((home / "skills/read-paper").exists())
                self.assertFalse((home / "skills/extract-paper-markdown").exists())
                self.assertFalse((home / "skills/prepare-paper").exists())
                for name in RETIRED_PAPER_COMMANDS:
                    self.assertFalse((home / "skills" / name).exists())
                manifest = json.loads((ROOT / "workflows/manifest.json").read_text(encoding="utf-8"))
                for workflow in manifest["workflows"]:
                    if workflow["id"] in {"harvest-paper", "paper-related-work"}:
                        self.assertEqual(1, len(workflow["steps"]))
                        if workflow["id"] == "paper-related-work":
                            self.assertEqual("related-work-scout", workflow["steps"][0]["agent"])
                        else:
                            self.assertIsNone(workflow["steps"][0]["agent"])

    def test_upgrade_removes_retired_paper_assets_from_both_runtimes(self) -> None:
        retired_workflows = {
            "federate-paper": "federate-paper-knowledge",
            "trace-lineage": "trace-concept-lineage",
            "import-paper": "import-paper-knowledge",
        }
        for runtime in ("codex", "claude"):
            with self.subTest(runtime=runtime), tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve()
                source = root / "product"
                home = root / runtime
                for name in ("skills", "workflows", ".codex/agents", ".claude/agents",
                             "scripts", "docs"):
                    shutil.copytree(ROOT / name, source / name,
                                    ignore=shutil.ignore_patterns("__pycache__"))
                manifest_path = source / "workflows" / (
                    "manifest.json" if runtime == "codex" else "claude-manifest.json"
                )
                current_manifest = manifest_path.read_text(encoding="utf-8")
                legacy_manifest = json.loads(current_manifest)
                for name in RETIRED_PAPER_COMMANDS:
                    folder = source / "skills" / name
                    folder.mkdir()
                    (folder / "SKILL.md").write_text(
                        f"---\nname: {name}\ndescription: Retired paper command.\n---\n",
                        encoding="utf-8",
                    )
                    (folder / "agents").mkdir()
                    (folder / "agents/openai.yaml").write_text(
                        (ROOT / "skills/harvest-paper/agents/openai.yaml")
                        .read_text(encoding="utf-8").replace("harvest-paper", name),
                        encoding="utf-8",
                    )
                    legacy_manifest["skills"].append({
                        "name": name, "path": f"skills/{name}",
                    })
                extension = "toml" if runtime == "codex" else "md"
                agent_path = source / f".{runtime}/agents/paper-distiller.{extension}"
                agent_path.write_text(
                    (source / f".{runtime}/agents/note-curator.{extension}")
                    .read_text(encoding="utf-8").replace("note-curator", "paper-distiller"),
                    encoding="utf-8",
                )
                legacy_manifest["agents"].append({
                    "name": "paper-distiller",
                    "path": f".{runtime}/agents/paper-distiller.{extension}",
                    "install_as": f"kgdistiller-paper-distiller.{extension}",
                })
                for workflow_id, skill in retired_workflows.items():
                    legacy_manifest["workflows"].append({
                        "id": workflow_id, "description": "Retired paper workflow.",
                        "steps": [{"id": "legacy", "skill": skill,
                                   "agent": "paper-distiller", "mode": "author"}],
                    })
                manifest_path.write_text(json.dumps(legacy_manifest), encoding="utf-8")
                unrelated = home / "skills/external-skill/SKILL.md"
                unrelated.parent.mkdir(parents=True)
                unrelated.write_text("external\n", encoding="utf-8")

                def link(runtime=runtime, home=home, source=source) -> dict:
                    if runtime == "codex":
                        return link_product(codex_home=home, source_root=source)
                    return link_claude_product(claude_home=home, source_root=source)

                link()
                for name in RETIRED_PAPER_COMMANDS:
                    self.assertTrue((home / "skills" / name / "SKILL.md").is_file())
                    shutil.rmtree(source / "skills" / name)
                agent_path.unlink()
                manifest_path.write_text(current_manifest, encoding="utf-8")

                updated = link()
                self.assertEqual(4, updated["removed"])
                for name in RETIRED_PAPER_COMMANDS:
                    target = home / "skills" / name
                    self.assertFalse(target.exists() or target.is_symlink())
                self.assertFalse(
                    (home / f"agents/kgdistiller-paper-distiller.{extension}").exists()
                )
                self.assertEqual("external\n", unrelated.read_text(encoding="utf-8"))
                for name in PAPER_COMMANDS:
                    self.assertTrue((home / "skills" / name / "SKILL.md").is_file())
                if runtime == "codex":
                    checked = doctor_product(codex_home=home, source_root=source)
                else:
                    checked = doctor_claude_product(claude_home=home, source_root=source)
                self.assertEqual("ok", checked["status"])

    def test_claude_scout_has_no_delegation_or_shell_capability(self) -> None:
        # Verify the packaged runtime boundary, not just a prose promise.
        text = (ROOT / ".claude/agents/related-work-scout.md").read_text(encoding="utf-8")
        header = text.split("---", 2)[1]
        tools_line = next(line for line in header.splitlines() if line.startswith("tools:"))
        allowed = {item.strip() for item in tools_line.split(":", 1)[1].split(",")}
        self.assertEqual({"Read", "WebSearch", "WebFetch", "ToolSearch"}, allowed)
        self.assertTrue(allowed.isdisjoint({"Agent", "Task", "Bash", "Write", "Edit", "Skill"}))
