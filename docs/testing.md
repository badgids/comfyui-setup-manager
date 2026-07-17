# Testing guide

[Documentation home](index.md) · [Development](development.md)

## Test levels

### Unit tests

Test one function or class with external processes mocked.

### Edge-case tests

Cover malformed YAML, missing files, unsafe paths, Unicode paths, spaces, conflicting package constraints, unsupported accelerators, corrupt archives, and interrupted operations.

### Smoke tests

Verify every CLI command parses, help opens, built-in profiles load, the installed wheel runs outside the source tree, and the complete Textual TUI mounts at normal and compact terminal sizes.

### Integration tests

Use temporary Git repositories and fake ComfyUI trees to test discovery, export, update snapshots, rollback, workflows, and local launchers without changing the real machine.

### Release tests

Inspect ZIP, TAR.GZ, and wheel contents. Check versions, package data, documentation links, prohibited source strings, personal paths, executable scripts, and checksums.

## Commands

```bash
python -m pytest -q
python -m pytest -q tests/test_cli_*.py
python -m pytest -q -m smoke
python -m pytest -q tests/test_updates.py
```

## Test-writing rules

- Use temporary directories.
- Never depend on a user's real ComfyUI installation.
- Mock downloads unless the test is explicitly an opt-in network test.
- Assert output and exit code.
- For every TUI action, assert the CLI capability map has a command.
- For every destructive command, test missing confirmation and unsafe paths.

## Shared asset tests

Shared-asset changes must cover:

- directory creation on empty paths;
- preservation of existing `extra_model_paths.yaml` sections;
- workflow migration and conflict policies;
- symlink/junction behavior through mocked platform calls where needed;
- safe-path rejection for downloads and deletes;
- partial-download cleanup and checksum failure;
- source and entry YAML round trips;
- portable `.comfyuisetup` companion YAML;
- workflow-pack companion YAML and nested-path preservation;
- CLI/TUI parity for Models, LoRAs, Workflows, Nodes & Plugins, Agents & Skills, MCPs, and Shared paths;
- documentation links and package-data inclusion.

## Startup and workspace-boundary tests

Release validation must also cover:

- constructor startup from YAML without platform, installation, node, workflow, or asset discovery;
- first-launch background scan state and atomic `runtime-state.yaml` persistence;
- stable-scrollbar View Contents and Review Installation viewports;
- runtime-log rotation, browser search, deletion safety, retention cleanup, and CLI parity;
- Manager requirements, captured-manifest selection, verified-version reconciliation, profile-constraint reapplication, dependency repair, and ComfyUI quick startup validation;
- mouse/keyboard text selection and copy shortcuts in inputs, console output, logs, inventories, and review documents;
- complete exact-profile locks, legacy embedded-wheel compatibility, Manager/Registry/Git custom-node resolution, unresolved-plugin-only embedding, full-path duplicate-node resolution and explicit omissions, duplicate-distribution rejection, source-tree editable reinstall, exact PyTorch backend retention, and final package-set/version auditing;
- explicit rescan versus reload-from-YAML behavior;
- the exact eight-tab order;
- strict domain separation for every workspace, especially Models versus LoRAs;
- visibility of the Setup & Install Continue button at compact terminal sizes;
- bundled Agent Skill discovery and safe target installation;
- portable MCP definition loading;
- wheel inclusion of every editable YAML file and Agent Skill.
