from __future__ import annotations

import csv
import json
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from comfy_setup.engine import InstallerEngine, InstallerError
from comfy_setup.environment_lock import (
    build_environment_lock,
    compatibility_issues,
    lock_embedded_wheels,
    lock_install_requirements,
    lock_source_tree_packages,
)
from comfy_setup.exporter import ExportError, build_profile_from_installation
from comfy_setup.inventory import InventoryError, load_inventory, profile_from_inventory
from comfy_setup.models import InstallOptions, PlatformInfo
from comfy_setup.pytorch_install import build_torch_install_plan
from comfy_setup.profile import (
    PROFILE_KIND,
    PROFILE_SCHEMA,
    ProfileError,
    read_profile_bundle,
    write_profile_bundle,
)


def platform_info() -> PlatformInfo:
    return PlatformInfo(
        os_name="linux",
        system="Linux",
        release="test",
        architecture="x86_64",
        is_wsl=False,
        package_manager=None,
        accelerator="nvidia",
        cuda_version="13.0",
    )


def exact_profile(lock: dict, *, wheel_paths: dict[str, str] | None = None) -> dict:
    profile = {
        "schema_version": PROFILE_SCHEMA,
        "kind": PROFILE_KIND,
        "id": "portable-lock-test",
        "name": "Portable Lock Test",
        "version": "0.8.4",
        "comfyui": {
            "repository": "https://github.com/Comfy-Org/ComfyUI.git",
            "branch": "master",
            "preferred_commit": "a" * 40,
        },
        "python": {"preferred": "3.13", "fallbacks": ["3.13"]},
        "torch": {
            "version": "2.12.1",
            "torchvision": "0.27.1",
            "torchaudio": "2.12.1",
            "indexes": {},
            "fallback_unpinned": False,
        },
        "constraints": {},
        "environment_lock": lock,
        "extra_python_packages": [],
        "dependency_repairs": {},
        "nodes": [],
        "accelerated_packages": [],
        "models": [],
        "workflows": [],
        "asset_sources": {},
        "libraries": {},
    }
    if wheel_paths:
        profile["_embedded_wheel_paths"] = wheel_paths
    return profile


def test_full_environment_lock_records_packages_without_archiving_local_wheels(tmp_path: Path) -> None:
    wheel = tmp_path / "flash_attn-2.8.3.post1-cp313-cp313-linux_x86_64.whl"
    wheel.write_bytes(b"portable wheel artifact")
    lock, warnings, wheel_paths = build_environment_lock(
        [
            {"name": "click", "version": "8.1.8", "direct_url": None},
            {"name": "numpy", "version": "2.4.4", "direct_url": None},
            {"name": "torch", "version": "2.12.1+cu130", "direct_url": None},
            {"name": "flash-attn", "version": "2.8.3.post1", "direct_url": {"url": wheel.as_uri(), "archive_info": {}}},
        ],
        source={"python": "3.13.5", "implementation": "CPython", "os": "linux", "architecture": "x86_64"},
    )
    packages = {item["name"]: item for item in lock["packages"]}
    assert lock["complete"] is True
    assert warnings == []
    assert packages["click"]["requirement"] == "click==8.1.8"
    assert packages["numpy"]["requirement"] == "numpy==2.4.4"
    assert packages["torch"]["install"] is False
    assert packages["torch"]["managed_by"] == "torch-backend"
    assert packages["flash-attn"]["source"] == "managed"
    assert packages["flash-attn"]["managed_by"] == "accelerated-package"
    assert wheel_paths == {}
    assert "click==8.1.8" in lock_install_requirements(lock)
    assert "flash-attn==2.8.3.post1" not in lock_install_requirements(lock)
    assert lock_embedded_wheels(lock) == []

def test_environment_lock_preserves_immutable_git_subdirectory() -> None:
    lock, warnings, _ = build_environment_lock(
        [
            {
                "name": "monorepo-package",
                "version": "1.2.3",
                "direct_url": {
                    "url": "https://github.com/example/monorepo.git",
                    "vcs_info": {"vcs": "git", "commit_id": "a" * 40},
                    "subdirectory": "python/package",
                },
            }
        ],
        source={"python": "3.13.5", "os": "linux", "architecture": "x86_64"},
    )
    assert warnings == []
    assert lock["packages"][0]["requirement"].endswith(
        "@" + "a" * 40 + "#subdirectory=python/package"
    )


def test_exact_environment_lock_rejects_ambiguous_duplicate_distributions() -> None:
    lock, warnings, _ = build_environment_lock(
        [
            {"name": "Example_Package", "version": "1.0", "direct_url": None},
            {"name": "example-package", "version": "2.0", "direct_url": None},
        ],
        source={"python": "3.13.5", "os": "linux", "architecture": "x86_64"},
    )
    assert lock["complete"] is False
    assert lock["ambiguous_packages"] == ["example-package"]
    assert lock["nonportable_packages"] == ["example-package"]
    assert lock["packages"][0]["source"] == "ambiguous-installed-distributions"
    assert any("Multiple installed distributions" in warning for warning in warnings)


def test_environment_lock_recognizes_external_custom_node_source_roots(tmp_path: Path) -> None:
    comfy = tmp_path / "ComfyUI"
    external_root = tmp_path / "SharedCustomNodes"
    editable_node = external_root / "example-node"
    comfy.mkdir()
    editable_node.mkdir(parents=True)
    lock, warnings, wheel_paths = build_environment_lock(
        [
            {
                "name": "example-node",
                "version": "1.0.0",
                "direct_url": {
                    "url": editable_node.as_uri(),
                    "dir_info": {"editable": True},
                },
            }
        ],
        source={"python": "3.13.5", "os": "linux", "architecture": "x86_64"},
        comfy_dir=comfy,
        source_roots=[external_root],
    )
    package = lock["packages"][0]
    assert lock["complete"] is True
    assert package["source"] == "source-tree"
    assert package["managed_by"] == "comfyui-or-custom-node-source"
    assert package["source_path"] == "custom_nodes/example-node"
    assert package["editable"] is True
    assert package["install"] is False
    assert lock_source_tree_packages(lock) == [package]
    assert warnings == []
    assert wheel_paths == {}


def test_environment_lock_excludes_source_packages_belonging_to_omitted_duplicate_node(tmp_path: Path) -> None:
    comfy = tmp_path / "ComfyUI"
    external_root = tmp_path / "SharedCustomNodes"
    omitted_node = external_root / "duplicate-node"
    comfy.mkdir()
    omitted_node.mkdir(parents=True)
    lock, warnings, _wheel_paths = build_environment_lock(
        [
            {
                "name": "duplicate-node-package",
                "version": "1.0.0",
                "direct_url": {
                    "url": omitted_node.as_uri(),
                    "dir_info": {"editable": True},
                },
            },
            {"name": "click", "version": "8.1.8", "direct_url": None},
        ],
        source={"python": "3.13.5", "os": "linux", "architecture": "x86_64"},
        comfy_dir=comfy,
        source_roots=[external_root],
        excluded_source_paths=[omitted_node],
    )
    assert [item["name"] for item in lock["packages"]] == ["click"]
    assert lock["explicitly_omitted_packages"] == [
        {
            "name": "duplicate-node-package",
            "version": "1.0.0",
            "reason": "source belongs to a custom node explicitly omitted during export",
        }
    ]
    assert any(str(omitted_node.resolve()) in warning for warning in warnings)


def test_omitted_source_distribution_does_not_make_kept_distribution_ambiguous(tmp_path: Path) -> None:
    comfy = tmp_path / "ComfyUI"
    omitted_node = tmp_path / "SharedCustomNodes" / "duplicate-node"
    comfy.mkdir()
    omitted_node.mkdir(parents=True)
    lock, _warnings, _wheel_paths = build_environment_lock(
        [
            {
                "name": "same-package",
                "version": "1.0.0",
                "direct_url": {"url": omitted_node.as_uri(), "dir_info": {"editable": True}},
            },
            {"name": "same-package", "version": "1.0.0", "direct_url": None},
        ],
        source={"python": "3.13.5", "os": "linux", "architecture": "x86_64"},
        comfy_dir=comfy,
        source_roots=[omitted_node.parent],
        excluded_source_paths=[omitted_node],
    )
    assert lock["ambiguous_packages"] == []
    assert [item["name"] for item in lock["packages"]] == ["same-package"]
    assert lock["packages"][0]["source"] == "index"


def test_profile_bundle_round_trip_does_not_archive_local_wheel(tmp_path: Path) -> None:
    wheel = tmp_path / "example_package-1.0-cp313-cp313-linux_x86_64.whl"
    wheel.write_bytes(b"machine-local wheel")
    lock, warnings, wheel_paths = build_environment_lock(
        [
            {"name": "click", "version": "8.1.8", "direct_url": None},
            {"name": "example-package", "version": "1.0", "direct_url": {"url": wheel.as_uri()}},
        ],
        source={"python": "3.13.5", "implementation": "CPython", "os": "linux", "architecture": "x86_64"},
    )
    assert wheel_paths == {}
    assert any("was not embedded" in warning for warning in warnings)
    first = write_profile_bundle(exact_profile(lock), tmp_path / "first.comfyuisetup")
    loaded = read_profile_bundle(first)
    package = next(item for item in loaded["environment_lock"]["packages"] if item["name"] == "example-package")
    assert package["source"] == "index"
    assert package["requirement"] == "example-package==1.0"
    assert "sha256" not in package
    with zipfile.ZipFile(first) as archive:
        assert not any(name.startswith("embedded_wheels/") for name in archive.namelist())

def test_profile_bundle_rejects_tampered_legacy_embedded_wheel(tmp_path: Path) -> None:
    wheel = tmp_path / "package-1.0-cp313-cp313-linux_x86_64.whl"
    wheel.write_bytes(b"original")
    payload = "embedded_wheels/package-1.0-cp313-cp313-linux_x86_64.whl"
    lock = {
        "schema_version": 1, "mode": "exact", "complete": True,
        "source": {"python": "3.13.5", "implementation": "CPython", "os": "linux", "architecture": "x86_64"},
        "packages": [{"name": "package", "version": "1.0", "requirement": "package==1.0", "install": True, "portable": True, "source": "embedded-wheel", "payload": payload}],
        "nonportable_packages": [],
    }
    bundle = write_profile_bundle(exact_profile(lock, wheel_paths={payload: str(wheel)}), tmp_path / "profile.comfyuisetup")
    package = lock_embedded_wheels(read_profile_bundle(bundle)["environment_lock"])[0]
    assert package["sha256"]
    tampered = tmp_path / "tampered.comfyuisetup"
    with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename == payload:
                content = b"tampered"
            target.writestr(info, content)
    with pytest.raises(ProfileError, match="checksum"):
        read_profile_bundle(tampered)

def test_profile_rejects_incomplete_exact_environment_lock(tmp_path: Path) -> None:
    lock = {
        "schema_version": 1,
        "mode": "exact",
        "complete": False,
        "source": {"python": "3.13.5", "os": "linux", "architecture": "x86_64"},
        "packages": [
            {
                "name": "local-package",
                "version": "1.0",
                "requirement": "local-package==1.0",
                "install": False,
                "portable": False,
                "source": "local",
            }
        ],
        "nonportable_packages": ["local-package"],
    }
    with pytest.raises(ProfileError, match="incomplete"):
        write_profile_bundle(exact_profile(lock), tmp_path / "incomplete.comfyuisetup")


def test_engine_supports_legacy_embedded_wheel_and_constrains_locked_distributions(tmp_path: Path) -> None:
    wheel = tmp_path / "legacy_package-1.0-cp313-cp313-linux_x86_64.whl"
    wheel.write_bytes(b"wheel")
    payload = "embedded_wheels/legacy_package-1.0-cp313-cp313-linux_x86_64.whl"
    lock = {
        "schema_version": 1, "mode": "exact", "complete": True,
        "source": {"python": "3.13.5", "implementation": "CPython", "os": "linux", "architecture": "x86_64"},
        "packages": [
            {"name": "click", "version": "8.1.8", "requirement": "click==8.1.8", "install": True, "portable": True, "source": "index"},
            {"name": "numpy", "version": "2.4.4", "requirement": "numpy==2.4.4", "install": True, "portable": True, "source": "index"},
            {"name": "legacy-package", "version": "1.0", "requirement": "legacy-package==1.0", "install": True, "portable": True, "source": "embedded-wheel", "payload": payload},
        ],
        "nonportable_packages": [],
    }
    bundle = write_profile_bundle(exact_profile(lock, wheel_paths={payload: str(wheel)}), tmp_path / "profile.comfyuisetup")
    profile = read_profile_bundle(bundle)
    options = InstallOptions(target_dir=tmp_path / "ComfyUI", python_version="3.13", accelerator="nvidia", selected_nodes=set(), selected_acceleration=set(), auto_install_system=False)
    engine = InstallerEngine(profile, platform_info(), options)
    commands: list[tuple[str, ...]] = []
    engine._uv_pip = lambda *args, **kwargs: commands.append(tuple(str(arg) for arg in args)) or 0  # type: ignore[method-assign]
    engine._install_environment_lock()
    constraints = engine._constraint_file().read_text(encoding="utf-8")
    assert "click==8.1.8" in constraints
    assert "numpy==2.4.4" in constraints
    command = commands[-1]
    assert any(value.endswith(".whl") for value in command)
    assert "-c" in command

def test_environment_lock_refuses_cross_runtime_substitution() -> None:
    lock = {
        "schema_version": 1,
        "mode": "exact",
        "complete": True,
        "source": {"python": "3.13.5", "os": "linux", "architecture": "x86_64"},
        "packages": [{"name": "click", "version": "8.1.8", "requirement": "click==8.1.8", "install": True}],
        "nonportable_packages": [],
    }
    assert compatibility_issues(lock, os_name="windows", architecture="amd64", python_version="3.12") == [
        "Python 3.13.5 lock cannot be reproduced with selected Python 3.12.",
        "Environment lock was created on linux, not windows.",
    ]


def test_environment_lock_refuses_different_torch_backend() -> None:
    lock = {
        "schema_version": 1,
        "mode": "exact",
        "complete": True,
        "source": {
            "python": "3.13.5",
            "os": "linux",
            "architecture": "x86_64",
            "accelerator": "nvidia",
            "torch_cuda": "13.0",
        },
        "packages": [{"name": "click", "version": "8.1.8", "requirement": "click==8.1.8", "install": True}],
        "nonportable_packages": [],
    }
    assert compatibility_issues(
        lock,
        os_name="linux",
        architecture="x86_64",
        python_version="3.13",
        accelerator="cpu",
        cuda_version=None,
    ) == ["Environment lock used the nvidia PyTorch backend, not cpu."]
    assert compatibility_issues(
        lock,
        os_name="linux",
        architecture="x86_64",
        python_version="3.13",
        accelerator="nvidia",
        cuda_version="12.8",
    ) == ["Environment lock used PyTorch CUDA 13.0, but the selected target reports CUDA 12.8."]


def test_managed_only_exact_lock_is_still_planned_and_checked(tmp_path: Path) -> None:
    lock, _, _ = build_environment_lock(
        [{"name": "torch", "version": "2.12.1+cu130", "direct_url": None}],
        source={
            "python": "3.13.5",
            "os": "linux",
            "architecture": "x86_64",
            "accelerator": "nvidia",
            "torch_cuda": "13.0",
        },
    )
    options = InstallOptions(
        target_dir=tmp_path / "ComfyUI",
        python_version="3.13",
        accelerator="cpu",
        selected_nodes=set(),
        selected_acceleration=set(),
        auto_install_system=False,
    )
    engine = InstallerEngine(exact_profile(lock), platform_info(), options)
    assert "environment-lock" in [step.key for step in engine.plan()]
    with pytest.raises(InstallerError, match="nvidia PyTorch backend"):
        engine._install_environment_lock()


def test_engine_reinstalls_locked_editable_source_tree_package(tmp_path: Path) -> None:
    lock = {
        "schema_version": 1,
        "mode": "exact",
        "complete": True,
        "source": {"python": "3.13.5", "os": "linux", "architecture": "x86_64"},
        "packages": [
            {
                "name": "example-node",
                "version": "1.0.0",
                "requirement": "example-node==1.0.0",
                "install": False,
                "portable": True,
                "source": "source-tree",
                "managed_by": "comfyui-or-custom-node-source",
                "source_path": "custom_nodes/example-node",
                "editable": True,
            }
        ],
        "nonportable_packages": [],
    }
    profile = exact_profile(lock)
    options = InstallOptions(
        target_dir=tmp_path / "ComfyUI",
        python_version="3.13",
        accelerator="nvidia",
        selected_nodes=set(),
        selected_acceleration=set(),
        auto_install_system=False,
    )
    source = options.target_dir / "custom_nodes" / "example-node"
    source.mkdir(parents=True)
    (source / "pyproject.toml").write_text(
        "[project]\nname='example-node'\nversion='1.0.0'\n", encoding="utf-8"
    )
    engine = InstallerEngine(profile, platform_info(), options)
    commands: list[tuple[str, ...]] = []
    engine._uv_pip = lambda *args, **kwargs: commands.append(tuple(str(arg) for arg in args)) or 0  # type: ignore[method-assign]
    engine._install_locked_source_packages()
    assert commands == [
        ("install", "--no-deps", "--reinstall", "--editable", str(source.resolve()))
    ]
    assert "source-packages" in [step.key for step in engine.plan()]
    assert "example-node==1.0.0" in engine._constraint_file().read_text(encoding="utf-8")


def test_exact_torch_plan_uses_exported_cuda_runtime_not_newer_target_toolkit() -> None:
    config = {
        "version": "2.12.1",
        "torchvision": "0.27.1",
        "torchaudio": "",
        "indexes": {"nvidia": "https://download.pytorch.org/whl/cu130"},
        "source_accelerator": "nvidia",
        "source_backend_version": "13.0",
        "exact_backend_index": "https://download.pytorch.org/whl/cu130",
        "exact_backend": True,
        "fallback_unpinned": False,
    }
    target = platform_info()
    target.cuda_version = "13.3"
    plan = build_torch_install_plan(config, target, "nvidia")
    assert plan.index_url == "https://download.pytorch.org/whl/cu130"
    assert plan.backend_version == "13.0"
    assert "torch==2.12.1" in plan.packages


def test_exact_existing_checkout_cannot_silently_keep_wrong_commit(tmp_path: Path) -> None:
    lock = {
        "schema_version": 1,
        "mode": "exact",
        "complete": True,
        "source": {"python": "3.13.5", "os": "linux", "architecture": "x86_64"},
        "packages": [{"name": "click", "version": "8.1.8", "requirement": "click==8.1.8", "install": True}],
        "nonportable_packages": [],
    }
    profile = exact_profile(lock)
    profile["comfyui"]["exact"] = True
    target = tmp_path / "ComfyUI"
    target.mkdir()
    (target / "comfy").mkdir()
    (target / "main.py").write_text("", encoding="utf-8")
    (target / "requirements.txt").write_text("", encoding="utf-8")
    options = InstallOptions(
        target_dir=target,
        python_version="3.13",
        accelerator="nvidia",
        selected_nodes=set(),
        selected_acceleration=set(),
        auto_install_system=False,
        use_current_checkout=True,
        pin_exact_refs=False,
    )
    engine = InstallerEngine(profile, platform_info(), options)
    with patch.object(engine, "_is_comfy_checkout", return_value=True), patch.object(
        engine, "_verify_existing_exact_checkout", side_effect=InstallerError("wrong commit")
    ) as verify:
        with pytest.raises(InstallerError, match="wrong commit"):
            engine._prepare_repository()
    verify.assert_called_once_with(target.resolve())


def test_non_exact_compatibility_export_does_not_claim_an_exact_environment_lock(tmp_path: Path) -> None:
    comfy = tmp_path / "ComfyUI"
    comfy.mkdir()
    (comfy / "comfy").mkdir()
    (comfy / "custom_nodes").mkdir()
    (comfy / "main.py").write_text("", encoding="utf-8")
    (comfy / "requirements.txt").write_text("", encoding="utf-8")
    with patch("comfy_setup.exporter._venv_python", return_value=Path("/fake/python")), patch(
        "comfy_setup.exporter._packages",
        return_value=(
            {"torch": "2.12.1+cu130", "click": "8.1.8"},
            ["click==8.1.8"],
            "3.13",
            [
                {"name": "torch", "version": "2.12.1+cu130", "direct_url": None},
                {"name": "click", "version": "8.1.8", "direct_url": None},
            ],
            {
                "python": "3.13.5",
                "os": "linux",
                "architecture": "x86_64",
                "accelerator": "nvidia",
                "torch_version": "2.12.1+cu130",
                "torch_cuda": "13.0",
            },
        ),
    ):
        profile, _ = build_profile_from_installation(comfy, name="Compatibility", exact_refs=False)
    assert profile["environment_lock"] == {}
    assert profile["comfyui"]["exact"] is False
    assert profile["torch"]["fallback_unpinned"] is True
    assert "click==8.1.8" in profile["extra_python_packages"]


def test_exact_export_reports_duplicate_custom_node_paths_until_user_resolves_them(tmp_path: Path) -> None:
    comfy = tmp_path / "ComfyUI"
    primary = comfy / "custom_nodes"
    external = tmp_path / "external-nodes"
    for root in (primary, external):
        node = root / "duplicate-node"
        node.mkdir(parents=True)
        (node / "__init__.py").write_text("NODE_CLASS_MAPPINGS = {}\n", encoding="utf-8")
    (comfy / "comfy").mkdir()
    (comfy / "main.py").write_text("", encoding="utf-8")
    (comfy / "requirements.txt").write_text("", encoding="utf-8")
    with patch("comfy_setup.exporter._custom_roots", return_value=[primary, external]):
        from comfy_setup.exporter import _nodes
        with pytest.raises(ExportError, match="Duplicate custom-node identities") as error:
            _nodes(comfy, exact_refs=True, include_unpublished_plugins=True)
    assert str((primary / "duplicate-node").resolve()) in str(error.value)
    assert str((external / "duplicate-node").resolve()) in str(error.value)


def test_exact_export_accepts_explicit_duplicate_node_omission(tmp_path: Path) -> None:
    comfy = tmp_path / "ComfyUI"
    primary = comfy / "custom_nodes"
    external = tmp_path / "external-nodes"
    for root in (primary, external):
        node = root / "duplicate-node"
        node.mkdir(parents=True)
        (node / "__init__.py").write_text("NODE_CLASS_MAPPINGS = {}\n", encoding="utf-8")
    with patch("comfy_setup.exporter._custom_roots", return_value=[primary, external]):
        from comfy_setup.exporter import _nodes

        nodes, warnings, _embedded = _nodes(
            comfy,
            exact_refs=True,
            include_unpublished_plugins=True,
            omitted_node_paths={(external / "duplicate-node").resolve()},
        )
    assert len(nodes) == 1
    assert any(str((external / "duplicate-node").resolve()) in warning for warning in warnings)


def test_exact_export_embeds_uncommitted_comfyui_source_changes(tmp_path: Path) -> None:
    comfy = tmp_path / "ComfyUI"
    comfy.mkdir()
    (comfy / "comfy").mkdir()
    (comfy / "main.py").write_text("print('working')\n", encoding="utf-8")
    (comfy / "requirements.txt").write_text("\n", encoding="utf-8")
    subprocess.run(["git", "init", str(comfy)], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-C", str(comfy), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(comfy), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(comfy), "add", "."], check=True)
    subprocess.run(["git", "-C", str(comfy), "commit", "-m", "base"], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-C", str(comfy), "remote", "add", "origin", "https://github.com/example/ComfyUI.git"], check=True)
    (comfy / "main.py").write_text("print('locally modified')\n", encoding="utf-8")

    with patch("comfy_setup.exporter._venv_python", return_value=Path("/fake/python")), patch(
        "comfy_setup.exporter._packages",
        return_value=(
            {"click": "8.1.8", "torch": "2.8.0+cpu"},
            [],
            "3.13",
            [{"name": "click", "version": "8.1.8", "direct_url": None}],
            {"python": "3.13.5", "implementation": "CPython", "os": "linux", "architecture": "x86_64", "accelerator": "cpu"},
        ),
    ), patch(
        "comfy_setup.exporter.build_environment_lock",
        return_value=({"schema_version": 1, "mode": "exact", "complete": True, "packages": [{"name": "click", "version": "8.1.8", "requirement": "click==8.1.8", "install": True}], "nonportable_packages": []}, [], {}),
    ):
        with patch("comfy_setup.exporter._commit_is_known_on_origin", return_value=True):
            profile, warnings = build_profile_from_installation(comfy, name="Dirty")
        source = profile["comfyui"]["source"]
        assert source["type"] == "remote"
        assert source["overlay"]["changed_files"] == ["main.py"]
        assert profile["_comfyui_overlay_paths"]["main.py"] == str(comfy / "main.py")
        assert "_embedded_comfyui_path" not in profile
        assert any("compact ComfyUI source overlay" in warning for warning in warnings)


def test_exact_export_embeds_local_only_main_commit(tmp_path: Path) -> None:
    comfy = tmp_path / "ComfyUI"
    comfy.mkdir()
    (comfy / "comfy").mkdir()
    (comfy / "main.py").write_text("print('working')\n", encoding="utf-8")
    (comfy / "requirements.txt").write_text("\n", encoding="utf-8")
    subprocess.run(["git", "init", str(comfy)], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-C", str(comfy), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(comfy), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(comfy), "add", "."], check=True)
    subprocess.run(["git", "-C", str(comfy), "commit", "-m", "base"], check=True, stdout=subprocess.DEVNULL)
    (comfy / "main.py").write_text("print('local-only commit')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(comfy), "add", "main.py"], check=True)
    subprocess.run(["git", "-C", str(comfy), "commit", "-m", "local-only"], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-C", str(comfy), "remote", "add", "origin", "https://github.com/example/ComfyUI.git"], check=True)

    with patch("comfy_setup.exporter._venv_python", return_value=Path("/fake/python")), patch(
        "comfy_setup.exporter._packages",
        return_value=(
            {"click": "8.1.8", "torch": "2.8.0+cpu"},
            [],
            "3.13",
            [{"name": "click", "version": "8.1.8", "direct_url": None}],
            {"python": "3.13.5", "implementation": "CPython", "os": "linux", "architecture": "x86_64", "accelerator": "cpu"},
        ),
    ), patch(
        "comfy_setup.exporter.build_environment_lock",
        return_value=({"schema_version": 1, "mode": "exact", "complete": True, "packages": [{"name": "click", "version": "8.1.8", "requirement": "click==8.1.8", "install": True}], "nonportable_packages": []}, [], {}),
    ):
        profile, warnings = build_profile_from_installation(comfy, name="Unpushed")
        source = profile["comfyui"]["source"]
        assert source["type"] == "remote"
        assert source["overlay"]["changed_files"] == ["main.py"]
        assert profile["comfyui"]["preferred_commit"] == source["overlay"]["base_commit"]
        assert "_embedded_comfyui_path" not in profile
        assert any("compact ComfyUI source overlay" in warning for warning in warnings)


def test_exact_profile_source_refs_cannot_be_disabled_at_install_time(tmp_path: Path) -> None:
    lock = {
        "schema_version": 1,
        "mode": "exact",
        "complete": True,
        "source": {"python": "3.13.5", "os": "linux", "architecture": "x86_64"},
        "packages": [{"name": "click", "version": "8.1.8", "requirement": "click==8.1.8", "install": True}],
        "nonportable_packages": [],
    }
    profile = exact_profile(lock)
    profile["comfyui"]["exact"] = True
    options = InstallOptions(
        target_dir=tmp_path / "ComfyUI",
        python_version="3.13",
        accelerator="nvidia",
        selected_nodes=set(),
        selected_acceleration=set(),
        auto_install_system=False,
        pin_exact_refs=False,
    )
    engine = InstallerEngine(profile, platform_info(), options)
    commands: list[tuple[str, ...]] = []
    engine.runner.run = lambda command, **kwargs: commands.append(tuple(str(item) for item in command)) or 0  # type: ignore[method-assign]
    engine._prepare_repository()
    assert any(command[:2] == ("git", "checkout") and command[2] == "a" * 40 for command in commands)




def test_exact_environment_audit_rejects_unrecorded_packages(tmp_path: Path) -> None:
    lock = {
        "schema_version": 1,
        "mode": "exact",
        "complete": True,
        "source": {"python": "3.13.5", "os": "linux", "architecture": "x86_64"},
        "packages": [
            {"name": "click", "version": "8.1.8", "requirement": "click==8.1.8", "install": True, "portable": True, "source": "index"},
            {"name": "numpy", "version": "2.4.4", "requirement": "numpy==2.4.4", "install": True, "portable": True, "source": "index"},
        ],
        "nonportable_packages": [],
    }
    options = InstallOptions(
        target_dir=tmp_path / "ComfyUI",
        python_version="3.13",
        accelerator="nvidia",
        selected_nodes=set(),
        selected_acceleration=set(),
        auto_install_system=False,
    )
    engine = InstallerEngine(exact_profile(lock), platform_info(), options)
    engine.runner.capture = lambda *args, **kwargs: subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps([
            ["click", "8.1.8"],
            ["numpy", "2.4.4"],
            ["pip", "26.0"],
            ["surprise-package", "1.0"],
        ]),
        stderr="",
    )  # type: ignore[method-assign]

    with pytest.raises(InstallerError, match="surprise-package==1.0"):
        engine._verify_environment_lock()


def test_exact_environment_audit_uses_selected_mutually_exclusive_provider(tmp_path: Path) -> None:
    lock = {
        "schema_version": 1,
        "mode": "exact",
        "complete": True,
        "source": {"python": "3.13.5", "os": "linux", "architecture": "x86_64"},
        "packages": [
            {"name": "onnxruntime", "version": "1.27.0", "install": False, "portable": True, "source": "managed", "managed_by": "accelerated-package"},
            {"name": "onnxruntime-gpu", "version": "1.27.0", "install": False, "portable": True, "source": "managed", "managed_by": "accelerated-package"},
        ],
        "nonportable_packages": [],
    }
    profile = exact_profile(lock)
    profile["accelerated_packages"] = [{
        "id": "onnxruntime", "name": "ONNX Runtime", "kind": "dynamic-onnxruntime",
        "package_name": "onnxruntime-gpu", "version": "1.27.0", "selected": True,
        "platforms": ["linux"], "accelerators": ["nvidia"],
    }]
    options = InstallOptions(
        target_dir=tmp_path / "ComfyUI", python_version="3.13", accelerator="nvidia",
        selected_nodes=set(), selected_acceleration={"onnxruntime"}, auto_install_system=False,
    )
    engine = InstallerEngine(profile, platform_info(), options)
    engine.runner.capture = lambda *args, **kwargs: subprocess.CompletedProcess(
        args=[], returncode=0, stdout=json.dumps([["onnxruntime-gpu", "1.27.0"]]), stderr=""
    )  # type: ignore[method-assign]

    engine._verify_environment_lock()


def test_existing_exact_profile_recovers_omitted_managed_accelerator(tmp_path: Path) -> None:
    lock = {
        "schema_version": 1, "mode": "exact", "complete": True,
        "source": {"python": "3.13.5", "os": "linux", "architecture": "x86_64"},
        "packages": [{
            "name": "pyopengl-accelerate", "version": "3.1.10", "install": False,
            "portable": True, "source": "managed", "managed_by": "accelerated-package",
        }],
        "nonportable_packages": [],
    }
    profile = exact_profile(lock)
    profile["accelerated_packages"] = []
    options = InstallOptions(
        target_dir=tmp_path / "ComfyUI", python_version="3.13", accelerator="nvidia",
        selected_nodes=set(), selected_acceleration=set(), auto_install_system=False,
    )
    selected = InstallerEngine(profile, platform_info(), options).selected_acceleration()

    assert selected == [{
        "id": "pyopengl-accelerate", "name": "PyOpenGL Accelerate",
        "import_name": "OpenGL_accelerate", "kind": "pip-wheel-preferred",
        "package": "PyOpenGL-accelerate==3.1.10",
        "platforms": ["windows", "linux", "macos"],
        "accelerators": ["nvidia", "rocm", "mps", "cpu"],
        "selected": True, "exact": True,
    }]


def test_final_validation_rechecks_exact_environment_after_startup(tmp_path: Path) -> None:
    lock = {
        "schema_version": 1,
        "mode": "exact",
        "complete": True,
        "source": {"python": "3.13.5", "os": "linux", "architecture": "x86_64"},
        "packages": [
            {"name": "click", "version": "8.1.8", "requirement": "click==8.1.8", "install": True, "portable": True, "source": "index"},
        ],
        "nonportable_packages": [],
    }
    profile = exact_profile(lock)
    profile["post_install"] = {"validate_imports": []}
    options = InstallOptions(
        target_dir=tmp_path / "ComfyUI",
        python_version="3.13",
        accelerator="nvidia",
        selected_nodes=set(),
        selected_acceleration=set(),
        auto_install_system=False,
    )
    engine = InstallerEngine(profile, platform_info(), options)
    engine.runner.capture = lambda *args, **kwargs: subprocess.CompletedProcess(
        args=[], returncode=0, stdout="{}\n", stderr=""
    )  # type: ignore[method-assign]
    with patch.object(engine, "_validate_comfyui_startup", return_value={"ok": True}), patch.object(
        engine, "_verify_environment_lock"
    ) as verify:
        result = engine._validate()

    assert result["comfyui_startup"] == {"ok": True}
    verify.assert_called_once_with()

def test_inventory_rejects_duplicate_custom_node_identity(tmp_path: Path) -> None:
    inventory_root = tmp_path / "comfyui-installer-inventory-duplicates"
    nodes = inventory_root / "custom_nodes"
    nodes.mkdir(parents=True)
    with (nodes / "inventory.tsv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            delimiter="\t",
            fieldnames=[
                "folder", "root", "repository", "manager_id", "manager_version",
                "branch", "commit", "dirty", "metadata", "kind", "payload",
            ],
        )
        writer.writeheader()
        writer.writerow({"folder": "Duplicate_Node", "root": "/first", "repository": "https://github.com/example/first.git", "kind": "directory"})
        writer.writerow({"folder": "duplicate-node", "root": "/second", "repository": "https://github.com/example/second.git", "kind": "directory"})
    archive = tmp_path / "duplicate-inventory.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(inventory_root, arcname=inventory_root.name)

    with pytest.raises(InventoryError, match="duplicate custom-node identity"):
        load_inventory(archive)

def test_inventory_conversion_keeps_complete_lock_and_embedded_wheel(tmp_path: Path) -> None:
    inventory_root = tmp_path / "comfyui-installer-inventory-test"
    nodes = inventory_root / "custom_nodes"
    python_dir = inventory_root / "python"
    main_repo = inventory_root / "main_repo"
    nodes.mkdir(parents=True)
    python_dir.mkdir()
    main_repo.mkdir()
    with (nodes / "inventory.tsv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            delimiter="\t",
            fieldnames=["folder", "root", "repository", "manager_id", "manager_version", "branch", "commit", "dirty", "metadata", "kind", "payload"],
        )
        writer.writeheader()
    (python_dir / "runtime.txt").write_text("version: 3.13.5\ntorch: 2.12.1+cu130\ntorch_cuda: 13.0\n", encoding="utf-8")
    (python_dir / "pip-freeze.txt").write_text("click==8.1.8\nnumpy==2.4.4\n", encoding="utf-8")
    wheel_payload = "embedded_wheels/flash_attn-2.8.3.post1-cp313-cp313-linux_x86_64.whl"
    wheel_file = python_dir / wheel_payload
    wheel_file.parent.mkdir()
    wheel_file.write_bytes(b"inventory wheel")
    import hashlib
    lock = {
        "schema_version": 1,
        "mode": "exact",
        "complete": True,
        "source": {"python": "3.13.5", "implementation": "CPython", "os": "linux", "architecture": "x86_64"},
        "packages": [
            {"name": "click", "version": "8.1.8", "requirement": "click==8.1.8", "install": True, "portable": True, "source": "index"},
            {"name": "numpy", "version": "2.4.4", "requirement": "numpy==2.4.4", "install": True, "portable": True, "source": "index"},
            {"name": "flash-attn", "version": "2.8.3.post1", "requirement": "flash-attn==2.8.3.post1", "install": True, "portable": True, "source": "embedded-wheel", "payload": wheel_payload, "sha256": hashlib.sha256(b"inventory wheel").hexdigest()},
        ],
        "nonportable_packages": [],
    }
    (python_dir / "environment-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    (main_repo / "git.txt").write_text(f"commit={'b' * 40}\ndirty=no\n", encoding="utf-8")
    archive = tmp_path / "inventory.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(inventory_root, arcname=inventory_root.name)

    inventory = load_inventory(archive)
    profile = profile_from_inventory(
        inventory,
        profile_name="Inventory",
        profile_id="inventory",
        repository="https://github.com/Comfy-Org/ComfyUI.git",
    )
    assert {item["name"] for item in profile["environment_lock"]["packages"]} == {"click", "numpy", "flash-attn"}
    assert profile["python"]["fallbacks"] == ["3.13"]
    assert profile["torch"]["exact_backend"] is True
    assert profile["torch"]["source_accelerator"] == "nvidia"
    assert profile["torch"]["source_backend_version"] == "13.0"
    assert profile["torch"]["exact_backend_index"].endswith("/cu130")
    assert wheel_payload in profile["_embedded_wheel_data"]
    assert profile["accelerated_packages"] == []
    bundle = write_profile_bundle(profile, tmp_path / "inventory.comfyuisetup")
    assert read_profile_bundle(bundle)["environment_lock"] == profile["environment_lock"]
