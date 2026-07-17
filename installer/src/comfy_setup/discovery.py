from __future__ import annotations

import os
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .managed_installations import load_instance_metadata


@dataclass(frozen=True, slots=True)
class InstalledNode:
    name: str
    path: Path
    repository: str | None = None
    commit: str | None = None
    manager_id: str | None = None
    manager_version: str | None = None
    display_name: str | None = None
    resolution_kind: str | None = None
    resolution_trust: str | None = None


@dataclass(frozen=True, slots=True)
class InstalledWorkflow:
    name: str
    path: Path
    user_name: str


@dataclass(frozen=True, slots=True)
class ComfyInstallation:
    path: Path
    name: str
    description: str
    profile_id: str | None = None
    profile_name: str | None = None
    version: str | None = None
    commit: str | None = None
    branch: str | None = None
    repository: str | None = None
    has_venv: bool = False
    node_count: int = 0
    workflow_count: int = 0

    @property
    def label(self) -> str:
        details: list[str] = []
        if self.version:
            details.append(f"v{self.version}")
        if self.branch:
            details.append(self.branch)
        suffix = f" — {' • '.join(details)}" if details else ""
        return f"{self.name}{suffix}"


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    installations: list[ComfyInstallation]
    nodes: dict[str, list[InstalledNode]]
    workflows: dict[str, list[InstalledWorkflow]]


def is_comfyui_directory(path: Path) -> bool:
    return (
        path.is_dir()
        and (path / "main.py").is_file()
        and (path / "requirements.txt").is_file()
        and ((path / "comfy").is_dir() or (path / "nodes.py").is_file())
    )


def _git_value(path: Path, *arguments: str) -> str | None:
    import subprocess

    try:
        completed = subprocess.run(
            ["git", "-C", str(path), *arguments],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = completed.stdout.strip()
    return value or None


def _version(path: Path) -> str | None:
    candidates = [
        path / "comfyui_version.py",
        path / "comfy" / "comfyui_version.py",
    ]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        text = candidate.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            if "__version__" not in line or "=" not in line:
                continue
            return line.split("=", 1)[1].strip().strip("'\"")
    return None


def installed_nodes(path: Path) -> list[InstalledNode]:
    from .node_resolver import NodeSourceResolver

    root = path.expanduser().resolve()
    node_roots = [root / "custom_nodes"]
    config = root / "extra_model_paths.yaml"
    if config.is_file():
        try:
            import yaml
            data = yaml.safe_load(config.read_text(encoding="utf-8", errors="ignore")) or {}
            if isinstance(data, dict):
                for section in data.values():
                    if not isinstance(section, dict):
                        continue
                    base_value = section.get("base_path")
                    base = Path(str(base_value)).expanduser() if base_value else root
                    if not base.is_absolute():
                        base = root / base
                    values = section.get("custom_nodes")
                    if isinstance(values, str):
                        values = [values]
                    if isinstance(values, list):
                        for value in values:
                            candidate = Path(str(value)).expanduser()
                            if not candidate.is_absolute():
                                candidate = base / candidate
                            node_roots.append(candidate)
        except Exception:
            pass

    python_candidates = [
        root / ".venv" / "bin" / "python",
        root / ".venv" / "Scripts" / "python.exe",
        root / "venv" / "bin" / "python",
        root / "venv" / "Scripts" / "python.exe",
    ]
    resolver = NodeSourceResolver(
        root,
        python=next((candidate for candidate in python_candidates if candidate.is_file()), None),
    )
    output: dict[str, InstalledNode] = {}
    for node_root in node_roots:
        if not node_root.is_dir():
            continue
        for child in node_root.iterdir():
            is_plugin_file = child.is_file() and child.suffix.lower() == ".py"
            if (
                (not child.is_dir() and not is_plugin_file)
                or child.name.startswith(".")
                or child.name == "__pycache__"
            ):
                continue
            try:
                resolved = child.resolve()
            except OSError:
                resolved = child.absolute()
            is_git_checkout = child.is_dir() and (child / ".git").exists()
            resolution = resolver.resolve(child, allow_network=False)
            git_repository = (
                _git_value(child, "remote", "get-url", "origin")
                if is_git_checkout
                else None
            )
            git_commit = _git_value(child, "rev-parse", "HEAD") if is_git_checkout else None
            output[str(resolved)] = InstalledNode(
                name=child.stem if is_plugin_file else child.name,
                path=resolved,
                repository=git_repository or (resolution.repository if resolution else None),
                commit=git_commit or (resolution.ref if resolution else None),
                manager_id=resolution.manager_id if resolution else None,
                manager_version=resolution.manager_version if resolution else None,
                display_name=resolution.display_name if resolution else None,
                resolution_kind=resolution.source_kind if resolution else None,
                resolution_trust=resolution.trust if resolution else None,
            )
    return sorted(output.values(), key=lambda item: item.name.lower())


def installed_workflows(path: Path) -> list[InstalledWorkflow]:
    root = path.expanduser().resolve()
    output: list[InstalledWorkflow] = []
    user_root = root / "user"
    if user_root.is_dir():
        for user_dir in user_root.iterdir():
            workflow_root = user_dir / "workflows"
            if not workflow_root.is_dir():
                continue
            for workflow in workflow_root.rglob("*.json"):
                if workflow.is_file() and not workflow.is_symlink():
                    output.append(InstalledWorkflow(workflow.stem, workflow.resolve(), user_dir.name))
    legacy = root / "workflows"
    if legacy.is_dir():
        for workflow in legacy.rglob("*.json"):
            if workflow.is_file() and not workflow.is_symlink():
                output.append(InstalledWorkflow(workflow.stem, workflow.resolve(), "legacy"))
    return sorted(output, key=lambda item: str(item.path).lower())


def describe_installation(
    path: Path,
    *,
    nodes: list[InstalledNode] | None = None,
    workflows: list[InstalledWorkflow] | None = None,
) -> ComfyInstallation:
    path = path.expanduser().resolve()
    metadata = load_instance_metadata(path)
    nodes = installed_nodes(path) if nodes is None else nodes
    workflows = installed_workflows(path) if workflows is None else workflows
    return ComfyInstallation(
        path=path,
        name=metadata.name,
        description=metadata.description,
        profile_id=metadata.profile_id,
        profile_name=metadata.profile_name,
        version=_version(path),
        commit=_git_value(path, "rev-parse", "HEAD"),
        branch=_git_value(path, "branch", "--show-current"),
        repository=_git_value(path, "remote", "get-url", "origin"),
        has_venv=(path / ".venv").is_dir(),
        node_count=len(nodes),
        workflow_count=len(workflows),
    )


def _candidate_roots(extra_roots: Iterable[Path] = ()) -> list[Path]:
    roots: list[Path] = []

    for variable in ("COMFYUI_PATH", "COMFYUI_HOME"):
        value = os.environ.get(variable)
        if value:
            roots.append(Path(value).expanduser())

    current = Path.cwd().resolve()
    roots.extend([current, *current.parents])

    home = Path.home().resolve()
    roots.append(home)

    # Relative names are intentionally generic and resolved under the current
    # user's own home directory. No username, drive, or installation path is
    # embedded in the program.
    for relative in (
        "ComfyUI",
        "comfyui",
        "AI/ComfyUI",
        "AI/comfyui",
        "Applications/ComfyUI",
        "Documents/ComfyUI",
    ):
        roots.append(home / relative)

    if os.name == "nt":
        for letter in string.ascii_uppercase:
            drive = Path(f"{letter}:/")
            if drive.exists():
                roots.extend([drive / "ComfyUI", drive / "AI" / "ComfyUI"])

    roots.extend(Path(item).expanduser() for item in extra_roots)

    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            key = str(root.resolve())
        except OSError:
            key = str(root.absolute())
        if key not in seen:
            unique.append(root)
            seen.add(key)
    return unique


def _bounded_scan(root: Path, *, max_depth: int, max_directories: int) -> Iterable[Path]:
    if not root.is_dir():
        return

    root = root.resolve()
    queue: list[tuple[Path, int]] = [(root, 0)]
    visited = 0
    ignored = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "models",
        "output",
        "input",
        "temp",
        "cache",
        ".cache",
        "Library",
        "AppData",
    }

    while queue and visited < max_directories:
        directory, depth = queue.pop(0)
        visited += 1
        if is_comfyui_directory(directory):
            yield directory
            continue
        if depth >= max_depth:
            continue
        try:
            children = list(directory.iterdir())
        except (OSError, PermissionError):
            continue
        for child in children:
            if not child.is_dir() or child.name in ignored or child.name.startswith("."):
                continue
            queue.append((child, depth + 1))


def discover_installations_detailed(
    extra_roots: Iterable[Path] = (),
    *,
    max_depth: int = 4,
    max_directories: int = 2500,
) -> DiscoveryResult:
    found: dict[str, ComfyInstallation] = {}
    node_map: dict[str, list[InstalledNode]] = {}
    workflow_map: dict[str, list[InstalledWorkflow]] = {}
    roots = _candidate_roots(extra_roots)

    def record(path: Path) -> None:
        resolved = path.expanduser().resolve()
        key = str(resolved)
        if key in found:
            return
        nodes = installed_nodes(resolved)
        workflows = installed_workflows(resolved)
        found[key] = describe_installation(resolved, nodes=nodes, workflows=workflows)
        node_map[key] = nodes
        workflow_map[key] = workflows

    for root in roots:
        if is_comfyui_directory(root):
            record(root)

    # Only recursively scan the user's home and explicitly requested roots.
    recursive_roots = [Path.home(), *extra_roots]
    budget_each = max(100, max_directories // max(1, len(recursive_roots)))
    for root in recursive_roots:
        for path in _bounded_scan(
            Path(root).expanduser(),
            max_depth=max_depth,
            max_directories=budget_each,
        ):
            record(path)

    installations = sorted(found.values(), key=lambda item: str(item.path).lower())
    return DiscoveryResult(installations=installations, nodes=node_map, workflows=workflow_map)


def discover_installations(
    extra_roots: Iterable[Path] = (),
    *,
    max_depth: int = 4,
    max_directories: int = 2500,
) -> list[ComfyInstallation]:
    return discover_installations_detailed(
        extra_roots, max_depth=max_depth, max_directories=max_directories
    ).installations
