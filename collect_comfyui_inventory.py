#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Iterable

# Prefer the resolver bundled with this source checkout, while still allowing
# the standalone collector to use an installed comfy_setup package.
_PROJECT_SRC = Path(__file__).resolve().parent / "installer" / "src"
if _PROJECT_SRC.is_dir() and str(_PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(_PROJECT_SRC))
try:
    from comfy_setup.node_resolver import NodeSourceResolver
except Exception:  # pragma: no cover - standalone fallback without the package
    NodeSourceResolver = None  # type: ignore[assignment,misc]

MODEL_SUFFIXES = {
    ".safetensors", ".ckpt", ".pt", ".pth", ".gguf", ".bin", ".onnx",
    ".engine", ".plan", ".trt", ".traineddata", ".npz", ".npy",
}
METADATA_NAMES = {
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "install.py",
    "install.sh",
    "install.bat",
    "install.ps1",
    "comfy-env.toml",
    "pixi.toml",
    "environment.yml",
    "environment.yaml",
    "package.json",
    "README.md",
    "README.rst",
}
MAX_METADATA_SIZE = 1_048_576
LOCAL_PLUGIN_EXCLUDED_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache", "build", "dist",
    "models", "model", "checkpoints", "loras", "embeddings", "vae", "unet",
    "diffusion_models", "controlnet", "clip", "clip_vision", "text_encoders",
    "input", "output", "temp", "user", "tessdata",
}
LOCAL_PLUGIN_EXCLUDED_NAMES = {
    ".env", "extra_model_paths.yaml", "config.ini", "cookies.txt",
    "credentials.json", "secrets.json",
}
LOCAL_PLUGIN_MAX_FILE_SIZE = 64 * 1024 * 1024


class CollectorError(RuntimeError):
    pass


def run_capture(command: list[str], cwd: Path | None = None) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=60,
        )
        return completed.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def prompt(value: str | None, message: str) -> str:
    if value:
        return value
    return input(message).strip()


def resolve_comfy_path(value: str) -> Path:
    expanded = Path(value).expanduser()
    if expanded.is_absolute() and expanded.exists():
        return expanded.resolve()

    current = Path.cwd() / expanded
    if current.exists():
        return current.resolve()

    home = Path.home() / expanded
    if home.exists():
        return home.resolve()

    raise CollectorError(
        "Could not locate the ComfyUI directory. Checked:\n"
        f"  {current}\n"
        f"  {home}"
    )


def resolve_output(value: str) -> Path:
    expanded = Path(value).expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    if not (str(expanded).endswith(".tar.gz") or str(expanded).endswith(".tgz")):
        raise CollectorError("The output filename must end in .tar.gz or .tgz")
    expanded.parent.mkdir(parents=True, exist_ok=True)
    return expanded.resolve()


def sanitize_git_url(url: str) -> str:
    return re.sub(r"(https://)[^/@\s]+(?::[^/@\s]+)?@", r"\1", url)


def git_metadata(path: Path) -> dict[str, str]:
    top = run_capture(["git", "-C", str(path), "rev-parse", "--show-toplevel"]).strip()
    try:
        own_checkout = bool(top) and Path(top).resolve() == path.resolve()
    except OSError:
        own_checkout = False
    if not own_checkout:
        return {
            "repository": "",
            "branch": "",
            "commit": "",
            "dirty": "not-git",
            "origin_reachable": "unknown",
        }

    repository = sanitize_git_url(
        run_capture(["git", "-C", str(path), "remote", "get-url", "origin"]).strip()
    )
    branch = run_capture(["git", "-C", str(path), "branch", "--show-current"]).strip()
    commit = run_capture(["git", "-C", str(path), "rev-parse", "HEAD"]).strip()
    dirty = "yes" if run_capture(["git", "-C", str(path), "status", "--porcelain"]).strip() else "no"
    origin_refs = run_capture(
        [
            "git", "-C", str(path), "for-each-ref", "--format=%(refname)",
            "--contains", commit, "refs/remotes/origin",
        ]
    ).strip() if commit and repository else ""
    return {
        "repository": repository,
        "branch": branch,
        "commit": commit,
        "dirty": dirty,
        "origin_reachable": "yes" if origin_refs else "no",
    }


def copy_small(source: Path, destination: Path) -> bool:
    if not source.is_file() or source.suffix.lower() in MODEL_SUFFIXES:
        return False
    if source.stat().st_size > MAX_METADATA_SIZE:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return True


def _expand_configured_path(value: str, base: Path) -> Path:
    expanded = Path(os.path.expandvars(os.path.expanduser(value.strip().strip('"\''))))
    return expanded if expanded.is_absolute() else base / expanded


def _fallback_extra_paths(config: Path, comfy: Path) -> list[Path]:
    """Read common extra_model_paths.yaml layouts without requiring PyYAML."""
    sections: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    lines = config.read_text(encoding="utf-8", errors="ignore").splitlines()
    index = 0
    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()
        index += 1
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        if indent == 0 and stripped.endswith(":"):
            current = {"base_path": None, "custom_nodes": []}
            sections.append(current)
            continue
        if current is None or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key == "base_path":
            current["base_path"] = value.strip('"\'')
        elif key == "custom_nodes":
            values: list[str] = current["custom_nodes"]  # type: ignore[assignment]
            if value and value not in {"|", ">"}:
                values.append(value.strip('"\''))
            else:
                while index < len(lines):
                    candidate = lines[index]
                    candidate_indent = len(candidate) - len(candidate.lstrip())
                    if candidate.strip() and candidate_indent <= indent:
                        break
                    index += 1
                    item = candidate.strip()
                    if item.startswith("-"):
                        item = item[1:].strip()
                    if item and not item.startswith("#"):
                        values.append(item.strip('"\''))

    roots: list[Path] = []
    for section in sections:
        base_value = section.get("base_path")
        base = (
            _expand_configured_path(str(base_value), comfy)
            if base_value
            else comfy
        )
        for value in section.get("custom_nodes", []):  # type: ignore[union-attr]
            roots.append(_expand_configured_path(str(value), base))
    return roots


def discover_custom_roots(comfy: Path, python_executable: Path | None) -> list[Path]:
    del python_executable  # Kept for API compatibility with older collectors.
    roots = [comfy / "custom_nodes"]
    config = comfy / "extra_model_paths.yaml"
    if not config.exists():
        return roots

    configured: list[Path] = []
    try:
        import yaml  # type: ignore[import-not-found]

        data = yaml.safe_load(config.read_text(encoding="utf-8", errors="ignore")) or {}
        for section in data.values():
            if not isinstance(section, dict):
                continue
            base_value = section.get("base_path")
            base = (
                _expand_configured_path(str(base_value), comfy)
                if isinstance(base_value, str)
                else comfy
            )
            values = section.get("custom_nodes")
            if isinstance(values, str):
                values = [values]
            if isinstance(values, list):
                for value in values:
                    if isinstance(value, str):
                        configured.append(_expand_configured_path(value, base))
    except Exception:
        configured = _fallback_extra_paths(config, comfy)

    roots.extend(path for path in configured if path.is_dir())
    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        resolved = root.resolve() if root.exists() else root
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def collect_main_repo(comfy: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    meta = git_metadata(comfy)
    with (destination / "git.txt").open("w", encoding="utf-8") as stream:
        stream.write(f"branch={meta['branch']}\n")
        stream.write(f"commit={meta['commit']}\n")
        stream.write(f"repository={meta['repository']}\n")
        stream.write(f"dirty={meta['dirty']}\n")
        stream.write(f"origin_reachable={meta['origin_reachable']}\n")
        stream.write("\n[status]\n")
        stream.write(run_capture(["git", "-C", str(comfy), "status", "--short"]))
        stream.write("\n[submodules]\n")
        stream.write(run_capture(["git", "-C", str(comfy), "submodule", "status", "--recursive"]))

    for name in [
        "pyproject.toml",
        "requirements.txt",
        "uv.lock",
        ".gitignore",
        "extra_model_paths.yaml.example",
    ]:
        copy_small(comfy / name, destination / name)

    (destination / "local-changes.diff").write_text(
        run_capture(
            [
                "git", "-C", str(comfy), "diff", "--",
                "pyproject.toml", "requirements.txt", ".gitignore",
            ]
        ),
        encoding="utf-8",
    )


def collect_python(comfy: Path, destination: Path, *, source_roots: list[Path] | None = None) -> Path | None:
    destination.mkdir(parents=True, exist_ok=True)
    candidates = [
        comfy / ".venv" / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python"),
        comfy / "venv" / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python"),
    ]
    python_executable = next((path for path in candidates if path.exists()), None)
    if python_executable is None:
        (destination / "MISSING_VENV.txt").write_text("No ComfyUI virtual environment was found.\n")
        return None

    runtime_code = r'''
import platform, sys
print("executable:", sys.executable)
print("version:", sys.version)
print("platform:", platform.platform())
try:
    import torch
    print("torch:", torch.__version__)
    print("torch_cuda:", torch.version.cuda)
    print("cuda_available:", torch.cuda.is_available())
    if torch.cuda.is_available():
        print("gpu:", torch.cuda.get_device_name(0))
        print("capability:", torch.cuda.get_device_capability(0))
except Exception as exc:
    print("torch_error:", repr(exc))
'''
    (destination / "runtime.txt").write_text(
        run_capture([str(python_executable), "-c", runtime_code]), encoding="utf-8"
    )
    (destination / "pip-freeze.txt").write_text(
        run_capture([str(python_executable), "-m", "pip", "freeze", "--all"]), encoding="utf-8"
    )
    (destination / "pip-list.json").write_text(
        run_capture([str(python_executable), "-m", "pip", "list", "--format=json"]), encoding="utf-8"
    )

    lock_code = r'''
import importlib.metadata as md, json, os, platform, re, sys
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

comfy = Path(sys.argv[1]).resolve()
custom_roots = [Path(value).resolve() for value in sys.argv[2:]]
source_targets = [(comfy, Path(".")), *[(root, Path("custom_nodes")) for root in custom_roots]]
source_targets = sorted(dict.fromkeys(source_targets), key=lambda item: len(item[0].parts), reverse=True)
installer_managed = {"pip", "setuptools", "wheel", "uv"}
torch_managed = {"torch", "torchvision", "torchaudio", "triton"}
accelerated_managed = {
    "cupy", "cupy-wheel", "cupy-cuda11x", "cupy-cuda12x", "cupy-cuda13x",
    "tensorrt", "tensorrt-cu12", "tensorrt-cu13", "tensorrt-cu13-bindings", "tensorrt-cu13-libs",
    "onnxruntime", "onnxruntime-gpu", "flash-attn", "flash-attn-3", "sageattention", "pyopengl-accelerate",
}

def name(value):
    return re.sub(r"[-_.]+", "-", value.strip().lower())

def within(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False

packages = []
nonportable = []
seen = set()
ambiguous = set()
for dist in sorted(md.distributions(), key=lambda item: name(item.metadata.get("Name") or item.name)):
    package = name(dist.metadata.get("Name") or dist.name)
    version = str(dist.version)
    if not package:
        continue
    if package in seen:
        ambiguous.add(package)
        continue
    seen.add(package)
    item = {
        "name": package,
        "version": version,
        "requirement": f"{package}=={version}",
        "install": True,
        "portable": True,
        "source": "index",
    }
    try:
        raw = dist.read_text("direct_url.json")
        direct = json.loads(raw) if raw else None
    except Exception:
        direct = None
    managed_reason = (
        "installer-tool" if package in installer_managed else
        "torch-backend" if package in torch_managed or package.startswith("nvidia-") else
        "accelerated-package" if package in accelerated_managed else
        None
    )
    if isinstance(direct, dict):
        url = str(direct.get("url") or "")
        parsed = urlparse(url)
        if parsed.scheme == "file":
            local = Path(unquote(parsed.path)).resolve()
            source_match = next(((root, prefix) for root, prefix in source_targets if within(local, root)), None)
            if source_match:
                root, prefix = source_match
                relative = local.relative_to(root)
                item["install"] = False
                item["managed_by"] = "comfyui-or-custom-node-source"
                item["source"] = "source-tree"
                item["source_path"] = (prefix / relative).as_posix()
                dir_info = direct.get("dir_info") if isinstance(direct.get("dir_info"), dict) else {}
                item["editable"] = bool(dir_info.get("editable"))
            elif local.suffix.lower() == ".whl" and local.is_file() and managed_reason not in {"installer-tool", "torch-backend"}:
                item["source"] = "index"
                item["original_source"] = "local-wheel"
                item["original_filename"] = local.name
            elif managed_reason:
                item["install"] = False
                item["managed_by"] = managed_reason
                item["source"] = "managed"
            else:
                item["install"] = False
                item["portable"] = False
                item["source"] = "local"
                item["reason"] = "Installed from a local path outside the ComfyUI source tree."
                nonportable.append(package)
        elif parsed.scheme == "https" and (parsed.hostname or "").lower() in {"github.com", "www.github.com"}:
            vcs = direct.get("vcs_info") if isinstance(direct.get("vcs_info"), dict) else {}
            commit = str(vcs.get("commit_id") or "")
            if commit:
                requirement = f"{package} @ git+{url}@{commit}"
                subdirectory = str(direct.get("subdirectory") or "").strip().strip("/")
                if subdirectory:
                    requirement += "#subdirectory=" + quote(subdirectory, safe="/._-")
                item["requirement"] = requirement
                item["source"] = "github"
            elif managed_reason:
                item["install"] = False
                item["managed_by"] = managed_reason
                item["source"] = "managed"
            else:
                item["install"] = False
                item["portable"] = False
                item["source"] = "unsupported-direct-url"
                nonportable.append(package)
        elif managed_reason:
            item["install"] = False
            item["managed_by"] = managed_reason
            item["source"] = "managed"
        else:
            item["install"] = False
            item["portable"] = False
            item["source"] = "unsupported-direct-url"
            nonportable.append(package)
    elif managed_reason:
        item["install"] = False
        item["managed_by"] = managed_reason
        item["source"] = "managed"
    packages.append(item)

if ambiguous:
    for item in packages:
        if item.get("name") in ambiguous:
            item["install"] = False
            item["portable"] = False
            item["source"] = "ambiguous-installed-distributions"
            item["reason"] = "Multiple installed distributions use the same canonical package name."
    for package in sorted(ambiguous):
        if package not in nonportable:
            nonportable.append(package)

torch_runtime = {"accelerator": "cpu", "torch_cuda": None, "torch_hip": None}
try:
    import torch
    torch_runtime["torch_version"] = str(torch.__version__)
    torch_runtime["torch_cuda"] = str(torch.version.cuda) if torch.version.cuda else None
    torch_runtime["torch_hip"] = str(torch.version.hip) if torch.version.hip else None
    if torch.version.cuda:
        torch_runtime["accelerator"] = "nvidia"
    elif torch.version.hip:
        torch_runtime["accelerator"] = "rocm"
    elif sys.platform == "darwin" and getattr(torch.backends, "mps", None) and torch.backends.mps.is_built():
        torch_runtime["accelerator"] = "mps"
except Exception:
    pass

print(json.dumps({
    "schema_version": 1,
    "mode": "exact",
    "complete": not nonportable,
    "source": {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "os": platform.system().lower(),
        "architecture": platform.machine().lower(),
        **torch_runtime,
    },
    "packages": packages,
    "nonportable_packages": nonportable,
    "ambiguous_packages": sorted(ambiguous),
}, indent=2))
'''
    raw_lock = run_capture(
        [
            str(python_executable),
            "-c",
            lock_code,
            str(comfy),
            *[str(path) for path in (source_roots or [])],
        ]
    )
    try:
        environment_lock = json.loads(raw_lock)
    except json.JSONDecodeError:
        environment_lock = {}
    (destination / "environment-lock.json").write_text(
        json.dumps(environment_lock, indent=2) if environment_lock else raw_lock,
        encoding="utf-8",
    )

    imports_code = r'''
import importlib, importlib.metadata as md
mods = ["torch", "torchvision", "torchaudio", "numpy", "transformers", "flash_attn", "sageattention", "cupy", "tensorrt", "comfy_kitchen", "librosa", "numba", "soundfile", "soxr", "OpenGL"]
for name in mods:
    try:
        mod = importlib.import_module(name)
        version = getattr(mod, "__version__", "")
        if not version:
            try: version = md.version(name.replace("_", "-"))
            except Exception: version = "unknown"
        print(f"{name}\tOK\t{version}\t{getattr(mod, '__file__', '')}")
    except Exception as exc:
        print(f"{name}\tERROR\t{exc!r}")
'''
    (destination / "important-imports.txt").write_text(
        run_capture([str(python_executable), "-c", imports_code]), encoding="utf-8"
    )
    return python_executable


def collect_system(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    commands = [
        "git", "uv", "python", "python3", "pip", "gcc", "g++", "clang",
        "cmake", "ninja", "make", "ffmpeg", "ffprobe", "sox", "tesseract",
        "cargo", "rustc", "node", "npm", "pnpm", "nvidia-smi", "nvcc",
        "rocm-smi", "rocminfo",
    ]
    lines = [
        "[platform]",
        f"system={platform.system()}",
        f"release={platform.release()}",
        f"architecture={platform.machine()}",
        f"python={platform.python_version()}",
        "",
        "[commands]",
    ]
    for command in commands:
        path = shutil.which(command)
        lines.append(f"{command}={path or 'MISSING'}")
        if path:
            output = run_capture([path, "--version"])
            if not output and command == "nvidia-smi":
                output = run_capture([path])
            lines.extend(f"  {line}" for line in output.splitlines()[:6])
    (destination / "system.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def node_remote_metadata(entry: Path) -> dict[str, str]:
    result = {"repository": "", "manager_id": "", "manager_version": ""}
    pyproject = entry / "pyproject.toml"
    if not pyproject.is_file():
        return result
    text = pyproject.read_text(encoding="utf-8", errors="ignore")
    repository = re.search(
        r'(?:Repository|Source|Homepage)\s*=\s*["\']([^"\']+)',
        text,
        re.IGNORECASE,
    )
    project_section = re.search(r'(?ms)^\[project\]\s*(.*?)(?=^\[|\Z)', text)
    comfy_section = re.search(r'(?ms)^\[tool\.comfy\]\s*(.*?)(?=^\[|\Z)', text)
    if repository:
        result["repository"] = sanitize_git_url(repository.group(1).strip())
    if project_section and comfy_section and re.search(
        r'PublisherId\s*=\s*["\'][^"\']+["\']',
        comfy_section.group(1),
        re.IGNORECASE,
    ):
        name = re.search(
            r'^name\s*=\s*["\']([^"\']+)',
            project_section.group(1),
            re.IGNORECASE | re.MULTILINE,
        )
        version = re.search(
            r'^version\s*=\s*["\']([^"\']+)',
            project_section.group(1),
            re.IGNORECASE | re.MULTILINE,
        )
        if name:
            result["manager_id"] = name.group(1).strip()
        if version:
            result["manager_version"] = version.group(1).strip()
    return result


def copy_local_plugin_tree(source: Path, destination: Path) -> int:
    copied = 0
    for path in source.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(source)
        if any(part.lower() in LOCAL_PLUGIN_EXCLUDED_DIRS for part in relative.parts[:-1]):
            continue
        if path.name.lower() in LOCAL_PLUGIN_EXCLUDED_NAMES:
            continue
        if path.suffix.lower() in MODEL_SUFFIXES or path.suffix.lower() in {".pyc", ".pyo", ".log"}:
            continue
        try:
            if path.stat().st_size > LOCAL_PLUGIN_MAX_FILE_SIZE:
                continue
        except OSError:
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied += 1
    return copied


def collect_nodes(
    comfy: Path,
    roots: list[Path],
    destination: Path,
    *,
    python: Path | None = None,
    include_local_plugins: bool = True,
) -> None:
    """Collect custom-node provenance, embedding only unresolved source.

    The direct inventory path follows the same provenance policy as normal
    profile export: inspect local Git/pyproject metadata, local Manager caches
    and snapshots, configured catalogs, the official Manager catalog, and the
    Comfy Registry before copying source files. Public Manager/Registry/Git
    nodes stay as small remote descriptors even when their installed folder no
    longer contains ``.git`` metadata.
    """

    destination.mkdir(parents=True, exist_ok=True)
    (destination / "roots.txt").write_text("\n".join(str(root) for root in roots) + "\n")
    inventory_path = destination / "inventory.tsv"
    fieldnames = [
        "folder", "root", "repository", "manager_id", "manager_version",
        "branch", "commit", "dirty", "origin_reachable", "metadata", "kind", "payload",
        "resolved_by", "resolution_kind", "resolution_trust",
    ]
    resolver = NodeSourceResolver(comfy, python=python) if NodeSourceResolver is not None else None

    with inventory_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, delimiter="\t", fieldnames=fieldnames)
        writer.writeheader()
        counter = 0
        for custom_root in roots:
            if not custom_root.is_dir():
                continue
            for entry in sorted(custom_root.iterdir(), key=lambda path: path.name.lower()):
                if entry.name.startswith(".") or entry.name == "__pycache__":
                    continue
                if entry.is_file() and entry.suffix == ".py":
                    counter += 1
                    report = destination / f"standalone_{counter}_{entry.stem}"
                    report.mkdir(parents=True, exist_ok=True)
                    resolution = resolver.resolve(entry) if resolver is not None else None
                    repository = resolution.repository if resolution else ""
                    manager_id = resolution.manager_id if resolution else ""
                    manager_version = resolution.manager_version if resolution else ""
                    payload = ""
                    copied = False
                    if not repository and not manager_id and include_local_plugins:
                        copied = copy_small(entry, report / entry.name)
                        payload = entry.name if copied else ""
                    writer.writerow(
                        {
                            "folder": entry.name,
                            "root": str(custom_root),
                            "repository": repository or "",
                            "manager_id": manager_id or "",
                            "manager_version": manager_version or "",
                            "branch": "",
                            "commit": resolution.ref if resolution and resolution.ref else "",
                            "dirty": "standalone",
                            "origin_reachable": "unknown",
                            "metadata": entry.name if copied else "",
                            "kind": "standalone-python",
                            "payload": payload,
                            "resolved_by": resolution.source_id if resolution else "",
                            "resolution_kind": resolution.source_kind if resolution else "",
                            "resolution_trust": resolution.trust if resolution else "",
                        }
                    )
                    continue
                if not entry.is_dir():
                    continue

                counter += 1
                report = destination / f"node_{counter}_{re.sub(r'[^A-Za-z0-9_.-]+', '_', entry.name)}"
                report.mkdir(parents=True, exist_ok=True)
                meta = git_metadata(entry)
                status = run_capture(["git", "-C", str(entry), "status", "--short"])
                submodules = run_capture(["git", "-C", str(entry), "submodule", "status", "--recursive"])
                (report / "git.txt").write_text(
                    "\n".join(
                        [
                            f"folder={entry.name}",
                            f"repository={meta['repository']}",
                            f"branch={meta['branch']}",
                            f"commit={meta['commit']}",
                            f"dirty={meta['dirty']}",
                            f"origin_reachable={meta['origin_reachable']}",
                            "",
                            "[status]",
                            status,
                            "[submodules]",
                            submodules,
                        ]
                    ),
                    encoding="utf-8",
                )
                if meta["dirty"] in {"yes", "no"}:
                    (report / "local-changes.diff").write_text(
                        run_capture(["git", "-C", str(entry), "diff", "--no-ext-diff", "--text"]),
                        encoding="utf-8",
                    )

                metadata: list[str] = []
                for path in entry.rglob("*"):
                    if not path.is_file():
                        continue
                    relative = path.relative_to(entry)
                    if len(relative.parts) > 5:
                        continue
                    if path.name not in METADATA_NAMES and not path.name.startswith("requirements-"):
                        continue
                    if copy_small(path, report / "metadata" / relative):
                        metadata.append(str(relative))

                remote = node_remote_metadata(entry)
                direct_repository = meta["repository"] or remote["repository"]
                direct_manager_id = remote["manager_id"]
                resolution = (
                    resolver.resolve(entry, allow_network=not bool(direct_repository or direct_manager_id))
                    if resolver is not None
                    else None
                )
                repository = direct_repository or (resolution.repository if resolution else "")
                manager_id = direct_manager_id or (resolution.manager_id if resolution else "")
                manager_version = remote["manager_version"] or (resolution.manager_version if resolution else "")

                # Record a commit only if it is fetchable from origin or came
                # from a Manager snapshot. A known public node must not be
                # embedded merely because Manager stripped its .git directory.
                portable_commit = ""
                if meta["commit"] and meta["origin_reachable"] == "yes":
                    portable_commit = meta["commit"]
                elif resolution and resolution.ref:
                    portable_commit = resolution.ref

                payload = ""
                kind = "directory"
                if not repository and not manager_id:
                    kind = "local-unpublished"
                    if include_local_plugins:
                        payload_dir = report / "embedded_plugin"
                        if copy_local_plugin_tree(entry, payload_dir):
                            payload = "embedded_plugin"

                writer.writerow(
                    {
                        "folder": entry.name,
                        "root": str(custom_root),
                        "repository": repository or "",
                        "manager_id": manager_id or "",
                        "manager_version": manager_version or "",
                        "branch": meta["branch"] if repository and not portable_commit else "",
                        "commit": portable_commit,
                        "dirty": meta["dirty"],
                        "origin_reachable": meta["origin_reachable"],
                        "metadata": ",".join(metadata),
                        "kind": kind,
                        "payload": payload,
                        "resolved_by": resolution.source_id if resolution else "",
                        "resolution_kind": resolution.source_kind if resolution else "",
                        "resolution_trust": resolution.trust if resolution else "",
                    }
                )


def collect_log(comfy: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    log_path = comfy / "user" / "comfyui.log"
    if not log_path.exists():
        (destination / "README.txt").write_text("No ComfyUI startup log was found.\n")
        return
    lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-5000:]
    (destination / "comfyui-last-5000-lines.txt").write_text("\n".join(lines) + "\n")


def sanitize_tree(root: Path, replacements: list[tuple[re.Pattern[str], str]]) -> None:
    generic = [
        (
            re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
            "<REDACTED_EMAIL>",
        ),
        (re.compile(r"(https://)[^/@\s]+(?::[^/@\s]+)?@", re.IGNORECASE), r"\1"),
        (
            re.compile(
                r"\b(api[_-]?key|token|secret|password|passwd|access_token|auth_token)\s*[:=]\s*[^\s,'\"]+",
                re.IGNORECASE,
            ),
            r"\1=<REDACTED>",
        ),
        (
            re.compile(r"\b(?:ghp|github_pat|hf|sk)-[A-Za-z0-9_-]{12,}\b"),
            "<REDACTED_TOKEN>",
        ),
    ]
    for file_path in root.rglob("*"):
        if not file_path.is_file():
            continue
        try:
            text = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern, replacement in [*replacements, *generic]:
            text = pattern.sub(replacement, text)
        file_path.write_text(text, encoding="utf-8")


def create_archive(comfy: Path, output: Path, *, include_local_plugins: bool = True) -> None:
    if not (comfy / "main.py").exists():
        raise CollectorError(f"The selected directory does not look like ComfyUI: {comfy}")
    if output.exists():
        answer = input(f"{output} already exists. Overwrite it? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            raise CollectorError("Cancelled without overwriting the archive.")
        output.unlink()

    with tempfile.TemporaryDirectory(
        prefix=".comfyui-inventory-work-",
        dir=output.parent,
    ) as temporary:
        work = Path(temporary)
        report_name = f"comfyui-installer-inventory-{time.strftime('%Y%m%d-%H%M%S')}"
        report = work / report_name
        report.mkdir()

        collect_main_repo(comfy, report / "main_repo")
        roots = discover_custom_roots(comfy, None)
        python_executable = collect_python(comfy, report / "python", source_roots=roots)
        collect_system(report / "system")
        collect_nodes(
            comfy,
            roots,
            report / "custom_nodes",
            python=python_executable,
            include_local_plugins=include_local_plugins,
        )
        collect_log(comfy, report / "logs")

        (report / "README.txt").write_text(
            "This is a sanitized ComfyUI installer inventory.\n\n"
            "Excluded: models, LoRAs, checkpoints, embeddings, GGUF files, engines, "
            "Tesseract trained data, workflows, inputs, outputs, user configuration, "
            "extra_model_paths.yaml, environment variables, credentials, tokens, and SSH keys.\n",
            encoding="utf-8",
        )

        replacements: list[tuple[re.Pattern[str], str]] = [
            (re.compile(re.escape(str(comfy))), "$COMFY_DIR"),
            (re.compile(re.escape(str(output.parent))), "$OUTPUT_DIR"),
        ]

        # Custom nodes may live on another drive or outside the user's home.
        # Replace those external roots (and their shared parent directory) so
        # roots.txt, inventory.tsv, logs, and metadata do not disclose them.
        home = Path.home().resolve()
        external_bases: list[Path] = []
        for custom_root in roots:
            resolved_root = custom_root.resolve()
            try:
                resolved_root.relative_to(comfy)
                continue
            except ValueError:
                pass
            try:
                resolved_root.relative_to(home)
                continue
            except ValueError:
                pass
            external_bases.append(resolved_root.parent)

        # Longest paths first prevents a shorter parent substitution from
        # leaving identifiable suffixes behind.
        unique_external = sorted(set(external_bases), key=lambda item: len(str(item)), reverse=True)
        for index, external_root in enumerate(unique_external, start=1):
            replacements.append(
                (re.compile(re.escape(str(external_root))), f"$EXTERNAL_ROOT_{index}")
            )

        replacements.append((re.compile(re.escape(str(home))), "$HOME"))
        sanitize_tree(report, replacements)

        with tarfile.open(output, "w:gz") as tar:
            tar.add(report, arcname=report_name)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a sanitized inventory of a working ComfyUI installation."
    )
    parser.add_argument("comfyui_directory", nargs="?")
    parser.add_argument("output_archive", nargs="?")
    parser.add_argument(
        "--skip-local-plugins",
        action="store_true",
        help=(
            "Deliberately omit unpublished local plugins. The resulting inventory cannot be "
            "converted into an exact profile when such plugins are present."
        ),
    )
    parser.add_argument("--include-local-plugins", action="store_true", help=argparse.SUPPRESS)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        comfy_value = prompt(args.comfyui_directory, "ComfyUI installation directory: ")
        output_value = prompt(args.output_archive, "Output archive path (.tar.gz or .tgz): ")
        comfy = resolve_comfy_path(comfy_value)
        output = resolve_output(output_value)
        print("\nResolved paths:")
        print(f"  ComfyUI: {comfy}")
        print(f"  Archive: {output}\n")
        create_archive(comfy, output, include_local_plugins=not args.skip_local_plugins)
        print("Inventory completed successfully.")
        print(f"Archive: {output}")
    except (CollectorError, KeyboardInterrupt) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
