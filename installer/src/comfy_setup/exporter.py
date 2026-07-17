from __future__ import annotations

import json
import os
import re
import subprocess
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from .compatibility_tags import build_compatibility_tags
from .configuration import load_editable_config, official_comfyui_repository
from .discovery import is_comfyui_directory
from .environment_lock import build_environment_lock
from .node_resolver import NodeSourceResolver
from .profile import (
    EMBEDDED_ROOT,
    COMFYUI_OVERLAY_ROOT,
    DEPENDENCY_MANIFESTS_ROOT,
    PROFILE_KIND,
    PROFILE_SCHEMA,
    write_profile_bundle,
)
from .shared_assets import inspect_instance_shared_assets


class ExportError(RuntimeError):
    pass


@dataclass(frozen=True)
class DuplicateNodeGroup:
    """One custom-node destination identity discovered in multiple roots."""

    identity: str
    paths: tuple[Path, ...]


class DuplicateNodeIdentityError(ExportError):
    """Raised when duplicate custom nodes remain unresolved for an export."""

    def __init__(self, groups: list[DuplicateNodeGroup]) -> None:
        self.groups = groups
        lines = [
            "Duplicate custom-node identities require a choice before this profile can be exported.",
        ]
        for group in groups:
            lines.append(f"{group.identity}:")
            lines.extend(f"  - {path}" for path in group.paths)
        lines.append(
            "Choose which path(s) to omit. The interactive CLI will prompt when attached to a terminal; "
            "automation can repeat --omit-node /complete/path/to/node."
        )
        super().__init__("\n".join(lines))


CORE_CONSTRAINTS = {
    "numpy",
    "transformers",
    "numba",
    "librosa",
    "comfy-kitchen",
    "comfyui-frontend-package",
    "comfyui-workflow-templates",
    "comfyui-embedded-docs",
    "comfy-aimdo",
    "comfy-env",
    "comfy-3d-viewers",
    "comfy-sparse-attn",
}

EXCLUDED_TOP_LEVEL = {
    "pip",
    "setuptools",
    "wheel",
    "uv",
    "torch",
    "torchvision",
    "torchaudio",
    *CORE_CONSTRAINTS,
}


def _sanitize_repository(url: str) -> str:
    value = re.sub(r"(https://)[^/@\s]+(?::[^/@\s]+)?@", r"\1", url.strip())
    if value.startswith(("file://", "/", "./", "../")):
        return ""
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return ""
    return value


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "custom-comfyui"


def _run(arguments: list[str], *, cwd: Path | None = None) -> str:
    try:
        completed = subprocess.run(
            arguments,
            cwd=str(cwd) if cwd else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout.strip()


def _git(path: Path, *arguments: str) -> str:
    return _run(["git", "-C", str(path), *arguments])


def _commit_is_known_on_origin(path: Path, commit: str) -> bool:
    """Return whether local origin-tracking refs contain ``commit``.

    A clean checkout may still be on an unpushed, machine-local commit. Storing
    that SHA in an "exact" profile would only postpone the failure until a new
    machine tried to clone it. We do not mutate or fetch the source checkout
    during export: uncertain custom nodes are snapshotted, while an uncertain
    main ComfyUI revision makes exact export stop with a clear error.
    """

    if not commit or not path.is_dir():
        return False
    refs = _git(
        path,
        "for-each-ref",
        "--format=%(refname)",
        "--contains",
        commit,
        "refs/remotes/origin",
    )
    return bool(refs.strip())


_COMFY_RUNTIME_ROOTS = {
    ".venv", "venv", "python_embeded", "python_embedded",
    "custom_nodes", "models", "input", "output", "temp", "user",
}

_COMFY_RECONSTRUCTED_ROOTS = {
    *_COMFY_RUNTIME_ROOTS,
    "web/extensions",
    "extensions",
}
_COMFY_GENERATED_FILES = {
    "extra_model_paths.yaml",
    ".comfy-setup/shared-assets.yaml",
    "comfyui",
    "comfyui.cmd",
    "comfyui.ps1",
}
_DEPENDENCY_MANIFEST_NAMES = {
    "pyproject.toml",
    "python.toml",
    "uv.lock",
    "requirements.txt",
    "requirements-no-cupy.txt",
    "manager_requirements.txt",
    "setup.cfg",
}


def _uncommitted_comfy_source_changes(comfy_dir: Path) -> list[str]:
    changes: list[str] = []
    tracked = _git(comfy_dir, "status", "--porcelain", "--untracked-files=no")
    if tracked:
        changes.extend(line for line in tracked.splitlines() if line.strip())
    untracked = _git(comfy_dir, "ls-files", "--others", "--exclude-standard", "-z")
    for relative in untracked.split("\0"):
        relative = relative.strip()
        if not relative:
            continue
        first = PurePosixPath(relative.replace("\\", "/")).parts[0]
        if first in _COMFY_RUNTIME_ROOTS or relative == "extra_model_paths.yaml":
            continue
        changes.append(f"?? {relative}")
    return changes


def _overlay_path_allowed(relative: str) -> bool:
    normalized = PurePosixPath(relative.replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts or not normalized.parts:
        return False
    text = normalized.as_posix()
    if text in _COMFY_GENERATED_FILES:
        return False
    for root in _COMFY_RECONSTRUCTED_ROOTS:
        root_path = PurePosixPath(root)
        if normalized == root_path or root_path in normalized.parents:
            return False
    return True


def _git_upstream_ref(comfy_dir: Path, branch: str) -> str | None:
    upstream = _git(
        comfy_dir,
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{upstream}",
    )
    if upstream:
        return upstream
    candidate = f"origin/{branch}"
    if _git(comfy_dir, "show-ref", "--verify", f"refs/remotes/{candidate}"):
        return candidate
    return None


def _git_name_status(comfy_dir: Path, base_commit: str) -> tuple[set[str], set[str]]:
    """Return current overlay files and repository-relative deletions."""

    raw = _run(
        [
            "git",
            "-C",
            str(comfy_dir),
            "diff",
            "--name-status",
            "--find-renames",
            "-z",
            base_commit,
            "--",
        ]
    )
    tokens = raw.split("\0") if raw else []
    changed: set[str] = set()
    deleted: set[str] = set()
    index = 0
    while index < len(tokens):
        status = tokens[index].strip()
        index += 1
        if not status or index >= len(tokens):
            continue
        if status.startswith(("R", "C")):
            old = tokens[index]
            index += 1
            if index >= len(tokens):
                break
            new = tokens[index]
            index += 1
            if status.startswith("R") and _overlay_path_allowed(old):
                deleted.add(old)
            if _overlay_path_allowed(new):
                changed.add(new)
            continue
        path = tokens[index]
        index += 1
        if not _overlay_path_allowed(path):
            continue
        if status.startswith("D"):
            deleted.add(path)
        else:
            changed.add(path)

    untracked = _git(comfy_dir, "ls-files", "--others", "--exclude-standard", "-z")
    for relative in untracked.split("\0"):
        if relative and _overlay_path_allowed(relative):
            changed.add(relative)
    return changed, deleted


def _comfyui_overlay(
    comfy_dir: Path,
    *,
    branch: str,
    commit: str | None,
    origin_has_commit: bool,
) -> tuple[str | None, dict[str, Path], list[str], list[str]]:
    """Capture only source differences from a fetchable ComfyUI base revision."""

    warnings: list[str] = []
    if not (comfy_dir / ".git").exists() or not commit:
        warnings.append(
            "The ComfyUI source is not a readable Git checkout. No full checkout was embedded; "
            "the profile will install the referenced repository and cannot preserve unidentified core edits."
        )
        return None, {}, [], warnings

    # A commit that is already available from the configured origin is itself
    # the reconstruction base.  Only dirty/untracked changes belong in the
    # overlay; comparing it with HEAD^ would duplicate an entire public commit.
    if origin_has_commit:
        base_commit = commit
        if not _uncommitted_comfy_source_changes(comfy_dir):
            return commit, {}, [], warnings
    else:
        upstream = _git_upstream_ref(comfy_dir, branch)
        base_commit = _git(comfy_dir, "merge-base", "HEAD", upstream) if upstream else ""
        if not base_commit:
            base_commit = _git(comfy_dir, "rev-parse", "HEAD^")
    if not base_commit:
        warnings.append(
            "No fetchable base revision could be identified for the local ComfyUI checkout. "
            "No full checkout was embedded; the profile will use the configured repository branch."
        )
        return None, {}, [], warnings

    changed, deleted = _git_name_status(comfy_dir, base_commit)
    paths: dict[str, Path] = {}
    for relative in sorted(changed):
        source = comfy_dir / PurePosixPath(relative)
        if source.is_file() and not source.is_symlink():
            paths[relative] = source
    if paths or deleted:
        warnings.append(
            f"Captured a compact ComfyUI source overlay with {len(paths)} changed/new file(s) "
            f"and {len(deleted)} deletion(s) relative to {base_commit[:12]}. Runtime data, custom nodes, "
            "generated launchers, environments, and shared asset paths were excluded."
        )
    return base_commit, paths, sorted(deleted), warnings


def _manifest_candidates(root: Path) -> list[Path]:
    """Return portable dependency manifests anywhere under a source root.

    Custom nodes often keep secondary requirements files in subpackages.  The
    exporter inventories all of them while pruning runtime/build/model trees so
    dependency comparison is complete without accidentally archiving payloads.
    """

    if not root.is_dir():
        return []
    excluded_dirs = {
        ".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__",
        ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache", "build",
        "dist", "models", "checkpoints", "loras", "input", "output",
        "temp", "user", "node_modules",
    }
    output: list[Path] = []
    for candidate in sorted(root.rglob("*")):
        if not candidate.is_file() or candidate.is_symlink():
            continue
        relative = candidate.relative_to(root)
        if any(part.lower() in excluded_dirs for part in relative.parts[:-1]):
            continue
        name = candidate.name.lower()
        if name not in _DEPENDENCY_MANIFEST_NAMES and not (
            name.startswith("requirements") and name.endswith(".txt")
        ):
            continue
        try:
            if candidate.stat().st_size > 4 * 1024 * 1024:
                continue
        except OSError:
            continue
        output.append(candidate)
    return output


def _dependency_manifests(
    comfy_dir: Path,
    *,
    omitted_node_paths: set[Path],
) -> tuple[list[dict[str, Any]], dict[str, Path]]:
    records: list[dict[str, Any]] = []
    paths: dict[str, Path] = {}

    for candidate in _manifest_candidates(comfy_dir):
        relative = candidate.relative_to(comfy_dir).as_posix()
        archive_path = f"{DEPENDENCY_MANIFESTS_ROOT}/comfyui/{relative}"
        records.append(
            {
                "scope": "comfyui",
                "relative_path": relative,
                "payload": archive_path,
            }
        )
        paths[archive_path] = candidate

    for identity, node_path in _custom_node_candidates(comfy_dir):
        if node_path in omitted_node_paths or not node_path.is_dir():
            continue
        for candidate in _manifest_candidates(node_path):
            relative = candidate.relative_to(node_path).as_posix()
            archive_path = (
                f"{DEPENDENCY_MANIFESTS_ROOT}/custom_nodes/{identity}/{relative}"
            )
            records.append(
                {
                    "scope": "custom-node",
                    "node_id": identity,
                    "relative_path": relative,
                    "payload": archive_path,
                }
            )
            paths[archive_path] = candidate
    return records, paths


def _manifest_requirements(path: Path) -> list[str]:
    if path.name == "pyproject.toml":
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return []
        project = data.get("project", {}) if isinstance(data, dict) else {}
        values = project.get("dependencies", []) if isinstance(project, dict) else []
        return [str(value).strip() for value in values if str(value).strip()] if isinstance(values, list) else []
    if not path.name.lower().endswith(".txt"):
        return []
    output: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(("-r", "--requirement", "-c", "--constraint")):
            continue
        if line.startswith(("--index", "--extra-index", "--find-links", "-f ")):
            continue
        # Remove only a trailing comment introduced by whitespace. URL fragments
        # and hashes remain intact.
        line = re.split(r"\s+#", line, maxsplit=1)[0].strip()
        if line:
            output.append(line)
    return output


def _resolve_manifest_dependencies(
    manifest_paths: dict[str, Path],
    installed_packages: dict[str, str],
    runtime: dict[str, str],
) -> tuple[dict[str, Any], list[str]]:
    environment = default_environment()
    python_value = str(runtime.get("python") or "")
    if python_value:
        environment["python_full_version"] = python_value
        environment["python_version"] = ".".join(python_value.split(".")[:2])
    if runtime.get("os"):
        environment["platform_system"] = str(runtime["os"]).title()
        environment["sys_platform"] = {
            "windows": "win32",
            "linux": "linux",
            "darwin": "darwin",
            "macos": "darwin",
        }.get(str(runtime["os"]).lower(), str(runtime["os"]).lower())

    requirement_records: list[dict[str, Any]] = []
    grouped: dict[str, list[Requirement]] = {}
    unparsed: list[dict[str, str]] = []
    for payload, path in sorted(manifest_paths.items()):
        for raw in _manifest_requirements(path):
            try:
                requirement = Requirement(raw)
            except InvalidRequirement:
                unparsed.append({"manifest": payload, "requirement": raw})
                continue
            try:
                if requirement.marker and not requirement.marker.evaluate(environment):
                    continue
            except Exception:
                pass
            canonical = canonicalize_name(requirement.name)
            grouped.setdefault(canonical, []).append(requirement)
            requirement_records.append(
                {
                    "manifest": payload,
                    "name": canonical,
                    "requirement": str(requirement),
                }
            )

    resolved: dict[str, str] = {}
    conflicts: list[dict[str, Any]] = []
    for name, requirements in sorted(grouped.items()):
        installed = installed_packages.get(name) or installed_packages.get(name.replace("-", "_"))
        specifications = [str(item.specifier) for item in requirements if str(item.specifier)]
        if installed and all(not item.specifier or installed in item.specifier for item in requirements):
            resolved[name] = installed
            continue
        conflicts.append(
            {
                "name": name,
                "installed_version": installed,
                "requirements": [str(item) for item in requirements],
                "specifier_union": specifications,
                "resolution": (
                    f"Use verified working environment version {installed} as the lock constraint."
                    if installed
                    else "Resolve through uv/pip from the combined manifests during installation."
                ),
            }
        )
        if installed:
            resolved[name] = installed

    warnings: list[str] = []
    if conflicts:
        warnings.append(
            f"Compared all captured ComfyUI/custom-node requirement manifests and found {len(conflicts)} "
            "declared constraint conflict(s). The verified working environment versions were recorded as final constraints."
        )
    return (
        {
            "strategy": "combined-manifests-plus-verified-working-environment",
            "requirements": requirement_records,
            "resolved_versions": resolved,
            "conflicts": conflicts,
            "unparsed_requirements": unparsed,
        },
        warnings,
    )


def _is_own_git_checkout(path: Path) -> bool:
    if not path.is_dir():
        return False
    top = _git(path, "rev-parse", "--show-toplevel")
    if not top:
        return False
    try:
        return Path(top).resolve() == path.resolve()
    except OSError:
        return False

def _venv_python(comfy_dir: Path) -> Path | None:
    candidates = [
        comfy_dir / ".venv" / "Scripts" / "python.exe",
        comfy_dir / ".venv" / "bin" / "python",
        comfy_dir / "venv" / "Scripts" / "python.exe",
        comfy_dir / "venv" / "bin" / "python",
        comfy_dir / "python_embeded" / "python.exe",
        comfy_dir / "python_embedded" / "python.exe",
    ]
    return next((path for path in candidates if path.is_file()), None)


def _packages(
    python: Path | None,
) -> tuple[dict[str, str], list[str], str | None, list[dict[str, Any]], dict[str, str]]:
    if python is None:
        return {}, [], None, [], {}
    script = r'''
import importlib.metadata as md, json, platform, sys
packages = {}
distributions = []
for d in md.distributions():
    name = (d.metadata.get("Name") or d.name).lower().replace("_", "-")
    packages[name] = d.version
    direct_url = None
    try:
        raw = d.read_text("direct_url.json")
        if raw:
            direct_url = json.loads(raw)
    except Exception:
        pass
    distributions.append({"name": name, "version": d.version, "direct_url": direct_url})
top = []
try:
    import subprocess
    data = json.loads(subprocess.check_output([sys.executable, "-m", "pip", "list", "--not-required", "--format=json"], text=True))
    top = [f"{item['name']}=={item['version']}" for item in data]
except Exception:
    pass
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
    "packages": packages,
    "top": top,
    "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    "distributions": distributions,
    "runtime": {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "os": platform.system().lower(),
        "architecture": platform.machine().lower(),
        **torch_runtime,
    },
}))
'''
    output = _run([str(python), "-c", script])
    try:
        data = json.loads(output.splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {}, [], None, [], {}
    return (
        data.get("packages", {}),
        data.get("top", []),
        data.get("python"),
        data.get("distributions", []),
        data.get("runtime", {}),
    )


def _custom_roots(comfy_dir: Path) -> list[Path]:
    roots = [comfy_dir / "custom_nodes"]
    config_path = comfy_dir / "extra_model_paths.yaml"
    if config_path.is_file():
        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except Exception:
            config = {}
        for section in config.values() if isinstance(config, dict) else []:
            if not isinstance(section, dict):
                continue
            base_value = section.get("base_path")
            base = Path(os.path.expandvars(os.path.expanduser(base_value))) if isinstance(base_value, str) else comfy_dir
            values = section.get("custom_nodes", [])
            if isinstance(values, str):
                values = [values]
            if isinstance(values, list):
                for value in values:
                    if not isinstance(value, str):
                        continue
                    candidate = Path(os.path.expandvars(os.path.expanduser(value)))
                    roots.append(candidate if candidate.is_absolute() else base / candidate)
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root.resolve()) if root.exists() else str(root.absolute())
        if key not in seen:
            unique.append(root)
            seen.add(key)
    return unique


def _resolved_path(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except OSError:
        return path.expanduser().absolute()


def _custom_node_candidates(comfy_dir: Path) -> list[tuple[str, Path]]:
    """Return portable destination identities and complete source paths."""

    candidates: list[tuple[str, Path]] = []
    seen_paths: set[Path] = set()
    for root in _custom_roots(comfy_dir):
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir(), key=lambda item: item.name.lower()):
            if path.name.startswith(".") or path.name == "__pycache__":
                continue
            if not path.is_dir() and not (path.is_file() and path.suffix.lower() == ".py"):
                continue
            resolved = _resolved_path(path)
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            identity = _slug(path.stem if path.is_file() else path.name)
            candidates.append((identity, resolved))
    return candidates


def duplicate_custom_nodes(
    comfy_dir: Path,
    *,
    omitted_node_paths: set[Path] | None = None,
) -> list[DuplicateNodeGroup]:
    """Find unresolved duplicate destination identities across custom-node roots.

    Paths are returned resolved and complete so callers can present an honest
    choice to the user.  Omissions are explicit: nothing is selected or dropped
    merely because one root happened to be scanned before another.
    """

    omitted = {_resolved_path(path) for path in (omitted_node_paths or set())}
    grouped: dict[str, list[Path]] = {}
    for identity, path in _custom_node_candidates(comfy_dir):
        if path in omitted:
            continue
        grouped.setdefault(identity, []).append(path)
    return [
        DuplicateNodeGroup(identity=identity, paths=tuple(paths))
        for identity, paths in sorted(grouped.items())
        if len(paths) > 1
    ]


def _node_platforms(identity: str) -> tuple[list[str], list[str]]:
    value = identity.lower()
    if any(token in value for token in ("tensorrt", "trellis2", "gimm-vfi")):
        return ["windows", "linux"], ["nvidia"]
    return ["windows", "linux", "macos"], ["nvidia", "rocm", "mps", "cpu"]


def _node_metadata(path: Path) -> dict[str, str | None]:
    if not path.is_dir():
        return {"repository": None, "manager_id": None, "manager_version": None, "display_name": None}
    pyproject = path / "pyproject.toml"
    if not pyproject.is_file():
        return {"repository": None, "manager_id": None, "manager_version": None, "display_name": None}
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except Exception:
        return {"repository": None, "manager_id": None, "manager_version": None, "display_name": None}
    project = data.get("project", {}) if isinstance(data, dict) else {}
    tool = data.get("tool", {}) if isinstance(data, dict) else {}
    comfy = tool.get("comfy", {}) if isinstance(tool, dict) else {}
    urls = project.get("urls", {}) if isinstance(project, dict) else {}
    repository = None
    if isinstance(urls, dict):
        for key in ("Repository", "repository", "Source", "source", "Homepage", "homepage"):
            value = urls.get(key)
            if isinstance(value, str) and value.strip():
                repository = _sanitize_repository(value)
                if repository:
                    break
    manager_id = None
    manager_version = None
    display_name = None
    # A Registry node id is [project].name; PublisherId is required for
    # Registry publishing. We record it as a Manager candidate and retain the
    # Git repository as a deterministic fallback.
    if isinstance(project, dict) and isinstance(comfy, dict) and comfy.get("PublisherId"):
        candidate = project.get("name")
        if isinstance(candidate, str) and candidate.strip():
            manager_id = candidate.strip()
        version = project.get("version")
        if isinstance(version, str) and version.strip():
            manager_version = version.strip()
        display = comfy.get("DisplayName")
        if isinstance(display, str) and display.strip():
            display_name = display.strip()
    return {
        "repository": repository,
        "manager_id": manager_id,
        "manager_version": manager_version,
        "display_name": display_name,
    }


def _requirements(path: Path) -> str | None:
    if not path.is_dir():
        return None
    for candidate in ("requirements-no-cupy.txt", "requirements.txt"):
        if (path / candidate).is_file():
            return candidate
    return None


def _install_py_policy(path: Path) -> str:
    """Classify node install.py without executing it during export.

    Exact profiles already contain the verified final Python distribution set.
    Scripts whose only job is package installation must therefore be audited but
    not rerun, while lifecycle scripts such as comfy-env bootstrap remain required.
    """
    if not path.is_dir() or not (path / "install.py").is_file():
        return "none"
    if any(path.rglob("comfy-env.toml")):
        return "required"
    try:
        text = (path / "install.py").read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return "required"
    dependency_markers = (
        "pip install", "pip uninstall", "uv pip", "-m pip", "pip_main(",
        "subprocess.call([sys.executable", "subprocess.run([sys.executable",
        "check_call([sys.executable", "install_requirements", "os.system(",
    )
    side_effect_markers = (
        "urlretrieve", "requests.get", "httpx.get", "git clone", "download_file",
        "shutil.copy", "shutil.move", "write_text(", "write_bytes(",
        "git apply", "patch ", "cmake", "ninja",
    )
    if any(marker in text for marker in dependency_markers) and not any(
        marker in text for marker in side_effect_markers
    ):
        return "dependencies-only"
    return "required"


def _nodes(
    comfy_dir: Path,
    *,
    exact_refs: bool,
    include_unpublished_plugins: bool,
    omitted_node_paths: set[Path] | None = None,
    python: Path | None = None,
    resolver: NodeSourceResolver | None = None,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Path]]:
    """Export custom nodes using public provenance before local snapshots.

    The exporter exhausts local ComfyUI-Manager caches, configured catalogs,
    the official Manager catalog, the Comfy Registry, node metadata, and Git
    metadata before treating a node as unpublished.  Known public nodes remain
    small remote descriptors even when their installed folder does not retain
    a standalone ``.git`` directory.  Only genuinely unresolved source is
    embedded in the bundle.
    """

    output: list[dict[str, Any]] = []
    warnings: list[str] = []
    embedded: dict[str, Path] = {}
    seen: set[str] = set()
    seen_source_paths: set[Path] = set()
    omitted = {_resolved_path(path) for path in (omitted_node_paths or set())}
    source_resolver = resolver or NodeSourceResolver(comfy_dir, python=python)

    unresolved = duplicate_custom_nodes(comfy_dir, omitted_node_paths=omitted)
    if unresolved:
        if exact_refs:
            raise DuplicateNodeIdentityError(unresolved)
        for group in unresolved:
            warnings.append(
                f"Duplicate custom node {group.identity!r} was included only once from {group.paths[0]}; "
                f"other copies were ignored: {', '.join(str(path) for path in group.paths[1:])}."
            )

    for root in _custom_roots(comfy_dir):
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir(), key=lambda item: item.name.lower()):
            if path.name.startswith(".") or path.name == "__pycache__":
                continue
            if not path.is_dir() and not (path.is_file() and path.suffix.lower() == ".py"):
                continue

            resolved_path = _resolved_path(path)
            if resolved_path in omitted:
                warnings.append(f"Omitted custom node by user choice: {resolved_path}")
                continue
            if resolved_path in seen_source_paths:
                continue
            seen_source_paths.add(resolved_path)

            metadata = _node_metadata(path)
            own_git = _is_own_git_checkout(path)
            direct_repository = _sanitize_repository(_git(path, "remote", "get-url", "origin")) if own_git else ""
            direct_repository = direct_repository or str(metadata.get("repository") or "")
            direct_manager_id = str(metadata.get("manager_id") or "").strip() or None
            direct_manager_version = str(metadata.get("manager_version") or "").strip() or None

            resolution = source_resolver.resolve(
                path,
                allow_network=not bool(direct_repository or direct_manager_id),
            )
            repository = direct_repository or (resolution.repository if resolution else None)
            manager_id = direct_manager_id or (resolution.manager_id if resolution else None)
            manager_version = direct_manager_version or (resolution.manager_version if resolution else None)

            node_id = _slug(path.stem if path.is_file() else path.name)
            if node_id in seen:
                if exact_refs:
                    raise DuplicateNodeIdentityError(
                        duplicate_custom_nodes(comfy_dir, omitted_node_paths=omitted)
                        or [DuplicateNodeGroup(node_id, (resolved_path,))]
                    )
                continue
            seen.add(node_id)

            commit = _git(path, "rev-parse", "HEAD") if own_git else ""
            branch = (_git(path, "branch", "--show-current") or "main") if own_git else ""
            dirty = bool(own_git and _git(path, "status", "--porcelain"))
            origin_has_commit = bool(
                own_git and repository and commit and _commit_is_known_on_origin(path, commit)
            )

            identity = f"{path.name} {repository or ''} {manager_id or ''}"
            platforms, accelerators = _node_platforms(identity)
            source: dict[str, Any]

            if repository or manager_id:
                source = {"type": "remote"}
                if manager_id:
                    source["manager_id"] = manager_id
                if manager_version:
                    source["manager_version"] = manager_version
                if repository:
                    source["repository"] = repository

                # A commit is safe to export only when another machine can fetch
                # it, or when it came from a Manager snapshot.  Otherwise use the
                # Manager/Registry identity or public repository instead of
                # inflating the bundle with an otherwise known public node.
                selected_ref = ""
                if exact_refs and commit and origin_has_commit:
                    selected_ref = commit
                elif exact_refs and resolution and resolution.ref and repository:
                    selected_ref = resolution.ref
                elif not exact_refs and branch:
                    selected_ref = branch
                if selected_ref:
                    source["ref"] = selected_ref
                if exact_refs and selected_ref:
                    source["exact"] = True

                if manager_id and not selected_ref:
                    source["preferred"] = "manager"
                else:
                    source["preferred"] = "git" if repository else "manager"

                if resolution:
                    source["resolved_by"] = resolution.source_id
                    source["resolution_kind"] = resolution.source_kind
                    source["resolution_trust"] = resolution.trust
                    if resolution.install_folder:
                        source["install_folder"] = resolution.install_folder

                # Preserve an honest note only for actual local modifications.
                # A normal Manager installation without a private .git folder is
                # not considered dirty and emits no warning.
                if dirty:
                    source["local_changes_omitted"] = True
                    warnings.append(
                        f"{path.name} matches a known public Manager/Registry or Git source. "
                        "Its uncommitted local edits were not embedded; the portable setup will install the known public source."
                    )
            elif include_unpublished_plugins:
                source = {
                    "type": "embedded",
                    "payload": f"{EMBEDDED_ROOT}/{node_id}",
                    "layout": "file" if path.is_file() else "directory",
                }
                embedded[node_id] = path
                warnings.append(
                    f"{path.name} could not be resolved through node metadata, configured sources, "
                    "ComfyUI-Manager, the Comfy Registry, or Git, so only this unresolved plugin was embedded."
                )
            elif exact_refs:
                raise ExportError(
                    f"{path.name} could not be resolved through Git, ComfyUI-Manager, the Comfy Registry, "
                    "or configured node sources, and local-plugin embedding is disabled."
                )
            else:
                warnings.append(
                    f"{path.name} was skipped because it could not be resolved through Git, ComfyUI-Manager, "
                    "the Comfy Registry, or configured node sources and embedding unpublished plugins was disabled."
                )
                continue

            output.append(
                {
                    "id": node_id,
                    "name": str(
                        metadata.get("display_name")
                        or (resolution.display_name if resolution else None)
                        or path.name
                    ),
                    "folder": path.name,
                    "source": source,
                    "description": (
                        f"Custom node exported from a working ComfyUI installation: {path.name}"
                    ),
                    "platforms": platforms,
                    "accelerators": accelerators,
                    "requirements": _requirements(path),
                    "run_install_py": path.is_dir() and (path / "install.py").is_file(),
                    "install_py_policy": _install_py_policy(path),
                    "selected": True,
                }
            )
    return output, warnings, embedded


def _distribution_git_source(
    distributions: list[dict[str, Any]], package_name: str
) -> tuple[str | None, str | None]:
    wanted = package_name.lower().replace("_", "-")
    for distribution in distributions:
        name = str(distribution.get("name") or "").lower().replace("_", "-")
        if name != wanted:
            continue
        direct = distribution.get("direct_url")
        if not isinstance(direct, dict):
            continue
        url = str(direct.get("url") or "").strip()
        if url.startswith("git+"):
            url = url[4:]
        repository = _sanitize_repository(url)
        if not repository or "github.com/" not in repository.lower():
            continue
        vcs = direct.get("vcs_info")
        ref = None
        if isinstance(vcs, dict):
            ref = str(vcs.get("commit_id") or vcs.get("requested_revision") or "").strip() or None
        return repository, ref
    return None, None


def _accelerated(
    packages: dict[str, str],
    *,
    distributions: list[dict[str, Any]] | None = None,
    locked_wheel_names: set[str] | None = None,
    exact: bool = False,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    distributions = distributions or []
    locked_wheel_names = locked_wheel_names or set()
    cupy_name = next((name for name in ("cupy-cuda13x", "cupy-cuda12x", "cupy") if name in packages), None)
    if cupy_name and cupy_name not in locked_wheel_names:
        items.append({
            "id": "cupy", "name": "CuPy", "import_name": "cupy", "kind": "dynamic-cupy",
            "package_name": cupy_name, "version": packages[cupy_name],
            "platforms": ["windows", "linux"],
            "accelerators": ["nvidia"], "selected": True, "exact": exact,
            "description": (
                "CUDA array and kernel runtime. Exact profiles preserve the working environment's "
                "distribution family and version; compatibility profiles select by target CUDA major."
            )
        })
    versions: dict[str, str] = {}
    for major in ("12", "13"):
        for name in (f"tensorrt-cu{major}", f"tensorrt_cu{major}"):
            if name in packages:
                versions[major] = packages[name]
    if versions and not any(name in locked_wheel_names for name in ("tensorrt", "tensorrt-cu12", "tensorrt-cu13", "tensorrt_cu12", "tensorrt_cu13")):
        source_major = next(iter(versions)) if len(versions) == 1 else None
        source_package = f"tensorrt-cu{source_major}" if source_major else None
        items.append({
            "id": "tensorrt", "name": "TensorRT", "import_name": "tensorrt",
            "kind": "dynamic-tensorrt", "versions": versions,
            **({"package_name": source_package, "version": versions[source_major]} if source_package and source_major else {}),
            "platforms": ["windows", "linux"], "accelerators": ["nvidia"],
            "selected": True, "exact": exact,
            "description": (
                "NVIDIA TensorRT runtime. Exact profiles preserve the source distribution/version; "
                "compatibility profiles select for the detected CUDA major."
            )
        })
    for package_name, item_id, display, module, default_repository in (
        ("flash-attn", "flash-attn", "FlashAttention", "flash_attn", "https://github.com/Dao-AILab/flash-attention.git"),
        ("sageattention", "sageattention", "SageAttention", "sageattention", "https://github.com/thu-ml/SageAttention.git"),
    ):
        if package_name in packages and package_name not in locked_wheel_names:
            captured_repository, captured_ref = _distribution_git_source(distributions, package_name)
            repository = captured_repository or default_repository
            source_ref = captured_ref or "main"
            item = {
                "id": item_id, "name": display, "import_name": module,
                "kind": "wheel-or-source", "package": f"{package_name}=={packages[package_name]}",
                "source_repository": repository, "source_ref": source_ref, "source_subdir": ".",
                "source_ref_fallback": "main", "verify_source_version": True,
                "build_requirements": ["packaging", "setuptools", "wheel", "ninja", "einops"],
                "build_with_torch": True, "requires_cuda": True,
                "platforms": ["windows", "linux"], "accelerators": ["nvidia"],
                "selected": True, "exact": exact,
                "description": (
                    f"{display} resolved from compatible wheels first, then built from its verified public source "
                    "with the source environment's PyTorch/CUDA ABI and recorded package version."
                ),
            }
            if captured_ref:
                item["captured_source_commit"] = captured_ref
            items.append(item)
    onnx = "onnxruntime-gpu" if "onnxruntime-gpu" in packages else "onnxruntime" if "onnxruntime" in packages else None
    if onnx and onnx not in locked_wheel_names:
        items.append({
            "id": "onnxruntime", "name": "ONNX Runtime", "import_name": "onnxruntime",
            "kind": "dynamic-onnxruntime", "package_name": onnx, "version": packages[onnx],
            "platforms": ["windows", "linux", "macos"],
            "accelerators": ["nvidia", "rocm", "mps", "cpu"],
            "selected": True, "exact": exact,
            "description": (
                "ONNX execution runtime. Exact profiles preserve the source distribution/version; "
                "compatibility profiles select for the target accelerator."
            )
        })
    if "pyopengl-accelerate" in packages and "pyopengl-accelerate" not in locked_wheel_names:
        version = packages["pyopengl-accelerate"]
        items.append({
            "id": "pyopengl-accelerate", "name": "PyOpenGL Accelerate",
            "import_name": "OpenGL_accelerate", "kind": "pip-wheel-preferred",
            "package": f"PyOpenGL-accelerate=={version}",
            "platforms": ["windows", "linux", "macos"],
            "accelerators": ["nvidia", "rocm", "mps", "cpu"],
            "selected": True, "exact": exact,
            "description": "Optional compiled PyOpenGL speedups preserved at the exact working version.",
        })
    return items



def _portable_asset_sources() -> dict[str, Any]:
    """Export public catalog sources and entries without machine-local paths."""
    output: dict[str, Any] = {}
    for kind, filename in (("models", "model-sources.yaml"), ("workflows", "workflow-sources.yaml")):
        payload = load_editable_config(filename)
        sources: list[dict[str, Any]] = []
        allowed_source_ids: set[str] = set()
        for source in payload.get("sources", []):
            if not isinstance(source, dict):
                continue
            base_url = source.get("base_url")
            source_kind = str(source.get("kind") or "")
            if source_kind == "local" or (base_url and not str(base_url).startswith(("https://", "http://"))):
                continue
            clean = {
                key: value
                for key, value in source.items()
                if key not in {"path", "directory", "token", "api_key", "headers"}
            }
            sources.append(clean)
            if clean.get("id"):
                allowed_source_ids.add(str(clean["id"]))

        entries: list[dict[str, Any]] = []
        for entry in payload.get("entries", []):
            if not isinstance(entry, dict):
                continue
            url = str(entry.get("url") or "")
            source_id = str(entry.get("source_id") or "")
            if not url.startswith(("https://", "http://")):
                continue
            if source_id and source_id not in allowed_source_ids:
                continue
            entries.append(
                {
                    key: value
                    for key, value in entry.items()
                    if key not in {"local_path", "path", "token", "api_key", "headers"}
                }
            )
        output[kind] = {"schema_version": 1, "sources": sources, "entries": entries}
    return output


def _portable_library_policy(comfy_dir: Path) -> dict[str, Any]:
    status = inspect_instance_shared_assets(comfy_dir)
    return {
        "shared_models": {
            "enabled": True,
            "required": False,
            "configured_in_source": status.model_configured,
            "description": "Use one external models library for checkpoints, LoRAs, VAEs, encoders, and related files.",
        },
        "shared_workflows": {
            "enabled": True,
            "required": False,
            "configured_in_source": status.workflow_configured,
            "description": "Use one external workflow library shared by managed ComfyUI installations.",
        },
        "paths_embedded": False,
        "reason": "Portable profiles describe shared-library policy but never export machine-specific paths.",
    }

def _torch_export_config(
    packages: dict[str, str],
    runtime: dict[str, str],
    *,
    exact: bool,
) -> dict[str, Any]:
    raw_version = str(packages.get("torch") or runtime.get("torch_version") or "").strip()
    if exact and not raw_version:
        raise ExportError(
            "The working environment does not expose an installed PyTorch distribution. "
            "Refusing to invent a package version for an exact profile."
        )
    torch_version = raw_version.split("+", 1)[0] or "2.8.0"
    source_accelerator = str(runtime.get("accelerator") or "cpu").lower()
    backend_version = ""
    exact_index = ""
    indexes = {
        "nvidia_cuda13": "https://download.pytorch.org/whl/cu130",
        "nvidia_cuda12": "https://download.pytorch.org/whl/cu128",
        "nvidia": "https://download.pytorch.org/whl/cu128",
        "rocm": "https://download.pytorch.org/whl/rocm7.2",
        "cpu": "https://download.pytorch.org/whl/cpu",
        "mps": "",
    }
    if source_accelerator == "nvidia":
        backend_version = str(runtime.get("torch_cuda") or "").strip()
        if exact and not backend_version:
            raise ExportError("The source PyTorch build is CUDA-enabled, but its CUDA runtime version could not be read.")
        if backend_version:
            compact = backend_version.replace(".", "")
            exact_index = f"https://download.pytorch.org/whl/cu{compact}"
            indexes["nvidia"] = exact_index
            indexes[f"nvidia_cuda{compact}"] = exact_index
            indexes[f"nvidia_cuda{backend_version.split('.', 1)[0]}"] = exact_index
    elif source_accelerator == "rocm":
        backend_version = str(runtime.get("torch_hip") or "").strip()
        if exact and not backend_version:
            raise ExportError("The source PyTorch build is ROCm-enabled, but its ROCm runtime version could not be read.")
        if backend_version:
            exact_index = f"https://download.pytorch.org/whl/rocm{backend_version}"
            indexes["rocm"] = exact_index
            indexes[f"rocm{backend_version.replace('.', '')}"] = exact_index
    elif source_accelerator == "cpu":
        exact_index = indexes["cpu"]

    return {
        "version": torch_version,
        "torchvision": str(packages.get("torchvision") or "").split("+", 1)[0],
        "torchaudio": str(packages.get("torchaudio") or "").split("+", 1)[0],
        "indexes": indexes,
        "source_accelerator": source_accelerator,
        "source_backend_version": backend_version,
        "exact_backend_index": exact_index,
        "exact_backend": bool(exact),
        "fallback_unpinned": not exact,
    }


def build_profile_from_installation(
    comfy_dir: Path,
    *,
    name: str,
    publisher: str = "",
    exact_refs: bool = True,
    include_top_level_packages: bool = True,
    include_unpublished_plugins: bool = True,
    omitted_node_paths: set[Path] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    comfy_dir = comfy_dir.expanduser().resolve()
    if not is_comfyui_directory(comfy_dir):
        raise ExportError(f"Not a ComfyUI installation: {comfy_dir}")

    omitted = {_resolved_path(path) for path in (omitted_node_paths or set())}
    available_node_paths = {path for _identity, path in _custom_node_candidates(comfy_dir)}
    unknown_omissions = sorted(omitted - available_node_paths, key=str)
    if unknown_omissions:
        raise ExportError(
            "The following --omit-node path(s) are not discovered custom nodes for this installation:\n"
            + "\n".join(f"  - {path}" for path in unknown_omissions)
        )

    python = _venv_python(comfy_dir)
    packages, top_level, python_version, distributions, runtime = _packages(python)
    resolver = NodeSourceResolver(comfy_dir, python=python)
    nodes, warnings, embedded = _nodes(
        comfy_dir,
        exact_refs=exact_refs,
        include_unpublished_plugins=include_unpublished_plugins,
        omitted_node_paths=omitted,
        python=python,
        resolver=resolver,
    )
    repository = _sanitize_repository(_git(comfy_dir, "remote", "get-url", "origin")) or official_comfyui_repository()
    branch = _git(comfy_dir, "branch", "--show-current") or "master"
    commit = _git(comfy_dir, "rev-parse", "HEAD") or None
    origin_has_commit = bool(commit and _commit_is_known_on_origin(comfy_dir, commit))
    preferred_commit, overlay_paths, overlay_deletions, overlay_warnings = _comfyui_overlay(
        comfy_dir,
        branch=branch,
        commit=commit,
        origin_has_commit=origin_has_commit,
    )
    warnings.extend(overlay_warnings)

    if exact_refs and python is None:
        raise ExportError(
            "No ComfyUI Python environment was found. Exact export requires the working virtual environment "
            "so every installed distribution and its source can be captured."
        )

    torch_config = _torch_export_config(packages, runtime, exact=exact_refs)
    profile_version = time.strftime("%Y.%m.%d")
    compatibility = build_compatibility_tags(
        profile_version=profile_version,
        python_version=python_version,
        torch_version=str(packages.get("torch") or runtime.get("torch_version") or "") or None,
        accelerator=str(torch_config.get("source_accelerator") or "cpu"),
        backend_version=str(torch_config.get("source_backend_version") or ""),
        exact=exact_refs,
    )
    tagged_name = name if f"[{compatibility['abi_tag']}]" in name else f"{name} [{compatibility['abi_tag']}]"
    constraints = {package_name: packages[package_name] for package_name in sorted(CORE_CONSTRAINTS) if package_name in packages}
    environment_lock: dict[str, Any] = {}
    embedded_wheel_paths: dict[str, Path] = {}
    if exact_refs:
        environment_lock, lock_warnings, embedded_wheel_paths = build_environment_lock(
            distributions,
            source=runtime,
            comfy_dir=comfy_dir,
            source_roots=_custom_roots(comfy_dir),
            excluded_source_paths=omitted,
        )
        warnings.extend(lock_warnings)
        if not environment_lock.get("packages"):
            raise ExportError(
                "The working Python environment could not be inventoried. Refusing to create an exact profile with an empty package lock."
            )
        if environment_lock.get("complete") is False:
            names = ", ".join(environment_lock.get("nonportable_packages", [])) or "unknown packages"
            raise ExportError(
                "The working Python environment contains packages with no portable source: " + names + ". "
                "Provide reusable wheel files or immutable public sources before exporting."
            )
    locked_wheel_names = {
        str(item.get("name"))
        for item in environment_lock.get("packages", [])
        if isinstance(item, dict) and item.get("source") == "embedded-wheel"
    }
    accelerated_packages = _accelerated(
        packages, distributions=distributions, locked_wheel_names=locked_wheel_names, exact=exact_refs
    )

    extra_packages: list[str] = []
    if include_top_level_packages and not exact_refs:
        for specification in top_level:
            package_name = re.split(r"[=<>!~ @]", specification, maxsplit=1)[0].lower().replace("_", "-")
            if package_name not in EXCLUDED_TOP_LEVEL:
                extra_packages.append(specification)

    asset_sources = _portable_asset_sources()
    library_policy = _portable_library_policy(comfy_dir)
    dependency_manifests, dependency_manifest_paths = _dependency_manifests(
        comfy_dir,
        omitted_node_paths=omitted,
    )
    dependency_resolution, dependency_warnings = _resolve_manifest_dependencies(
        dependency_manifest_paths,
        packages,
        runtime,
    )
    warnings.extend(dependency_warnings)

    comfy_source: dict[str, Any] = {
        "type": "remote",
        "repository": repository,
        "ref": preferred_commit or (commit if origin_has_commit else branch),
    }
    if overlay_paths or overlay_deletions:
        comfy_source["overlay"] = {
            "payload": COMFYUI_OVERLAY_ROOT,
            "base_commit": preferred_commit,
            "changed_files": sorted(overlay_paths),
            "deleted_files": overlay_deletions,
        }

    profile: dict[str, Any] = {
        "schema_version": PROFILE_SCHEMA,
        "kind": PROFILE_KIND,
        "id": _slug(name),
        "name": tagged_name,
        "display_name": tagged_name,
        "version": compatibility["profile_version"],
        "compatibility": compatibility,
        "publisher": publisher,
        "description": "Portable ComfyUI setup exported from a working installation.",
        "comfyui": {
            "repository": repository,
            "branch": branch,
            "preferred_commit": preferred_commit or (commit if origin_has_commit else None),
            "exact": bool(exact_refs),
            "official_repository": official_comfyui_repository(),
            "source": comfy_source,
        },
        "python": {
            "preferred": python_version or "3.12",
            "fallbacks": ([python_version] if exact_refs and python_version else ["3.12", "3.11", "3.13"]),
        },
        "torch": torch_config,
        "constraints": constraints,
        "environment_lock": environment_lock,
        "dependency_manifests": dependency_manifests,
        "dependency_resolution": dependency_resolution,
        "extra_python_packages": sorted(set(extra_packages), key=str.lower),
        "models": asset_sources["models"].get("entries", []),
        "workflows": asset_sources["workflows"].get("entries", []),
        "asset_sources": asset_sources,
        "libraries": library_policy,
        "system_dependencies": [
            {"id": "git", "name": "Git", "commands": ["git"], "required": True, "why": "Clones ComfyUI and remote custom-node repositories."},
            {"id": "ffmpeg", "name": "FFmpeg", "commands": ["ffmpeg", "ffprobe"], "required": False, "why": "Video and audio processing."},
            {"id": "sox", "name": "SoX", "commands": ["sox"], "required": False, "why": "Speech and audio processing."},
            {"id": "tesseract", "name": "Tesseract OCR", "commands": ["tesseract"], "required": False, "why": "OCR nodes; trained data is never bundled."},
            {"id": "cmake", "name": "CMake", "commands": ["cmake"], "required": False, "why": "Native source builds."},
            {"id": "ninja", "name": "Ninja", "commands": ["ninja"], "required": False, "why": "Native source builds."},
            {"id": "compiler", "name": "C/C++ compiler", "commands": ["__compiler__"], "required": False, "why": "Native source builds."},
            {"id": "pkg-config", "name": "pkg-config", "commands": ["pkg-config"], "required": False, "why": "Native library discovery."},
            {"id": "rust", "name": "Rust toolchain", "commands": ["cargo", "rustc"], "required": False, "why": "Rust-based source builds."},
        ],
        "nodes": nodes,
        "accelerated_packages": accelerated_packages,
        "post_install": {
            "copy_websocket_example": True,
            "generate_launchers": True,
            "validate_imports": ["torch", "numpy", "transformers"],
        },
        "export_metadata": {
            "base_name": name,
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "source_comfyui_commit": commit,
            "node_count": len(nodes),
            "remote_node_count": sum(1 for node in nodes if node["source"]["type"] == "remote"),
            "embedded_unpublished_node_count": len(embedded),
            "comfyui_overlay_file_count": len(overlay_paths),
            "dependency_manifest_count": len(dependency_manifests),
            "locked_python_package_count": len(environment_lock.get("packages", [])),
            "environment_lock_complete": environment_lock.get("complete", False),
            "explicitly_omitted_custom_node_count": len(omitted),
            "explicitly_omitted_source_package_count": len(
                environment_lock.get("explicitly_omitted_packages", [])
            ),
            "models_included": False,
            "machine_paths_included": False,
        },
    }
    if overlay_paths:
        profile["_comfyui_overlay_paths"] = {
            relative: str(path) for relative, path in overlay_paths.items()
        }
    if embedded:
        profile["_embedded_plugin_paths"] = {node_id: str(path) for node_id, path in embedded.items()}
    if embedded_wheel_paths:
        profile["_embedded_wheel_paths"] = {payload: str(path) for payload, path in embedded_wheel_paths.items()}
    if dependency_manifest_paths:
        profile["_dependency_manifest_paths"] = {
            payload: str(path) for payload, path in dependency_manifest_paths.items()
        }
    if python is None:
        warnings.append("No ComfyUI Python environment was found; package versions could not be captured.")
    return profile, warnings


def export_setup(
    comfy_dir: Path,
    output_path: Path,
    *,
    name: str,
    publisher: str = "",
    exact_refs: bool = True,
    include_top_level_packages: bool = True,
    include_unpublished_plugins: bool = True,
    omitted_node_paths: set[Path] | None = None,
) -> tuple[Path, list[str]]:
    profile, warnings = build_profile_from_installation(
        comfy_dir,
        name=name,
        publisher=publisher,
        exact_refs=exact_refs,
        include_top_level_packages=include_top_level_packages,
        include_unpublished_plugins=include_unpublished_plugins,
        omitted_node_paths=omitted_node_paths,
    )
    path = write_profile_bundle(profile, output_path)
    return path, warnings
