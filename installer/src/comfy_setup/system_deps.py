from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from .models import PlatformInfo
from .platforms import compiler_available


@dataclass(slots=True)
class MissingDependency:
    id: str
    name: str
    why: str
    required: bool


def _command_available(command: str, info: PlatformInfo) -> bool:
    if command == "__compiler__":
        return compiler_available(info)
    return shutil.which(command) is not None


def missing_dependencies(
    profile: dict[str, Any],
    info: PlatformInfo,
    *,
    source_builds: bool,
    selected_nodes: set[str],
) -> list[MissingDependency]:
    missing: list[MissingDependency] = []
    build_only = {"cmake", "ninja", "compiler", "pkg-config", "rust"}
    for dependency in profile.get("system_dependencies", []):
        dep_id = dependency["id"]
        if dep_id in build_only and not source_builds:
            continue
        if dep_id == "sox" and "qwen3-tts" not in selected_nodes:
            continue
        if dep_id == "tesseract" and "was-node-suite" not in selected_nodes:
            continue
        commands = dependency.get("commands", [])
        if not all(_command_available(command, info) for command in commands):
            missing.append(
                MissingDependency(
                    id=dep_id,
                    name=dependency.get("name", dep_id),
                    why=dependency.get("why", ""),
                    required=bool(dependency.get("required", False)),
                )
            )
    return missing


def _ids(missing: list[MissingDependency]) -> set[str]:
    return {item.id for item in missing}


def _elevate() -> list[str]:
    if os.name == "nt":
        return []
    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None and geteuid() == 0:
        return []
    return ["sudo"] if shutil.which("sudo") else []


def install_commands(
    info: PlatformInfo,
    missing: list[MissingDependency],
) -> list[list[str]]:
    wanted = _ids(missing)
    manager = info.package_manager
    if not wanted or manager is None:
        return []

    if info.os_name == "linux":
        packages: dict[str, dict[str, list[str]]] = {
            "apt-get": {
                "git": ["git"],
                "ffmpeg": ["ffmpeg"],
                "sox": ["sox", "libsox-fmt-all"],
                "tesseract": ["tesseract-ocr"],
                "cmake": ["cmake"],
                "ninja": ["ninja-build"],
                "compiler": ["build-essential", "python3-dev"],
                "pkg-config": ["pkg-config"],
                "rust": ["rustc", "cargo"],
            },
            "dnf": {
                "git": ["git"],
                "ffmpeg": ["ffmpeg"],
                "sox": ["sox"],
                "tesseract": ["tesseract"],
                "cmake": ["cmake"],
                "ninja": ["ninja-build"],
                "compiler": ["gcc", "gcc-c++", "make", "python3-devel"],
                "pkg-config": ["pkgconf-pkg-config"],
                "rust": ["rust", "cargo"],
            },
            "pacman": {
                "git": ["git"],
                "ffmpeg": ["ffmpeg"],
                "sox": ["sox"],
                "tesseract": ["tesseract"],
                "cmake": ["cmake"],
                "ninja": ["ninja"],
                "compiler": ["base-devel", "python"],
                "pkg-config": ["pkgconf"],
                "rust": ["rust"],
            },
            "zypper": {
                "git": ["git"],
                "ffmpeg": ["ffmpeg"],
                "sox": ["sox"],
                "tesseract": ["tesseract-ocr"],
                "cmake": ["cmake"],
                "ninja": ["ninja"],
                "compiler": ["patterns-devel-base-devel_basis", "python3-devel"],
                "pkg-config": ["pkg-config"],
                "rust": ["rust", "cargo"],
            },
            "apk": {
                "git": ["git"],
                "ffmpeg": ["ffmpeg"],
                "sox": ["sox"],
                "tesseract": ["tesseract-ocr"],
                "cmake": ["cmake"],
                "ninja": ["ninja"],
                "compiler": ["build-base", "python3-dev"],
                "pkg-config": ["pkgconf"],
                "rust": ["rust", "cargo"],
            },
        }
        mapping = packages.get(manager, {})
        install_packages: list[str] = []
        for dep_id in wanted:
            install_packages.extend(mapping.get(dep_id, []))
        install_packages = list(dict.fromkeys(install_packages))
        if not install_packages:
            return []
        prefix = _elevate()
        if manager == "apt-get":
            return [
                [*prefix, "apt-get", "update"],
                [*prefix, "apt-get", "install", "-y", *install_packages],
            ]
        if manager == "dnf":
            return [[*prefix, "dnf", "install", "-y", *install_packages]]
        if manager == "pacman":
            return [[*prefix, "pacman", "-S", "--needed", "--noconfirm", *install_packages]]
        if manager == "zypper":
            return [[*prefix, "zypper", "--non-interactive", "install", *install_packages]]
        if manager == "apk":
            return [[*prefix, "apk", "add", *install_packages]]

    if info.os_name == "macos" and manager == "brew":
        mapping = {
            "git": "git",
            "ffmpeg": "ffmpeg",
            "sox": "sox",
            "tesseract": "tesseract",
            "cmake": "cmake",
            "ninja": "ninja",
            "compiler": None,
            "pkg-config": "pkg-config",
            "rust": "rust",
        }
        packages = [mapping[item] for item in wanted if mapping.get(item)]
        commands: list[list[str]] = []
        if "compiler" in wanted:
            commands.append(["xcode-select", "--install"])
        if packages:
            commands.append(["brew", "install", *list(dict.fromkeys(packages))])
        return commands

    if info.os_name == "windows":
        if manager == "winget":
            package_ids = {
                "git": "Git.Git",
                "ffmpeg": "Gyan.FFmpeg",
                "sox": "ChrisBagwell.SoX",
                "tesseract": "UB-Mannheim.TesseractOCR",
                "cmake": "Kitware.CMake",
                "ninja": "Ninja-build.Ninja",
                "compiler": "Microsoft.VisualStudio.2022.BuildTools",
                "pkg-config": "Bloodrock.PkgConfigLite",
                "rust": "Rustlang.Rustup",
            }
            commands = []
            for dep_id in wanted:
                package_id = package_ids.get(dep_id)
                if not package_id:
                    continue
                command = [
                    "winget", "install", "--id", package_id, "--exact",
                    "--accept-package-agreements", "--accept-source-agreements",
                ]
                if dep_id == "compiler":
                    command.extend(
                        [
                            "--override",
                            "--wait --passive --add Microsoft.VisualStudio.Workload.VCTools "
                            "--includeRecommended",
                        ]
                    )
                commands.append(command)
            return commands
        if manager == "choco":
            mapping = {
                "git": "git",
                "ffmpeg": "ffmpeg",
                "sox": "sox.portable",
                "tesseract": "tesseract",
                "cmake": "cmake",
                "ninja": "ninja",
                "compiler": "visualstudio2022buildtools",
                "pkg-config": "pkgconfiglite",
                "rust": "rustup.install",
            }
            packages = [mapping[item] for item in wanted if item in mapping]
            return [["choco", "install", "-y", *packages]] if packages else []
        if manager == "scoop":
            mapping = {
                "git": "git",
                "ffmpeg": "ffmpeg",
                "sox": "sox",
                "tesseract": "tesseract",
                "cmake": "cmake",
                "ninja": "ninja",
                "compiler": "llvm",
                "pkg-config": "pkg-config",
                "rust": "rustup",
            }
            packages = [mapping[item] for item in wanted if item in mapping]
            return [["scoop", "install", *packages]] if packages else []

    return []


def _render(command: list[str]) -> str:
    return subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)


def manual_instructions(info: PlatformInfo, missing: list[MissingDependency]) -> str:
    details = "; ".join(
        f"{item.name}: {item.why}" if item.why else item.name for item in missing
    )
    manager = info.package_manager or "no supported package manager detected"
    commands = install_commands(info, missing)
    if commands:
        rendered = "\n".join(f"  {_render(command)}" for command in commands)
        return (
            f"Missing dependencies ({manager}): {details}\n"
            f"Install them with:\n{rendered}\nThen rerun the installer."
        )
    return (
        f"Missing dependencies: {details}. Detected package manager: {manager}. "
        "Install the named tools with your operating system's preferred package manager, "
        "then rerun the installer."
    )
