from __future__ import annotations

import json
import re
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
from kgdistiller.mcp import TOOL_SCHEMAS
from tests.test_cli import COMMANDS

ROOT = Path(__file__).resolve().parents[1]
WRITING_SKILLS = ("capture-kgdistiller", "compile-knowledge-sheets", "harvest-kgdistiller")
CODE_SPAN_RE = re.compile(r"```.*?```|`[^`\n]+`", re.DOTALL)
COMMAND_RE = re.compile(r"(?<![\w/.-])(?:kgd|kgdistiller)[ ]+([a-z][a-z-]*)")
TOOL_RE = re.compile(r"\bkg_[a-z_]+\b")


def _frontmatter(text: str) -> list[str]:
    return text.split("---", 2)[1].splitlines()


def _product_texts() -> dict[Path, str]:
    """Every text that describes the current product: Skills, presets, docs and agent guidance.

    The changelog and the release notes record removals; README files are owner-owned.
    None of them are scanned.
    """
    paths = [
        *(ROOT / "skills").rglob("*"),
        *(ROOT / ".claude" / "agents").glob("*"),
        *(ROOT / ".codex" / "agents").glob("*"),
        *(ROOT / "docs").glob("*.md"),
        ROOT / "AGENTS.md",
        ROOT / "CLAUDE.md",
    ]
    return {path: path.read_text(encoding="utf-8") for path in paths if path.is_file()}


class ProductSkillTests(unittest.TestCase):
    def test_harvest_allows_model_invocation_in_both_runtimes(self) -> None:
        for runtime in ("codex", "claude"):
            with self.subTest(runtime=runtime), tempfile.TemporaryDirectory() as temp:
                home = Path(temp).resolve() / runtime
                if runtime == "codex":
                    link_product(codex_home=home, mode="copy", source_root=ROOT)
                else:
                    link_claude_product(claude_home=home, mode="copy", source_root=ROOT)
                folder = home / "skills" / "harvest-kgdistiller"
                skill = (folder / "SKILL.md").read_text(encoding="utf-8")
                self.assertIn("name: harvest-kgdistiller", _frontmatter(skill))
                self.assertNotIn("disable-model-invocation", skill)
                metadata = (folder / "agents" / "openai.yaml").read_text(encoding="utf-8")
                self.assertIn("$harvest-kgdistiller", metadata)
                self.assertNotIn("allow_implicit_invocation", metadata)
                manifest = json.loads((ROOT / "workflows" / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual({item["name"] for item in manifest["skills"]},
                                 {path.name for path in (home / "skills").iterdir()})

    def test_capture_and_compile_ship_the_same_record_format(self) -> None:
        capture = ROOT / "skills/capture-kgdistiller/references/record-format.md"
        compile_ = ROOT / "skills/compile-knowledge-sheets/references/record-format.md"
        self.assertEqual(capture.read_bytes(), compile_.read_bytes())
        for name in ("capture-kgdistiller", "compile-knowledge-sheets"):
            skill = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("(references/record-format.md)", skill)

    def test_writing_skills_end_with_kgd_index(self) -> None:
        for name in WRITING_SKILLS:
            with self.subTest(skill=name):
                skill = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
                self.assertIn("kgd index", skill)

    def test_product_texts_name_only_offered_commands_and_tools(self) -> None:
        for path, text in _product_texts().items():
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                commands = {match[1] for span in CODE_SPAN_RE.findall(text) for match in COMMAND_RE.finditer(span)}
                self.assertLessEqual(commands, COMMANDS)
                self.assertLessEqual(set(TOOL_RE.findall(text)), set(TOOL_SCHEMAS))
        self.assertEqual(
            {"capture-kgdistiller", "compile-knowledge-sheets", "deploy-kgdistiller",
             "harvest-kgdistiller", "query-kgdistiller"},
            {path.parent.name for path in (ROOT / "skills").glob("*/SKILL.md")},
        )

    def test_workflow_step_modes(self) -> None:
        for mode in ("read-only", "author", "write"):
            workflow = [{
                "id": "harvest", "description": "Harvest a sheet",
                "steps": [{"id": "harvest", "skill": "harvest-kgdistiller", "agent": None, "mode": mode}],
            }]
            _validate_workflows(workflow, {"harvest-kgdistiller"}, set())
        for mode in ("transaction", "export", "", None):
            with self.subTest(mode=mode):
                workflow[0]["steps"][0]["mode"] = mode
                with self.assertRaisesRegex(CodexProductError, "invalid step mode"):
                    _validate_workflows(workflow, {"harvest-kgdistiller"}, set())
        workflow[0]["steps"][0]["mode"] = "write"
        for invalid in ("unknown", "", False, []):
            with self.subTest(agent=invalid):
                workflow[0]["steps"][0]["agent"] = invalid
                with self.assertRaises(CodexProductError):
                    _validate_workflows(workflow, {"harvest-kgdistiller"}, set())

    def test_manifests_declare_the_shipped_workflows(self) -> None:
        expected = {
            "capture-knowledge": [("capture-kgdistiller", None, "write")],
            "compile-knowledge-sheets": [("compile-knowledge-sheets", None, "author")],
            "harvest-kgdistiller": [("harvest-kgdistiller", None, "write")],
            "query-knowledge": [("query-kgdistiller", "query-reviewer", "read-only")],
            "deploy-kgdistiller": [("deploy-kgdistiller", None, "write")],
        }
        for name in ("manifest.json", "claude-manifest.json"):
            with self.subTest(manifest=name):
                manifest = json.loads((ROOT / "workflows" / name).read_text(encoding="utf-8"))
                self.assertEqual(["query-reviewer"], [item["name"] for item in manifest["agents"]])
                self.assertEqual(
                    expected,
                    {
                        workflow["id"]: [(step["skill"], step["agent"], step["mode"]) for step in workflow["steps"]]
                        for workflow in manifest["workflows"]
                    },
                )
                self.assertEqual(
                    ["docs/model.md", "docs/retrieval.md", "docs/obsidian.md", "docs/deployment.md"],
                    manifest["workflow_resources"],
                )

    def test_upgrade_removes_assets_dropped_from_the_manifest(self) -> None:
        dropped_workflows = {
            "removed-a": "removed-skill-a",
            "removed-b": "removed-skill-b",
            "removed-c": "removed-skill-c",
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
                previous_manifest = json.loads(current_manifest)
                for name in dropped_workflows.values():
                    folder = source / "skills" / name
                    folder.mkdir()
                    (folder / "SKILL.md").write_text(
                        f"---\nname: {name}\ndescription: Removed command.\n---\n",
                        encoding="utf-8",
                    )
                    (folder / "agents").mkdir()
                    (folder / "agents/openai.yaml").write_text(
                        (ROOT / "skills/harvest-kgdistiller/agents/openai.yaml")
                        .read_text(encoding="utf-8").replace("harvest-kgdistiller", name),
                        encoding="utf-8",
                    )
                    previous_manifest["skills"].append({"name": name, "path": f"skills/{name}"})
                extension = "toml" if runtime == "codex" else "md"
                agent_path = source / f".{runtime}/agents/removed-agent.{extension}"
                agent_path.write_text(
                    (source / f".{runtime}/agents/query-reviewer.{extension}")
                    .read_text(encoding="utf-8").replace("query-reviewer", "removed-agent"),
                    encoding="utf-8",
                )
                previous_manifest["agents"].append({
                    "name": "removed-agent",
                    "path": f".{runtime}/agents/removed-agent.{extension}",
                    "install_as": f"kgdistiller-removed-agent.{extension}",
                })
                for workflow_id, skill in dropped_workflows.items():
                    previous_manifest["workflows"].append({
                        "id": workflow_id, "description": "Removed workflow.",
                        "steps": [{"id": "old", "skill": skill,
                                   "agent": "removed-agent", "mode": "author"}],
                    })
                manifest_path.write_text(json.dumps(previous_manifest), encoding="utf-8")
                unrelated = home / "skills/external-skill/SKILL.md"
                unrelated.parent.mkdir(parents=True)
                unrelated.write_text("external\n", encoding="utf-8")

                def link(runtime=runtime, home=home, source=source) -> dict:
                    if runtime == "codex":
                        return link_product(codex_home=home, source_root=source)
                    return link_claude_product(claude_home=home, source_root=source)

                link()
                for name in dropped_workflows.values():
                    self.assertTrue((home / "skills" / name / "SKILL.md").is_file())
                    shutil.rmtree(source / "skills" / name)
                agent_path.unlink()
                manifest_path.write_text(current_manifest, encoding="utf-8")

                updated = link()
                self.assertEqual(4, updated["removed"])
                for name in dropped_workflows.values():
                    target = home / "skills" / name
                    self.assertFalse(target.exists() or target.is_symlink())
                self.assertFalse(
                    (home / f"agents/kgdistiller-removed-agent.{extension}").exists()
                )
                self.assertTrue((home / f"agents/kgdistiller-query-reviewer.{extension}").is_file())
                self.assertEqual("external\n", unrelated.read_text(encoding="utf-8"))
                self.assertTrue((home / "skills/harvest-kgdistiller/SKILL.md").is_file())
                if runtime == "codex":
                    checked = doctor_product(codex_home=home, source_root=source)
                else:
                    checked = doctor_claude_product(claude_home=home, source_root=source)
                self.assertEqual("ok", checked["status"])


if __name__ == "__main__":
    unittest.main()
