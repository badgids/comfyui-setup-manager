from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .configuration import pytorch_release_config
from .models import PlatformInfo

PYTORCH_OFFICIAL_BASE = "https://download.pytorch.org/whl"


@dataclass(slots=True)
class TorchInstallPlan:
    packages: list[str]
    index_url: str | None
    backend: str
    backend_version: str | None
    description: str
    source_documentation: str


def _version_tuple(value: str | None) -> tuple[int, ...]:
    if not value:
        return (0,)
    result: list[int] = []
    for part in value.split("."):
        try:
            result.append(int(part))
        except ValueError:
            break
    return tuple(result or [0])


def _closest_supported(detected: str | None, supported: tuple[str, ...]) -> str:
    """Choose the newest official runtime supported by the detected driver.

    PyTorch CUDA wheels carry their own runtime. A newer driver can run an older
    supported runtime, but an older driver must not be handed a wheel requiring
    a newer runtime. When no runtime can be detected, prefer the newest official
    build listed for that PyTorch release and verify it after installation.
    """
    if not supported:
        return ""
    ordered = sorted(supported, key=_version_tuple)
    if not detected:
        return ordered[-1]
    detected_tuple = _version_tuple(detected)
    eligible = [value for value in ordered if _version_tuple(value) <= detected_tuple]
    return eligible[-1] if eligible else ""



def _catalog_release(torch_version: str) -> tuple[dict[str, Any], str]:
    catalog = pytorch_release_config()
    releases = catalog.get("releases", {})
    release = releases.get(torch_version, {}) if isinstance(releases, dict) else {}
    metadata = catalog.get("metadata", {}) if isinstance(catalog.get("metadata"), dict) else {}
    documentation = str(metadata.get("source") or "https://pytorch.org/get-started/previous-versions/")
    return release if isinstance(release, dict) else {}, documentation


def _profile_backend_index(indexes: dict[str, Any], accelerator: str, version: str | None) -> str:
    keys: list[str] = []
    if accelerator == "nvidia":
        if version:
            compact = version.replace(".", "")
            keys.extend([f"nvidia_cuda{compact}", f"nvidia_cuda{version}"])
        keys.extend(["nvidia", "cuda"])
    elif accelerator == "rocm":
        if version:
            compact = version.replace(".", "")
            keys.extend([f"rocm{compact}", f"rocm{version}"])
        keys.append("rocm")
    elif accelerator == "cpu":
        keys.append("cpu")
    elif accelerator == "mps":
        keys.append("mps")
    for key in keys:
        value = str(indexes.get(key) or "").strip()
        if value:
            return value
    return ""


def build_torch_install_plan(
    torch_config: dict[str, object],
    platform_info: PlatformInfo,
    accelerator: str,
) -> TorchInstallPlan:
    torch_version = str(torch_config.get("version") or "").strip()
    release, documentation = _catalog_release(torch_version)
    torchvision = str(torch_config.get("torchvision") or release.get("torchvision") or "").strip()
    torchaudio = str(torch_config.get("torchaudio") or release.get("torchaudio") or "").strip()
    packages = [f"torch=={torch_version}" if torch_version else "torch"]
    if torchvision:
        packages.append(f"torchvision=={torchvision}")
    if torchaudio:
        packages.append(f"torchaudio=={torchaudio}")

    indexes = dict(torch_config.get("indexes") or {})
    backends = release.get("backends", {}) if isinstance(release.get("backends"), dict) else {}

    exact_backend = bool(torch_config.get("exact_backend"))
    source_accelerator = str(torch_config.get("source_accelerator") or "").strip().lower()
    source_backend_version = str(torch_config.get("source_backend_version") or "").strip()
    exact_backend_index = str(torch_config.get("exact_backend_index") or "").strip()
    if exact_backend and source_accelerator and source_accelerator != accelerator:
        raise ValueError(
            f"This exact profile was exported from the {source_accelerator} PyTorch backend, not {accelerator}."
        )
    if exact_backend and accelerator == "nvidia":
        if platform_info.os_name not in {"linux", "windows"}:
            raise ValueError("Official PyTorch CUDA wheels are available for Linux and Windows, not this platform.")
        if not source_backend_version:
            raise ValueError("The exact profile does not record its source PyTorch CUDA runtime version.")
        index = exact_backend_index or _profile_backend_index(indexes, "nvidia", source_backend_version)
        if not index:
            index = f"{PYTORCH_OFFICIAL_BASE}/cu{source_backend_version.replace('.', '')}"
        return TorchInstallPlan(
            packages, index, "cuda", source_backend_version,
            f"exact exported PyTorch CUDA {source_backend_version} wheels",
            documentation,
        )
    if exact_backend and accelerator == "rocm":
        if platform_info.os_name != "linux":
            raise ValueError("Official PyTorch ROCm wheels are supported on Linux only.")
        if not source_backend_version:
            raise ValueError("The exact profile does not record its source PyTorch ROCm runtime version.")
        index = exact_backend_index or _profile_backend_index(indexes, "rocm", source_backend_version)
        if not index:
            index = f"{PYTORCH_OFFICIAL_BASE}/rocm{source_backend_version}"
        return TorchInstallPlan(
            packages, index, "rocm", source_backend_version,
            f"exact exported PyTorch ROCm {source_backend_version} wheels",
            documentation,
        )
    if exact_backend and accelerator == "mps":
        if platform_info.os_name != "macos":
            raise ValueError("PyTorch MPS builds are available only on macOS.")
        return TorchInstallPlan(
            packages, None, "mps", None,
            "exact exported macOS PyPI wheels with Apple MPS support",
            documentation,
        )
    if exact_backend and accelerator == "cpu":
        index = exact_backend_index or _profile_backend_index(indexes, "cpu", None) or f"{PYTORCH_OFFICIAL_BASE}/cpu"
        return TorchInstallPlan(
            packages, index, "cpu", None,
            "exact exported CPU-only PyTorch wheels",
            documentation,
        )

    if accelerator == "nvidia":
        if platform_info.os_name not in {"linux", "windows"}:
            raise ValueError("Official PyTorch CUDA wheels are available for Linux and Windows, not this platform.")
        supported = tuple(str(value) for value in backends.get("cuda", []) or [])
        chosen = _closest_supported(platform_info.cuda_version, supported)
        if supported and platform_info.cuda_version and not chosen:
            raise ValueError(
                f"The detected NVIDIA driver/runtime ({platform_info.cuda_version}) is older than "
                f"every official CUDA wheel available for PyTorch {torch_version}: {', '.join(supported)}."
            )
        index = f"{PYTORCH_OFFICIAL_BASE}/cu{chosen.replace('.', '')}" if chosen else ""
        if not index and not supported:
            index = _profile_backend_index(indexes, "nvidia", platform_info.cuda_version)
        if not index:
            raise ValueError(
                f"PyTorch {torch_version or 'requested version'} has no configured official CUDA wheel for this system."
            )
        return TorchInstallPlan(
            packages, index, "cuda", chosen or platform_info.cuda_version,
            f"official PyTorch CUDA {chosen or platform_info.cuda_version or 'GPU'} wheels",
            documentation,
        )

    if accelerator == "rocm":
        if platform_info.os_name != "linux":
            raise ValueError("Official PyTorch ROCm wheels are supported on Linux only.")
        supported = tuple(str(value) for value in backends.get("rocm", []) or [])
        chosen = _closest_supported(platform_info.rocm_version, supported)
        if supported and platform_info.rocm_version and not chosen:
            raise ValueError(
                f"The detected ROCm runtime ({platform_info.rocm_version}) is older than "
                f"every official ROCm wheel available for PyTorch {torch_version}: {', '.join(supported)}."
            )
        index = f"{PYTORCH_OFFICIAL_BASE}/rocm{chosen}" if chosen else ""
        if not index and not supported:
            index = _profile_backend_index(indexes, "rocm", platform_info.rocm_version)
        if not index:
            raise ValueError(
                f"PyTorch {torch_version or 'requested version'} has no configured official ROCm wheel for this system."
            )
        return TorchInstallPlan(
            packages, index, "rocm", chosen or platform_info.rocm_version,
            f"official PyTorch ROCm {chosen or platform_info.rocm_version or 'GPU'} wheels",
            documentation,
        )

    if accelerator == "mps":
        if platform_info.os_name != "macos":
            raise ValueError("PyTorch MPS builds are available only on macOS.")
        if release and not bool(backends.get("mps", False)):
            raise ValueError(f"PyTorch {torch_version} does not list an official macOS/MPS build.")
        return TorchInstallPlan(
            packages, None, "mps", None,
            "official macOS PyPI wheels with Apple MPS support",
            documentation,
        )

    if release and not bool(backends.get("cpu", False)):
        raise ValueError(f"PyTorch {torch_version} does not list an official CPU wheel.")
    index = _profile_backend_index(indexes, "cpu", None) or f"{PYTORCH_OFFICIAL_BASE}/cpu"
    return TorchInstallPlan(packages, index, "cpu", None, "official CPU-only PyTorch wheels", documentation)


def verification_code(accelerator: str) -> str:
    return f'''
import json, sys
import torch
payload = {{
  "version": torch.__version__,
  "cuda": torch.version.cuda,
  "hip": torch.version.hip,
  "cuda_available": torch.cuda.is_available(),
  "mps_built": bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_built()),
  "mps_available": bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()),
}}
print(json.dumps(payload))
expected = {accelerator!r}
if expected == "nvidia" and not payload["cuda"]:
    sys.exit(21)
if expected == "rocm" and not payload["hip"]:
    sys.exit(22)
if expected == "mps" and not payload["mps_built"]:
    sys.exit(23)
if expected == "cpu" and (payload["cuda"] or payload["hip"]):
    sys.exit(24)
'''
