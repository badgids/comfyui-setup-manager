# Validation record — v0.8.7

This records the checks executed for the v0.8.7 quality-of-life release.

## Automated suite

The complete suite ran with Textual enabled:

```text
223 passed
4 package-source policy subtests passed
```

The suite includes normal, edge, integration, CLI, TUI, documentation, update,
rollback, profile, workflow, packaging, and subprocess coverage. The release is
also tested again from a freshly extracted copy of the final source archive.

New v0.8.7 coverage verifies:

- active-theme syntax roles for log levels, commands, package requirements,
  paths, URLs, inventory identifiers, sizes, sources, YAML, JSON, Markdown, and
  supported profile source files;
- ANSI/OSC-free selectable documents and plain-text clipboard behavior;
- batched large-log/inventory insertion without changing selection or line
  retention behavior;
- Profile Library access to the internal archive editor;
- successful profile-member edits, regenerated integrity metadata, preserved
  unknown safe members, and complete post-write bundle validation;
- invalid YAML and schema edits leaving the original archive byte-for-byte
  unchanged;
- CLI parity for profile member listing, reading, and transactional editing;
- automatic non-conflicting migration from `setups/` to `profiles/`, retained
  conflict files, and the compatibility function alias;
- required update-package selection and automatic rollback after a resolver
  failure;
- detached source-tree launches retaining relative dependency paths after the
  relay changes into the ComfyUI checkout;
- local documentation links, the manual archive-authoring guide, and the
  canonical `.comfyuisetup`, `.json`, and `.comfyworkflows` terminology.

Existing regression coverage still verifies exact-profile reconstruction,
environment locks, custom-node resolution, dependency reconciliation, ABI
tags, package-source policy, updater preflight and rollback, responsive failure
recovery, visible background scans, asset boundaries, workflow packs, themes,
Agent Skills, MCP definitions, and platform-specific launcher generation.

## Manual-format recipe validation

The examples from `docs/manual-profile-workflow-authoring.md` were exercised
through the production readers:

- a minimal ZIP-compatible profile containing only `profile.yaml` loaded and
  validated as `vanilla-comfyui`;
- a hand-built `.comfyworkflows` archive with `bundle.yaml`, `README.md`, a
  native JSON workflow, and exact SHA-256 values reported one valid workflow.

The built-in `examples/vanilla-comfyui.comfyuisetup` and
`examples/badgids-complete.comfyuisetup` archives were regenerated with the
current schema-4 writer and read back successfully.

## Static checks

Completed successfully:

- Python bytecode compilation for every `comfy_setup` module and
  `collect_comfyui_inventory.py`;
- `bash -n` for `install.sh`, `comfyui-setup-manager`, and
  `collect_comfyui_inventory.sh`;
- Markdown relative-link validation;
- version, package-data, capability-map, and source-policy tests;
- archive scans confirming no virtual environment, bytecode cache, pytest
  cache, generated egg-info, or former project `setups/` directory is shipped.

## Build and installed-wheel smoke

Clean version 0.8.7 wheel and source distributions were built. The wheel has 68
entries and includes the stylesheet, all editable YAML defaults, both built-in
profiles, all three Agent Skills, `selectable_widgets.py`, and the transactional
profile service.

The wheel was installed without dependencies into an isolated site directory
and exercised outside the source package. Successful checks included:

- `python -m comfy_setup --version` returning `0.8.7`;
- structured output containing all 40 capabilities;
- a mounted selectable log using semantic error highlighting while storing
  only sanitized plain text;
- a transactional `profile.yaml` edit through the installed-wheel service;
- complete archive readback after the edit.

## Scope note

Validation ran on Linux. Windows, WSL2, and macOS paths and launchers are
covered by unit/static tests, but native hosts were not available. No full
networked CUDA profile installation was performed; dependency resolution,
package protection, snapshot, startup validation, and rollback behavior use
controlled repositories and environment fixtures.
