from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from packaging.tags import Tag
from packaging.version import Version

from comfy_setup.configuration import (
    EDITABLE_CONFIG_FILES,
    ensure_editable_config,
    official_comfyui_repository,
)
from comfy_setup.models import PlatformInfo
from comfy_setup.pytorch_install import build_torch_install_plan
from comfy_setup.wheel_sources import (
    TargetWheelEnvironment,
    WheelSourceRegistry,
    default_source_path,
    parse_source_file,
    WheelCandidate,
    WheelSource,
    select_candidates,
    wheel_compatibility_score,
)


def platform_info(
    *,
    os_name: str = "linux",
    accelerator: str = "nvidia",
    cuda: str | None = "13.0",
    rocm: str | None = None,
) -> PlatformInfo:
    return PlatformInfo(
        os_name=os_name,
        system="Linux" if os_name == "linux" else os_name,
        release="test",
        architecture="x86_64" if os_name != "macos" else "aarch64",
        is_wsl=False,
        package_manager="apt-get" if os_name == "linux" else None,
        accelerator=accelerator,
        gpu_name="Test GPU",
        cuda_version=cuda,
        rocm_version=rocm,
    )


class YamlConfigurationTests(unittest.TestCase):
    def test_all_editable_configuration_files_are_yaml(self) -> None:
        self.assertTrue(EDITABLE_CONFIG_FILES)
        self.assertTrue(all(name.endswith(".yaml") for name in EDITABLE_CONFIG_FILES))
        self.assertEqual(default_source_path().suffix, ".yaml")
        self.assertFalse(default_source_path().with_suffix(".txt").exists())

    def test_editable_yaml_files_are_created_and_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            "os.environ", {"COMFYUI_SETUP_CONFIG_DIR": temporary}, clear=False
        ):
            for name in EDITABLE_CONFIG_FILES:
                path = ensure_editable_config(name)
                payload = yaml.safe_load(path.read_text(encoding="utf-8"))
                self.assertIsInstance(payload, dict)
            self.assertEqual(
                official_comfyui_repository(),
                "https://github.com/Comfy-Org/ComfyUI.git",
            )


    def test_custom_node_source_defaults_merge_without_overwriting_user_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            "os.environ", {"COMFYUI_SETUP_CONFIG_DIR": temporary}, clear=False
        ):
            path = ensure_editable_config("custom-node-sources.yaml")
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            registry = next(source for source in payload["sources"] if source["id"] == "comfy-registry-search")
            registry["enabled"] = False
            payload["sources"] = [
                registry,
                {
                    "id": "studio-catalog",
                    "name": "Studio catalog",
                    "kind": "manager-list-file",
                    "path": "/srv/catalog/custom-node-list.json",
                    "trust": "Organization",
                    "enabled": True,
                },
            ]
            path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

            merged_path = ensure_editable_config("custom-node-sources.yaml")
            merged = yaml.safe_load(merged_path.read_text(encoding="utf-8"))
            by_id = {source["id"]: source for source in merged["sources"]}
            self.assertIn("comfyui-manager-node-db", by_id)
            self.assertIn("studio-catalog", by_id)
            self.assertFalse(by_id["comfy-registry-search"]["enabled"])

    def test_bundled_registry_contains_badgids_wheel_sources(self) -> None:
        sources = parse_source_file(default_source_path())
        identifiers = {source.id for source in sources}
        expected = {
            "official-flash-attention-releases",
            "official-cupy-pypi",
            "official-tensorrt-pypi",
            "official-onnxruntime-pypi",
            "official-pyopengl-accelerate-pypi",
            "third-party-pozzetti-flash-attn",
            "third-party-pozzetti-sageattention",
            "third-party-pozzetti-cumesh",
            "third-party-pozzetti-cumesh-vb",
            "third-party-pozzetti-drtk",
            "third-party-pozzetti-flex-gemm-ap",
            "third-party-pozzetti-o-voxel-vb-ap",
            "third-party-triton-windows",
        }
        self.assertTrue(expected.issubset(identifiers))
        self.assertTrue({source.label for source in sources}.issubset({"Official", "3rd Party", "Custom/Local"}))

    def test_user_and_backup_sources_are_saved_as_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            "os.environ", {"COMFYUI_SETUP_CONFIG_DIR": temporary}, clear=False
        ):
            registry = WheelSourceRegistry()
            wheel_dir = Path(temporary) / "wheelhouse"
            wheel_dir.mkdir()
            registry.register_local_backup("flash-attn", wheel_dir, platform_info())
            payload = yaml.safe_load(registry.path.read_text(encoding="utf-8"))
            local = [entry for entry in payload["sources"] if entry["label"] == "Custom/Local"]
            self.assertTrue(local)
            self.assertEqual(local[-1]["kind"], "local-directory")
            self.assertEqual(Path(local[-1]["location"]), wheel_dir.resolve())


class OfficialPytorchResolverTests(unittest.TestCase):
    TORCH = {"version": "2.12.1", "torchvision": "0.27.1", "torchaudio": None}

    def _plan(self, info: PlatformInfo, accelerator: str):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            "os.environ", {"COMFYUI_SETUP_CONFIG_DIR": temporary}, clear=False
        ):
            return build_torch_install_plan(self.TORCH, info, accelerator)

    def test_cuda_13_uses_official_cu130(self) -> None:
        plan = self._plan(platform_info(cuda="13.0"), "nvidia")
        self.assertEqual(plan.index_url, "https://download.pytorch.org/whl/cu130")
        self.assertEqual(plan.backend, "cuda")

    def test_cuda_12_8_uses_nearest_supported_official_runtime(self) -> None:
        plan = self._plan(platform_info(cuda="12.8"), "nvidia")
        self.assertEqual(plan.index_url, "https://download.pytorch.org/whl/cu126")

    def test_rocm_uses_official_rocm_index(self) -> None:
        plan = self._plan(
            platform_info(accelerator="rocm", cuda=None, rocm="7.2"), "rocm"
        )
        self.assertEqual(plan.index_url, "https://download.pytorch.org/whl/rocm7.2")

    def test_macos_mps_uses_official_pypi(self) -> None:
        plan = self._plan(
            platform_info(os_name="macos", accelerator="mps", cuda=None), "mps"
        )
        self.assertIsNone(plan.index_url)
        self.assertEqual(plan.backend, "mps")


class WheelCompatibilityTests(unittest.TestCase):
    def test_compact_torch_and_cuda_markers_must_match(self) -> None:
        environment = TargetWheelEnvironment(
            tags=frozenset({Tag("cp313", "cp313", "linux_x86_64")}),
            torch_version=Version("2.12.1"),
            torch_cuda="13.0",
            torch_hip=None,
            cxx11_abi=True,
        )
        good = "flash_attn-2.8.3.post1+cu130torch212cxx11abitrue-cp313-cp313-linux_x86_64.whl"
        wrong_torch = "flash_attn-2.8.3.post1+cu130torch211cxx11abitrue-cp313-cp313-linux_x86_64.whl"
        wrong_cuda = "flash_attn-2.8.3.post1+cu128torch212cxx11abitrue-cp313-cp313-linux_x86_64.whl"
        self.assertIsNotNone(wheel_compatibility_score(good, environment))
        self.assertIsNone(wheel_compatibility_score(wrong_torch, environment))
        self.assertIsNone(wheel_compatibility_score(wrong_cuda, environment))

    def test_requirement_filters_other_compatible_versions(self) -> None:
        environment = TargetWheelEnvironment(
            tags=frozenset({Tag("cp313", "cp313", "linux_x86_64")}),
            torch_version=Version("2.12.1"),
            torch_cuda="13.0",
            torch_hip=None,
            cxx11_abi=True,
        )
        source = WheelSource(
            id="local-test",
            enabled=True,
            label="Custom/Local",
            package="flash-attn",
            kind="local-directory",
            location=".",
            platforms=("linux",),
            architectures=("x86_64",),
            accelerators=("nvidia",),
        )
        # Patch discovery by using a temporary source directory with two empty
        # wheel files; only filenames are examined during candidate selection.
        with tempfile.TemporaryDirectory() as temporary:
            source.location = temporary
            matching = Path(temporary) / "flash_attn-2.8.3.post1+cu130torch212cxx11abitrue-cp313-cp313-linux_x86_64.whl"
            other = Path(temporary) / "flash_attn-2.8.2+cu130torch212cxx11abitrue-cp313-cp313-linux_x86_64.whl"
            matching.touch()
            other.touch()
            candidates = select_candidates(
                [source], environment=environment, package_id="flash-attn",
                requirement="flash-attn==2.8.3.post1",
            )
            self.assertEqual([candidate.name for candidate in candidates], [matching.name])


if __name__ == "__main__":
    unittest.main()
