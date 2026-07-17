from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from comfy_setup.engine import _write_filtered_requirements


class RequirementFilterTests(unittest.TestCase):
    def test_conflicting_packages_are_managed_centrally(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "requirements.txt"
            destination = root / "filtered.txt"
            source.write_text(
                "torch\nnumpy>=2\ntransformers>=4\ncupy-cuda12x\nopencv-python\n"
            )
            _write_filtered_requirements(source, destination)
            result = destination.read_text()
            self.assertIn("# Managed by installer: torch", result)
            self.assertIn("# Managed by installer: numpy>=2", result)
            self.assertIn("# Managed by installer: cupy-cuda12x", result)
            self.assertIn("opencv-python", result)


if __name__ == "__main__":
    unittest.main()
