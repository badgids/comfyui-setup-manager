# PEP 440 versions and ABI compatibility tags

[Documentation home](index.md) · [Setup profiles](setup-profiles.md) · [Wheels and builds](wheels-and-builds.md)

## Why the tags exist

Python packages can have the same project name and public version while targeting different binary environments. A compiled wheel built for CPython 3.12 and CUDA 12.4 is not automatically safe in CPython 3.13 or CUDA 13.0. PyTorch extensions add another boundary because their binaries are compiled against a particular PyTorch and accelerator runtime.

Every newly exported `.comfyuisetup` profile therefore records two related identifiers:

- a normalized **PEP 440 profile version**, used for reliable ordering and comparison; and
- a manager **ABI compatibility tag**, used as a concise identity for the expected Python, accelerator, and PyTorch build.

PEP 440 defines Python distribution version syntax. Python packaging compatibility tags separately describe interpreter, ABI, and platform compatibility for wheels. The manager's combined profile tag is a readable extension for whole ComfyUI environments; it is metadata, not a replacement wheel filename tag. See [PEP 440](https://peps.python.org/pep-0440/) and the [Python Packaging Authority compatibility-tag specification](https://packaging.python.org/specifications/platform-compatibility-tags/).

## Tag format

An exact NVIDIA profile can display:

```text
cp312-cuda124-torch2_6_0_cu124
```

Read it left to right:

| Segment | Meaning |
|---|---|
| `cp312` | CPython 3.12 interpreter/extension ABI family |
| `cuda124` | CUDA runtime ABI 12.4 used by the PyTorch build |
| `torch2_6_0_cu124` | PyTorch `2.6.0+cu124`, normalized to tag-safe characters |

Other accelerator segments include `rocm70`, `mps`, and `cpu`. `accelany` means a compatibility profile can select the accelerator at install time; it does not claim that one compiled binary works on every accelerator.

The profile also keeps the complete values separately:

```yaml
version: 2026.7.17
compatibility:
  profile_version: 2026.7.17
  pep440: true
  exact: true
  python_version: 3.12.9
  python_abi: cp312
  accelerator: nvidia
  backend_version: '12.4'
  accelerator_abi: cuda124
  pytorch_version: 2.6.0+cu124
  pytorch_abi: torch2_6_0_cu124
  abi_tag: cp312-cuda124-torch2_6_0_cu124
```

## How to use the tag

1. Prefer an exact profile whose Python ABI, accelerator runtime, and PyTorch build match the intended machine.
2. Treat a different `cp` segment as a different Python ABI even when the source code is identical.
3. Treat a different CUDA/ROCm segment as requiring compatible PyTorch and compiled extension wheels or rebuilds.
4. Treat a different PyTorch segment as requiring revalidation or rebuilding of packages such as FlashAttention, SageAttention, xFormers, and other native extensions.
5. Use a profile marked **compatible target** when portability is intended; the installer resolves the target accelerator and validates the resulting ABI instead of pretending the export is byte-for-byte exact.

The TUI shows the profile's PEP 440 version, creation timestamp when available, readable compatibility label, and combined ABI tag in both Setup & Install and the Profile Library. The tag and compatibility mapping are also stored in `profile.yaml`, `metadata.yaml`, the archive README, and new installation reports.

## Update behavior

Core ComfyUI packages may move to newer compatible versions. During update, the manager protects non-core native packages, local/direct artifacts, the accelerator stack, and profile-owned packages. A core change proceeds only when the combined resolver plan, `pip check`, and ComfyUI/custom-node startup validation pass. If any candidate crosses an incompatible ABI boundary, use a separate ABI-tagged installation or let the automatic rollback restore the previous build.
