from __future__ import annotations

import csv
import tarfile
import tempfile
import unittest
from pathlib import Path

from comfy_setup.inventory import load_inventory, profile_from_inventory


class InventoryTests(unittest.TestCase):
    def test_loads_old_inventory_as_remote_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inventory_root = root / "comfyui-installer-inventory-test"
            nodes = inventory_root / "custom_nodes"
            python = inventory_root / "python"
            main_repo = inventory_root / "main_repo"
            nodes.mkdir(parents=True)
            python.mkdir()
            main_repo.mkdir()

            with (nodes / "inventory.tsv").open("w", newline="") as stream:
                writer = csv.DictWriter(
                    stream,
                    delimiter="\t",
                    fieldnames=[
                        "folder", "root", "repo_url", "branch", "commit",
                        "dirty", "install_metadata",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "folder": "ace-step",
                        "root": "$COMFY_DIR/custom_nodes",
                        "repo_url": "https://example.invalid/ace-step.git",
                        "branch": "main",
                        "commit": "a" * 40,
                        "dirty": "no",
                        "install_metadata": "requirements.txt,pyproject.toml",
                    }
                )

            (python / "runtime.txt").write_text(
                "version: 3.13.1\ntorch: 2.12.1+cu130\ntorch_cuda: 13.0\n"
            )
            (python / "pip-freeze.txt").write_text(
                "numpy==2.4.4\ntransformers==4.57.3\n"
            )
            (main_repo / "git.txt").write_text(f"commit={'b' * 40}\n")

            archive = root / "inventory.tgz"
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(inventory_root, arcname=inventory_root.name)

            loaded = load_inventory(archive)
            self.assertEqual(len(loaded["nodes"]), 1)
            node = loaded["nodes"][0]
            self.assertEqual(node["folder"], "ace-step")
            self.assertEqual(node["source"]["type"], "remote")
            self.assertEqual(
                node["source"]["repository"],
                "https://example.invalid/ace-step.git",
            )
            self.assertEqual(loaded["python"], "3.13")
            self.assertEqual(loaded["cuda"], "13.0")
            profile = profile_from_inventory(
                loaded,
                profile_name="Inventory",
                profile_id="inventory",
                repository="https://github.com/Comfy-Org/ComfyUI.git",
            )
            self.assertEqual(profile["schema_version"], 4)


if __name__ == "__main__":
    unittest.main()
