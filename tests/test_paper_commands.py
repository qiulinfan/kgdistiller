from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kgdistiller.codex_product import CodexProductError, _validate_workflows, link_product
from kgdistiller.claude_product import link_claude_product


ROOT = Path(__file__).resolve().parents[1]
PAPER_COMMANDS = {
    "distill-paper", "harvest-paper", "distill-paper-knowledge",
    "trace-concept-lineage", "import-paper-knowledge", "paper-related-work",
}


class PaperCommandTests(unittest.TestCase):
    def test_commands_run_without_a_specialized_agent(self) -> None:
        workflow = [{
            "id": "prepare", "description": "Prepare a paper",
            "steps": [{"id": "prepare", "skill": "distill-paper", "agent": None,
                       "mode": "author"}],
        }]
        _validate_workflows(workflow, {"distill-paper"}, set())
        for invalid in ("unknown", "", False, []):
            with self.subTest(agent=invalid):
                workflow[0]["steps"][0]["agent"] = invalid
                with self.assertRaises(CodexProductError):
                    _validate_workflows(workflow, {"distill-paper"}, set())

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
                manifest = json.loads((ROOT / "workflows/manifest.json").read_text(encoding="utf-8"))
                for workflow in manifest["workflows"]:
                    if workflow["id"] in {"distill-paper", "harvest-paper", "paper-related-work"}:
                        self.assertEqual(1, len(workflow["steps"]))
                        if workflow["id"] == "paper-related-work":
                            self.assertEqual("related-work-scout", workflow["steps"][0]["agent"])
                        else:
                            self.assertIsNone(workflow["steps"][0]["agent"])

    def test_claude_scout_has_no_delegation_or_shell_capability(self) -> None:
        # Verify the packaged runtime boundary, not just a prose promise.
        text = (ROOT / ".claude/agents/related-work-scout.md").read_text(encoding="utf-8")
        header = text.split("---", 2)[1]
        tools_line = next(line for line in header.splitlines() if line.startswith("tools:"))
        allowed = {item.strip() for item in tools_line.split(":", 1)[1].split(",")}
        self.assertEqual({"Read", "WebSearch", "WebFetch", "ToolSearch"}, allowed)
        self.assertTrue(allowed.isdisjoint({"Agent", "Task", "Bash", "Write", "Edit", "Skill"}))
