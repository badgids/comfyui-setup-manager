---
name: comfyui-setup-manager-release
description: Audit and package a ComfyUI Setup Manager release, synchronizing version metadata, tests, documentation, launchers, wheel contents, and validation records. Use when preparing a distributable update.
---

# ComfyUI Setup Manager release auditor

## Release checklist

1. Read `PRD.md`, `RELEASE_NOTES.md`, `VALIDATION.md`, `.claude/commands/release.md`, and `docs/development.md`.
2. Set the requested version consistently in:
   - `installer/src/comfy_setup/__init__.py`
   - `installer/pyproject.toml`
   - root launchers
   - README and installer README
   - PRD target and acceptance heading
   - release notes and validation record
   - version assertions in tests
3. Update documentation for every user-visible behavior change.
4. Run all tests, including Textual selection/copy/plain-clipboard and active-theme syntax tests, transactional profile-member editing, `setups/` to `profiles/` migration, fixed update-action accessibility, no-op resolver skipping, duplicate metadata normalization, update package selection/automatic rollback, ABI-tag/PEP-440, and dependency manifest-versus-lock reconciliation tests.
5. Build a fresh wheel. Remove stale wheels from `installer/dist/` before copying the final artifact there.
6. Inspect wheel contents and verify bundled YAML, profiles, styles, and Agent Skills.
7. Verify the launchers accept the new installed version and export the project/config paths.
8. Search for stale release versions and machine-specific paths.
9. Create the final source archive without `.venv`, `__pycache__`, `.pytest_cache`, temporary files, or prior archives.
10. Verify the update engine never invokes upstream requirement files directly, skips uv when no package mutation is required, protects non-core/unselected packages, normalizes duplicate installed metadata, and stores matching profile/archive/install-report version and ABI tags.
11. Verify the source archive contains `profiles/` (not a project `setups/` directory), the profile file editor has CLI parity, copied syntax-colored output is plain text, and the manual archive-authoring guide is linked.

## Validation commands

```bash
pytest -q
python -m build installer
python -m zipfile -l installer/dist/comfyui_setup_manager-*.whl
rg 'OLD_VERSION|/home/[^/]+|C:\\Users\\' .
```

## Release report

Record exact test counts, skipped tests and reasons, wheel filename, source archive filename, and any validation that could not be performed on the current operating system.

### Exact reconstruction invariants (v0.8.7)

- Treat the exported environment lock as the proven final package solution. Install it once; do not re-solve every custom node into the shared environment.
- Clone a validated node repository directly. Use Manager/Registry acquisition only when no repository exists, and pass `--no-deps`. Never use per-node `--uv-compile` in an exact reconstruction.
- Reconcile and retain node manifests for audit. Skip dependency-only `install.py` scripts under a complete exact lock, but preserve required lifecycle/bootstrap behavior such as `comfy-env`.
- Build SageAttention/FlashAttention from configured source repositories when wheels are unavailable. Match target Python, torch, CUDA, and compute capability, then verify wheel version and import before success.

- Treat the exact environment lock as authoritative for accelerator distribution identity as well as version. Do not infer a replacement CuPy, TensorRT, or ONNX Runtime package family from the receiving toolkit when the source lock records one. For native extensions, prepare generic build tools first, then install the target's exact PyTorch stack last before compiling.
