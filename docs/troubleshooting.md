# Troubleshooting

[Documentation home](index.md) · [Prerequisites](prerequisites.md) · [Getting started](getting-started.md)

## The manager will not start

Run:

```bash
./install.sh --version
```

Then check:

```bash
./comfyui-setup-manager system check
```

If the private manager environment is damaged, remove only `installer/.venv` and rerun `install.sh`.


## Windows says WinGet is required

The Windows bootstrap uses WinGet only to install missing Python or Git. Check:

```powershell
winget --version
py -3 --version
git --version
```

When `winget` is missing, install or repair Microsoft's App Installer, then close and reopen the terminal. Alternatively, manually install Python 3.10+ and Git; once both are available on `PATH`, `install.ps1` no longer needs WinGet for the manager bootstrap.

Windows Sandbox and some Store-disabled or older Server environments do not include a ready-to-use WinGet client. Follow Microsoft's standalone/repair instructions linked from [Prerequisites and platform preparation](prerequisites.md).

## Python exists but the private environment cannot be created

The selected Python must include `venv` and `pip` support. Verify:

```bash
python3 -m venv --help
python3 -m ensurepip --version
```

On Debian/Ubuntu and WSL distributions, install `python3-venv` and `python3-pip`. On Windows, repair or reinstall the full Python distribution rather than relying on the Microsoft Store execution alias.

## Automatic prerequisite installation cannot elevate

Linux/WSL package installation normally needs `sudo` or a root shell. If the manager reports no supported package manager or cannot elevate, install the displayed packages manually, then rerun the check. Corporate package repositories, stale distribution sources, proxies, and TLS inspection can also prevent package installation.

## A ComfyUI installation is not detected

Confirm the directory contains `main.py`, then scan its parent:

```bash
./comfyui-setup-manager installations discover --scan-root /path/to/parent
```

## A setup install fails

Repeat the command with `--traceback` for development diagnostics. Long-running installation progress is written to stderr. The final structured error is written to stdout or stderr using the selected format.

Check prerequisites:

```bash
./comfyui-setup-manager system check --source-builds
```

Check wheel sources:

```bash
./comfyui-setup-manager wheel-sources validate
```

## No compatible wheel exists

Add or edit a trusted source, or rerun with source builds enabled. Source builds need a compiler and the correct accelerator toolkit.

## A ComfyUI update breaks a node

Current updates validate custom-node imports before keeping the candidate and automatically restore the pre-update snapshot on failure. Read the update result first: **last working snapshot restored automatically** means no manual action is needed. If automatic rollback could not finish, use the recorded snapshot below.

List snapshots:

```bash
./comfyui-setup-manager snapshots list /path/to/ComfyUI
```

Restore the most recent working snapshot:

```bash
./comfyui-setup-manager snapshots rollback /path/to/ComfyUI SNAPSHOT_ID
```

Do not repair a failed candidate by running the new checkout's `requirements.txt` directly. Re-run preflight and choose **Try to patch current setup**, remove an incompatible package from the selected core changes, or create a separate ABI-tagged installation.

## An already-current update reports two versions of the same package

An error such as `tensorrt-cu13-libs==A` together with `tensorrt-cu13-libs==B` does not prove the working installation is broken. Some environments retain stale or alias `dist-info` records even though Python selects one effective distribution and `pip check` passes.

Version 0.8.6 does not run a dependency dry run when the installation already matches the target commit or when no installed core package needs to change. When resolution is required, duplicate normalized metadata is reduced to the effective version Python sees before constraints are written. Do not delete TensorRT metadata manually merely to satisfy the updater.

For a real pending update, **Try to patch current setup** and **Continue anyway** remain available after a failed protected dry run. Both snapshot first and automatically restore the working state if resolution or validation fails.

## Runtime output is needed for a bug report

```bash
./comfyui-setup-manager installations logs /path/to/ComfyUI --lines 1000
```

In the TUI, use **Launch & Manage → Browse logs** to search archived sessions, copy a selected log, or delete an old archive. **Clear output** rotates to a fresh active file without deleting the previous session.

Remove secrets and personal paths before sharing a log publicly.

## Models are not visible in one installation

Run:

```bash
comfyui-setup-manager shared-paths inspect /path/to/ComfyUI
```

Check that `model_configured` is true and that `extra_model_paths.yaml` contains the `comfyui_setup_manager_shared_models` section. Reapply the paths if needed:

```bash
comfyui-setup-manager shared-paths apply /path/to/ComfyUI
```

## Workflows are not shared

Inspect the installation and verify `user/default/workflows` points to the configured shared workflow root. If the directory contains local files, rerun configuration with migration enabled after reviewing conflicts.

## A model or workflow download failed

Open the Models or Workflows task screen, read the error, then retry. Check the matching source entry in YAML, its URL, optional checksum, and destination. Partial `.part` files are removed after a failed task.

## Selected custom nodes are missing after installation

The installer now installs `manager_requirements.txt`, re-applies profile constraints after node and acceleration packages, audits transitive dependencies, and runs ComfyUI's quick custom-node validation. An installation is not reported successful while selected nodes still show import failures.

For an older Badgids installation created before v0.8.5, re-run the profile so the NumPy compatibility pin and `click` repair are applied. Read [Badgids Complete profile](badgids-complete-profile.md) for its FFmpeg, NVIDIA/CUDA, and conditional source-build requirements. Review the installation console or search logs for `IMPORT FAILED`, `No module named`, or `Numba needs NumPy`.

## The TUI stops responding after an installation error

Upgrade to 0.8.5. Installer construction, callbacks, and other unexpected worker
exceptions are converted into the normal failed-install state, where recovery
and navigation controls are enabled. Pending password and wheel-source requests
are released when the screen closes. If a command is still running, its output
continues in the embedded console; the interface itself should remain usable.

## Return Home appears to pause after an installation

Version 0.8.5 performs the required inventory refresh in a Textual worker and
shows a progress modal. The dashboard is restored before scanning begins, and
the new target is selected after the refreshed state is applied. A visible
**REFRESHING INSTALLATION INVENTORY** dialog is expected; an unchanging terminal
with no dialog is not.

## uv reports two incompatible exact versions of the same package

A working source environment can contain a version that differs from a repository's current `requirements.txt`, especially when the repository or branch changed after the environment was created. Older builds passed both the raw manifest and exact lock to uv, producing errors such as `comfy-kitchen==0.2.21` together with `comfy-kitchen==0.2.20`.

Current 0.8.5 builds use the dependency manifest captured in the profile and reconcile it with the complete package set verified in the working source environment before invoking uv. The generated file under `<target>/.comfy-setup/` contains the verified version and a comment explaining the override. The installation console also reports the change. The original `.comfyuisetup` payload is not modified, and the final package/version audit remains exact.

When reporting another dependency issue, focus the installation console, press `Ctrl+A`/`Cmd+A`, then `Ctrl+C`/`Cmd+C` to copy the complete output. Partial selections can be made with the mouse or `Shift+Arrow`.

## Exact verification says ONNX Runtime or PyOpenGL Accelerate is missing

Upgrade the Setup Manager to 0.8.5 and retry the paused installation. Completed
repository, environment, node, and source-build steps are reused. Version 0.8.5
recognizes `onnxruntime` and `onnxruntime-gpu` as mutually exclusive providers
of the same runtime, verifies the exact provider selected by the profile, and
recovers a missing `PyOpenGL-accelerate` install descriptor from older exact
environment locks. Do not manually install both ONNX Runtime variants into the
same environment; their files overlap.

## SageAttention 2.2.0 says no matching distribution instead of compiling

This indicates that the installer tried to build the package name from the package index rather than the configured source repository. SageAttention 2.2.0 may not be present in the active index even though the official repository contains the source. Corrected 0.8.5 builds clone the configured SageAttention repository, prepare generic build tools before reapplying the exact target PyTorch stack, reproduce the target Python/PyTorch/CUDA build environment, apply the official parallel/NVCC settings, build without isolation, verify the generated wheel is the recorded version, install it with `--no-deps`, and import-test it. A package-index message that only lists SageAttention 0.x/1.x is not a compiler failure; it means the old installer never reached the repository build step.

## Node installation repeatedly upgrades and downgrades torch, NumPy, or Hugging Face packages

Do not use an older 0.8.5 build that invokes Comfy Manager with `--uv-compile` for each node. That mode performs a new global solve and can combine platform-specific requirements from unrelated nodes. Corrected 0.8.5 builds clone known repositories directly, use Manager-only acquisition with `--no-deps`, install the exact environment lock once, and audit rather than reinstall node requirements. Dependency-only node installers are skipped; required lifecycle scripts remain visible in the console.
