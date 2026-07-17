---
name: comfyui-setup-manager-operator
description: Operate ComfyUI Setup Manager safely to discover, install, launch, update, roll back, and manage ComfyUI assets. Use when an agent is asked to manage a ComfyUI installation with this project.
---

# ComfyUI Setup Manager operator

## Use this skill when

Use this skill for tasks involving the `comfyui-setup-manager` executable, its YAML configuration, managed ComfyUI instances, shared models, LoRAs, workflows, nodes, snapshots, or update preflights.

## Operating rules

1. Read `PRD.md`, `README.md`, and the relevant file under `docs/` before changing manager-owned configuration.
2. Prefer the CLI with `--format json` for automation. Do not scrape the TUI.
3. Run a read-only inspection or plan before a changing command.
4. Never guess an installation path, asset relative path, snapshot ID, profile ID, or catalog entry ID. List it first.
5. Require the manager's explicit confirmation flag for destructive operations.
6. Do not delete shared model, LoRA, or workflow libraries when uninstalling an instance.
7. Run update preflight before an update. Normal core file/library changes are review items. An already-current or file-only result must not invoke the resolver. Treat Python/ABI, protected-package, accelerator, profile-lock, custom-node, Git-customization, resolver, or `pip check` findings as risks.
8. Prefer `--strategy safe`; use `patch` for a snapshotted protected attempt. Patch and force remain valid choices for a real pending update after dry-run failure. Recommend `new` when compatibility cannot be proven. Use `force` only after explicit risk acceptance. Never run the checkout's requirements files by hand.
9. Trust the manager's automatic rollback result after a failed mutated candidate. Use manual snapshot rollback only when automatic restoration reports failure.
10. After changing files outside the manager, run a rescan. After editing manager YAML only, reload settings.
11. During exact profile export, never choose between duplicate custom-node paths on the user's behalf. Show every complete path from the manager's conflict output, ask which copy to omit, and rerun with repeatable `--omit-node /complete/path` arguments.
12. For TUI debugging, copy directly from the focused input or output pane: use mouse/Shift+Arrow selection, Ctrl/Cmd+A for the focused field, and Ctrl/Cmd+C to copy. Theme-derived syntax colors are display-only; copied text is sanitized plain text. Do not require screenshots or log-file browsing when the visible text is sufficient.
13. If installation reports a dependency-manifest override, report both the repository request and the verified source-environment version. Do not hand-edit the profile lock unless the final source environment itself was inconsistent.
14. Report and compare the profile's PEP 440 version and complete Python/accelerator/PyTorch ABI tag before installing or cloning an exact profile.
15. Do not treat duplicate normalized `dist-info` metadata as two packages. The updater must select Python's effective installed version and write one constraint.
16. Use `profiles/` as the project export directory. The manager migrates non-conflicting files from the former `setups/` name.
17. Inspect or edit archive members with `profiles files`, `profiles read-file`, and `profiles edit-file`. Never modify a checksummed `.comfyuisetup` payload with a generic ZIP tool and leave stale integrity values.

## Preferred command flow

```bash
./comfyui-setup-manager --format json capabilities
./comfyui-setup-manager --format json installations discover
./comfyui-setup-manager --format json installations show /absolute/path/to/ComfyUI
```

For installation:

```bash
./comfyui-setup-manager --format json install plan --profile PROFILE_ID --target /absolute/target
./comfyui-setup-manager --format json install run --profile PROFILE_ID --target /absolute/target
```

For updates:

```bash
./comfyui-setup-manager --format json updates check /absolute/path/to/ComfyUI
./comfyui-setup-manager --format json updates run /absolute/path/to/ComfyUI --strategy safe
./comfyui-setup-manager --format json updates run /absolute/path/to/ComfyUI --strategy patch
```

For shared assets:

```bash
./comfyui-setup-manager --format json models list
./comfyui-setup-manager --format json loras list
./comfyui-setup-manager --format json workflows list
```

For an exact portable profile:

```bash
./comfyui-setup-manager --format json profiles from-installation /absolute/path/to/ComfyUI --name "Profile Name"
```

For a transactional profile-member edit:

```bash
./comfyui-setup-manager profiles files ./profiles/profile.comfyuisetup
./comfyui-setup-manager profiles edit-file ./profiles/profile.comfyuisetup profile.yaml --from-file ./profile.yaml
```

Structured/non-interactive export intentionally does not prompt. If duplicate node identities are reported, present the full paths to the user and repeat the command with one or more explicit omissions:

```bash
./comfyui-setup-manager --format json profiles from-installation /absolute/path/to/ComfyUI \
  --name "Profile Name" \
  --omit-node /absolute/path/to/the/copy/the/user-chose-to-omit
```

## Completion checks

Report the exact command, exit status, changed paths, warnings, and the next safe recovery command when applicable.

### Exact reconstruction invariants (v0.8.7)

- Treat the exported environment lock as the proven final package solution. Install it once; do not re-solve every custom node into the shared environment.
- Clone a validated node repository directly. Use Manager/Registry acquisition only when no repository exists, and pass `--no-deps`. Never use per-node `--uv-compile` in an exact reconstruction.
- Reconcile and retain node manifests for audit. Skip dependency-only `install.py` scripts under a complete exact lock, but preserve required lifecycle/bootstrap behavior such as `comfy-env`.
- Build SageAttention/FlashAttention from configured source repositories when wheels are unavailable. Match target Python, torch, CUDA, and compute capability, then verify wheel version and import before success.

- Treat the exact environment lock as authoritative for accelerator distribution identity as well as version. Do not infer a replacement CuPy, TensorRT, or ONNX Runtime package family from the receiving toolkit when the source lock records one. For native extensions, prepare generic build tools first, then install the target's exact PyTorch stack last before compiling.
