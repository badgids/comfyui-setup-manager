from __future__ import annotations

import csv
import json
import re
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any

from .compatibility_tags import build_compatibility_tags
from .environment_lock import build_environment_lock
from .profile import EMBEDDED_ROOT, PROFILE_KIND, PROFILE_SCHEMA


class InventoryError(RuntimeError):
    pass


def _safe_extract(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:*") as tar:
        root = destination.resolve()
        for member in tar.getmembers():
            target = (destination / member.name).resolve()
            if root not in target.parents and target != root:
                raise InventoryError(f"Unsafe archive member: {member.name}")
        tar.extractall(destination, filter="data")


def _find_inventory_root(extracted: Path) -> Path:
    candidates = [
        path
        for path in extracted.iterdir()
        if path.is_dir() and (path / "custom_nodes" / "inventory.tsv").exists()
    ]
    if len(candidates) != 1:
        raise InventoryError(
            "The archive must contain one inventory root with custom_nodes/inventory.tsv."
        )
    return candidates[0]


def _node_reports(root: Path, folder: str) -> list[Path]:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", folder)
    custom_root = root / "custom_nodes"
    return [
        path
        for path in custom_root.iterdir()
        if path.is_dir()
        and (
            path.name.endswith(f"_{normalized}")
            or path.name.startswith(folder.replace(" ", "_"))
            or (path / folder).exists()
        )
    ]


def _metadata_for_node(root: Path, folder: str) -> dict[str, str | None]:
    repository = None
    version = None
    manager_id = None
    manager_version = None
    repo_pattern = re.compile(r'Repository\s*=\s*["\']([^"\']+)', re.IGNORECASE)
    version_pattern = re.compile(r'version\s*=\s*["\']([^"\']+)', re.IGNORECASE)
    name_pattern = re.compile(r'^name\s*=\s*["\']([^"\']+)', re.IGNORECASE | re.MULTILINE)
    publisher_pattern = re.compile(r'PublisherId\s*=\s*["\']([^"\']+)', re.IGNORECASE)
    for node_dir in _node_reports(root, folder):
        candidates = [node_dir / "pyproject.toml", node_dir / "README.md"]
        candidates.extend(node_dir.glob("metadata/**/pyproject.toml"))
        candidates.extend(node_dir.glob("metadata/**/README.md"))
        for metadata in candidates:
            if not metadata.exists():
                continue
            text = metadata.read_text(encoding="utf-8", errors="ignore")
            repo_match = repo_pattern.search(text)
            version_match = version_pattern.search(text)
            name_match = name_pattern.search(text)
            publisher_match = publisher_pattern.search(text)
            if repo_match and not repository:
                repository = repo_match.group(1)
            if version_match and not version:
                version = version_match.group(1)
            if name_match and publisher_match and not manager_id:
                manager_id = name_match.group(1)
                manager_version = version_match.group(1) if version_match else None
    return {
        "repository": repository,
        "version": version,
        "manager_id": manager_id,
        "manager_version": manager_version,
    }


def _read_embedded_payload(root: Path, folder: str, payload_value: str, kind: str) -> dict[str, bytes]:
    data: dict[str, bytes] = {}
    reports = _node_reports(root, folder)
    for report in reports:
        if payload_value:
            payload = report / payload_value
            if payload.is_dir():
                for file_path in payload.rglob("*"):
                    if file_path.is_file() and not file_path.is_symlink():
                        data[file_path.relative_to(payload).as_posix()] = file_path.read_bytes()
                if data:
                    return data
            elif payload.is_file():
                return {payload.name: payload.read_bytes()}
        if kind == "standalone-python":
            candidate = report / folder
            if candidate.is_file():
                return {candidate.name: candidate.read_bytes()}
        fallback = report / "embedded_plugin"
        if fallback.is_dir():
            for file_path in fallback.rglob("*"):
                if file_path.is_file() and not file_path.is_symlink():
                    data[file_path.relative_to(fallback).as_posix()] = file_path.read_bytes()
            if data:
                return data
    return data


def load_inventory(archive: Path) -> dict[str, Any]:
    archive = archive.expanduser().resolve()
    if not archive.exists():
        raise InventoryError(f"Inventory archive not found: {archive}")

    with tempfile.TemporaryDirectory(prefix="comfy-inventory-") as temporary:
        extracted = Path(temporary)
        _safe_extract(archive, extracted)
        root = _find_inventory_root(extracted)

        with (root / "custom_nodes" / "inventory.tsv").open(
            newline="", encoding="utf-8"
        ) as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))

        nodes: list[dict[str, Any]] = []
        embedded_plugins: dict[str, dict[str, bytes]] = {}
        seen: set[str] = set()
        for row in rows:
            folder = row.get("folder", "").strip()
            if not folder:
                continue
            node_id = re.sub(r"[^a-z0-9]+", "-", Path(folder).stem.lower()).strip("-")
            if not node_id:
                continue
            if node_id in seen:
                raise InventoryError(
                    f"The inventory contains duplicate custom-node identity {folder!r} across configured roots. "
                    "An exact profile cannot silently discard one of the working node trees."
                )
            seen.add(node_id)

            repository = (
                row.get("repository", "").strip()
                or row.get("repo_url", "").strip()
                or None
            )
            manager_id = row.get("manager_id", "").strip() or None
            manager_version = row.get("manager_version", "").strip() or None
            metadata_value = row.get("metadata", "") or row.get("install_metadata", "")
            metadata = _metadata_for_node(root, folder)
            repository = repository or metadata.get("repository")
            manager_id = manager_id or metadata.get("manager_id")
            manager_version = manager_version or metadata.get("manager_version")
            reference_version = metadata.get("version")

            if "requirements-no-cupy.txt" in metadata_value:
                requirements = "requirements-no-cupy.txt"
            elif "requirements.txt" in metadata_value:
                requirements = "requirements.txt"
            else:
                requirements = None

            lower_identity = f"{folder} {repository or ''} {manager_id or ''}".lower()
            nvidia_only = any(
                token in lower_identity
                for token in ["tensorrt", "gimm-vfi", "trellis2"]
            )
            platforms = ["windows", "linux"] if nvidia_only else ["windows", "linux", "macos"]
            accelerators = ["nvidia"] if nvidia_only else ["nvidia", "rocm", "mps", "cpu"]
            run_install = "install.py" in metadata_value
            if "frame-interpolation" in lower_identity:
                run_install = False

            source: dict[str, Any]
            payload_value = row.get("payload", "").strip()
            kind = row.get("kind", "directory").strip()
            payload_data = _read_embedded_payload(root, folder, payload_value, kind) if payload_value else {}
            if kind == "snapshot" and payload_data:
                source = {
                    "type": "snapshot",
                    "payload": f"{EMBEDDED_ROOT}/{node_id}",
                    "layout": "directory",
                }
                if repository:
                    source["repository"] = repository
                if manager_id:
                    source["manager_id"] = manager_id
                if manager_version:
                    source["manager_version"] = manager_version
                ref = row.get("commit", "").strip() or row.get("branch", "").strip()
                if ref:
                    source["base_ref"] = ref
                embedded_plugins[node_id] = payload_data
            elif kind == "snapshot":
                raise InventoryError(
                    f"The inventory identifies {folder!r} as an exact source snapshot, but its payload is missing. "
                    "Collect the inventory again with the corrected collector."
                )
            elif repository or manager_id:
                source = {"type": "remote"}
                if manager_id:
                    source["manager_id"] = manager_id
                if manager_version:
                    source["manager_version"] = manager_version
                if repository:
                    source["repository"] = repository
                ref = row.get("commit", "").strip() or row.get("branch", "").strip()
                if ref:
                    source["ref"] = ref
                if row.get("commit", "").strip():
                    source["exact"] = True
                source["preferred"] = "git" if repository and ref else "manager"
            else:
                if not payload_data:
                    payload_data = _read_embedded_payload(root, folder, payload_value, kind)
                if not payload_data:
                    raise InventoryError(
                        f"The inventory omitted unpublished local plugin {folder!r}. An exact portable profile "
                        "cannot silently drop a node that existed in the working installation."
                    )
                source = {
                    "type": "embedded",
                    "payload": f"{EMBEDDED_ROOT}/{node_id}",
                    "layout": "file" if kind == "standalone-python" else "directory",
                }
                embedded_plugins[node_id] = payload_data

            nodes.append(
                {
                    "id": node_id,
                    "name": folder,
                    "folder": folder,
                    "source": source,
                    "reference_version": reference_version,
                    "description": f"Imported from inventory: {folder}",
                    "platforms": platforms,
                    "accelerators": accelerators,
                    "requirements": requirements,
                    "run_install_py": run_install,
                    "selected": True,
                }
            )

        freeze_path = root / "python" / "pip-freeze.txt"
        freeze = freeze_path.read_text(encoding="utf-8", errors="ignore") if freeze_path.exists() else ""
        packages: dict[str, str] = {}
        raw_distributions: list[dict[str, Any]] = []
        for line in freeze.splitlines():
            stripped = line.strip()
            match = re.match(r"^([A-Za-z0-9_.-]+)==([^\s]+)$", stripped)
            if match:
                package_name = match.group(1).lower().replace("_", "-")
                packages[package_name] = match.group(2)
                raw_distributions.append(
                    {"name": package_name, "version": match.group(2), "direct_url": None}
                )
                continue
            direct = re.match(r"^([A-Za-z0-9_.-]+)\s+@\s+(.+)$", stripped)
            if direct:
                package_name = direct.group(1).lower().replace("_", "-")
                location = direct.group(2)
                escaped_name = re.escape(package_name).replace(r"\-", "[-_]")
                wheel_version = re.search(
                    rf"{escaped_name}[-_]([0-9][A-Za-z0-9.+!-]*)[-_]",
                    location,
                    re.IGNORECASE,
                )
                if wheel_version:
                    packages[package_name] = wheel_version.group(1)
                    location = location.strip()
                    direct_url: dict[str, Any] = {"url": location}
                    git_match = re.match(r"git\+(https://.+?)@([0-9a-f]{7,40})(?:#.*)?$", location)
                    if git_match:
                        direct_url = {
                            "url": git_match.group(1),
                            "vcs_info": {"vcs": "git", "commit_id": git_match.group(2)},
                        }
                    raw_distributions.append(
                        {
                            "name": package_name,
                            "version": wheel_version.group(1),
                            "direct_url": direct_url,
                        }
                    )

        runtime_path = root / "python" / "runtime.txt"
        runtime = runtime_path.read_text(encoding="utf-8", errors="ignore") if runtime_path.exists() else ""
        python_match = re.search(r"version:\s*([0-9]+\.[0-9]+)", runtime)
        torch_match = re.search(r"torch:\s*([^\s+]+)", runtime)
        cuda_match = re.search(r"torch_cuda:\s*([^\s]+)", runtime)

        lock_path = root / "python" / "environment-lock.json"
        environment_lock: dict[str, Any] = {}
        if lock_path.is_file():
            try:
                loaded_lock = json.loads(lock_path.read_text(encoding="utf-8"))
                if isinstance(loaded_lock, dict):
                    environment_lock = loaded_lock
            except json.JSONDecodeError:
                environment_lock = {}
        if not environment_lock:
            if not raw_distributions:
                raw_distributions = [
                    {"name": name, "version": version, "direct_url": None}
                    for name, version in packages.items()
                ]
            environment_lock, _, _ = build_environment_lock(
                raw_distributions,
                source={
                    "python": python_match.group(1) if python_match else "3.12",
                    "implementation": "CPython",
                    "os": "",
                    "architecture": "",
                },
            )

        embedded_wheels: dict[str, bytes] = {}
        for package in environment_lock.get("packages", []) if isinstance(environment_lock, dict) else []:
            if not isinstance(package, dict) or package.get("source") != "embedded-wheel":
                continue
            payload = str(package.get("payload") or "")
            candidate = root / "python" / PurePosixPath(payload)
            if candidate.is_file():
                embedded_wheels[payload] = candidate.read_bytes()
            else:
                package["portable"] = False
                package["install"] = False
                package["reason"] = "The inventory archive is missing the recorded local wheel payload."
                environment_lock["complete"] = False
                missing = environment_lock.setdefault("nonportable_packages", [])
                if package.get("name") not in missing:
                    missing.append(package.get("name"))

        main_git = (root / "main_repo" / "git.txt").read_text(
            encoding="utf-8", errors="ignore"
        )
        commit_match = re.search(r"commit=([0-9a-f]{7,40})", main_git)
        dirty_match = re.search(r"^dirty=([^\s]+)", main_git, re.MULTILINE)
        origin_match = re.search(r"^origin_reachable=([^\s]+)", main_git, re.MULTILINE)

        return {
            "nodes": nodes,
            "embedded_plugins": embedded_plugins,
            "packages": packages,
            "environment_lock": environment_lock,
            "embedded_wheels": embedded_wheels,
            "python": python_match.group(1) if python_match else "3.12",
            "torch": torch_match.group(1) if torch_match else packages.get("torch", ""),
            "cuda": cuda_match.group(1) if cuda_match else None,
            "comfyui_commit": commit_match.group(1) if commit_match else None,
            "comfyui_dirty": dirty_match.group(1) if dirty_match else "unknown",
            "comfyui_origin_reachable": origin_match.group(1) if origin_match else "unknown",
        }


def _accelerated_packages(packages: dict[str, str], *, locked_wheel_names: set[str] | None = None, exact: bool = True) -> list[dict[str, Any]]:
    accelerated: list[dict[str, Any]] = []
    locked_wheel_names = locked_wheel_names or set()
    cupy_name = next((name for name in ("cupy-cuda13x", "cupy-cuda12x", "cupy") if name in packages), None)
    if cupy_name and not any(name in locked_wheel_names for name in ("cupy-cuda13x", "cupy-cuda12x", "cupy")):
        accelerated.append({
            "id": "cupy", "name": "CuPy", "import_name": "cupy",
            "kind": "dynamic-cupy", "package_name": cupy_name,
            "version": packages.get(cupy_name) or "13.6.0",
            "platforms": ["windows", "linux"], "accelerators": ["nvidia"],
            "selected": True, "exact": exact, "description": "CUDA array and kernel runtime."
        })
    tensorrt_versions: dict[str, str] = {}
    for major in ("12", "13"):
        value = packages.get(f"tensorrt-cu{major}") or packages.get(f"tensorrt_cu{major}")
        if value:
            tensorrt_versions[major] = value
    if tensorrt_versions and not any(name in locked_wheel_names for name in ("tensorrt", "tensorrt-cu12", "tensorrt-cu13", "tensorrt_cu12", "tensorrt_cu13")):
        source_major = next(iter(tensorrt_versions)) if len(tensorrt_versions) == 1 else None
        accelerated.append({
            "id": "tensorrt", "name": "TensorRT", "import_name": "tensorrt",
            "kind": "dynamic-tensorrt", "versions": tensorrt_versions,
            **({"package_name": f"tensorrt-cu{source_major}", "version": tensorrt_versions[source_major]} if source_major else {}),
            "platforms": ["windows", "linux"], "accelerators": ["nvidia"],
            "selected": True, "exact": exact,
            "description": "NVIDIA TensorRT runtime selected for the detected CUDA major version."
        })
    onnx_name = "onnxruntime-gpu" if "onnxruntime-gpu" in packages else "onnxruntime" if "onnxruntime" in packages else None
    onnx_version = packages.get(onnx_name) if onnx_name else None
    if onnx_version and onnx_name and not any(name in locked_wheel_names for name in ("onnxruntime", "onnxruntime-gpu")):
        accelerated.append({
            "id": "onnxruntime", "name": "ONNX Runtime", "import_name": "onnxruntime",
            "kind": "dynamic-onnxruntime", "package_name": onnx_name, "version": onnx_version,
            "platforms": ["windows", "linux", "macos"],
            "accelerators": ["nvidia", "rocm", "mps", "cpu"],
            "selected": True, "exact": exact, "description": "ONNX execution runtime."
        })
    for package_name, item_id, display, import_name, repository in (
        ("flash-attn", "flash-attn", "FlashAttention 2", "flash_attn", "https://github.com/Dao-AILab/flash-attention.git"),
        ("sageattention", "sageattention", "SageAttention", "sageattention", "https://github.com/thu-ml/SageAttention.git"),
    ):
        version = packages.get(package_name)
        if version and package_name not in locked_wheel_names:
            accelerated.append({
                "id": item_id, "name": display, "import_name": import_name,
                "kind": "wheel-or-source", "package": f"{package_name}=={version}",
                "source_repository": repository, "source_ref": "main", "source_subdir": ".",
                "build_requirements": ["packaging", "setuptools", "wheel", "ninja", "einops"],
                "requires_cuda": True, "platforms": ["windows", "linux"],
                "accelerators": ["nvidia"], "selected": True, "exact": exact,
                "description": f"{display} with exact wheel/source-version reproduction."
            })
    return accelerated


def profile_from_inventory(
    inventory: dict[str, Any],
    *,
    profile_name: str,
    profile_id: str,
    repository: str,
) -> dict[str, Any]:
    packages = inventory.get("packages", {})
    if not inventory.get("comfyui_commit"):
        raise InventoryError(
            "The inventory does not contain an immutable ComfyUI Git commit. Refusing to create a profile that "
            "would silently install a different ComfyUI revision."
        )
    if inventory.get("comfyui_dirty") == "yes":
        raise InventoryError(
            "The inventoried ComfyUI checkout had uncommitted tracked changes. Commit those changes before "
            "collecting an inventory for exact reproduction."
        )
    if inventory.get("comfyui_origin_reachable") == "no":
        raise InventoryError(
            "The inventoried ComfyUI commit was not contained in its local origin-tracking refs. "
            "It may be local-only or unpushed, so another machine cannot be expected to clone it."
        )
    environment_lock = inventory.get("environment_lock", {})
    if isinstance(environment_lock, dict) and environment_lock.get("complete") is False:
        names = ", ".join(environment_lock.get("nonportable_packages", [])) or "unknown packages"
        raise InventoryError(
            "The inventory contains machine-local Python packages with no portable source: " + names
        )
    critical_names = [
        "numpy", "transformers", "numba", "librosa", "comfy-kitchen",
        "comfyui-frontend-package", "comfyui-workflow-templates",
        "comfyui-embedded-docs", "comfy-aimdo", "comfy-env",
        "comfy-3d-viewers", "comfy-sparse-attn",
    ]
    constraints = {name: packages[name] for name in critical_names if name in packages}

    torch_raw = str(inventory.get("torch") or packages.get("torch") or "").strip()
    if not torch_raw:
        raise InventoryError(
            "The inventory does not contain the installed PyTorch version. Refusing to invent one for an exact profile."
        )
    torch_version = torch_raw.split("+", 1)[0]
    torchvision = str(packages.get("torchvision") or "").split("+", 1)[0]
    torchaudio = str(packages.get("torchaudio") or "").split("+", 1)[0]
    lock_source = environment_lock.get("source", {}) if isinstance(environment_lock, dict) else {}
    if not isinstance(lock_source, dict):
        lock_source = {}
    source_accelerator = str(lock_source.get("accelerator") or ("nvidia" if inventory.get("cuda") else "cpu")).lower()
    source_backend_version = ""
    exact_backend_index = ""
    torch_indexes = {
        "nvidia": "https://download.pytorch.org/whl/cu130",
        "nvidia_cuda13": "https://download.pytorch.org/whl/cu130",
        "nvidia_cuda12": "https://download.pytorch.org/whl/cu128",
        "rocm": "https://download.pytorch.org/whl/rocm7.2",
        "cpu": "https://download.pytorch.org/whl/cpu",
        "mps": "",
    }
    if source_accelerator == "nvidia":
        source_backend_version = str(lock_source.get("torch_cuda") or inventory.get("cuda") or "").strip()
        if not source_backend_version:
            raise InventoryError("The inventory reports an NVIDIA PyTorch build without its CUDA runtime version.")
        compact = source_backend_version.replace(".", "")
        exact_backend_index = f"https://download.pytorch.org/whl/cu{compact}"
        torch_indexes["nvidia"] = exact_backend_index
        torch_indexes[f"nvidia_cuda{compact}"] = exact_backend_index
        torch_indexes[f"nvidia_cuda{source_backend_version.split('.', 1)[0]}"] = exact_backend_index
    elif source_accelerator == "rocm":
        source_backend_version = str(lock_source.get("torch_hip") or "").strip()
        if not source_backend_version:
            raise InventoryError("The inventory reports a ROCm PyTorch build without its ROCm runtime version.")
        exact_backend_index = f"https://download.pytorch.org/whl/rocm{source_backend_version}"
        torch_indexes["rocm"] = exact_backend_index
        torch_indexes[f"rocm{source_backend_version.replace('.', '')}"] = exact_backend_index
    elif source_accelerator == "cpu":
        exact_backend_index = torch_indexes["cpu"]

    profile_version = time.strftime("%Y.%m.%d")
    compatibility = build_compatibility_tags(
        profile_version=profile_version,
        python_version=str(inventory.get("python") or "") or None,
        torch_version=torch_raw,
        accelerator=source_accelerator,
        backend_version=source_backend_version,
        exact=True,
    )
    tagged_name = (
        profile_name
        if f"[{compatibility['abi_tag']}]" in profile_name
        else f"{profile_name} [{compatibility['abi_tag']}]"
    )

    locked_wheel_names = {
        str(item.get("name"))
        for item in environment_lock.get("packages", [])
        if isinstance(item, dict) and item.get("source") == "embedded-wheel"
    }

    profile: dict[str, Any] = {
        "schema_version": PROFILE_SCHEMA,
        "kind": PROFILE_KIND,
        "id": profile_id,
        "name": tagged_name,
        "display_name": tagged_name,
        "version": compatibility["profile_version"],
        "compatibility": compatibility,
        "description": "Generated from a sanitized ComfyUI inventory.",
        "source_inventory": {
            "python": inventory.get("python"),
            "torch": inventory.get("torch"),
            "cuda": inventory.get("cuda"),
            "comfyui_commit": inventory.get("comfyui_commit"),
        },
        "comfyui": {
            "repository": repository,
            "branch": "master",
            "preferred_commit": inventory.get("comfyui_commit"),
            "exact": True,
            "use_current_checkout_when_available": True,
        },
        "python": {
            "preferred": inventory.get("python", "3.12"),
            "fallbacks": [inventory.get("python", "3.12")],
        },
        "torch": {
            "version": torch_version,
            "torchvision": torchvision,
            "torchaudio": torchaudio,
            "indexes": torch_indexes,
            "source_accelerator": source_accelerator,
            "source_backend_version": source_backend_version,
            "exact_backend_index": exact_backend_index,
            "exact_backend": True,
            "fallback_unpinned": False,
        },
        "constraints": constraints,
        "environment_lock": environment_lock,
        "extra_python_packages": [],
        "system_dependencies": [
            {"id": "git", "name": "Git", "commands": ["git"], "required": True, "why": "Repository installation."},
            {"id": "ffmpeg", "name": "FFmpeg", "commands": ["ffmpeg", "ffprobe"], "required": False, "why": "Video and audio nodes."},
            {"id": "sox", "name": "SoX", "commands": ["sox"], "required": False, "why": "Speech and audio nodes."},
            {"id": "tesseract", "name": "Tesseract OCR", "commands": ["tesseract"], "required": False, "why": "OCR-capable nodes; language data stays system-managed."},
            {"id": "cmake", "name": "CMake", "commands": ["cmake"], "required": False, "why": "Source builds."},
            {"id": "ninja", "name": "Ninja", "commands": ["ninja"], "required": False, "why": "Native builds."},
            {"id": "compiler", "name": "C/C++ compiler", "commands": ["__compiler__"], "required": False, "why": "Native builds."},
            {"id": "pkg-config", "name": "pkg-config", "commands": ["pkg-config"], "required": False, "why": "Native library discovery."},
            {"id": "rust", "name": "Rust toolchain", "commands": ["cargo", "rustc"], "required": False, "why": "Rust-based source builds when no wheel exists."},
        ],
        "nodes": inventory.get("nodes", []),
        "accelerated_packages": _accelerated_packages(packages, locked_wheel_names=locked_wheel_names),
        "export_metadata": {
            "base_name": profile_name,
            "source": "sanitized-inventory",
            "remote_node_count": sum(1 for node in inventory.get("nodes", []) if node.get("source", {}).get("type") == "remote"),
            "embedded_unpublished_node_count": len(inventory.get("embedded_plugins", {})),
            "models_included": False,
            "machine_paths_included": False,
        },
        "post_install": {
            "copy_websocket_example": True,
            "generate_launchers": True,
            "validate_imports": ["torch", "numpy", "transformers"],
        },
    }
    if inventory.get("embedded_plugins"):
        profile["_embedded_plugin_data"] = inventory["embedded_plugins"]
    if inventory.get("embedded_wheels"):
        profile["_embedded_wheel_data"] = inventory["embedded_wheels"]
    return profile
