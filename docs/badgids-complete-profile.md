# Badgids Complete ComfyUI profile

[Documentation home](index.md) · [Setup profiles](setup-profiles.md) · [General prerequisites](prerequisites.md) · [Wheels and source builds](wheels-and-builds.md)

This page documents the bundled `badgids-comfyui-complete` profile. Everything here is specific to installing or using that profile and must not be treated as a universal ComfyUI Setup Manager requirement.

## Profile identity

| Field | Value |
|---|---|
| Profile ID | `badgids-comfyui-complete` |
| Display name | `Badgids Complete ComfyUI [cp313-cuda130-torch2_12_1_cu130]` |
| Profile version | `2026.7.16` |
| Profile type | Exact, reconstruction-first installation profile |
| ComfyUI source | `https://github.com/badgids/ComfyUI.git`, branch `master` |
| Captured ComfyUI version | `0.28.0` |
| Captured Python ABI | Python `3.13.14`, `cp313` |
| Captured accelerator ABI | NVIDIA CUDA `13.0`, `cuda130` |
| Captured PyTorch ABI | `2.12.1+cu130` |
| Complete ABI tag | `cp313-cuda130-torch2_12_1_cu130` |

The source inventory was captured from an NVIDIA GeForce RTX 4090, but an RTX 4090 is not itself a hard requirement. The receiving GPU and driver must support the profile's CUDA/PyTorch ABI and every selected GPU-only component.

## Intended platform and hardware

The complete profile should be treated as a **Windows or Linux/WSL2 NVIDIA profile**. Several included components are limited to Windows/Linux and NVIDIA, including TRELLIS2, RIFE TensorRT, GIMM-VFI, CuPy, TensorRT, FlashAttention, and SageAttention.

Required for the full selected profile:

- a supported 64-bit Windows or Linux/WSL2 environment capable of running the manager and ComfyUI;
- an NVIDIA GPU with a driver compatible with the captured CUDA 13.0/PyTorch `cu130` runtime;
- enough VRAM, RAM, and storage for the selected 3D, video, audio, and acceleration workloads;
- reliable internet access to retrieve the ComfyUI fork, custom-node repositories, Python packages, and any models installed separately;
- the [general manager prerequisites](prerequisites.md).

The profile is not a CPU, AMD ROCm, or Apple MPS equivalent of the captured setup. The installer may filter incompatible components, but doing so produces a reduced installation rather than the documented complete profile.

## Python, PyTorch, and CUDA behavior

The manager bootstrap still needs only Python 3.10 or newer. You do **not** need to replace the manager's Python with 3.13.14 manually. During profile installation, the manager uses its private `uv` toolchain to create the target ComfyUI environment with the profile's exact Python version when available.

The profile requests:

- Python `3.13.14` with no alternate profile fallback;
- PyTorch `2.12.1` from the official CUDA 13.0 index;
- torchvision `0.27.1`;
- exact package constraints captured from the working installation.

A compatible NVIDIA driver is required for prebuilt CUDA wheels. A complete CUDA 13.x development toolkit, including `nvcc`, is additionally required when FlashAttention or SageAttention must build from source. Keep the compiler, CUDA toolkit, Python ABI, and PyTorch ABI aligned; mixing CUDA 12 build tools with this CUDA 13 profile is not a supported exact reconstruction.

## Required profile-specific system tools

These are required in addition to the manager's own prerequisites:

| Tool | Commands checked | Why the profile needs it |
|---|---|---|
| FFmpeg | `ffmpeg`, `ffprobe` | Video Helper Suite, frame interpolation, and audio/video decoding and encoding |
| Git | `git` | Clones the Badgids ComfyUI fork and custom-node repositories; normally already satisfied by the manager bootstrap |

The installer can offer package-manager commands when automatic system dependency installation is enabled. On Windows it can use WinGet, Chocolatey, or Scoop for profile-level packages, depending on which supported package manager is detected. The initial manager bootstrap remains different: `install.ps1` specifically needs WinGet only when it must install missing Python or Git.

Verify the required profile tools:

```bash
git --version
ffmpeg -version
ffprobe -version
```

## Conditional source-build requirements

FlashAttention 2 and SageAttention 2.2 are selected and use a compatible wheel when available, otherwise a source build. Prepare these tools before allowing source builds:

| Tool | Windows | Linux/WSL2 |
|---|---|---|
| C/C++ compiler | Visual Studio 2022 Build Tools with the VCTools/Desktop development with C++ workload and recommended Windows SDK components | GCC/G++ or Clang and Python development headers |
| CUDA toolkit | CUDA 13.x matching the profile ABI, with `nvcc` on `PATH` or `CUDA_PATH`/`CUDA_HOME` set | CUDA 13.x matching the profile ABI, with `nvcc` available |
| CMake | `cmake` | `cmake` |
| Ninja | `ninja` | `ninja` or the distribution package commonly named `ninja-build` |
| Python build support | Included with the selected Python plus setuptools/wheel installed by the manager | Python headers such as `python3-dev`/`python3-devel` when required |

FlashAttention 3 is present in the profile as **experimental and disabled by default**. It requires compute capability 9.0 or newer and must not be enabled merely because the other acceleration packages are selected.

Check the build environment:

```bash
cmake --version
ninja --version
nvcc --version
```

On Windows, compiler detection also recognizes an installed Visual Studio environment through `vswhere` even when `cl.exe` is not initially on the ordinary terminal `PATH`.

## Optional component dependencies

These tools are checked only when the related feature or source-build path is selected:

| Tool | When it is useful |
|---|---|
| SoX (`sox`) | Additional Qwen3-TTS audio processing and format support |
| Tesseract OCR (`tesseract`) | OCR-capable WAS Node Suite workflows; language packs remain operating-system managed |
| `pkg-config` | Native source builds that must locate audio, image, OpenGL, or compiler libraries |
| Rust (`cargo`, `rustc`) | A Python dependency has no wheel and its source build requires Rust |
| CMake/Ninja/compiler | A selected native package has no compatible wheel |

Optional does not mean that every workflow using the related feature will work without the tool. It means the base profile can finish without treating that missing utility as a universal hard failure unless its selected installation path requires it.

## Included custom nodes

The profile selects twelve public custom-node repositories:

| Area | Included nodes |
|---|---|
| Video and frame interpolation | ComfyUI Frame Interpolation, RIFE TensorRT Auto, GIMM-VFI, Video Helper Suite, TeaCache HunyuanVideo |
| 3D and geometry | GeometryPack, TRELLIS2 |
| Audio and speech | ACE-Step, Qwen3-TTS |
| Model/runtime formats and utilities | ComfyUI-GGUF, KJNodes, WAS Node Suite (Revised) |

GeometryPack and TRELLIS2 run their required `install.py`/`comfy-env` lifecycle because they use isolated environment tooling such as Pixi. Other dependency-only installers are not allowed to replace the profile's exact locked environment.

## Included acceleration packages

Selected by default:

- CuPy `13.6.0` for CUDA array kernels;
- TensorRT `10.14.1.48.post1` for CUDA 13;
- ONNX Runtime `1.27.0` using the provider family appropriate to the profile;
- FlashAttention `2.8.3.post1`, wheel preferred with Git source fallback;
- SageAttention `2.2.0`, wheel preferred with Git source fallback;
- PyOpenGL Accelerate.

Present but disabled by default:

- FlashAttention 3 experimental package, requiring compute capability 9.0 or newer.

The exact environment lock and acceleration descriptors control both package version and distribution family. Do not manually substitute a different CuPy, TensorRT, ONNX Runtime, PyTorch, or CUDA family after installation and still expect exact-profile validation to pass.

## Models, workflows, and shared libraries

The profile contains **no model files and no workflow files**. It also does not embed machine-specific library paths.

During installation, choose or create external shared model and workflow directories. Models required by TRELLIS2, ACE-Step, Qwen3-TTS, frame interpolation, HunyuanVideo, and other nodes must be downloaded separately through their documented sources or the manager's asset catalogs.

This design keeps the profile portable and prevents multi-gigabyte assets from being duplicated in every ComfyUI checkout. See [Shared models and workflows](shared-assets.md).

## Install the profile

Start with the manager already installed and the required profile tools available.

TUI:

1. Open **Setup & Install**.
2. Select **Badgids Complete ComfyUI**.
3. Choose a new installation directory.
4. Confirm NVIDIA/CUDA detection and the exact ABI shown in the review.
5. Keep source builds disabled when compatible wheels are available; enable them only after preparing the complete compiler/CUDA toolchain.
6. Review automatic operating-system package installation before approving it.
7. Choose shared model and workflow library paths.
8. Run the installation and keep the final validation report.

CLI example:

```bash
./comfyui-setup-manager install run \
  --profile badgids-comfyui-complete \
  --target /path/to/Badgids-ComfyUI \
  --repository-mode profile
```

Use the plan/review command and structured output before unattended installation:

```bash
./comfyui-setup-manager --format yaml install plan \
  --profile badgids-comfyui-complete \
  --target /path/to/Badgids-ComfyUI
```

## Validation checklist

After installation, confirm:

```bash
/path/to/Badgids-ComfyUI/comfyui --help
ffmpeg -version
nvidia-smi
```

The manager's final validation should also report successful imports for `torch`, `numpy`, `transformers`, `comfy_kitchen`, `librosa`, and `numba`, then perform ComfyUI's quick custom-node load test.

A profile installation is not complete merely because repository cloning and Python package installation finished. Treat the manager's dependency audit, exact package verification, accelerator import tests, and custom-node startup validation as the authoritative result.

## Common profile-specific failures

### FFmpeg or FFprobe is missing

Install FFmpeg with your operating system's package manager, open a new terminal, verify both commands, and rerun the profile. Video Helper Suite and interpolation workflows require both executables.

### CUDA is detected but `nvcc` is missing

Prebuilt wheels may still run, but a FlashAttention or SageAttention source fallback cannot compile. Install the matching CUDA 13.x toolkit or disable source builds and use only compatible wheels.

### Visual Studio Build Tools is installed but no compiler is detected

Modify the Build Tools installation to include the Desktop development with C++/VCTools workload and recommended Windows SDK components. The manager can locate the environment through `vswhere`; restart the terminal after modifying Visual Studio.

### The GPU is not compatible with the exact profile

Use a different compatible profile or export a fresh exact profile from a working installation built for that GPU/runtime family. Do not edit the ABI tag or replace CUDA/PyTorch versions by hand to suppress compatibility checks.

### A node installs but its model is missing

This profile intentionally includes no models. Configure shared model paths and install the node's required model assets separately.
