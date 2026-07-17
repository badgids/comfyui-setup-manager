from __future__ import annotations

import copy
from pathlib import Path

import pytest

from comfy_setup.compatibility_tags import build_compatibility_tags, pep440_version
from comfy_setup.profile import ProfileError, ProfileRecord, get_profile, validate_profile


def test_builds_exact_python_cuda_pytorch_abi_tag() -> None:
    tags = build_compatibility_tags(
        profile_version="2026.07.17",
        python_version="3.12.9",
        torch_version="2.6.0+cu124",
        accelerator="nvidia",
        backend_version="12.4",
        exact=True,
    )
    assert tags["profile_version"] == "2026.7.17"
    assert tags["python_abi"] == "cp312"
    assert tags["accelerator_abi"] == "cuda124"
    assert tags["pytorch_abi"] == "torch2_6_0_cu124"
    assert tags["abi_tag"] == "cp312-cuda124-torch2_6_0_cu124"


def test_profile_record_always_displays_pep440_version_and_abi() -> None:
    profile = copy.deepcopy(get_profile("vanilla-comfyui"))
    record = ProfileRecord(profile, Path("vanilla.yaml"), "test")
    assert "v0.8.7" in record.label
    assert "cp312" in record.label
    assert "PyTorch" in record.compatibility_label


def test_profile_validation_rejects_non_pep440_version() -> None:
    profile = copy.deepcopy(get_profile("vanilla-comfyui"))
    profile["compatibility"] = build_compatibility_tags(
        profile_version="0.8.7",
        python_version="3.12",
        torch_version="2.8.0",
        accelerator="any",
        backend_version=None,
        exact=False,
    )
    profile["version"] = "rolling latest"
    with pytest.raises(ProfileError, match="PEP 440"):
        validate_profile(profile)


def test_legacy_non_pep440_version_is_upgraded_without_breaking_profile() -> None:
    profile = copy.deepcopy(get_profile("vanilla-comfyui"))
    profile.pop("compatibility", None)
    profile["version"] = "rolling"
    upgraded = validate_profile(profile)
    assert upgraded["version"] == "0+legacy"
    assert upgraded["legacy_version"] == "rolling"


def test_pep440_normalization_keeps_pytorch_local_build_tag() -> None:
    assert pep440_version("2.6.0+CU124") == "2.6.0+cu124"
