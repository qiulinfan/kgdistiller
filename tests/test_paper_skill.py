from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = (
    REPO_ROOT
    / "skills"
    / "extract-paper-markdown"
    / "scripts"
    / "validate_paper_markdown.py"
)


class PaperMarkdownSkillTests(unittest.TestCase):
    def make_package(self, root: Path) -> tuple[Path, Path]:
        source = root / "source.pdf"
        source.write_bytes(b"%PDF-minimal-test-fixture\n")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        text = root / "evidence" / "text" / "page-0001.txt"
        text.parent.mkdir(parents=True)
        text.write_text("Complete extracted page text.\n", encoding="utf-8")
        markdown = root / "paper.md"
        markdown.write_text(
            "\n".join(
                (
                    "<!-- qlpaper-markdown-v1 -->",
                    f"<!-- qlpaper-source-sha256: {digest} -->",
                    "",
                    "# A complete semantic transcription",
                    "",
                    "<!-- qlpaper-source: page=1 -->",
                    "The paper makes a source-grounded claim.",
                    "",
                )
            ),
            encoding="utf-8",
        )
        manifest = root / "source.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema": "qlpaper-markdown-source-v1",
                    "source_pdf": "source.pdf",
                    "source_sha256": digest,
                    "page_count": 1,
                    "pages": [{"page": 1, "text_path": "evidence/text/page-0001.txt"}],
                    "object_candidates": [],
                    "visual_pages": [],
                    "markdown": "paper.md",
                    "attachments": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return manifest, markdown

    def run_validator(
        self, manifest: Path, markdown: Path
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(VALIDATOR),
                "--manifest",
                str(manifest),
                "--markdown",
                str(markdown),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_complete_image_free_package_validates(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="kgdistiller-paper-skill-"
        ) as temporary:
            manifest, markdown = self.make_package(Path(temporary))
            completed = self.run_validator(manifest, markdown)
            self.assertEqual(0, completed.returncode, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual("ok", result["status"])
            self.assertEqual(1, result["pages"])

    def test_package_path_escape_and_embedded_media_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="kgdistiller-paper-skill-invalid-"
        ) as temporary:
            root = Path(temporary)
            manifest, markdown = self.make_package(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["source_pdf"] = "../outside.pdf"
            manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            markdown.write_text(
                markdown.read_text(encoding="utf-8") + "![leak](figure.png)\n",
                encoding="utf-8",
            )
            completed = self.run_validator(manifest, markdown)
            self.assertNotEqual(0, completed.returncode)
            self.assertIn("package path escapes its root", completed.stderr)
            self.assertIn("forbidden Markdown image embed", completed.stderr)


if __name__ == "__main__":
    unittest.main()
