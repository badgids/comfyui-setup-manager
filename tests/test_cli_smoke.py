from __future__ import annotations

import subprocess
import sys
import unittest
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
INHERITED_PYTHONPATH = [
    str(Path(item).resolve()) if not Path(item).is_absolute() else item
    for item in os.environ.get("PYTHONPATH", "").split(os.pathsep)
    if item
]
ENV = {"PYTHONPATH": os.pathsep.join([str(ROOT / "installer" / "src"), *INHERITED_PYTHONPATH])}


HELP_COMMANDS = [
    [], ["system", "--help"], ["installations", "--help"], ["install", "--help"],
    ["profiles", "--help"], ["workflows", "--help"], ["updates", "--help"],
    ["snapshots", "--help"], ["themes", "--help"], ["wheel-sources", "--help"],
    ["nodes", "--help"], ["models", "--help"], ["loras", "--help"],
    ["skills", "--help"], ["mcps", "--help"], ["config", "--help"],
    ["capabilities", "--help"],
]


@pytest.mark.smoke
class CLISmokeTests(unittest.TestCase):
    def test_all_top_level_help_commands_exit_cleanly(self) -> None:
        import os
        env = os.environ.copy()
        env.update(ENV)
        for suffix in HELP_COMMANDS:
            command = [sys.executable, "-m", "comfy_setup", *suffix]
            if not suffix:
                command.append("--help")
            completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 0, msg=f"{' '.join(command)}\n{completed.stderr}")
            self.assertIn("usage:", completed.stdout.lower())

    def test_version_and_structured_capabilities(self) -> None:
        import json, os
        env = os.environ.copy()
        env.update(ENV)
        version = subprocess.run(
            [sys.executable, "-m", "comfy_setup", "--version"],
            cwd=ROOT, env=env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(version.returncode, 0)
        self.assertIn("0.8.7", version.stdout)
        result = subprocess.run(
            [sys.executable, "-m", "comfy_setup", "--format", "json", "capabilities"],
            cwd=ROOT, env=env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater(len(json.loads(result.stdout)), 20)
