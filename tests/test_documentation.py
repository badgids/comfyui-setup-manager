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
        documents = [path for path in ROOT.rglob("*.md") if ".venv" not in path.parts]
        for document in sorted(documents):
            text = document.read_text(encoding="utf-8")
            for match in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", text):
                target = match.group(1).split("#", 1)[0]
                if not target or "://" in target or target.startswith("mailto:"):
                    continue
                path = (document.parent / target).resolve()
                if not path.exists():
                    missing.append(f"{document.relative_to(ROOT)} -> {target}")
        self.assertEqual(missing, [])

    def test_prerequisites_and_badgids_profile_are_documented(self) -> None:
        prerequisites = (ROOT / "docs/prerequisites.md").read_text(encoding="utf-8")
        self.assertIn("When WinGet is required", prerequisites)
        self.assertIn("Python 3.10 or newer", prerequisites)
        self.assertIn("venv", prerequisites)
        self.assertIn("Git", prerequisites)
        self.assertIn("Source-build prerequisites", prerequisites)

        badgids = (ROOT / "docs/badgids-complete-profile.md").read_text(encoding="utf-8")
        self.assertIn("Everything here is specific", badgids)
        self.assertIn("FFmpeg", badgids)
        self.assertIn("cp313-cuda130-torch2_12_1_cu130", badgids)
        self.assertIn("Models, workflows, and shared libraries", badgids)

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("WinGet is a prerequisite for automatic Windows bootstrapping", readme)
        self.assertIn("docs/badgids-complete-profile.md", readme)

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
