---
name: comfyui-setup-manager-developer
description: Develop and repair the ComfyUI Setup Manager codebase while preserving TUI/CLI parity, YAML configuration, cross-platform behavior, safety rules, tests, and documentation. Use for implementation work in this repository.
---

# ComfyUI Setup Manager developer

## Source of truth

Read these files before implementation:

1. `PRD.md`
2. `.claude/CLAUDE.md`
3. `docs/architecture.md`
4. `docs/development.md`
5. `docs/testing.md`
6. the module and tests closest to the requested behavior

## Engineering constraints

- Keep service logic outside Textual event handlers and CLI dispatch blocks.
- Preserve TUI/CLI capability parity.
- Use YAML for manager-owned mutable state and atomic writes for changes.
- Do not import or execute third-party custom nodes while inspecting them.
- Never hardcode usernames, home directories, drive letters, toolkit locations, or one operating system.
- Do not use destructive path operations without resolved-path containment checks and explicit confirmation.
- Keep the Textual event loop free at startup and during navigation. Filesystem scans, Git/GPU probing, network/package work, and other long operations must use workers and display progress before they begin.
- Convert unexpected installation-worker exceptions to a failed `InstallResult`, restore every recovery/navigation control, and release thread-waiting prompts when their screen closes.
- Cache completed scans in `runtime-state.yaml`; ordinary startup should read the cache rather than probing the machine.
- Each top-level tab must contain only controls and content for its named workspace.
- Raw subprocess output must remain literal and must not be interpreted as Rich markup. Syntax styles are render-only, derive from the active Textual theme, and must never enter the stored document, clipboard, cache, or log file.
- Every path/input, log, console, inventory, review, and diagnostic text surface must use the shared selectable widgets. Preserve mouse selection, Shift+Arrow, Ctrl/Cmd+A, and Ctrl/Cmd+C behavior.
- Treat the complete package set from the verified working environment as authoritative. Reconcile captured requirement manifests before uv execution; never pass contradictory exact manifest and constraint pins for the same distribution.
- Treat core update files and direct core packages as reviewable changes. Skip uv for already-current/file-only updates. Resolve only selected core packages that must change, protect every non-core and unselected core package, reduce duplicate normalized metadata to Python's effective distribution, require `pip check` and startup/import validation, and automatically roll back every failed mutated candidate.
- New profiles must use a PEP 440 version and persist/display Python, accelerator-runtime, and complete PyTorch ABI identity. Keep legacy profile reads compatible.
- Route `.comfyuisetup` member reads/edits through the profile service. Rebuild transactionally, regenerate integrity fields, validate before replacement, preserve unknown safe members, and expose TUI/CLI parity.
- Use `configuration.profiles_directory()` for project exports and keep `setups_directory()` only as a migration compatibility alias.
- Every Textual update worker must catch preflight/service exceptions, restore controls, and report whether automatic rollback succeeded. Keep the update action dock outside the scrolling review and above the footer; patch and force remain accessible for pending updates after dry-run failure.

## Implementation workflow

1. Reproduce or locate the defect with a focused test.
2. Change the smallest appropriate service module.
3. Wire the TUI and CLI to the shared service.
4. Add or update tests for service logic, static TUI structure, CLI behavior, documentation, and release metadata.
5. Run the full test suite.
6. Build the wheel and inspect the archive for required config, profiles, styles, and skills.
7. Search the release tree for stale version strings, personal paths, generated caches, and unrelated artifacts.

## Required validation

```bash
pytest -q
python -m build installer
PYTHONPATH=installer/src python -m comfy_setup --version
PYTHONPATH=installer/src python -m comfy_setup --format json capabilities
```

Use temporary directories and mocks in tests. Do not depend on a real ComfyUI installation or the user's home directory.

### Exact reconstruction invariants (v0.8.7)

- Treat the exported environment lock as the proven final package solution. Install it once; do not re-solve every custom node into the shared environment.
- Clone a validated node repository directly. Use Manager/Registry acquisition only when no repository exists, and pass `--no-deps`. Never use per-node `--uv-compile` in an exact reconstruction.
- Reconcile and retain node manifests for audit. Skip dependency-only `install.py` scripts under a complete exact lock, but preserve required lifecycle/bootstrap behavior such as `comfy-env`.
- Build SageAttention/FlashAttention from configured source repositories when wheels are unavailable. Match target Python, torch, CUDA, and compute capability, then verify wheel version and import before success.

- Treat the exact environment lock as authoritative for accelerator distribution identity as well as version. Do not infer a replacement CuPy, TensorRT, or ONNX Runtime package family from the receiving toolkit when the source lock records one. For native extensions, prepare generic build tools first, then install the target's exact PyTorch stack last before compiling.
