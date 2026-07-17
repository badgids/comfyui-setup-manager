from __future__ import annotations

import unittest
from unittest.mock import patch

from comfy_setup.models import PlatformInfo
from comfy_setup.prerequisites import check_prerequisites, install_prerequisites


INFO = PlatformInfo(
    os_name="linux", system="Linux", release="test", architecture="x86_64",
    is_wsl=False, package_manager="apt-get", accelerator="cpu",
)


class PrerequisiteTests(unittest.TestCase):
    def test_runtime_tools_are_required(self) -> None:
        with patch("comfy_setup.prerequisites.shutil.which", return_value=None):
            result = check_prerequisites(source_builds=False, info=INFO)
        by_name = {item.name: item for item in result}
        self.assertTrue(by_name["git"].required)
        self.assertTrue(by_name["python"].required)
        self.assertFalse(by_name["cmake"].required)

    def test_source_build_tools_become_required(self) -> None:
        with patch("comfy_setup.prerequisites.shutil.which", return_value=None):
            result = check_prerequisites(source_builds=True, info=INFO)
        self.assertTrue(all(item.required for item in result))

    def test_dry_run_does_not_execute_commands(self) -> None:
        with patch("comfy_setup.prerequisites.shutil.which", return_value=None), \
             patch("comfy_setup.prerequisites.subprocess.run") as run:
            result = install_prerequisites(source_builds=True, dry_run=True, info=INFO)
        self.assertTrue(result["commands"])
        run.assert_not_called()
