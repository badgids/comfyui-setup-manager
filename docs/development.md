# Development guide

[Documentation home](index.md) · [Prerequisites](prerequisites.md) · [Testing](testing.md) · [PRD](../PRD.md)

## Development prerequisites

Contributors need the [general manager prerequisites](prerequisites.md), plus Python virtual-environment support and the test dependencies installed below. Git is required by repository/export/update tests. Native-build tests or profile work additionally require the matching compiler/CMake/Ninja/accelerator toolchain; they are not required for ordinary unit-test development.

On Windows, WinGet is needed only when the bootstrap must install missing Python or Git. A developer who already has compatible Python and Git can work without WinGet.

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
