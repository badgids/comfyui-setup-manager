# Updates and rollback

[Documentation home](index.md) · [Installation management](installation-management.md)

## Transactional update policy

ComfyUI core is allowed to evolve. A changed core file or direct ComfyUI library is not a blocker by itself. The manager blocks or warns only when the candidate would invalidate the current Python ABI, compiled system or accelerator package, profile-owned non-core package, custom-node requirement, Git customization, or the installed environment.

The updater never executes the new checkout's `requirements.txt` or `manager_requirements.txt` as independent install commands. It compares them with the current core manifests and submits only reviewed core packages whose installed versions must change. Every installed non-core package and every unselected core package is constrained to its working version.

Custom-node requirement files are inspected for direct conflicts with changed core libraries, but they are not combined into a new global install recipe. Such files are frequently stale even when the node works. Installed distribution metadata, current `pip check`, and full ComfyUI/custom-node startup validation are the authoritative compatibility checks.

When the installation already matches the official target, or an update changes only core files while all installed libraries still satisfy the target manifests, dependency resolution is not needed and uv/pip is not invoked.

## Preflight and review

```bash
comfyui-setup-manager --format yaml updates check /path/to/ComfyUI
```

The result includes:

- every changed ComfyUI core file reported by Git;
- every changed direct core package requirement, its installed version, target requirement, and whether a change is required;
- protected packages that the resolver may not change;
- current `pip check` status and, only when a core library must change, a protected uv dry run;
- Python, PyTorch/CUDA, compiled-native, custom-node, profile-lock, local-change, fork, and divergence findings.

The TUI shows the file list and a package selection list before it enables an update. Required package changes are selected. A compatible manifest-only change is optional and does not cause a package install by itself. Packages no longer required by core are shown but left installed. The action dock is fixed above the footer so scrolling the review cannot cover its buttons.

If multiple installed metadata records normalize to the same package name, the manager selects the version Python actually sees and writes only that constraint. This prevents a working environment with stale or alias `dist-info` metadata from producing contradictory exact pins.

## Choices

The update screen always offers:

- **Safe update** — available only when preflight has no risk and every required core package is selected.
- **Try to patch current setup** — attempts the selected core changes while preserving every non-core and unselected core package. It remains available after a failed protected dry run because the attempt is snapshotted and automatically reversible.
- **Continue anyway** — ignores preflight blockers, but still snapshots first and requires final `pip check` plus startup/import validation.
- **Create new install** — reuses the installation's profile. For an unmanaged customized install, it first creates an exact ABI-tagged profile and selects that profile for a new destination.
- **Abort update** — changes nothing.

CLI equivalents:

```bash
comfyui-setup-manager updates run /path/to/ComfyUI --strategy safe
comfyui-setup-manager updates run /path/to/ComfyUI --strategy patch
comfyui-setup-manager updates run /path/to/ComfyUI --strategy force
comfyui-setup-manager updates run /path/to/ComfyUI --strategy new
comfyui-setup-manager updates run /path/to/ComfyUI --strategy abort
```

Use repeatable `--package NAME` to select reviewed package changes, or `--all-core-packages`. With no selection argument, required changes are selected. `--force` remains a deprecated alias for `--strategy force`.

## Commit, validation, and automatic rollback

Before the first mutation, the manager records source state, tracked and staged patches, small untracked core files, the exact package manifest, locally referenced wheels, custom-node Git metadata, and relevant configuration. Models, workflows, outputs, custom-node trees, caches, and the full `.venv` are not copied.

The selected official commit is checked out only after the snapshot succeeds. A package plan is applied only when a selected core package actually needs a different version. The candidate is kept only if:

1. uv completes the plan;
2. `uv pip check` passes;
3. ComfyUI reaches its server-ready marker; and
4. no custom-node or module import failure appears in startup output.

Any failure after mutation automatically restores the snapshot's source commit, patches, untracked core files, local wheel references, and exact package manifest, then validates the restored installation. The TUI explicitly reports whether automatic rollback passed. Manual rollback remains available:

```bash
comfyui-setup-manager snapshots list /path/to/ComfyUI
comfyui-setup-manager snapshots rollback /path/to/ComfyUI SNAPSHOT_ID
```

After success, the inventory refresh runs in a visible progress screen instead of blocking the TUI.
