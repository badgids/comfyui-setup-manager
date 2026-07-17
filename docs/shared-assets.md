# Shared models and workflows

[Documentation home](index.md) · [Main README](../README.md) · [Models](models.md) · [Workflows](workflows.md)

## Why shared libraries matter

A model file may be several gigabytes. A LoRA, VAE, text encoder, or ControlNet can also be large. If every ComfyUI installation keeps its own copy, storage use grows very quickly.

ComfyUI Setup Manager therefore recommends two external libraries:

```text
ComfyUI-Shared/
├── models/
│   ├── checkpoints/
│   ├── loras/
│   ├── vae/
│   ├── text_encoders/
│   ├── controlnet/
│   └── ...
└── workflows/
    ├── image/
    ├── video/
    └── audio/
```

Every managed ComfyUI installation can use these same directories. One downloaded checkpoint or workflow can then serve several installations.

Benefits:

- less wasted disk space;
- no repeated model and LoRA downloads;
- the same workflow collection is available in every managed installation;
- a new ComfyUI checkout can be tested without copying all assets;
- removing one ComfyUI installation does not remove the shared libraries;
- setup profiles stay portable because they describe requirements without embedding local paths.

## How models are shared

ComfyUI supports an `extra_model_paths.yaml` file in the ComfyUI installation directory. The manager writes or updates this file and preserves unrelated sections already added by the user.

The manager-owned section is named:

```yaml
comfyui_setup_manager_shared_models:
  base_path: /path/chosen/by/the/user/models
  is_default: true
  checkpoints: checkpoints
  loras: loras
  vae: vae
  text_encoders: text_encoders
  controlnet: controlnet
```

The complete file contains all model categories supported by the manager. The path is chosen on the current computer; it is never embedded in a portable setup profile.

## How workflows are shared

ComfyUI normally reads workflows from:

```text
<ComfyUI>/user/default/workflows/
```

The manager connects that native directory to the external workflow library:

- symbolic link on Linux, WSL2, and macOS;
- directory junction or supported link on Windows.

ComfyUI still sees its normal workflow directory. The files physically live in the shared library.

## First-time setup in the TUI

1. Open **Models** or **Workflows**.
2. Select **Shared paths**.
3. Choose a models directory and a workflows directory.
4. Choose whether existing local workflows should be migrated.
5. Save the paths.
6. Apply them to detected ComfyUI installations.

During a new installation, shared paths are enabled by default. The review screen shows both paths before any files are changed.

## CLI setup

Show the current shared paths:

```bash
./comfyui-setup-manager shared-paths status
```

Create or change them:

```bash
./comfyui-setup-manager shared-paths configure \
  --models-dir /data/ComfyUI/models \
  --workflows-dir /data/ComfyUI/workflows
```

Migrate existing local workflow files:

```bash
./comfyui-setup-manager shared-paths configure \
  --models-dir /data/ComfyUI/models \
  --workflows-dir /data/ComfyUI/workflows \
  --migrate-existing
```

Apply the saved paths to an installation:

```bash
./comfyui-setup-manager shared-paths apply /path/to/ComfyUI
```

Inspect one installation:

```bash
./comfyui-setup-manager --format yaml shared-paths inspect /path/to/ComfyUI
```

## Existing files and conflicts

The default conflict policy is **preserve**:

- an existing destination file is not overwritten;
- the original file remains available for manual review;
- the manager reports what was moved and what was skipped.

Other policies can be set in `asset-paths.yaml`, but preserving files is safest.

## Removing a ComfyUI installation

Removing a managed installation does not delete external models or workflows. The uninstall confirmation explains this distinction. Always review the selected path before confirming a full uninstall.

## Portable profiles

A `.comfyuisetup` profile may say that shared libraries are recommended or required. It may include model and workflow download manifests. It does not include the actual computer-specific library path.

When the profile is installed on another computer, the user chooses suitable local paths there.
