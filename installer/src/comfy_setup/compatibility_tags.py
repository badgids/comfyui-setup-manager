from __future__ import annotations

import re
from typing import Any

from packaging.version import InvalidVersion, Version


def pep440_version(value: str | None) -> str | None:
    """Return a normalized PEP 440 version, or ``None`` for unknown input."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return str(Version(text))
    except InvalidVersion:
        return None


def python_abi_tag(version: str | None) -> str:
    normalized = pep440_version(version)
    if not normalized:
        return "pyunknown"
    release = Version(normalized).release
    if len(release) < 2:
        return "pyunknown"
    return f"cp{release[0]}{release[1]}"


def accelerator_abi_tag(accelerator: str | None, backend_version: str | None) -> str:
    kind = str(accelerator or "cpu").strip().lower()
    backend = re.sub(r"[^0-9]", "", str(backend_version or ""))
    if kind in {"any", "auto", "flexible"}:
        return "accelany"
    if kind in {"nvidia", "cuda"}:
        return f"cuda{backend}" if backend else "cudaunknown"
    if kind in {"amd", "rocm", "hip"}:
        return f"rocm{backend}" if backend else "rocmunknown"
    if kind in {"mps", "metal"}:
        return "mps"
    return "cpu"


def pytorch_abi_tag(version: str | None) -> str:
    normalized = pep440_version(version)
    if not normalized:
        return "torchunknown"
    safe = re.sub(r"[^a-z0-9]+", "_", normalized.lower()).strip("_")
    return f"torch{safe}"


def build_compatibility_tags(
    *,
    profile_version: str,
    python_version: str | None,
    torch_version: str | None,
    accelerator: str | None,
    backend_version: str | None,
    exact: bool,
) -> dict[str, Any]:
    normalized_profile = pep440_version(profile_version)
    if normalized_profile is None:
        raise ValueError(f"Profile version is not PEP 440 compliant: {profile_version!r}")
    normalized_python = pep440_version(python_version)
    normalized_torch = pep440_version(torch_version)
    python_abi = python_abi_tag(normalized_python)
    accelerator_abi = accelerator_abi_tag(accelerator, backend_version)
    pytorch_abi = pytorch_abi_tag(normalized_torch)
    abi_tag = f"{python_abi}-{accelerator_abi}-{pytorch_abi}"
    backend_label = {
        "cpu": "CPU",
        "mps": "MPS",
        "accelany": "accelerator auto",
    }.get(accelerator_abi, accelerator_abi.upper())
    label = (
        f"Python {normalized_python or 'unknown'} ({python_abi}) · "
        f"{backend_label} · PyTorch {normalized_torch or 'unknown'}"
    )
    return {
        "profile_version": normalized_profile,
        "pep440": True,
        "exact": bool(exact),
        "python_version": normalized_python,
        "python_abi": python_abi,
        "accelerator": str(accelerator or "cpu").lower(),
        "backend_version": str(backend_version or ""),
        "accelerator_abi": accelerator_abi,
        "pytorch_version": normalized_torch,
        "pytorch_abi": pytorch_abi,
        "abi_tag": abi_tag,
        "label": label,
    }


def compatibility_from_profile(profile: dict[str, Any]) -> dict[str, Any]:
    existing = profile.get("compatibility")
    if isinstance(existing, dict) and existing.get("abi_tag"):
        return dict(existing)
    torch = profile.get("torch", {}) if isinstance(profile.get("torch"), dict) else {}
    python = profile.get("python", {}) if isinstance(profile.get("python"), dict) else {}
    comfy = profile.get("comfyui", {}) if isinstance(profile.get("comfyui"), dict) else {}
    exact = bool(comfy.get("exact") or torch.get("exact_backend"))
    version = pep440_version(str(profile.get("version") or "")) or "0"
    return build_compatibility_tags(
        profile_version=version,
        python_version=str(python.get("preferred") or "") or None,
        torch_version=str(torch.get("version") or "") or None,
        accelerator=str(torch.get("source_accelerator") or ("cpu" if exact else "any")),
        backend_version=str(torch.get("source_backend_version") or ""),
        exact=exact,
    )


def compatibility_label(profile: dict[str, Any]) -> str:
    compatibility = compatibility_from_profile(profile)
    qualifier = "exact" if compatibility.get("exact") else "compatible target"
    return f"{compatibility.get('label', 'ABI unknown')} · {qualifier}"
