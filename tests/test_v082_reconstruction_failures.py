from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from comfy_setup.engine import InstallerEngine
from comfy_setup.exporter import _accelerated, _install_py_policy
from comfy_setup.models import InstallOptions, PlatformInfo
from comfy_setup.wheels import WheelManager


def info() -> PlatformInfo:
    return PlatformInfo(
        os_name="linux",
        system="Linux",
        release="test",
        architecture="x86_64",
        is_wsl=True,
        package_manager="apt",
        accelerator="nvidia",
        gpu_name="RTX test",
        compute_capability=8.9,
        cuda_version="13.0",
    )


def options(target: Path) -> InstallOptions:
    return InstallOptions(
        target_dir=target,
        python_version="3.13",
        accelerator="nvidia",
        selected_nodes=set(),
        selected_acceleration=set(),
        auto_install_system=False,
        allow_source_builds=True,
        use_current_checkout=False,
    )


class RecordingRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.capture_commands: list[list[str]] = []

    def run(self, command, *, cwd=None, **kwargs):
        values = [str(value) for value in command]
        self.commands.append(values)
        if "git" in values[:1] and "clone" in values:
            Path(values[-1]).mkdir(parents=True, exist_ok=True)
        return 0

    def capture(self, command, **kwargs):
        values = [str(value) for value in command]
        self.capture_commands.append(values)
        completed = subprocess.run(values, text=True, capture_output=True, check=False)
        return SimpleNamespace(
            returncode=completed.returncode,
            stdout=(completed.stdout or "") + (completed.stderr or ""),
        )

    def log(self, message: str) -> None:
        pass


def wheel_manager(tmp_path: Path, runner: RecordingRunner) -> WheelManager:
    target = tmp_path / "ComfyUI"
    target.mkdir()
    return WheelManager(
        runner=runner,  # type: ignore[arg-type]
        uv=Path("uv"),
        target_python=Path(sys.executable),
        target_dir=target,
        profile={
            "id": "test",
            "torch": {
                "version": "2.12.1",
                "torchvision": "0.27.1",
                "torchaudio": "2.11.0",
                "index_urls": {"nvidia": "https://download.pytorch.org/whl/cu130"},
            },
        },
        platform_info=info(),
        options=options(target),
        decision_provider=lambda item, message, path: "build",
    )


def test_exact_sageattention_build_uses_repository_not_missing_pypi_release(
    tmp_path: Path, monkeypatch
) -> None:
    runner = RecordingRunner()
    manager = wheel_manager(tmp_path, runner)
    calls: list[str] = []
    monkeypatch.setattr(manager, "_build_from_source", lambda item: calls.append("source") or [])
    monkeypatch.setattr(manager, "_build_package_from_index", lambda item: calls.append("index") or [])

    manager._resolve_or_build(
        {
            "id": "sageattention",
            "name": "SageAttention",
            "package": "sageattention==2.2.0",
            "source_repository": "https://github.com/thu-ml/SageAttention.git",
            "source_ref": "main",
            "exact": True,
        },
        RuntimeError("no wheel"),
    )

    assert calls == ["source"]


def test_build_torch_probe_is_valid_python_and_preserves_runtime_versions(tmp_path: Path) -> None:
    runner = RecordingRunner()
    manager = wheel_manager(tmp_path, runner)
    manager._install_build_torch(tmp_path / "build-python")

    probe = runner.capture_commands[0]
    assert probe[-2] == "-c"
    assert "\\n'.join" in probe[-1]
    assert runner.commands
    install = runner.commands[-1]
    assert "pip" in install and "install" in install
    assert any(value.startswith("torch==") for value in install)


def test_repository_backed_node_is_cloned_without_invoking_manager(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "ComfyUI"
    target.mkdir()
    profile = {"environment_lock": {}, "nodes": [], "torch": {}, "comfyui": {}}
    engine = InstallerEngine(profile, info(), options(target))
    runner = RecordingRunner()
    engine.runner = runner  # type: ignore[assignment]
    monkeypatch.setattr(
        engine,
        "_install_manager_node",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Manager must not run")),
    )
    node = {
        "id": "known",
        "name": "Known node",
        "folder": "known-node",
        "source": {
            "type": "remote",
            "manager_id": "known-id",
            "repository": "https://github.com/example/known-node.git",
            "preferred": "manager",
        },
    }

    installed, lifecycle = engine._clone_or_update_remote_node(node)

    assert installed == target / "custom_nodes" / "known-node"
    assert lifecycle is False
    assert any(command[:2] == ["git", "clone"] for command in runner.commands)


def test_manager_only_acquisition_disables_dependency_resolution(tmp_path: Path) -> None:
    target = tmp_path / "ComfyUI"
    target.mkdir()
    engine = InstallerEngine({}, info(), options(target))

    class ManagerRunner(RecordingRunner):
        def run(self, command, **kwargs):
            result = super().run(command, **kwargs)
            (target / "custom_nodes" / "manager-node").mkdir(parents=True, exist_ok=True)
            return result

    runner = ManagerRunner()
    engine.runner = runner  # type: ignore[assignment]
    node = {
        "id": "manager-node",
        "name": "Manager node",
        "folder": "manager-node",
        "source": {"type": "remote", "manager_id": "manager-node"},
    }

    installed = engine._install_manager_node(node, "manager-node")

    assert installed == target / "custom_nodes" / "manager-node"
    flattened = runner.commands[0]
    assert "--no-deps" in flattened
    assert "--uv-compile" not in flattened


def test_exact_lock_audits_node_requirements_without_reinstalling_them(tmp_path: Path) -> None:
    target = tmp_path / "ComfyUI"
    node_dir = target / "custom_nodes" / "node"
    node_dir.mkdir(parents=True)
    (node_dir / "requirements.txt").write_text("numpy>=2\n", encoding="utf-8")
    profile = {
        "environment_lock": {
            "mode": "exact",
            "complete": True,
            "packages": [
                {"name": "numpy", "version": "2.4.4", "requirement": "numpy==2.4.4"}
            ],
        },
        "dependency_resolution": {"resolved_versions": {"numpy": "2.4.4"}},
        "constraints": {},
        "nodes": [],
        "torch": {},
        "comfyui": {},
    }
    messages: list[str] = []
    engine = InstallerEngine(profile, info(), options(target), log=messages.append)
    engine._uv_pip = lambda *args, **kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError("node requirements must not be reinstalled after an exact lock")
    )

    engine._install_node_requirements(
        {"id": "node", "name": "Node", "requirements": "requirements.txt"},
        node_dir,
    )

    reconciled = target / ".comfy-setup" / "requirements-node.txt"
    assert reconciled.is_file()
    assert "numpy>=2" in reconciled.read_text(encoding="utf-8")
    assert any("will not be installed a second time" in message for message in messages)


def test_dependency_only_install_script_is_skipped_but_comfy_env_lifecycle_runs(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "ComfyUI"
    target.mkdir()
    profile = {
        "environment_lock": {"mode": "exact", "complete": True, "packages": [{"name": "x"}]},
        "nodes": [], "torch": {}, "comfyui": {},
    }
    engine = InstallerEngine(profile, info(), options(target))
    runner = RecordingRunner()
    engine.runner = runner  # type: ignore[assignment]
    monkeypatch.setattr(engine, "_install_node_requirements", lambda node, node_dir: None)

    dependency_node = target / "custom_nodes" / "dependency-node"
    dependency_node.mkdir(parents=True)
    (dependency_node / "install.py").write_text(
        "import os,sys\nos.system(f'{sys.executable} -m pip install cupy-wheel')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(engine, "_acquire_node", lambda node: (dependency_node, False))
    engine._install_node({"id": "dep", "name": "Dependency", "run_install_py": True})
    assert runner.commands == []

    required_node = target / "custom_nodes" / "required-node"
    required_node.mkdir(parents=True)
    (required_node / "install.py").write_text("print('bootstrap')\n", encoding="utf-8")
    (required_node / "comfy-env.toml").write_text("[env]\n", encoding="utf-8")
    monkeypatch.setattr(engine, "_acquire_node", lambda node: (required_node, False))
    engine._install_node({"id": "required", "name": "Required", "run_install_py": True})
    assert runner.commands[-1][-1] == "install.py"


def test_exporter_marks_dependency_only_installers_and_captures_acceleration_commit(
    tmp_path: Path,
) -> None:
    node = tmp_path / "node"
    node.mkdir()
    (node / "install.py").write_text(
        "import os,sys\nos.system(f'{sys.executable} -m pip install cupy-wheel')\n",
        encoding="utf-8",
    )
    assert _install_py_policy(node) == "dependencies-only"

    items = _accelerated(
        {"sageattention": "2.2.0"},
        distributions=[
            {
                "name": "sageattention",
                "version": "2.2.0",
                "direct_url": {
                    "url": "https://github.com/thu-ml/SageAttention.git",
                    "vcs_info": {"vcs": "git", "commit_id": "abc123"},
                },
            }
        ],
        exact=True,
    )
    sage = next(item for item in items if item["id"] == "sageattention")
    assert sage["source_repository"] == "https://github.com/thu-ml/SageAttention.git"
    assert sage["source_ref"] == "abc123"
    assert sage["captured_source_commit"] == "abc123"
    assert sage["verify_source_version"] is True


def test_exact_acceleration_preserves_locked_distribution_family(tmp_path: Path, monkeypatch) -> None:
    runner = RecordingRunner()
    manager = wheel_manager(tmp_path, runner)
    manager.profile["environment_lock"] = {
        "mode": "exact",
        "complete": True,
        "packages": [
            {
                "name": "cupy-cuda12x",
                "version": "14.1.1",
                "install": False,
                "managed_by": "accelerated-package",
            },
            {
                "name": "onnxruntime",
                "version": "1.27.0",
                "install": False,
                "managed_by": "accelerated-package",
            },
            {
                "name": "tensorrt-cu13",
                "version": "10.14.1.48.post1",
                "install": False,
                "managed_by": "accelerated-package",
            },
        ],
    }
    installed: list[tuple[str, str]] = []
    monkeypatch.setattr(manager, "_uv_pip", lambda *args, **kwargs: 0)
    monkeypatch.setattr(
        manager,
        "_install_package_from_indexes",
        lambda package, import_name, **kwargs: installed.append((package, import_name)),
    )

    manager.install_item(
        {
            "id": "cupy",
            "name": "CuPy",
            "import_name": "cupy",
            "kind": "dynamic-cupy",
            "package_name": "cupy-cuda13x",
            "version": "13.6.0",
            "exact": True,
        }
    )
    manager.install_item(
        {
            "id": "onnxruntime",
            "name": "ONNX Runtime",
            "import_name": "onnxruntime",
            "kind": "dynamic-onnxruntime",
            "package_name": "onnxruntime-gpu",
            "version": "1.28.0",
            "exact": True,
        }
    )
    manager.install_item(
        {
            "id": "tensorrt",
            "name": "TensorRT",
            "import_name": "tensorrt",
            "kind": "dynamic-tensorrt",
            "package_name": "tensorrt-cu12",
            "versions": {"12": "10.13.0", "13": "10.15.0"},
            "exact": True,
        }
    )

    assert installed == [
        ("cupy-cuda12x==14.1.1", "cupy"),
        ("onnxruntime==1.27.0", "onnxruntime"),
        ("tensorrt-cu13==10.14.1.48.post1", "tensorrt"),
    ]


def test_source_build_prepares_tools_before_reapplying_exact_torch(tmp_path: Path, monkeypatch) -> None:
    runner = RecordingRunner()
    manager = wheel_manager(tmp_path, runner)
    build_venv = tmp_path / "build-venv"
    source = tmp_path / "source"
    source.mkdir()
    order: list[str] = []

    monkeypatch.setattr(manager, "_build_environment", lambda package_id: (build_venv, source))
    monkeypatch.setattr(manager, "_checkout_source", lambda item, source_dir: source)
    monkeypatch.setattr(manager, "_install_build_torch", lambda build_python: order.append("torch"))
    monkeypatch.setattr(manager, "_verify_built_wheels", lambda item, wheels: None)
    monkeypatch.setattr(manager, "_verify_installed_item", lambda item: None)
    monkeypatch.setattr(manager, "_backup", lambda *args, **kwargs: None)
    monkeypatch.setattr(manager, "_uv_pip", lambda *args, **kwargs: 0)

    original_run = runner.run

    def run(command, **kwargs):
        values = [str(value) for value in command]
        if values[:3] == [str(manager.uv), "pip", "install"]:
            order.append("tools")
        if "wheel" in values and "--wheel-dir" in values:
            wheel_dir = Path(values[values.index("--wheel-dir") + 1])
            wheel_dir.mkdir(parents=True, exist_ok=True)
            (wheel_dir / "sageattention-2.2.0-py3-none-any.whl").write_bytes(b"wheel")
        return original_run(command, **kwargs)

    monkeypatch.setattr(runner, "run", run)
    manager._build_from_source(
        {
            "id": "sageattention",
            "name": "SageAttention",
            "package": "sageattention==2.2.0",
            "import_name": "sageattention",
            "source_repository": "https://github.com/thu-ml/SageAttention.git",
            "source_ref": "main",
            "source_subdir": ".",
            "build_requirements": ["einops"],
        }
    )

    assert order[:2] == ["tools", "torch"]


def test_exporter_records_exact_acceleration_distribution_names() -> None:
    items = _accelerated(
        {
            "cupy-cuda12x": "14.1.1",
            "tensorrt-cu13": "10.14.1.48.post1",
            "onnxruntime": "1.27.0",
            "pyopengl-accelerate": "3.1.10",
        },
        exact=True,
    )
    by_id = {item["id"]: item for item in items}
    assert by_id["cupy"]["package_name"] == "cupy-cuda12x"
    assert by_id["tensorrt"]["package_name"] == "tensorrt-cu13"
    assert by_id["onnxruntime"]["package_name"] == "onnxruntime"
    assert by_id["pyopengl-accelerate"]["package"] == "PyOpenGL-accelerate==3.1.10"
    assert by_id["pyopengl-accelerate"]["exact"] is True
