# Installation management

[Documentation home](index.md) · [CLI reference](cli-reference.md)

## Discovery

The manager recognizes a ComfyUI installation by its source files and project structure. It checks common user locations and any extra roots supplied by the user.

```bash
comfyui-setup-manager installations discover --scan-root /data
```

Discovery does not import or execute custom nodes.

## Offline overview

```bash
comfyui-setup-manager installations contents /path/to/ComfyUI
```

The overview reports Git source, branch, commit, environment readiness, custom-node repositories, and workflows. Custom nodes in `extra_model_paths.yaml` are included when that file provides additional node roots.

## Launching

Background launch:

```bash
comfyui-setup-manager installations launch /path/to/ComfyUI
```

Foreground launch:

```bash
comfyui-setup-manager installations launch /path/to/ComfyUI --foreground
```

Extra ComfyUI arguments are repeatable:

```bash
comfyui-setup-manager installations launch /path/to/ComfyUI \
  --arg=--listen \
  --arg=0.0.0.0
```

The manager records the background PID in `.comfy-setup/runtime-process.yaml`. A persistent relay writes unbuffered output to `.comfy-setup/runtime.log` and can rotate that file while ComfyUI remains running. Archived logs are stored under `.comfy-setup/logs/`.

## Stopping

```bash
comfyui-setup-manager installations stop /path/to/ComfyUI
```

If normal termination times out:

```bash
comfyui-setup-manager installations stop /path/to/ComfyUI --force
```

## Logs

Read the most recent 500 lines:

```bash
comfyui-setup-manager installations logs /path/to/ComfyUI --lines 500
```

Follow live output:

```bash
comfyui-setup-manager installations logs /path/to/ComfyUI --follow
```

List or search managed log files:

```bash
comfyui-setup-manager installations logs /path/to/ComfyUI --list-files
comfyui-setup-manager installations logs /path/to/ComfyUI --search "IMPORT FAILED"
```

Rotate to a fresh active log without deleting the previous output:

```bash
comfyui-setup-manager installations logs /path/to/ComfyUI --clear
```

Automatic archived-log cleanup defaults to Monthly/30 days. Choose a preset or a custom number of days:

```bash
comfyui-setup-manager installations logs /path/to/ComfyUI --retention daily
comfyui-setup-manager installations logs /path/to/ComfyUI --retention weekly
comfyui-setup-manager installations logs /path/to/ComfyUI --retention monthly
comfyui-setup-manager installations logs /path/to/ComfyUI --retention custom --days 45
comfyui-setup-manager installations logs /path/to/ComfyUI --cleanup
```

Delete only archived logs, with explicit confirmation:

```bash
comfyui-setup-manager installations logs /path/to/ComfyUI --delete runtime-20260716-120000-session.log --yes
```

## Metadata

```bash
comfyui-setup-manager installations edit /path/to/ComfyUI \
  --name "Production ComfyUI" \
  --description "Stable workflows for image generation"
```

## Uninstall

Uninstall is intentionally difficult to trigger by accident:

```bash
comfyui-setup-manager installations uninstall /path/to/ComfyUI --confirm UNINSTALL
```

The manager refuses filesystem roots, the home directory, shallow unsafe paths, and directories that are not valid ComfyUI installations. External model directories referenced from configuration are not deleted.

## Shared assets and uninstall

Managed installations may point to external model and workflow libraries. These libraries are shown in the installation overview but are not part of the installation directory.

A full instance uninstall removes the selected ComfyUI checkout only. It does not delete the shared model or workflow roots.
