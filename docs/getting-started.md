# Getting started

[Documentation home](index.md) · [README](../README.md) · [Troubleshooting](troubleshooting.md)

## What this program does

ComfyUI Setup Manager keeps different ComfyUI installations separate. Each installation has its own source checkout, `.venv`, custom nodes, workflows, models, and local launchers.

The manager itself also has a private environment under `installer/.venv`. This prevents the manager's packages from mixing with a ComfyUI installation.

## Prerequisites

The first-run scripts check for Python 3.10 or newer and Git. When possible, they install missing prerequisites with the system package manager.

Source builds may also need a compiler, CMake, Ninja, and the matching accelerator development toolkit.

Check without changing anything:

```bash
./comfyui-setup-manager system check
```

Include source-build tools:

```bash
./comfyui-setup-manager system check --source-builds
```

Install missing prerequisites:

```bash
./comfyui-setup-manager system install-prerequisites --source-builds --yes
```

Use `--dry-run` first to display the commands:

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
