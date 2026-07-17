from __future__ import annotations

import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseMetadataTests(unittest.TestCase):
    def test_project_metadata(self) -> None:
        data = tomllib.loads((ROOT / "installer" / "pyproject.toml").read_text(encoding="utf-8"))
        project = data["project"]
        self.assertEqual(project["version"], "0.8.7")
        self.assertEqual(project["license"], "Apache-2.0")
        self.assertEqual(project["authors"][0]["name"], "Alan Guice (Badgids)")

    def test_apache_license_and_notice(self) -> None:
        self.assertIn("Apache License", (ROOT / "LICENSE").read_text(encoding="utf-8"))
        self.assertIn("Alan Guice", (ROOT / "NOTICE").read_text(encoding="utf-8"))


    def test_shared_asset_yaml_is_packaged_and_documented(self) -> None:
        package_config = ROOT / "installer" / "src" / "comfy_setup" / "config"
        root_config = ROOT / "config"
        required = {
            "asset-paths.yaml",
            "custom-node-sources.yaml",
            "model-sources.yaml",
            "workflow-sources.yaml",
            "download-tasks.yaml",
            "runtime-state.yaml",
            "lora-sources.yaml",
            "agents-skills.yaml",
            "mcps.yaml",
        }
        self.assertTrue(required <= {path.name for path in package_config.glob("*.yaml")})
        self.assertTrue(required <= {path.name for path in root_config.glob("*.yaml")})
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("## Important: use shared model and workflow libraries", readme)
        self.assertIn("docs/shared-assets.md", readme)
        self.assertIn("docs/models.md", readme)
        self.assertIn("docs/workflows.md", readme)
        self.assertIn("Agents & Skills", readme)
        self.assertIn("MCPs", readme)
        skills = ROOT / "skills"
        self.assertTrue((skills / "comfyui-setup-manager-operator" / "SKILL.md").is_file())
        self.assertTrue((skills / "comfyui-setup-manager-developer" / "SKILL.md").is_file())
        self.assertTrue((skills / "comfyui-setup-manager-release" / "SKILL.md").is_file())
        pyproject = tomllib.loads((ROOT / "installer" / "pyproject.toml").read_text(encoding="utf-8"))
        package_data = pyproject["tool"]["setuptools"]["package-data"]["comfy_setup"]
        self.assertIn("skills/*/SKILL.md", package_data)

    def test_no_unrelated_service_name_in_project_text(self) -> None:
        needle = "open" + "ai"
        matches: list[str] = []
        for path in ROOT.rglob("*"):
            if not path.is_file() or any(part in {".venv", "__pycache__", "dist"} for part in path.parts):
                continue
            if path.suffix.lower() not in {".py", ".md", ".yaml", ".yml", ".toml", ".json", ".sh", ".ps1", ".cmd", ""}:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore").lower()
            except OSError:
                continue
            if needle in text:
                matches.append(str(path.relative_to(ROOT)))
        self.assertEqual(matches, [])
