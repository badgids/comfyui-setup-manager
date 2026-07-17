from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from comfy_setup.engine import InstallerEngine, InstallerError
from comfy_setup.models import InstallOptions, PlatformInfo


class FakeRunner:
    def __init__(self, *, dirty: bool = False) -> None:
        self.dirty = dirty
        self.commands: list[tuple[list[str], Path | None]] = []

    def capture(self, command: list[str], *, cwd: Path | None = None, env=None, check: bool = False):
        if command[:3] == ["git", "status", "--porcelain"]:
            output, code = (" M local.py\n", 0) if self.dirty else ("", 0)
        elif command[:4] == ["git", "remote", "get-url", "origin"]:
            output, code = "https://github.com/badgids/ComfyUI.git\n", 0
        elif command[:3] == ["git", "rev-parse", "--short"]:
            output, code = "abc1234\n", 0
        elif command[:4] == ["git", "show-ref", "--verify", "--quiet"]:
            output, code = "", 0
        else:
            output, code = "", 0
        completed = subprocess.CompletedProcess(command, code, output, "")
        if check and code:
            raise RuntimeError(command)
        return completed

    def run(self, command: list[str], *, cwd: Path | None = None, env=None, check: bool = True, interactive: bool = False) -> int:
        self.commands.append((command, cwd))
        return 0


class RepositoryPolicyTests(unittest.TestCase):
    def make_engine(self, target: Path) -> InstallerEngine:
        profile = {
            "comfyui": {
                "repository": "https://github.com/Comfy-Org/ComfyUI.git",
                "branch": "master",
                "preferred_commit": None,
            },
            "nodes": [],
            "accelerated_packages": [],
        }
        platform = PlatformInfo(
            os_name="linux",
            system="Linux",
            release="test",
            architecture="x86_64",
            is_wsl=False,
            package_manager="apt-get",
            accelerator="cpu",
        )
        options = InstallOptions(
            target_dir=target,
            python_version="3.12",
            accelerator="cpu",
            selected_nodes=set(),
            selected_acceleration=set(),
            use_current_checkout=False,
        )
        return InstallerEngine(profile, platform, options)

    def test_selected_repository_is_applied_to_existing_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            (target / "main.py").write_text("\n")
            (target / "requirements.txt").write_text("\n")
            (target / ".git").mkdir()
            engine = self.make_engine(target)
            runner = FakeRunner()
            engine.runner = runner
            engine._prepare_repository()
            commands = [command for command, _ in runner.commands]
            self.assertIn(
                ["git", "remote", "set-url", "origin", "https://github.com/Comfy-Org/ComfyUI.git"],
                commands,
            )
            self.assertIn(["git", "fetch", "origin", "--prune"], commands)
            self.assertIn(["git", "checkout", "-B", "master", "origin/master"], commands)
            self.assertTrue(any(command[:2] == ["git", "branch"] for command in commands))

    def test_dirty_checkout_is_not_modified(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            (target / "main.py").write_text("\n")
            (target / "requirements.txt").write_text("\n")
            (target / ".git").mkdir()
            engine = self.make_engine(target)
            runner = FakeRunner(dirty=True)
            engine.runner = runner
            with self.assertRaises(InstallerError):
                engine._prepare_repository()
            self.assertEqual(runner.commands, [])


if __name__ == "__main__":
    unittest.main()
