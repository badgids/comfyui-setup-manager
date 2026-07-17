from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

from .models import PlatformInfo


def _capture(args: list[str]) -> str:
    try:
        completed = subprocess.run(
            args,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=12,
        )
        return completed.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _package_manager(os_name: str) -> str | None:
    candidates: dict[str, list[str]] = {
        "windows": ["winget", "choco", "scoop"],
        "macos": ["brew", "port"],
        "linux": ["apt-get", "dnf", "pacman", "zypper", "apk"],
    }
    for candidate in candidates.get(os_name, []):
        if shutil.which(candidate):
            return candidate
    return None


def _nvidia() -> tuple[str | None, float | None, str | None]:
    if not shutil.which("nvidia-smi"):
        return None, None, None

    output = _capture(
        [
            "nvidia-smi",
            "--query-gpu=name,compute_cap,driver_version",
            "--format=csv,noheader",
        ]
    )
    if not output:
        return None, None, None

    first = output.splitlines()[0]
    parts = [part.strip() for part in first.split(",")]
    name = parts[0] if parts else None
    capability = None
    if len(parts) > 1:
        try:
            capability = float(parts[1])
        except ValueError:
            pass

    cuda_version = None
    verbose = _capture(["nvidia-smi"])
    match = re.search(r"CUDA Version:\s*([0-9.]+)", verbose)
    if match:
        cuda_version = match.group(1)

    nvcc = _capture(["nvcc", "--version"]) if shutil.which("nvcc") else ""
    match = re.search(r"release\s+([0-9.]+)", nvcc)
    if match:
        cuda_version = match.group(1)

    return name, capability, cuda_version


def _rocm() -> tuple[str | None, str | None]:
    if not (shutil.which("rocminfo") or shutil.which("rocm-smi")):
        return None, None

    output = _capture(["rocm-smi", "--showproductname"]) if shutil.which("rocm-smi") else ""
    name = None
    for line in output.splitlines():
        if "Card series" in line or "Card model" in line:
            name = line.split(":", 1)[-1].strip()
            break

    version = None
    if shutil.which("hipconfig"):
        version_output = _capture(["hipconfig", "--version"])
        version = version_output.splitlines()[0].strip() if version_output else None
    return name or "AMD GPU", version


def detect_platform(force_accelerator: str | None = None) -> PlatformInfo:
    system = platform.system()
    if system == "Windows":
        os_name = "windows"
    elif system == "Darwin":
        os_name = "macos"
    else:
        os_name = "linux"

    is_wsl = bool(
        os.environ.get("WSL_INTEROP")
        or os.environ.get("WSL_DISTRO_NAME")
        or "microsoft" in platform.release().lower()
    )

    gpu_name, capability, cuda_version = _nvidia()
    rocm_name, rocm_version = _rocm()

    if force_accelerator and force_accelerator != "auto":
        accelerator = force_accelerator
    elif gpu_name:
        accelerator = "nvidia"
    elif rocm_name:
        accelerator = "rocm"
        gpu_name = rocm_name
    elif os_name == "macos" and platform.machine().lower() in {"arm64", "aarch64"}:
        accelerator = "mps"
        gpu_name = "Apple Silicon"
    else:
        accelerator = "cpu"

    notes: list[str] = []
    if is_wsl:
        notes.append("WSL2 detected; Linux package and CUDA rules will be used.")
    if accelerator == "cpu":
        notes.append("No supported GPU runtime was detected; GPU-only components will be disabled.")
    if os_name == "windows" and accelerator == "rocm":
        notes.append("Native Windows ROCm is not supported by this installer; CPU fallback will be used.")
        accelerator = "cpu"

    return PlatformInfo(
        os_name=os_name,
        system=system,
        release=platform.release(),
        architecture=platform.machine(),
        is_wsl=is_wsl,
        package_manager=_package_manager(os_name),
        accelerator=accelerator,
        gpu_name=gpu_name,
        compute_capability=capability,
        cuda_version=cuda_version,
        rocm_version=rocm_version,
        notes=notes,
    )


def compiler_available(info: PlatformInfo) -> bool:
    if info.os_name == "windows":
        return bool(
            shutil.which("cl")
            or shutil.which("clang-cl")
            or shutil.which("g++")
            or windows_msvc_environment()
        )
    return bool(shutil.which("c++") or shutil.which("g++") or shutil.which("clang++"))


def cuda_home() -> Path | None:
    for key in ("CUDA_HOME", "CUDA_PATH"):
        value = os.environ.get(key)
        if value:
            candidate = Path(value).expanduser()
            if candidate.exists():
                return candidate

    nvcc = shutil.which("nvcc")
    if nvcc:
        return Path(nvcc).resolve().parent.parent

    return None


def windows_msvc_environment() -> dict[str, str]:
    """Return an MSVC developer environment without assuming a user path."""
    if platform.system() != "Windows":
        return {}
    if shutil.which("cl"):
        return {}

    vswhere = shutil.which("vswhere")
    if not vswhere:
        program_files_x86 = os.environ.get("ProgramFiles(x86)")
        if program_files_x86:
            candidate = (
                Path(program_files_x86)
                / "Microsoft Visual Studio"
                / "Installer"
                / "vswhere.exe"
            )
            if candidate.exists():
                vswhere = str(candidate)
    if not vswhere:
        return {}

    installation = _capture(
        [
            vswhere,
            "-latest",
            "-products",
            "*",
            "-requires",
            "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
            "-property",
            "installationPath",
        ]
    ).strip()
    if not installation:
        return {}

    vcvars = Path(installation) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    if not vcvars.exists():
        return {}

    completed = subprocess.run(
        ["cmd.exe", "/d", "/s", "/c", f'""{vcvars}" >nul && set"'],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode != 0:
        return {}

    environment: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        environment[key] = value
    return environment
