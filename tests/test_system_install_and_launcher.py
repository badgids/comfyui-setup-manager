from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from comfy_setup.engine import InstallerEngine
from comfy_setup.models import InstallOptions, PlatformInfo
from comfy_setup.system_deps import MissingDependency


class FakeRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def run(self, command: list[str], **kwargs: object) -> int:
        self.commands.append(command)
        if "apt-get" in command and command[-1] == "update":
            return 100
        return 0


class SystemInstallAndLauncherTests(unittest.TestCase):
    def _engine(self, target: Path, *, command_alias: bool = False) -> InstallerEngine:
        profile = {
            "name": "Test",
            "comfyui": {"repository": "https://github.com/Comfy-Org/ComfyUI.git", "branch": "master"},
            "system_dependencies": [],
            "nodes": [],
            "accelerated_packages": [],
            "torch": {"indexes": {}},
        }
        info = PlatformInfo(
            os_name="linux",
            system="Linux",
            release="test",
            architecture="x86_64",
            is_wsl=True,
            package_manager="apt-get",
            accelerator="nvidia",
        )
        options = InstallOptions(
            target_dir=target,
            python_version="3.13",
            accelerator="nvidia",
            selected_nodes=set(),
            selected_acceleration=set(),
            install_command_alias=command_alias,
        )
        return InstallerEngine(profile, info, options)

    def test_apt_update_failure_is_warning_and_install_continues(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            engine = self._engine(Path(temporary) / "ComfyUI")
            fake = FakeRunner()
            engine.runner = fake  # type: ignore[assignment]
            missing = [MissingDependency("git", "Git", "needed", True)]
            with patch("comfy_setup.engine.missing_dependencies", side_effect=[missing, []]), patch(
                "comfy_setup.engine.install_commands",
                return_value=[["sudo", "apt-get", "update"], ["sudo", "apt-get", "install", "-y", "git"]],
            ):
                engine._install_system_dependencies()
            self.assertEqual(len(fake.commands), 2)
            self.assertTrue(any("apt-get update failed" in warning for warning in engine.warnings))

    def test_local_launchers_are_created_without_global_path_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "ComfyUI"
            target.mkdir()
            (target / ".venv" / "bin").mkdir(parents=True)
            (target / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
            engine = self._engine(target, command_alias=True)
            installed = engine._generate_launchers()
            self.assertEqual(installed, target / "comfyui")
            self.assertTrue((target / "comfyui.ps1").is_file())
            self.assertTrue((target / "comfyui.cmd").is_file())
            content = installed.read_text(encoding="utf-8")
            self.assertIn('ROOT=$(CDPATH=', content)
            self.assertFalse((root / "bin" / "comfyui").exists())



if __name__ == "__main__":
    unittest.main()
