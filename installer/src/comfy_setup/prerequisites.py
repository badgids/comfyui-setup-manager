from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .models import PlatformInfo
from .platforms import detect_platform


@dataclass(slots=True)
class PrerequisiteStatus:
    name: str
    required: bool
    available: bool
    executable: str | None
    purpose: str
    install_hint: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _hint(name: str, info: PlatformInfo) -> str:
    if info.os_name in {"linux", "wsl"}:
        package = {
            "git": "git",
            "python": "python3 python3-venv python3-pip",
            "cmake": "cmake",
            "ninja": "ninja-build",
            "compiler": "build-essential",
        }[name]
        manager = info.package_manager or "apt-get"
        if manager == "apt-get":
            return f"sudo apt-get update && sudo apt-get install -y {package}"
        if manager == "dnf":
            return f"sudo dnf install -y {package}"
        if manager == "pacman":
            return f"sudo pacman -S --needed {package}"
    if info.os_name == "macos":
        package = {"git": "git", "python": "python", "cmake": "cmake", "ninja": "ninja", "compiler": "llvm"}[name]
        return f"brew install {package}"
    if info.os_name == "windows":
        package = {
            "git": "Git.Git",
            "python": "Python.Python.3.12",
            "cmake": "Kitware.CMake",
            "ninja": "Ninja-build.Ninja",
            "compiler": "Microsoft.VisualStudio.2022.BuildTools",
        }[name]
        return f"winget install --id {package} --exact"
    return f"Install {name} using the operating system package manager."


def check_prerequisites(*, source_builds: bool = False, info: PlatformInfo | None = None) -> list[PrerequisiteStatus]:
    platform_info = info or detect_platform()
    commands = [
        ("git", True, "Clone and update ComfyUI and public custom nodes", "git"),
        ("python", True, "Run the manager and create ComfyUI virtual environments", "python3" if os.name != "nt" else "python"),
        ("cmake", source_builds, "Build native Python extensions when no wheel exists", "cmake"),
        ("ninja", source_builds, "Parallel native extension builds", "ninja"),
        ("compiler", source_builds, "Compile C, C++, CUDA, or ROCm extensions", "cl" if os.name == "nt" else "c++"),
    ]
    result: list[PrerequisiteStatus] = []
    for name, required, purpose, command in commands:
        executable = shutil.which(command)
        result.append(
            PrerequisiteStatus(
                name=name,
                required=required,
                available=bool(executable) or not required,
                executable=executable,
                purpose=purpose,
                install_hint=_hint(name, platform_info),
            )
        )
    return result


def install_prerequisites(
    *,
    source_builds: bool = False,
    yes: bool = False,
    dry_run: bool = False,
    info: PlatformInfo | None = None,
) -> dict[str, Any]:
    platform_info = info or detect_platform()
    missing = [item for item in check_prerequisites(source_builds=source_builds, info=platform_info) if item.required and not item.available]
    if not missing:
        return {"changed": False, "commands": [], "missing": []}
    hints = list(dict.fromkeys(item.install_hint for item in missing))
    if not yes and not dry_run:
        raise RuntimeError("Missing prerequisites. Re-run with --yes to install them or use --dry-run to view commands.")
    if dry_run:
        return {"changed": False, "commands": hints, "missing": [item.to_dict() for item in missing]}
    completed: list[str] = []
    for hint in hints:
        if os.name == "nt":
            command = ["powershell.exe", "-NoProfile", "-Command", hint]
        else:
            command = ["sh", "-lc", hint]
        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"Prerequisite command failed with exit code {result.returncode}: {hint}")
        completed.append(hint)
    remaining = [item.to_dict() for item in check_prerequisites(source_builds=source_builds, info=platform_info) if item.required and not item.available]
    return {"changed": bool(completed), "commands": completed, "missing": remaining}
