from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib
import urllib.parse
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

from .configuration import official_comfyui_repository
from .discovery import installed_nodes, is_comfyui_directory
from .package_sources import PYPI_SIMPLE, clean_package_environment
from .runner import CommandError, Runner

OFFICIAL_COMFYUI_REPOSITORY = official_comfyui_repository()
OFFICIAL_REMOTE_NAME = "comfyui-official"
SNAPSHOT_VERSION = 1
MAX_UNTRACKED_SNAPSHOT_BYTES = 64 * 1024 * 1024
UPDATE_STRATEGIES = {"safe", "patch", "force", "new", "abort"}

# These directories are large, user-owned, or intentionally unaffected by a
# source update. They are never copied into a lightweight update snapshot.
SNAPSHOT_EXCLUDED_ROOTS = {
    ".git",
    ".venv",
    ".comfy-setup",
    ".installer-wheelhouse",
    "custom_nodes",
    "models",
    "user",
    "input",
    "output",
    "temp",
    "cache",
    ".cache",
}

# Packages commonly tied to a particular Python, PyTorch, CUDA, ROCm, or GPU
# architecture. Safe updates preserve these versions unless the user explicitly
# forces an update through a known incompatibility.
ACCELERATOR_PACKAGES = {
    "torch",
    "torchvision",
    "torchaudio",
    "triton",
    "xformers",
    "flash-attn",
    "flash_attn",
    "sageattention",
    "cupy",
    "cupy-cuda11x",
    "cupy-cuda12x",
    "cupy-cuda13x",
    "tensorrt",
    "tensorrt-cu12",
    "tensorrt-cu13",
    "onnxruntime-gpu",
}


class UpdateError(RuntimeError):
    """Raised when an update or rollback cannot be completed safely."""


@dataclass(slots=True)
class UpdateIssue:
    severity: str
    title: str
    detail: str


@dataclass(slots=True)
class UpdatePackageChange:
    name: str
    current_version: str | None
    current_requirement: str | None
    target_requirement: str | None
    action: str
    required: bool
    selected: bool = True


@dataclass(slots=True)
class UpdatePreflight:
    installation: Path
    current_commit: str
    current_branch: str
    current_repository: str | None
    official_branch: str
    target_commit: str
    commits_behind: int
    commits_ahead: int
    requirements_changed: bool
    python_supported: bool
    custom_node_count: int
    dirty_custom_nodes: int
    issues: list[UpdateIssue] = field(default_factory=list)
    baseline_pip_ok: bool = True
    resolution_possible: bool = True
    resolution_checked: bool = False
    required_changes: list[str] = field(default_factory=list)
    resolution_error: str | None = None
    core_file_changes: list[str] = field(default_factory=list)
    core_package_changes: list[UpdatePackageChange] = field(default_factory=list)
    protected_packages: list[str] = field(default_factory=list)
    normalized_duplicate_packages: list[str] = field(default_factory=list)

    @property
    def update_available(self) -> bool:
        return bool(self.target_commit and self.target_commit != self.current_commit)

    @property
    def blocking(self) -> bool:
        return any(issue.severity == "blocking" for issue in self.issues)

    @property
    def high_risk(self) -> bool:
        return self.blocking or any(issue.severity == "warning" for issue in self.issues)

    @property
    def patchable(self) -> bool:
        # Patch is an explicit recovery attempt.  It must remain available when
        # preflight cannot prove the candidate plan, because the operation is
        # snapshotted and rolls back on resolver or validation failure.
        return self.python_supported

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["installation"] = str(self.installation)
        return data


@dataclass(slots=True)
class SnapshotRecord:
    snapshot_id: str
    path: Path
    created_at: str
    commit: str
    branch: str
    target_commit: str | None
    package_count: int
    custom_node_count: int
    restorable: bool
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["path"] = str(self.path)
        return data


@dataclass(slots=True)
class UpdateResult:
    success: bool
    snapshot: SnapshotRecord | None
    previous_commit: str | None
    current_commit: str | None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    validation_log: Path | None = None
    strategy: str = "safe"
    rolled_back: bool = False
    rollback_error: str | None = None
    required_changes: list[str] = field(default_factory=list)
    recommended_new_install: bool = False
    selected_packages: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RollbackResult:
    success: bool
    snapshot: SnapshotRecord
    current_commit: str | None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    validation_log: Path | None = None


def _normalise_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _python_executable(root: Path) -> Path:
    if os.name == "nt":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def _uv_executable() -> str:
    configured = os.environ.get("COMFY_INSTALLER_UV")
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which("uv")
    if found:
        return found
    # The manager depends on uv, but some package layouts expose only the
    # Python module. Use the current interpreter in that case.
    return sys.executable


def _uv_command(*arguments: str) -> list[str]:
    executable = _uv_executable()
    if Path(executable).resolve() == Path(sys.executable).resolve() and shutil.which("uv") is None:
        return [executable, "-m", "uv", *arguments]
    return [executable, *arguments]


def _git_output(root: Path, *arguments: str, check: bool = False) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=45,
    )
    if check and completed.returncode != 0:
        raise UpdateError(completed.stdout.strip() or f"git {' '.join(arguments)} failed")
    return completed.stdout.strip()


def _parse_requirements(text: str) -> dict[str, Requirement]:
    requirements: dict[str, Requirement] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        try:
            requirement = Requirement(line)
        except Exception:
            continue
        if requirement.marker is not None:
            try:
                if not requirement.marker.evaluate():
                    continue
            except Exception:
                pass
        requirements[_normalise_name(requirement.name)] = requirement
    return requirements


def _parse_pyproject_requirements(path: Path) -> dict[str, Requirement]:
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}
    values = data.get("project", {}).get("dependencies", [])
    if not isinstance(values, list):
        return {}
    return _parse_requirements("\n".join(str(item) for item in values))


def _exact_versions(requirement: Requirement) -> list[str]:
    return [
        specifier.version
        for specifier in requirement.specifier
        if specifier.operator in {"==", "==="} and "*" not in specifier.version
    ]


def _compatible_upper(version: Version) -> Version | None:
    release = list(version.release)
    if len(release) < 2:
        return None
    index = len(release) - 2
    upper = release[: index + 1]
    upper[index] += 1
    return Version(".".join(str(part) for part in upper))


def _specifier_bounds(specifiers: SpecifierSet) -> tuple[tuple[Version, bool] | None, tuple[Version, bool] | None]:
    lower: tuple[Version, bool] | None = None
    upper: tuple[Version, bool] | None = None

    def choose_lower(candidate: tuple[Version, bool]) -> None:
        nonlocal lower
        if lower is None or candidate[0] > lower[0] or (
            candidate[0] == lower[0] and not candidate[1] and lower[1]
        ):
            lower = candidate

    def choose_upper(candidate: tuple[Version, bool]) -> None:
        nonlocal upper
        if upper is None or candidate[0] < upper[0] or (
            candidate[0] == upper[0] and not candidate[1] and upper[1]
        ):
            upper = candidate

    for specifier in specifiers:
        operator = specifier.operator
        value = specifier.version
        if "*" in value or operator in {"!=", "==="}:
            continue
        try:
            version = Version(value)
        except InvalidVersion:
            continue
        if operator == ">=":
            choose_lower((version, True))
        elif operator == ">":
            choose_lower((version, False))
        elif operator == "<=":
            choose_upper((version, True))
        elif operator == "<":
            choose_upper((version, False))
        elif operator == "==":
            choose_lower((version, True))
            choose_upper((version, True))
        elif operator == "~=":
            choose_lower((version, True))
            compatible_upper = _compatible_upper(version)
            if compatible_upper is not None:
                choose_upper((compatible_upper, False))
    return lower, upper


def _requirement_conflicts(core: Requirement, node: Requirement) -> bool:
    core_exact = _exact_versions(core)
    node_exact = _exact_versions(node)
    candidates = core_exact or node_exact
    for candidate in candidates:
        try:
            version = Version(candidate)
        except InvalidVersion:
            continue
        if version not in core.specifier or version not in node.specifier:
            return True
    if core_exact and node_exact:
        return set(core_exact).isdisjoint(node_exact)

    core_lower, core_upper = _specifier_bounds(core.specifier)
    node_lower, node_upper = _specifier_bounds(node.specifier)
    lowers = [item for item in (core_lower, node_lower) if item is not None]
    uppers = [item for item in (core_upper, node_upper) if item is not None]
    if not lowers or not uppers:
        return False
    lower = max(lowers, key=lambda item: (item[0], not item[1]))
    upper = min(uppers, key=lambda item: (item[0], item[1]))
    if lower[0] > upper[0]:
        return True
    if lower[0] == upper[0] and (not lower[1] or not upper[1]):
        return True
    return False

def _path_is_excluded(relative: Path) -> bool:
    return bool(relative.parts and relative.parts[0] in SNAPSHOT_EXCLUDED_ROOTS)


def _core_status(root: Path) -> str:
    """Return Git status entries that belong to the ComfyUI source itself.

    Runtime/user directories are intentionally preserved by updates and should
    not make an otherwise safe core update look dirty.
    """
    completed = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "-z"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=20,
    )
    entries: list[str] = []
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        text = raw.decode("utf-8", errors="replace")
        path_text = text[3:] if len(text) > 3 else ""
        if " -> " in path_text:
            path_text = path_text.split(" -> ", 1)[1]
        if path_text and _path_is_excluded(Path(path_text)):
            continue
        entries.append(text)
    return "\n".join(entries)


def _safe_snapshot_id(commit: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    return f"{timestamp}-{(commit or 'unknown')[:12]}"


class ComfyUpdateManager:
    """Transactional ComfyUI source updater with lightweight rollback snapshots.

    The source checkout and Python package manifest are snapshotted. Models,
    workflows, outputs, custom-node directories, and the full virtual
    environment are deliberately not copied. Custom nodes are left in place and
    are validated against the updated core after dependency installation.
    """

    def __init__(
        self,
        installation: Path,
        *,
        runner: Runner | None = None,
        official_repository: str = OFFICIAL_COMFYUI_REPOSITORY,
    ) -> None:
        self.root = installation.expanduser().resolve()
        self.runner = runner or Runner()
        self.official_repository = official_repository
        self.state_dir = self.root / ".comfy-setup"
        self.snapshots_dir = self.state_dir / "snapshots"
        self.python = _python_executable(self.root)
        self._native_package_cache: set[str] | None = None
        self._freeze_duplicate_packages: set[str] = set()

    def _validate_installation(self) -> None:
        if not is_comfyui_directory(self.root):
            raise UpdateError(f"Not a ComfyUI installation: {self.root}")
        if not (self.root / ".git").exists():
            raise UpdateError("This ComfyUI installation is not a Git checkout and cannot be updated in place.")
        if not self.python.is_file():
            raise UpdateError("The installation has no usable .venv. Repair it before updating.")
        if shutil.which("git") is None:
            raise UpdateError("Git is required for ComfyUI updates.")

    def _official_default_branch(self) -> str:
        completed = subprocess.run(
            ["git", "ls-remote", "--symref", self.official_repository, "HEAD"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=45,
            check=False,
        )
        for line in completed.stdout.splitlines():
            match = re.match(r"ref:\s+refs/heads/(\S+)\s+HEAD", line)
            if match:
                return match.group(1)
        return "master"

    def _ensure_official_remote(self, branch: str) -> None:
        remotes = set(_git_output(self.root, "remote").splitlines())
        if OFFICIAL_REMOTE_NAME not in remotes:
            self.runner.run(
                ["git", "-C", str(self.root), "remote", "add", OFFICIAL_REMOTE_NAME, self.official_repository]
            )
        else:
            current = _git_output(self.root, "remote", "get-url", OFFICIAL_REMOTE_NAME)
            if current != self.official_repository:
                self.runner.run(
                    ["git", "-C", str(self.root), "remote", "set-url", OFFICIAL_REMOTE_NAME, self.official_repository]
                )
        self.runner.run(
            ["git", "-C", str(self.root), "fetch", "--prune", OFFICIAL_REMOTE_NAME, branch]
        )

    def _git_show(self, ref: str, path: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(self.root), "show", f"{ref}:{path}"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=20,
        )
        return completed.stdout if completed.returncode == 0 else ""

    def _installed_versions(self) -> dict[str, str]:
        code = (
            "import importlib.metadata as m,json;"
            "print(json.dumps([[d.metadata['Name'],d.version] for d in m.distributions() if d.metadata.get('Name')]))"
        )
        completed = subprocess.run(
            [str(self.python), "-c", code],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        )
        try:
            raw = json.loads(completed.stdout.strip())
        except json.JSONDecodeError:
            return {}
        if not isinstance(raw, list):
            return {}
        # importlib.metadata returns the distribution that Python actually sees
        # first.  Keep that effective version when stale/alias dist-info records
        # canonicalize to the same project name.
        versions: dict[str, str] = {}
        for item in raw:
            if not isinstance(item, list) or len(item) != 2:
                continue
            name, version = item
            versions.setdefault(_normalise_name(str(name)), str(version))
        return versions

    def _target_python_supported(self, target_ref: str) -> tuple[bool, str | None]:
        pyproject = self._git_show(target_ref, "pyproject.toml")
        if not pyproject:
            return True, None
        try:
            data = tomllib.loads(pyproject)
            requires_python = data.get("project", {}).get("requires-python")
        except Exception:
            return True, None
        if not requires_python:
            return True, None
        current = Version(
            subprocess.run(
                [str(self.python), "-c", "import platform;print(platform.python_version())"],
                text=True,
                stdout=subprocess.PIPE,
                check=False,
            ).stdout.strip()
        )
        specifier = SpecifierSet(str(requires_python))
        return current in specifier, str(requires_python)

    def _custom_node_conflicts(self, target_requirements: dict[str, Requirement]) -> list[str]:
        conflicts: list[str] = []
        for node in installed_nodes(self.root):
            combined: dict[str, Requirement] = {}
            requirement_files = [node.path / "requirements.txt", *node.path.glob("requirements-*.txt")]
            for requirement_file in requirement_files:
                if not requirement_file.is_file():
                    continue
                try:
                    combined.update(
                        _parse_requirements(requirement_file.read_text(encoding="utf-8", errors="ignore"))
                    )
                except OSError:
                    continue
            combined.update(_parse_pyproject_requirements(node.path / "pyproject.toml"))
            for name, node_requirement in combined.items():
                core_requirement = target_requirements.get(name)
                if core_requirement and _requirement_conflicts(core_requirement, node_requirement):
                    conflicts.append(
                        f"{node.name}: {node_requirement} conflicts with ComfyUI requirement {core_requirement}"
                    )
        return conflicts

    def _target_requirements(self, target_ref: str) -> list[tuple[str, Requirement]]:
        output: list[tuple[str, Requirement]] = []
        for filename in ("requirements.txt", "manager_requirements.txt"):
            for requirement in _parse_requirements(self._git_show(target_ref, filename)).values():
                output.append((f"ComfyUI {filename}", requirement))
        return output

    def _custom_requirements(self) -> list[tuple[str, Requirement]]:
        output: list[tuple[str, Requirement]] = []
        for node in installed_nodes(self.root):
            combined: dict[str, Requirement] = {}
            for path in (node.path / "requirements.txt", *node.path.glob("requirements-*.txt")):
                if path.is_file():
                    try:
                        combined.update(_parse_requirements(path.read_text(encoding="utf-8", errors="ignore")))
                    except OSError:
                        pass
            combined.update(_parse_pyproject_requirements(node.path / "pyproject.toml"))
            output.extend((node.name, requirement) for requirement in combined.values())
        return output

    def _required_direct_changes(self, target_ref: str) -> list[str]:
        installed = self._installed_versions()
        changes: list[str] = []
        for owner, requirement in self._target_requirements(target_ref):
            name = _normalise_name(requirement.name)
            version = installed.get(name)
            if version is None:
                changes.append(f"install {requirement} ({owner})")
                continue
            try:
                parsed = Version(version.split("+", 1)[0])
                if requirement.specifier and parsed not in requirement.specifier:
                    changes.append(f"change {requirement.name} {version} to satisfy {requirement} ({owner})")
            except InvalidVersion:
                changes.append(f"verify non-standard version {requirement.name}=={version} against {requirement}")
        return changes

    @staticmethod
    def _version_satisfies(version: str | None, requirement: Requirement) -> bool:
        if version is None:
            return False
        if not requirement.specifier:
            return True
        try:
            return Version(version.split("+", 1)[0]) in requirement.specifier
        except InvalidVersion:
            return False

    def _core_package_changes(
        self,
        current_requirements_text: str,
        current_manager_requirements: str,
        target_ref: str,
    ) -> list[UpdatePackageChange]:
        installed = self._installed_versions()
        current: dict[str, list[Requirement]] = {}
        for text in (current_requirements_text, current_manager_requirements):
            for name, requirement in _parse_requirements(text).items():
                current.setdefault(name, []).append(requirement)
        target: dict[str, list[Requirement]] = {}
        for _owner, requirement in self._target_requirements(target_ref):
            target.setdefault(_normalise_name(requirement.name), []).append(requirement)
        changes: list[UpdatePackageChange] = []
        for name in sorted(set(current) | set(target)):
            before = current.get(name, [])
            after = target.get(name, [])
            before_text = " AND ".join(dict.fromkeys(str(item) for item in before)) or None
            after_text = " AND ".join(dict.fromkeys(str(item) for item in after)) or None
            if before_text == after_text:
                continue
            version = installed.get(name)
            if not after:
                changes.append(UpdatePackageChange(
                    name=name,
                    current_version=version,
                    current_requirement=before_text,
                    target_requirement=None,
                    action="no longer required by ComfyUI (kept installed)",
                    required=False,
                    selected=False,
                ))
                continue
            required = any(not self._version_satisfies(version, requirement) for requirement in after)
            action = "install or change" if required else "manifest changed; installed version remains compatible"
            changes.append(UpdatePackageChange(
                name=name,
                current_version=version,
                current_requirement=before_text,
                target_requirement=after_text,
                action=action,
                required=required,
                selected=required,
            ))
        return changes

    def _core_file_changes(self, current_ref: str, target_ref: str) -> list[str]:
        output = _git_output(self.root, "diff", "--name-status", current_ref, target_ref)
        return [line for line in output.splitlines() if line.strip()]

    def _native_package_names(self) -> set[str]:
        if self._native_package_cache is not None:
            return set(self._native_package_cache)
        code = (
            "import importlib.metadata as m,json;"
            "suffixes=('.so','.pyd','.dll','.dylib');"
            "print(json.dumps(sorted({d.metadata.get('Name','') for d in m.distributions() "
            "if d.metadata.get('Name') and any(str(f).lower().endswith(suffixes) for f in (d.files or []))})))"
        )
        completed = subprocess.run(
            [str(self.python), "-c", code], text=True, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, check=False, timeout=45,
        )
        try:
            names = json.loads(completed.stdout.strip())
        except json.JSONDecodeError:
            names = []
        self._native_package_cache = {_normalise_name(str(name)) for name in names if str(name)}
        return set(self._native_package_cache)

    def _profile_protected_names(self) -> set[str]:
        report_path = self.state_dir / "install-report.json"
        if not report_path.is_file():
            return set()
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set()
        profile = report.get("profile", {}) if isinstance(report, dict) else {}
        values = profile.get("protected_packages", []) if isinstance(profile, dict) else []
        protected = {_normalise_name(str(value)) for value in values if str(value)}
        profile_id = str(profile.get("id") or "") if isinstance(profile, dict) else ""
        if profile_id:
            try:
                from .profile import list_profiles

                record = next((item for item in list_profiles() if item.id == profile_id), None)
            except Exception:
                record = None
            if record is not None:
                declared = record.profile
                lock = declared.get("environment_lock", {})
                if isinstance(lock, dict):
                    protected.update(
                        _normalise_name(str(item.get("name")))
                        for item in lock.get("packages", [])
                        if isinstance(item, dict) and item.get("name")
                    )
                constraints = declared.get("constraints", {})
                if isinstance(constraints, dict):
                    protected.update(_normalise_name(str(name)) for name in constraints)
                protected.update(
                    _normalise_name(requirement.name)
                    for requirement in _parse_requirements(
                        "\n".join(str(item) for item in declared.get("extra_python_packages", []))
                    ).values()
                )
                for item in declared.get("accelerated_packages", []):
                    if isinstance(item, dict):
                        package_name = item.get("package") or item.get("name") or item.get("id")
                        if package_name:
                            protected.add(_normalise_name(str(package_name)))
        return protected

    def _protected_package_names(self, target_ref: str) -> set[str]:
        target_names = {
            _normalise_name(requirement.name)
            for _owner, requirement in self._target_requirements(target_ref)
        }
        # Everything outside ComfyUI's direct package surface belongs to the
        # user's/profile's working environment.  Preserve it exactly; selected
        # core packages are the only resolver-owned changes during an update.
        installed_non_core = set(self._installed_versions()) - target_names
        compiled_non_core = self._native_package_names() - target_names
        profile_non_core = self._profile_protected_names() - target_names
        return {
            *(_normalise_name(item) for item in ACCELERATOR_PACKAGES),
            *installed_non_core,
            *compiled_non_core,
            *profile_non_core,
        }

    @staticmethod
    def _frozen_line_score(requirement: Requirement, current_version: str | None) -> int:
        """Prefer the requirement describing Python's effective distribution."""
        if current_version:
            for specifier in requirement.specifier:
                if specifier.operator not in {"==", "==="} or "*" in specifier.version:
                    continue
                try:
                    if Version(specifier.version) == Version(current_version):
                        return 4
                except InvalidVersion:
                    if specifier.version == current_version:
                        return 4
        if requirement.url:
            return 3
        if any(specifier.operator in {"==", "==="} for specifier in requirement.specifier):
            return 2
        return 1

    def _deduplicate_freeze_lines(self, freeze_lines: Iterable[str]) -> list[str]:
        """Return one resolver constraint per normalized installed project.

        Some otherwise working environments contain stale or alias ``dist-info``
        records for the same normalized project.  ``pip check`` and Python use
        the first effective distribution, but feeding every record to uv creates
        an impossible ``name==A`` plus ``name==B`` constraint set.
        """
        installed = self._installed_versions()
        output: list[str] = []
        positions: dict[str, int] = {}
        parsed: dict[str, Requirement] = {}
        duplicates: set[str] = set()
        for raw in freeze_lines:
            line = str(raw).strip()
            if not line:
                continue
            try:
                requirement = Requirement(line)
            except Exception:
                if line not in output:
                    output.append(line)
                continue
            name = _normalise_name(requirement.name)
            if name not in positions:
                positions[name] = len(output)
                parsed[name] = requirement
                output.append(line)
                continue
            duplicates.add(name)
            previous = parsed[name]
            current_version = installed.get(name)
            if self._frozen_line_score(requirement, current_version) > self._frozen_line_score(
                previous, current_version
            ):
                output[positions[name]] = line
                parsed[name] = requirement
        self._freeze_duplicate_packages.update(duplicates)
        for name in sorted(duplicates):
            self.runner.log(
                f"Normalized duplicate installed metadata for {name}; "
                f"using {output[positions[name]]!r} as the effective resolver constraint."
            )
        return output

    def _write_update_inputs(
        self,
        target_ref: str,
        *,
        include_custom_nodes: bool,
        freeze_lines: list[str] | None = None,
        prefix: str = "update",
        selected_core_packages: set[str] | None = None,
        selectable_core_packages: set[str] | None = None,
        additional_protected_names: set[str] | None = None,
    ) -> tuple[Path, Path]:
        """Write audited resolver inputs without executing upstream requirements files."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        requirements_path = self.state_dir / f"{prefix}-reconciled-requirements.txt"
        constraints_path = self.state_dir / f"{prefix}-protected-constraints.txt"
        selected = {_normalise_name(name) for name in selected_core_packages or set()}
        selectable = {_normalise_name(name) for name in selectable_core_packages or set()}
        rows = [
            (owner, requirement)
            for owner, requirement in self._target_requirements(target_ref)
            if not selectable
            or _normalise_name(requirement.name) not in selectable
            or _normalise_name(requirement.name) in selected
        ]
        if include_custom_nodes:
            rows.extend(self._custom_requirements())
        seen: set[str] = set()
        lines = [
            "# Generated by ComfyUI Setup Manager.",
            "# Only reviewed core package changes are installed; upstream requirements.txt is never executed directly.",
            "# Custom-node manifests are checked for direct conflicts, then the installed nodes are validated at startup.",
        ]
        for owner, requirement in rows:
            rendered = str(requirement)
            key = rendered.lower()
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"# {owner}")
            lines.append(rendered)
        requirements_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        protected_names = {
            *self._protected_package_names(target_ref),
            *(_normalise_name(name) for name in additional_protected_names or set()),
        }
        protected: list[str] = []
        source_freeze = freeze_lines if freeze_lines is not None else self._freeze_environment()
        for line in self._deduplicate_freeze_lines(source_freeze):
            try:
                normalised = _normalise_name(Requirement(line).name)
            except Exception:
                normalised = ""
            if normalised in protected_names:
                if " @ __SNAPSHOT__" not in line and line not in protected:
                    protected.append(line)
        constraints_path.write_text("\n".join(protected) + ("\n" if protected else ""), encoding="utf-8")
        return requirements_path, constraints_path

    def _pip_check(self) -> tuple[bool, str]:
        completed = self.runner.capture(
            _uv_command("pip", "check", "--python", str(self.python), "--no-config"),
            cwd=self.root,
            env=clean_package_environment(),
        )
        return completed.returncode == 0, completed.stdout.strip()

    def _dry_run_resolution(
        self,
        target_ref: str,
        *,
        selected_core_packages: set[str],
    ) -> tuple[bool, str]:
        target_names = {
            _normalise_name(requirement.name)
            for _owner, requirement in self._target_requirements(target_ref)
        }
        requirements, constraints = self._write_update_inputs(
            target_ref,
            include_custom_nodes=False,
            prefix="update-preflight",
            selected_core_packages=selected_core_packages,
            selectable_core_packages=target_names,
            additional_protected_names=target_names - selected_core_packages,
        )
        command = _uv_command(
            "pip", "install", "--dry-run", "--index-strategy", "unsafe-best-match",
            "--python", str(self.python), "--no-config", "--default-index", PYPI_SIMPLE,
            "-r", str(requirements),
        )
        if constraints.stat().st_size:
            command.extend(["-c", str(constraints)])
        completed = self.runner.capture(command, cwd=self.root, env=clean_package_environment())
        return completed.returncode == 0, completed.stdout.strip()

    def preflight(self, *, fetch: bool = True) -> UpdatePreflight:
        self._validate_installation()
        current_commit = _git_output(self.root, "rev-parse", "HEAD", check=True)
        current_branch = _git_output(self.root, "branch", "--show-current") or "(detached)"
        current_repository = _git_output(self.root, "remote", "get-url", "origin") or None
        official_branch = self._official_default_branch()
        if fetch:
            self._ensure_official_remote(official_branch)
        target_ref = f"{OFFICIAL_REMOTE_NAME}/{official_branch}"
        target_commit = _git_output(self.root, "rev-parse", target_ref, check=True)
        behind = int(_git_output(self.root, "rev-list", "--count", f"{current_commit}..{target_commit}") or "0")
        ahead = int(_git_output(self.root, "rev-list", "--count", f"{target_commit}..{current_commit}") or "0")
        current_requirements = (self.root / "requirements.txt").read_text(
            encoding="utf-8", errors="ignore"
        ) if (self.root / "requirements.txt").is_file() else ""
        current_manager_requirements = (self.root / "manager_requirements.txt").read_text(
            encoding="utf-8", errors="ignore"
        ) if (self.root / "manager_requirements.txt").is_file() else ""
        target_requirements_text = self._git_show(target_ref, "requirements.txt")
        target_manager_requirements = self._git_show(target_ref, "manager_requirements.txt")
        requirements_changed = (
            current_requirements != target_requirements_text
            or current_manager_requirements != target_manager_requirements
        )
        core_package_changes = self._core_package_changes(
            current_requirements,
            current_manager_requirements,
            target_ref,
        )
        required_package_names = {
            _normalise_name(item.name)
            for item in core_package_changes
            if item.required and item.target_requirement
        }
        required_changes = [
            f"{item.name}: {item.current_version or 'not installed'} → {item.target_requirement}"
            for item in core_package_changes
            if item.required and item.target_requirement
        ]
        core_file_changes = self._core_file_changes(current_commit, target_commit)
        update_available = target_commit != current_commit
        python_supported, requires_python = self._target_python_supported(target_ref)
        nodes = installed_nodes(self.root)
        dirty_nodes = [
            node for node in nodes
            if node.commit and _git_output(node.path, "status", "--porcelain")
        ]
        issues: list[UpdateIssue] = []

        dirty_core = _core_status(self.root)
        if dirty_core:
            issues.append(
                UpdateIssue(
                    "warning",
                    "The ComfyUI source checkout has local changes",
                    "A snapshot can preserve tracked patches and small untracked core files, but applying those patches to a newer core may conflict. A separate installation is safer.",
                )
            )
        if ahead:
            issues.append(
                UpdateIssue(
                    "warning",
                    "The current checkout contains commits not present in official ComfyUI",
                    f"The checkout is {ahead} commit(s) ahead or divergent. Updating in place switches to a manager-controlled branch at the latest official commit; the original commit remains recoverable from the snapshot branch.",
                )
            )
        if current_repository and current_repository.rstrip("/").removesuffix(".git").lower() != self.official_repository.rstrip("/").removesuffix(".git").lower():
            issues.append(
                UpdateIssue(
                    "warning",
                    "This installation uses a fork or different origin",
                    f"Current origin: {current_repository}. The update source is official ComfyUI. Consider installing a separate official instance if fork-specific changes matter.",
                )
            )
        if not python_supported:
            issues.append(
                UpdateIssue(
                    "blocking",
                    "The current Python version is outside the latest ComfyUI requirement",
                    f"Latest ComfyUI declares requires-python {requires_python}. A separate installation with a supported Python is strongly recommended.",
                )
            )

        target_requirements = _parse_requirements(
            target_requirements_text + "\n" + target_manager_requirements
        )
        conflicts = self._custom_node_conflicts(target_requirements) if update_available else []
        for conflict in conflicts[:12]:
            issues.append(UpdateIssue("blocking", "Custom-node dependency conflict", conflict))
        if len(conflicts) > 12:
            issues.append(
                UpdateIssue("blocking", "Additional dependency conflicts", f"{len(conflicts) - 12} more conflicts were found.")
            )

        installed_versions = self._installed_versions()
        accelerator_changes: list[str] = []
        for package in ACCELERATOR_PACKAGES if required_package_names else ():
            normalised = _normalise_name(package)
            current_version = installed_versions.get(normalised)
            requirement = target_requirements.get(normalised)
            if current_version and requirement:
                try:
                    compatible = Version(current_version.split("+", 1)[0]) in requirement.specifier
                except InvalidVersion:
                    compatible = True
                if not compatible:
                    accelerator_changes.append(f"{package} {current_version} does not satisfy {requirement}")
        if accelerator_changes:
            issues.append(
                UpdateIssue(
                    "blocking",
                    "The update would require changing the compiled accelerator stack",
                    "; ".join(accelerator_changes[:6])
                    + ". Updating a separate ComfyUI installation is safer because compiled custom packages may need rebuilding.",
                )
            )
        if dirty_nodes:
            issues.append(
                UpdateIssue(
                    "warning",
                    "Some custom nodes contain local changes",
                    f"{len(dirty_nodes)} node checkout(s) are dirty. They will not be updated or deleted, and their state is recorded in the snapshot.",
                )
            )
        if nodes and not conflicts:
            issues.append(
                UpdateIssue(
                    "info",
                    "Custom nodes will remain in place",
                    f"{len(nodes)} custom-node directories will not be modified. The manager will run a full startup validation after the core update.",
                )
            )

        baseline_pip_ok, baseline_pip_output = self._pip_check()
        if not baseline_pip_ok:
            issues.append(
                UpdateIssue(
                    "warning",
                    "The current Python environment already fails dependency validation",
                    (baseline_pip_output[-1600:] or "uv pip check failed without output")
                    + ". Safe update is disabled; use Try to patch current setup or create a new installation.",
                )
            )
        resolution_checked = bool(update_available and required_package_names)
        resolution_possible = True
        resolution_output = ""
        self._freeze_duplicate_packages.clear()
        if resolution_checked:
            resolution_possible, resolution_output = self._dry_run_resolution(
                target_ref,
                selected_core_packages=required_package_names,
            )
        if resolution_checked and not resolution_possible:
            issues.append(
                UpdateIssue(
                    "blocking",
                    "No compatible protected dependency plan could be resolved",
                    (resolution_output[-2000:] or "The resolver failed without output")
                    + ". Create a new installation unless you intentionally choose Continue anyway.",
                )
            )
        if not resolution_checked:
            issues.append(UpdateIssue(
                "info",
                "No dependency mutation is required",
                "The installed versions already satisfy the target ComfyUI manifests. "
                "The updater will not invoke the package resolver for this no-op or file-only update.",
            ))
        normalized_duplicates = sorted(self._freeze_duplicate_packages)
        if normalized_duplicates:
            issues.append(UpdateIssue(
                "info",
                "Duplicate installed metadata was normalized safely",
                "The working environment exposes more than one dist-info record for: "
                + ", ".join(normalized_duplicates)
                + ". Only Python's effective installed version is used as a resolver constraint.",
            ))
        protected_packages = sorted(self._protected_package_names(target_ref)) if resolution_checked else []
        if core_file_changes or core_package_changes:
            issues.append(UpdateIssue(
                "info",
                "ComfyUI core changes are reviewable and allowed",
                f"{len(core_file_changes)} core file change(s) and {len(core_package_changes)} direct core package manifest change(s) were found. "
                "Core changes are not blockers unless the protected environment, system ABI, profile packages, or custom nodes become incompatible.",
            ))

        return UpdatePreflight(
            installation=self.root,
            current_commit=current_commit,
            current_branch=current_branch,
            current_repository=current_repository,
            official_branch=official_branch,
            target_commit=target_commit,
            commits_behind=behind,
            commits_ahead=ahead,
            requirements_changed=requirements_changed,
            python_supported=python_supported,
            custom_node_count=len(nodes),
            dirty_custom_nodes=len(dirty_nodes),
            issues=issues,
            baseline_pip_ok=baseline_pip_ok,
            resolution_possible=resolution_possible,
            resolution_checked=resolution_checked,
            required_changes=required_changes,
            resolution_error=None if resolution_possible or not resolution_checked else resolution_output[-4000:],
            core_file_changes=core_file_changes,
            core_package_changes=core_package_changes,
            protected_packages=protected_packages,
            normalized_duplicate_packages=normalized_duplicates,
        )

    def _freeze_environment(self) -> list[str]:
        command = _uv_command(
            "pip", "freeze", "--python", str(self.python), "--no-config"
        )
        completed = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            env=clean_package_environment(),
            timeout=90,
        )
        if completed.returncode != 0:
            completed = subprocess.run(
                [str(self.python), "-m", "pip", "freeze", "--all"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=90,
            )
        return self._deduplicate_freeze_lines(
            line.strip() for line in completed.stdout.splitlines() if line.strip()
        )

    def _snapshot_local_wheels(self, snapshot_dir: Path, freeze_lines: list[str]) -> tuple[list[str], list[str]]:
        wheelhouse = snapshot_dir / "wheelhouse"
        rewritten: list[str] = []
        warnings: list[str] = []
        for line in freeze_lines:
            match = re.match(r"^([^\s]+)\s+@\s+file://(.+)$", line)
            if not match:
                rewritten.append(line)
                continue
            package, encoded = match.groups()
            parsed = urllib.parse.urlparse("file://" + encoded)
            local_path = Path(urllib.parse.unquote(parsed.path))
            if os.name == "nt" and re.match(r"^/[A-Za-z]:", str(local_path)):
                local_path = Path(str(local_path)[1:])
            if local_path.is_file() and local_path.suffix == ".whl":
                wheelhouse.mkdir(parents=True, exist_ok=True)
                target = wheelhouse / local_path.name
                shutil.copy2(local_path, target)
                rewritten.append(f"{package} @ __SNAPSHOT__/{target.relative_to(snapshot_dir).as_posix()}")
            else:
                rewritten.append(line)
                warnings.append(f"Local package source was not copied: {line}")
        return rewritten, warnings

    def _snapshot_untracked(self, snapshot_dir: Path) -> list[str]:
        raw = _git_output(self.root, "ls-files", "--others", "--exclude-standard", "-z")
        paths = [Path(value) for value in raw.split("\0") if value]
        selected: list[Path] = []
        total = 0
        warnings: list[str] = []
        for relative in paths:
            if _path_is_excluded(relative):
                continue
            source = self.root / relative
            if not source.is_file() or source.is_symlink():
                continue
            try:
                size = source.stat().st_size
            except OSError:
                continue
            if total + size > MAX_UNTRACKED_SNAPSHOT_BYTES:
                warnings.append(
                    f"Skipped untracked core file because the lightweight snapshot limit was reached: {relative}"
                )
                continue
            total += size
            selected.append(relative)
        if selected:
            archive = snapshot_dir / "untracked-core-files.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                for relative in selected:
                    bundle.write(self.root / relative, relative.as_posix())
        return warnings

    def create_snapshot(
        self,
        *,
        target_commit: str | None = None,
        baseline_pip_ok: bool | None = None,
    ) -> SnapshotRecord:
        self._validate_installation()
        commit = _git_output(self.root, "rev-parse", "HEAD", check=True)
        branch = _git_output(self.root, "branch", "--show-current") or "(detached)"
        snapshot_id = _safe_snapshot_id(commit)
        snapshot_dir = self.snapshots_dir / snapshot_id
        snapshot_dir.mkdir(parents=True, exist_ok=False)
        warnings: list[str] = []

        freeze_lines = self._freeze_environment()
        restore_lines, wheel_warnings = self._snapshot_local_wheels(snapshot_dir, freeze_lines)
        warnings.extend(wheel_warnings)
        (snapshot_dir / "environment-freeze.txt").write_text("\n".join(freeze_lines) + "\n", encoding="utf-8")
        (snapshot_dir / "restore-requirements.txt").write_text("\n".join(restore_lines) + "\n", encoding="utf-8")

        for arguments, filename in (
            (("diff", "--binary"), "working-tree.patch"),
            (("diff", "--cached", "--binary"), "staged.patch"),
        ):
            text = _git_output(self.root, *arguments)
            if text:
                (snapshot_dir / filename).write_text(text + "\n", encoding="utf-8")
        warnings.extend(self._snapshot_untracked(snapshot_dir))

        nodes_data: list[dict[str, Any]] = []
        node_patch_dir = snapshot_dir / "node-patches"
        for index, node in enumerate(installed_nodes(self.root), start=1):
            status = _git_output(node.path, "status", "--porcelain") if node.commit else ""
            item = {
                "name": node.name,
                "path": str(node.path),
                "repository": node.repository,
                "commit": node.commit,
                "branch": _git_output(node.path, "branch", "--show-current") or None,
                "dirty": bool(status),
            }
            nodes_data.append(item)
            if status:
                patch = _git_output(node.path, "diff", "--binary")
                if patch:
                    node_patch_dir.mkdir(parents=True, exist_ok=True)
                    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", node.name)
                    (node_patch_dir / f"{index:03d}-{safe_name}.patch").write_text(
                        patch + "\n", encoding="utf-8"
                    )
        (snapshot_dir / "custom-nodes.json").write_text(
            json.dumps(nodes_data, indent=2) + "\n", encoding="utf-8"
        )

        for filename in ("requirements.txt", "manager_requirements.txt", "pyproject.toml", "uv.lock", "extra_model_paths.yaml"):
            source = self.root / filename
            if source.is_file() and source.stat().st_size <= 2 * 1024 * 1024:
                metadata_dir = snapshot_dir / "metadata"
                metadata_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, metadata_dir / filename)
        report = self.state_dir / "install-report.json"
        if report.is_file():
            metadata_dir = snapshot_dir / "metadata"
            metadata_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(report, metadata_dir / report.name)

        safety_branch = f"comfy-setup-snapshot-{snapshot_id}"
        subprocess.run(
            ["git", "-C", str(self.root), "branch", "-f", safety_branch, commit],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        manifest = {
            "version": SNAPSHOT_VERSION,
            "snapshot_id": snapshot_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "installation": str(self.root),
            "commit": commit,
            "branch": branch,
            "repository": _git_output(self.root, "remote", "get-url", "origin") or None,
            "target_commit": target_commit,
            "safety_branch": safety_branch,
            "package_count": len(freeze_lines),
            "custom_node_count": len(nodes_data),
            "baseline_pip_ok": baseline_pip_ok,
            "warnings": warnings,
        }
        (snapshot_dir / "snapshot.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        return self._record_from_manifest(snapshot_dir, manifest)

    def _record_from_manifest(self, path: Path, manifest: dict[str, Any]) -> SnapshotRecord:
        return SnapshotRecord(
            snapshot_id=str(manifest.get("snapshot_id") or path.name),
            path=path,
            created_at=str(manifest.get("created_at") or ""),
            commit=str(manifest.get("commit") or ""),
            branch=str(manifest.get("branch") or "(detached)"),
            target_commit=str(manifest.get("target_commit")) if manifest.get("target_commit") else None,
            package_count=int(manifest.get("package_count") or 0),
            custom_node_count=int(manifest.get("custom_node_count") or 0),
            restorable=bool(manifest.get("commit") and (path / "restore-requirements.txt").is_file()),
            warnings=[str(item) for item in manifest.get("warnings", [])],
        )

    def list_snapshots(self) -> list[SnapshotRecord]:
        output: list[SnapshotRecord] = []
        if not self.snapshots_dir.is_dir():
            return output
        for directory in self.snapshots_dir.iterdir():
            manifest_path = directory / "snapshot.json"
            if not directory.is_dir() or not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(manifest, dict):
                output.append(self._record_from_manifest(directory, manifest))
        return sorted(output, key=lambda item: item.created_at, reverse=True)

    def delete_snapshot(self, snapshot_id: str) -> None:
        record = next((item for item in self.list_snapshots() if item.snapshot_id == snapshot_id), None)
        if record is None:
            raise UpdateError(f"Snapshot not found: {snapshot_id}")
        shutil.rmtree(record.path)

    def _install_reconciled_requirements(
        self,
        snapshot: SnapshotRecord,
        *,
        target_ref: str,
        strategy: str,
        selected_core_packages: set[str] | None = None,
        selectable_core_packages: set[str] | None = None,
    ) -> None:
        """Install one audited plan; never execute upstream requirement files directly."""
        freeze = (snapshot.path / "environment-freeze.txt").read_text(
            encoding="utf-8", errors="ignore"
        ).splitlines()
        target_names = {
            _normalise_name(requirement.name)
            for _owner, requirement in self._target_requirements(target_ref)
        }
        selected = (
            {_normalise_name(name) for name in selected_core_packages}
            if selected_core_packages is not None
            else set(target_names)
        )
        requirements, constraints = self._write_update_inputs(
            target_ref,
            # Node requirements files are install-time hints and are often stale.
            # Installed distribution metadata, static direct-core conflict checks,
            # pip check, and startup validation are the authoritative safeguards.
            include_custom_nodes=False,
            freeze_lines=freeze,
            prefix="update-apply",
            selected_core_packages=selected,
            selectable_core_packages=target_names,
            additional_protected_names=target_names - selected,
        )
        command = _uv_command(
            "pip",
            "install",
            "--index-strategy",
            "unsafe-best-match",
            "--python",
            str(self.python),
            "--no-config",
            "--default-index",
            PYPI_SIMPLE,
            "-r",
            str(requirements),
        )
        if constraints.stat().st_size and strategy != "force":
            command.extend(["-c", str(constraints)])
        self.runner.run(command, cwd=self.root, env=clean_package_environment())

    def _startup_validation(self, *, timeout: float = 90.0) -> tuple[bool, Path, list[str]]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.state_dir / "update-validation.log"
        warnings: list[str] = []
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
            probe_socket.bind(("127.0.0.1", 0))
            port = probe_socket.getsockname()[1]
        command = [
            str(self.python),
            str(self.root / "main.py"),
            "--listen",
            "127.0.0.1",
            "--port",
            str(port),
        ]
        self.runner.log(f"$ {Runner.render(command)}")
        process = subprocess.Popen(
            command,
            cwd=str(self.root),
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            bufsize=1,
        )
        lines: list[str] = []
        ready = threading.Event()

        def reader() -> None:
            assert process.stdout is not None
            for raw in process.stdout:
                line = raw.rstrip("\r\n")
                lines.append(line)
                self.runner.log(line)
                lowered = line.lower()
                if "starting server" in lowered or "to see the gui go to" in lowered:
                    ready.set()

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and process.poll() is None and not ready.is_set():
            time.sleep(0.15)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        thread.join(timeout=3)
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        failed_markers = (
            "import failed",
            "cannot import",
            "critical import error",
            "modulenotfounderror",
            "traceback (most recent call last)",
        )
        failures = [line for line in lines if any(marker in line.lower() for marker in failed_markers)]
        if failures:
            warnings.append(f"Startup validation found {len(failures)} import/error line(s).")
        if not ready.is_set():
            warnings.append("ComfyUI did not reach its server-ready marker during validation.")
        return ready.is_set() and not failures, log_path, warnings

    def update(
        self,
        preflight: UpdatePreflight,
        *,
        strategy: str = "safe",
        force: bool | None = None,
        selected_packages: set[str] | None = None,
    ) -> UpdateResult:
        """Apply a checked update and restore the snapshot on every failed mutation.

        ``force`` remains as a backwards-compatible alias for callers from
        releases before the explicit strategy selector was introduced.
        """
        self._validate_installation()
        if force:
            strategy = "force"
        if strategy not in UPDATE_STRATEGIES:
            raise UpdateError(f"Unknown update strategy: {strategy}")
        if strategy == "abort":
            return UpdateResult(
                success=True, snapshot=None, previous_commit=preflight.current_commit,
                current_commit=preflight.current_commit,
                warnings=["Update aborted. No files or packages were changed."],
                strategy=strategy, required_changes=preflight.required_changes,
            )
        if strategy == "new":
            return UpdateResult(
                success=True, snapshot=None, previous_commit=preflight.current_commit,
                current_commit=preflight.current_commit,
                warnings=["The existing installation was left unchanged. Create a separate installation from its profile or an exact export."],
                strategy=strategy, required_changes=preflight.required_changes,
                recommended_new_install=True,
            )
        if not preflight.update_available:
            return UpdateResult(
                success=True, snapshot=None, previous_commit=preflight.current_commit,
                current_commit=preflight.current_commit, warnings=["Already current."],
                strategy=strategy, required_changes=preflight.required_changes,
            )
        current_head = _git_output(self.root, "rev-parse", "HEAD", check=True)
        if current_head != preflight.current_commit:
            raise UpdateError("The installation changed after the compatibility check. Run Check again before updating.")
        if strategy == "safe" and (preflight.high_risk or not preflight.baseline_pip_ok):
            raise UpdateError(
                "Safe update is unavailable because compatibility risks were found. "
                "Choose Try to patch current setup, create a separate installation, Continue anyway, or abort."
            )
        if strategy == "patch" and not preflight.python_supported:
            raise UpdateError(
                "The current Python ABI is outside the target ComfyUI range. "
                "Create a separate installation or explicitly choose Continue anyway."
            )
        selectable_packages = {
            _normalise_name(item.name)
            for item in preflight.core_package_changes
            if item.target_requirement is not None
        }
        chosen_packages = (
            {_normalise_name(name) for name in selected_packages}
            if selected_packages is not None
            else {
                _normalise_name(item.name)
                for item in preflight.core_package_changes
                if item.target_requirement is not None and (item.selected or item.required)
            }
        )
        required_packages = {
            _normalise_name(item.name)
            for item in preflight.core_package_changes
            if item.required and item.target_requirement is not None
        }
        missing_required = sorted(required_packages - chosen_packages)
        if strategy == "safe" and missing_required:
            raise UpdateError(
                "Safe update requires every direct core package needed by the target source. "
                "Re-select: " + ", ".join(missing_required)
            )
        packages_to_install = required_packages & chosen_packages

        snapshot: SnapshotRecord | None = None
        mutated = False
        validation_log: Path | None = None
        update_warnings: list[str] = []
        try:
            snapshot = self.create_snapshot(
                target_commit=preflight.target_commit,
                baseline_pip_ok=preflight.baseline_pip_ok,
            )
            dirty = _core_status(self.root)
            if dirty:
                if strategy == "safe":
                    raise UpdateError("The checkout changed after preflight; update cancelled. Run Check again.")
                mutated = True
                self.runner.run(["git", "-C", str(self.root), "reset", "--hard", "HEAD"])
            mutated = True
            self.runner.run([
                "git", "-C", str(self.root), "checkout", "-B", "comfyui-managed",
                preflight.target_commit,
            ])
            if packages_to_install:
                self._install_reconciled_requirements(
                    snapshot,
                    target_ref=preflight.target_commit,
                    strategy=strategy,
                    selected_core_packages=packages_to_install,
                    selectable_core_packages=selectable_packages,
                )
            else:
                update_warnings.append(
                    "No Python package changes were required; the resolver was not invoked."
                )
            pip_ok, pip_output = self._pip_check()
            if not pip_ok:
                raise UpdateError(
                    "The candidate environment failed dependency validation:\n"
                    + (pip_output[-4000:] or "uv pip check failed without output")
                )
            valid, validation_log, validation_warnings = self._startup_validation()
            update_warnings.extend(validation_warnings)
            if not valid:
                raise UpdateError("The candidate ComfyUI did not pass startup and custom-node import validation.")
            self._append_history("update", snapshot, preflight.target_commit, True, update_warnings)
            return UpdateResult(
                success=True, snapshot=snapshot, previous_commit=preflight.current_commit,
                current_commit=_git_output(self.root, "rev-parse", "HEAD"),
                warnings=update_warnings, validation_log=validation_log,
                strategy=strategy, required_changes=preflight.required_changes,
                selected_packages=sorted(chosen_packages),
            )
        except Exception as exc:
            original_error = str(exc)
            if snapshot:
                self._append_history("update", snapshot, preflight.target_commit, False, [original_error])
            rolled_back = False
            rollback_error: str | None = None
            current_commit = _git_output(self.root, "rev-parse", "HEAD")
            if snapshot is not None and mutated:
                try:
                    rollback_result = self.rollback(snapshot.snapshot_id)
                    rolled_back = rollback_result.success
                    rollback_error = rollback_result.error
                    current_commit = rollback_result.current_commit
                    update_warnings.extend(rollback_result.warnings)
                    if rollback_result.validation_log is not None:
                        validation_log = rollback_result.validation_log
                except Exception as rollback_exc:
                    rollback_error = str(rollback_exc)
            if rolled_back:
                original_error += " The last working snapshot was restored automatically."
            elif mutated:
                original_error += " Automatic rollback did not complete; use Snapshots to restore the recorded state."
            return UpdateResult(
                success=False, snapshot=snapshot, previous_commit=preflight.current_commit,
                current_commit=current_commit, warnings=update_warnings, error=original_error,
                validation_log=validation_log, strategy=strategy, rolled_back=rolled_back,
                rollback_error=rollback_error, required_changes=preflight.required_changes,
                recommended_new_install=True, selected_packages=sorted(chosen_packages),
            )

    def _restore_requirements_path(self, snapshot: SnapshotRecord) -> Path:
        source = snapshot.path / "restore-requirements.txt"
        text = source.read_text(encoding="utf-8", errors="ignore")
        replacement = snapshot.path.as_uri()
        text = text.replace("__SNAPSHOT__", replacement)
        generated = snapshot.path / "restore-requirements-resolved.txt"
        generated.write_text(text, encoding="utf-8")
        return generated

    def _restore_git_state(self, snapshot: SnapshotRecord) -> None:
        manifest = json.loads((snapshot.path / "snapshot.json").read_text(encoding="utf-8"))
        commit = str(manifest["commit"])
        branch = str(manifest.get("branch") or "(detached)")
        self.runner.run(["git", "-C", str(self.root), "reset", "--hard", "HEAD"])
        if branch and branch != "(detached)":
            self.runner.run(["git", "-C", str(self.root), "checkout", "-B", branch, commit])
        else:
            self.runner.run(["git", "-C", str(self.root), "checkout", "--detach", commit])
        archive = snapshot.path / "untracked-core-files.zip"
        if archive.is_file():
            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.infolist():
                    destination = (self.root / member.filename).resolve()
                    if self.root not in destination.parents and destination != self.root:
                        raise UpdateError(f"Unsafe snapshot path: {member.filename}")
                bundle.extractall(self.root)
        for patch_name in ("working-tree.patch", "staged.patch"):
            patch = snapshot.path / patch_name
            if patch.is_file() and patch.stat().st_size:
                self.runner.run(
                    ["git", "-C", str(self.root), "apply", "--3way", str(patch)],
                    check=True,
                )
        extra_paths = snapshot.path / "metadata" / "extra_model_paths.yaml"
        if extra_paths.is_file():
            shutil.copy2(extra_paths, self.root / "extra_model_paths.yaml")

    def _restore_environment(self, snapshot: SnapshotRecord) -> None:
        requirements = self._restore_requirements_path(snapshot)
        command = _uv_command(
            "pip",
            "sync",
            "--python",
            str(self.python),
            "--no-config",
            "--default-index",
            PYPI_SIMPLE,
            str(requirements),
        )
        freeze = requirements.read_text(encoding="utf-8", errors="ignore").lower()
        torch_match = re.search(r"torch==[^\n]*\+cu(\d+)", freeze)
        if torch_match:
            command.extend(["--index", f"https://download.pytorch.org/whl/cu{torch_match.group(1)}"])
        if "tensorrt" in freeze:
            command.extend(["--index", "https://pypi.nvidia.com"])
        self.runner.run(command, cwd=self.root, env=clean_package_environment())

    def rollback(self, snapshot_id: str) -> RollbackResult:
        self._validate_installation()
        snapshot = next((item for item in self.list_snapshots() if item.snapshot_id == snapshot_id), None)
        if snapshot is None:
            raise UpdateError(f"Snapshot not found: {snapshot_id}")
        try:
            self._restore_git_state(snapshot)
            self._restore_environment(snapshot)
            manifest = json.loads((snapshot.path / "snapshot.json").read_text(encoding="utf-8"))
            baseline_pip_ok = manifest.get("baseline_pip_ok")
            pip_ok, pip_output = self._pip_check()
            warnings: list[str] = []
            if not pip_ok:
                if baseline_pip_ok is False:
                    warnings.append(
                        "The exact pre-update environment was restored. It still has the dependency "
                        "issues recorded before the patch attempt: "
                        + (pip_output[-1600:] or "uv pip check failed without output")
                    )
                else:
                    raise UpdateError(
                        "Rollback restored packages but dependency validation failed:\n"
                        + (pip_output[-4000:] or "uv pip check failed without output")
                    )
            valid, validation_log, startup_warnings = self._startup_validation()
            warnings.extend(startup_warnings)
            if not valid:
                result = RollbackResult(
                    False,
                    snapshot,
                    _git_output(self.root, "rev-parse", "HEAD"),
                    warnings,
                    "Rollback restored the recorded source and packages, but startup validation still found errors.",
                    validation_log,
                )
            else:
                result = RollbackResult(
                    True,
                    snapshot,
                    _git_output(self.root, "rev-parse", "HEAD"),
                    warnings,
                    validation_log=validation_log,
                )
            self._append_history("rollback", snapshot, snapshot.commit, result.success, warnings)
            return result
        except Exception as exc:
            self._append_history("rollback", snapshot, snapshot.commit, False, [str(exc)])
            return RollbackResult(
                False,
                snapshot,
                _git_output(self.root, "rev-parse", "HEAD"),
                error=str(exc),
            )

    def _append_history(
        self,
        action: str,
        snapshot: SnapshotRecord,
        target_commit: str | None,
        success: bool,
        warnings: Iterable[str],
    ) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.state_dir / "update-history.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        except (OSError, json.JSONDecodeError):
            data = []
        if not isinstance(data, list):
            data = []
        data.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": action,
                "snapshot_id": snapshot.snapshot_id,
                "target_commit": target_commit,
                "success": success,
                "warnings": list(warnings),
            }
        )
        path.write_text(json.dumps(data[-100:], indent=2) + "\n", encoding="utf-8")
