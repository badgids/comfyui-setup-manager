from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from comfy_setup.discovery import discover_installations, is_comfyui_directory


class DiscoveryTests(unittest.TestCase):
    def test_discovers_explicit_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            comfy = root / "nested" / "ComfyUI"
            (comfy / "comfy").mkdir(parents=True)
            (comfy / "main.py").write_text("print('ok')\n")
            (comfy / "requirements.txt").write_text("torch\n")
            self.assertTrue(is_comfyui_directory(comfy))
            found = discover_installations([root], max_depth=4, max_directories=100)
            self.assertIn(comfy.resolve(), [item.path for item in found])


if __name__ == "__main__":
    unittest.main()
