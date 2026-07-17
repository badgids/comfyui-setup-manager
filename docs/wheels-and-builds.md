# Wheels, accelerated PyTorch, and source builds

[Documentation home](index.md) · [Configuration](configuration.md)

## PyTorch

Compatibility profiles use official accelerated PyTorch distributions selected from `pytorch-releases.yaml`. Exact exports instead retain the source installation's accelerator family, PyTorch package versions, CUDA/ROCm runtime, and official backend index. A fresh exact install uses that recorded backend rather than selecting a newer wheel from the receiving machine's toolkit. The manager verifies the installed CUDA, ROCm, MPS, or CPU backend before continuing.

## Wheel-source registry

The editable source list is `wheel-sources.yaml`.

```bash
comfyui-setup-manager wheel-sources list
comfyui-setup-manager wheel-sources edit
```

Each entry has a trust label, source type, package, location, platform restrictions, accelerator restrictions, architecture restrictions, priority, and notes.

Trust labels are `Official`, `3rd Party`, and `Custom/Local`.

## Compatibility matching

Candidate wheels are matched against package version, Python ABI, operating system, architecture, PyTorch version, CUDA version, accelerator, C++ ABI markers, and wheel platform tags.

## Add a source

```bash
comfyui-setup-manager wheel-sources add \
  --package flash-attn \
  --label "3rd Party" \
  --kind github-releases \
  --location https://github.com/example/wheels \
  --priority 80
```


## Select an existing local wheel

When no compatible wheel is found, choose **Custom/Local**, type or browse to an existing `.whl` file, and select **Add source & retry**. Local files do not require an `https://` URL. The manager validates that the file exists and ends in `.whl`, records it as a `direct-wheel` source, checks its package/version/Python/platform tags, and retries installation.

A local directory containing multiple wheels can also be selected. Invalid local paths report a filesystem-specific error instead of the remote-source HTTPS message.

## Local wheel backups

```bash
comfyui-setup-manager wheel-sources register-local flash-attn /path/to/wheelhouse
```

When source compilation creates a wheel and the user chooses backup, the directory is automatically registered as `Custom/Local`.

## Exact environment lock and constraint preservation

An exact `.comfyuisetup` contains `environment-lock.yaml`, which records every installed distribution, including transitive and manually installed packages. A local wheel used by the working environment is recorded by exact package/version and original provenance, but new profiles do not archive it. Installation resolves that package through configured/public wheel sources, package indexes, a public Git source, or a source-build lifecycle. Immutable Git package sources retain their commits, while editable distributions supplied by reconstructed ComfyUI or custom-node source trees retain a safe target-relative path and editable state. Legacy profiles containing embedded wheels remain checksum-verified and installable.

Dynamic package-index installs such as CuPy, ONNX Runtime, and TensorRT receive the selected profile's complete constraints file. For exact profiles, both the package family and version are taken from the working environment lock—`cupy-cuda12x` is not replaced by `cupy-cuda13x` merely because the receiving driver or toolkit advertises CUDA 13. After all nodes and accelerated packages are installed, the engine re-applies the lock, audits dependency metadata, and verifies both the exact expected package versions and the absence of unexpected runtime distributions. The complete audit is repeated after ComfyUI's custom-node startup validation in case a declared missing-module repair changed the environment. This prevents a node or accelerated package from silently upgrading NumPy, omitting a manually installed package, or adding an unrecorded dependency while still appearing successful.

## Source builds

Source builds occur in isolated temporary environments. The build environment receives the same Python and official PyTorch backend as the target. The live ComfyUI environment is not used as the compiler workspace. The target interpreter is probed with valid generated Python code and the build environment receives the exact installed torch/torchvision/torchaudio versions. If an exact acceleration package has a public source repository, that repository is authoritative when no wheel exists; the installer never asks the package index to provide a missing source release instead. It checks out the captured Git commit when available and verifies the built wheel version before installation.

For SageAttention 2.2.0, the fallback clones the official SageAttention repository, builds with `--no-build-isolation`, passes `EXT_PARALLEL`, `NVCC_APPEND_FLAGS`, `MAX_JOBS`, `CUDA_HOME`, and `TORCH_CUDA_ARCH_LIST`, and verifies both the wheel metadata and a real `import sageattention`.
