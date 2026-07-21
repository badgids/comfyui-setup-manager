# Getting started

[Documentation home](index.md) · [Prerequisites](prerequisites.md) · [README](../README.md) · [Troubleshooting](troubleshooting.md)

## What this program does

ComfyUI Setup Manager keeps different ComfyUI installations separate. Each installation has its own source checkout, `.venv`, custom nodes, workflows, models, and local launchers.

The manager itself also has a private environment under `installer/.venv`. This prevents the manager's packages from mixing with a ComfyUI installation.

## Prerequisites

Read [Prerequisites and platform preparation](prerequisites.md) before the first run.

The manager needs Python 3.10+ with `venv`/`pip`, Git, internet access for public downloads, and writable install/library directories. On Windows, `install.ps1` uses WinGet only when it must install missing Python or Git. Run `winget --version` first, or manually install Python and Git before starting.

Source builds add compiler, CMake, Ninja, Python-header, and accelerator-toolkit requirements. Individual setup profiles may add tools such as FFmpeg; those requirements belong to the selected profile. See [Badgids Complete profile](badgids-complete-profile.md) for the bundled custom profile.

After the manager has been installed, check without changing anything:

```bash
./comfyui-setup-manager system check
```

Include source-build tools:

```bash
./comfyui-setup-manager system check --source-builds
```

Use a dry run before approving package-manager changes:

```bash
./comfyui-setup-manager --format yaml system install-prerequisites --source-builds --dry-run
```

## First installation

### Linux, WSL2, macOS

```bash
chmod +x install.sh comfyui-setup-manager
./install.sh
```

### Windows PowerShell

Confirm WinGet when Python or Git may be missing:

```powershell
winget --version
```

Then run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```

### Windows Command Prompt

```bat
install.cmd
```

## First ComfyUI setup

In the TUI, open **Setup & Install**, select a profile, choose **New installation**, review the repository and dependency choices, and start installation.

The CLI equivalent is:

```bash
./comfyui-setup-manager install run \
  --profile vanilla-comfyui \
  --target /path/to/ComfyUI \
  --repository-mode official
```

## After installation

Launch from the manager:

```bash
./comfyui-setup-manager installations launch /path/to/ComfyUI
```

Or run the local launcher created inside that installation:

```bash
/path/to/ComfyUI/comfyui
```

Windows installations also receive `comfyui.ps1` and `comfyui.cmd`.

## Choose shared asset paths before creating several installations

On first use, open **Models** or **Workflows**, choose **Set shared paths**, and select one external models directory and one external workflows directory.

This prevents every ComfyUI checkout from downloading a separate copy of the same checkpoint, LoRA, VAE, text encoder, or workflow. The manager creates the folders and configures each managed installation for you.

Full guide: [Shared models and workflows](shared-assets.md).
