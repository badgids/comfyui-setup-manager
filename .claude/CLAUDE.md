# Claude Code project instructions

Project: ComfyUI Setup Manager  
Creator: Alan Guice (Badgids)  
License: Apache 2.0

## Read first

1. `PRD.md`
2. `docs/architecture.md`
3. `docs/development.md`
4. `docs/testing.md`
5. `SECURITY.md`

## Non-negotiable rules

- Every TUI feature must have a complete CLI equivalent.
- Every textual TUI input/output surface must use the shared selectable widgets and retain mouse selection, Shift+Arrow, Ctrl/Cmd+A, and Ctrl/Cmd+C. Syntax roles derive from the active theme, but the underlying document/clipboard/cache remains sanitized plain text without ANSI/OSC/Rich styling codes.
- Shared behavior belongs in service modules, not duplicated in screen and CLI handlers.
- Editable manager configuration is YAML.
- Native ComfyUI workflows remain JSON.
- Never hardcode usernames, drive letters, home paths, install locations, or toolkit paths.
- Exact profiles list public plugins only by Registry/Manager identity and/or validated repository. A sanitized payload is permitted only for a plugin that remains unresolved after all configured and official sources are exhausted.
- Do not commit models, virtual environments, build caches, outputs, or secrets.
- Destructive operations require explicit confirmation and safe-path validation.
- Package and repository trust policy must not be bypassed.
- Captured requirements are evidence, but the verified working environment lock is authoritative for exact reconstruction. Reconcile contradictory manifest specifiers before invoking uv and make every override visible.
- Core update source/package changes are reviewable, not blockers by themselves. Never invoke upstream requirement files independently. Skip resolution for already-current/file-only updates; resolve only reviewed core packages that must change; protect every non-core and unselected core package; normalize duplicate installed metadata; require `pip check` and startup/import validation; and automatically roll back every failed mutated candidate.
- New `.comfyuisetup` profiles require a normalized PEP 440 version and a displayed/stored Python, accelerator-runtime, and complete PyTorch ABI tag. Legacy profiles remain readable.
- Internal `.comfyuisetup` edits must use the shared transactional archive service, keep profile IDs stable in the imported library, regenerate integrity values, validate before replacement, and expose equivalent CLI commands.
- Project profile exports belong in `profiles/`. Migrate non-conflicting files from the former `setups/` name and preserve the old Python function only as a compatibility alias.
- Every feature change includes normal, edge, smoke, and documentation updates.
- Never run filesystem discovery, hardware probing, Git/network access, package operations, or other long work on Textual's UI thread. Use a worker and show visible progress before work begins.
- Every installation-worker exit path must restore navigation/recovery controls. Screen teardown must release any thread waiting for a TUI prompt.
- Increment patch versions by 0.0.1 unless the user requests a different release type.
- Keep language clear enough for a young or non-native reader without removing technical accuracy.
- Do not add references to unrelated companies, services, or private infrastructure.

## Useful commands

```bash
PYTHONPATH=installer/src python -m comfy_setup --help
python -m pytest -q
python -m pytest -q tests/test_cli_*.py
bash -n install.sh comfyui-setup-manager collect_comfyui_inventory.sh
```

Use the slash commands in `.claude/commands` for repeatable workflows.

## Shared assets and catalogs

- Prefer one external models library and one external workflows library for all managed installations.
- `shared_assets.py` is the only service allowed to create `extra_model_paths.yaml` sections or workflow links.
- `asset_catalog.py` is the only service allowed to download, import, list, or delete shared model/workflow files.
- All source catalogs and task state are YAML.
- Never embed machine-specific shared-library paths in `.comfyuisetup` or `.comfyworkflows` archives.
- Never package model or LoRA files. Profiles and workflow packs may include source metadata and requirements only.
- Preserve directory trees in workflow packs.
- Every Models/Workflows TUI action needs a CLI equivalent and test coverage.

### Exact reconstruction and transactional-update invariants (v0.8.5)

- Treat the exported environment lock as the proven final package solution. Install it once; do not re-solve every custom node into the shared environment.
- Clone a validated node repository directly. Use Manager/Registry acquisition only when no repository exists, and pass `--no-deps`. Never use per-node `--uv-compile` in an exact reconstruction.
- Reconcile and retain node manifests for audit. Skip dependency-only `install.py` scripts under a complete exact lock, but preserve required lifecycle/bootstrap behavior such as `comfy-env`.
- Build SageAttention/FlashAttention from configured source repositories when wheels are unavailable. Match target Python, torch, CUDA, and compute capability, then verify wheel version and import before success.
- Preserve the exact accelerator distribution family as well as version. Prepare generic native-build tools before installing the exact target PyTorch stack in isolated compiler environments.
- Show core file/package changes and explicit safe/patch/force/new/abort choices in a fixed, accessible action dock. Patch and force remain available for pending updates after a dry-run failure. Snapshot before mutation and restore automatically after resolver, dependency-audit, or startup/import failure.
