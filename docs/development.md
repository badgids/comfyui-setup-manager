# Development guide

[Documentation home](index.md) · [Testing](testing.md) · [PRD](../PRD.md)

## Local environment

```bash
python3 -m venv .dev-venv
. .dev-venv/bin/activate
python -m pip install -U pip setuptools wheel
python -m pip install -e ./installer
python -m pip install pytest
```

Windows activation:

```powershell
.\.dev-venv\Scripts\Activate.ps1
```

## Run from source

```bash
PYTHONPATH=installer/src python -m comfy_setup --help
PYTHONPATH=installer/src python -m comfy_setup tui
```

## Design rules

- Keep TUI and CLI feature parity.
- Put reusable behavior in service modules, not screen event handlers.
- Keep editable configuration in YAML.
- Keep native ComfyUI workflows in JSON.
- Do not hardcode usernames, drives, install paths, or accelerator locations.
- Do not add a new package source without explicit trust metadata and tests.
- Do not make destructive behavior implicit.
- Add tests and documentation in the same change as a feature.

## Versioning

Patch work increments by `0.0.1`. Update `comfy_setup.__version__`, `installer/pyproject.toml`, root launchers, README, release notes, and bundled wheel together.

## Claude Code

Read [`.claude/CLAUDE.md`](../.claude/CLAUDE.md). Slash commands under `.claude/commands` provide repeatable test, docs, audit, and release workflows.
