from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path

from comfy_setup.profile import default_profile, write_profile_bundle
from comfy_setup.workflows import (
    WORKFLOW_PACK_EXTENSION,
    WorkflowBundleSummary,
    discover_workflow_files,
    discover_workflow_libraries,
    export_native_workflow,
    import_workflow_artifact,
    inspect_workflow_artifact,
    read_workflow_pack,
    summarize_workflow,
    write_workflow_pack,
)


class WorkflowTests(unittest.TestCase):
    def _workflow(self, path: Path, *, model: str = "example.safetensors") -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "last_node_id": 2,
                    "last_link_id": 0,
                    "nodes": [
                        {
                            "id": 1,
                            "type": "CheckpointLoaderSimple",
                            "pos": [0, 0],
                            "size": [300, 100],
                            "widgets_values": [model],
                        },
                        {
                            "id": 2,
                            "type": "KSampler",
                            "pos": [400, 0],
                            "size": [300, 200],
                            "widgets_values": [1, "fixed", 20],
                        },
                    ],
                    "links": [],
                    "groups": [],
                    "config": {},
                    "extra": {},
                    "version": 0.4,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def _fake_comfy(self, root: Path) -> Path:
        comfy = root / "ComfyUI"
        (comfy / "comfy").mkdir(parents=True)
        (comfy / "main.py").write_text("print('ok')\n", encoding="utf-8")
        (comfy / "requirements.txt").write_text("\n", encoding="utf-8")
        return comfy

    def test_native_json_export_is_not_wrapped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self._workflow(root / "vehicle-build.json")
            output = export_native_workflow(source, root / "shared" / "vehicle-build")
            self.assertEqual(output.suffix, ".json")
            self.assertEqual(json.loads(output.read_text()), json.loads(source.read_text()))
            self.assertFalse(zipfile.is_zipfile(output))

    def test_bundle_preserves_subdirectories_and_support_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_root = root / "workflows"
            first = self._workflow(source_root / "vehicles" / "hot-rods" / "build.json")
            second = self._workflow(source_root / "audio" / "voice.json", model="voice.gguf")
            (first.parent / "README.md").write_text("# Hot Rod Workflow\n", encoding="utf-8")
            (first.parent / "preview.png").write_bytes(b"not-a-real-image-but-a-sidecar")
            custom_readme = root / "PACK_README.md"
            custom_readme.write_text("# Complete Workflow Pack\n", encoding="utf-8")
            fake_setup = root / "recommended.comfysetup"
            fake_setup.write_bytes(b"portable setup placeholder")

            bundle = write_workflow_pack(
                [source_root],
                root / "starter",
                name="Starter Workflows",
                description="Directory-preserving test",
                default_install_subdirectory="shared/starter",
                source_root=source_root,
                readme_path=custom_readme,
                setup_profile_path=fake_setup,
                setup_reference="https://example.invalid/setup.comfysetup",
            )
            self.assertTrue(bundle.name.endswith(WORKFLOW_PACK_EXTENSION))
            manifest, entries = read_workflow_pack(bundle)
            self.assertEqual(manifest["workflow_count"], 2)
            self.assertEqual(manifest["default_install_subdirectory"], "shared/starter")
            self.assertEqual(len(entries), 2)
            paths = {entry["path"] for entry, _ in entries}
            self.assertEqual(paths, {"vehicles/hot-rods/build.json", "audio/voice.json"})
            with zipfile.ZipFile(bundle) as archive:
                names = set(archive.namelist())
                self.assertIn("workflows/vehicles/hot-rods/build.json", names)
                self.assertIn("workflows/vehicles/hot-rods/README.md", names)
                self.assertIn("workflows/vehicles/hot-rods/preview.png", names)
                self.assertIn("setup/recommended.comfysetup", names)
                self.assertEqual(archive.read("README.md").decode(), "# Complete Workflow Pack\n")
                self.assertFalse(any(name.endswith(".safetensors") for name in names))

            summary = inspect_workflow_artifact(bundle)
            self.assertIsInstance(summary, WorkflowBundleSummary)
            assert isinstance(summary, WorkflowBundleSummary)
            self.assertEqual(summary.workflow_count, 2)
            self.assertTrue(summary.setup_embedded)

    def test_bundle_installs_to_archive_default_and_preserves_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_root = root / "source"
            self._workflow(source_root / "vehicles" / "build.json")
            self._workflow(source_root / "vehicles" / "paint" / "paint.json")
            (source_root / "vehicles" / "README.md").write_text("# Vehicles\n", encoding="utf-8")
            bundle = write_workflow_pack(
                [source_root],
                root / "bundle",
                name="Vehicle Pack",
                source_root=source_root,
                default_install_subdirectory="community/vehicle-pack",
            )
            comfy = self._fake_comfy(root)
            result = import_workflow_artifact(bundle, comfy)
            expected_root = comfy / "user" / "default" / "workflows" / "community" / "vehicle-pack"
            self.assertEqual(result.destination, expected_root.resolve())
            self.assertTrue((expected_root / "vehicles" / "build.json").is_file())
            self.assertTrue((expected_root / "vehicles" / "paint" / "paint.json").is_file())
            self.assertTrue((expected_root / "vehicles" / "README.md").is_file())
            self.assertTrue((expected_root / ".comfyworkflow-bundle.json").is_file())

    def test_bundle_can_install_into_exact_custom_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_root = root / "source"
            self._workflow(source_root / "nested" / "one.json")
            bundle = write_workflow_pack(
                [source_root],
                root / "bundle",
                name="Custom Destination Pack",
                source_root=source_root,
                default_install_subdirectory="ignored-default",
            )
            custom = root / "my-custom-workflow-library"
            result = import_workflow_artifact(
                bundle,
                None,
                custom_directory=custom,
                use_bundle_default=False,
            )
            self.assertEqual(result.destination, custom.resolve())
            self.assertTrue((custom / "nested" / "one.json").is_file())


    def test_embedded_setup_can_be_imported_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_root = root / "source"
            self._workflow(source_root / "one.json")
            setup = write_profile_bundle(default_profile(), root / "vanilla.comfysetup")
            bundle = write_workflow_pack(
                [source_root],
                root / "bundle",
                name="Bundle With Setup",
                source_root=source_root,
                setup_profile_path=setup,
            )
            comfy = self._fake_comfy(root)
            with patch.dict(os.environ, {"XDG_DATA_HOME": str(root / "manager-data")}):
                result = import_workflow_artifact(
                    bundle,
                    comfy,
                    import_setup=True,
                )
            self.assertIsNotNone(result.setup_profile)
            assert result.setup_profile is not None
            self.assertTrue(result.setup_profile.is_file())
            self.assertTrue(result.setup_profile.name.endswith(".comfyuisetup"))

    def test_companion_yaml_is_validated_and_installed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_root = root / "source"
            self._workflow(source_root / "one.json")
            models_yaml = root / "models.yaml"
            models_yaml.write_text(
                "schema: 1\nmodels:\n  - id: example-model\n    required: true\n",
                encoding="utf-8",
            )
            bundle = write_workflow_pack(
                [source_root],
                root / "bundle",
                name="Bundle With Requirements",
                source_root=source_root,
                companion_yaml_paths=[models_yaml],
            )
            manifest, _entries = read_workflow_pack(bundle)
            self.assertEqual(manifest["companion_yaml"], ["models.yaml"])
            self.assertEqual(manifest["companion_files"][0]["path"], "models.yaml")
            self.assertTrue(manifest["companion_files"][0]["sha256"])

            comfy = self._fake_comfy(root)
            result = import_workflow_artifact(bundle, comfy)
            requirement = (
                comfy
                / "user"
                / "default"
                / "workflows"
                / ".comfyworkflow-requirements"
                / "models.yaml"
            )
            self.assertTrue(requirement.is_file())
            self.assertIn(requirement.resolve(), result.support_files)

    def test_corrupt_companion_yaml_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_root = root / "source"
            self._workflow(source_root / "one.json")
            models_yaml = root / "models.yaml"
            models_yaml.write_text("schema: 1\nmodels: []\n", encoding="utf-8")
            bundle = write_workflow_pack(
                [source_root],
                root / "bundle",
                name="Companion Checksum",
                source_root=source_root,
                companion_yaml_paths=[models_yaml],
            )
            corrupt = root / "corrupt-companion.comfyworkflows"
            with zipfile.ZipFile(bundle) as source_archive, zipfile.ZipFile(corrupt, "w") as target_archive:
                for item in source_archive.infolist():
                    payload = source_archive.read(item.filename)
                    if item.filename == "models.yaml":
                        payload += b"# changed\n"
                    target_archive.writestr(item, payload)
            comfy = self._fake_comfy(root)
            with self.assertRaisesRegex(Exception, "Companion YAML checksum"):
                import_workflow_artifact(corrupt, comfy)

    def test_corrupt_bundle_payload_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_root = root / "source"
            self._workflow(source_root / "one.json")
            bundle = write_workflow_pack(
                [source_root],
                root / "bundle",
                name="Checksum Test",
                source_root=source_root,
            )
            corrupt = root / "corrupt.comfyworkflows"
            with zipfile.ZipFile(bundle) as source_archive, zipfile.ZipFile(corrupt, "w") as target_archive:
                for item in source_archive.infolist():
                    payload = source_archive.read(item.filename)
                    if item.filename == "workflows/one.json":
                        payload = payload + b" "
                    target_archive.writestr(item, payload)
            comfy = self._fake_comfy(root)
            with self.assertRaisesRegex(Exception, "checksum"):
                import_workflow_artifact(corrupt, comfy)

    def test_native_workflow_library_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            comfy = self._fake_comfy(root)
            workflow = self._workflow(comfy / "user" / "default" / "workflows" / "cars" / "one.json")
            libraries = discover_workflow_libraries(comfy)
            self.assertTrue(any(item.path == workflow.parents[1].resolve() for item in libraries))
            self.assertEqual(discover_workflow_files(comfy), [workflow.resolve()])

    def test_raw_json_import_avoids_overwrite_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workflow = self._workflow(root / "same.json")
            comfy = self._fake_comfy(root)
            first = import_workflow_artifact(workflow, comfy)
            second = import_workflow_artifact(workflow, comfy)
            self.assertNotEqual(first.installed[0], second.installed[0])
            self.assertEqual(second.installed[0].name, "same-2.json")

    def test_absolute_path_is_reported_not_silently_changed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self._workflow(root / "absolute.json", model="/private/models/model.safetensors")
            summary = summarize_workflow(json.loads(source.read_text()))
            self.assertTrue(summary.warnings)
            self.assertIn("/private/models/model.safetensors", summary.model_references)


if __name__ == "__main__":
    unittest.main()
