from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DocumentationTests(unittest.TestCase):
    def test_readme_has_required_sections_and_attribution(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("## Table of contents", text)
        self.assertIn("## Requirements", text)
        self.assertIn("Alan Guice (Badgids)", text)
        self.assertIn("Apache License 2.0", text)
        self.assertIn("docs/cli-reference.md", text)
        self.assertIn("PRD.md", text)

    def test_local_markdown_links_exist(self) -> None:
        missing: list[str] = []
        for document in [ROOT / "README.md", ROOT / "PRD.md", *sorted((ROOT / "docs").glob("*.md"))]:
            text = document.read_text(encoding="utf-8")
            for match in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", text):
                target = match.group(1).split("#", 1)[0]
                if not target or "://" in target or target.startswith("mailto:"):
                    continue
                path = (document.parent / target).resolve()
                if not path.exists():
                    missing.append(f"{document.relative_to(ROOT)} -> {target}")
        self.assertEqual(missing, [])

    def test_claude_workspace_is_present(self) -> None:
        required = [
            ".claude/CLAUDE.md", ".claude/settings.json", ".claude/next_task.md",
            ".claude/commands/test.md", ".claude/commands/release.md",
        ]
        self.assertEqual([item for item in required if not (ROOT / item).is_file()], [])

    def test_manual_archive_authoring_and_profiles_directory_are_documented(self) -> None:
        guide = ROOT / "docs/manual-profile-workflow-authoring.md"
        self.assertTrue(guide.is_file())
        text = guide.read_text(encoding="utf-8")
        self.assertIn("Create a minimal `.comfyuisetup` by hand", text)
        self.assertIn("Create a `.comfyworkflows` pack by hand", text)
        self.assertIn("profiles edit-file", text)
        self.assertTrue((ROOT / "profiles/README.md").is_file())
        self.assertFalse((ROOT / "setups").exists())
