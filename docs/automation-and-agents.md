# Automation and AI agent use

[Documentation home](index.md) · [CLI reference](cli-reference.md)

The automation interface is designed so a script or coding agent can use the entire manager without terminal UI interaction.

## Rules for reliable automation

1. Use `--format json` or `--format yaml`.
2. Run a plan or check before a changing operation.
3. Read the exit code.
4. Treat stderr as progress and diagnostic output during long installs.
5. Pass destructive confirmation flags explicitly.
6. Store paths as absolute paths when a job may change its working directory.
7. Never parse the decorative TUI.

## Discovery example

```bash
result=$(./comfyui-setup-manager --format json installations discover)
printf '%s\n' "$result"
```

## Installation example

First produce a plan:

```bash
./comfyui-setup-manager --format json install plan \
  --profile vanilla-comfyui \
  --target /srv/comfyui/main \
  --repository-mode official > plan.json
```

Then run the same resolved choices:

```bash
./comfyui-setup-manager --format json install run \
  --profile vanilla-comfyui \
  --target /srv/comfyui/main \
  --repository-mode official > result.json
```

Progress is written to stderr. The final result is written to stdout.

## Update example

```bash
./comfyui-setup-manager --format json updates check /srv/comfyui/main > preflight.json
```

An agent should inspect `high_risk`, `blocking`, `issues`, `core_file_changes`, `core_package_changes`, `protected_packages`, baseline `pip check`, and resolver status before running:

```bash
./comfyui-setup-manager --format json updates run /srv/comfyui/main --strategy safe
```

Use `--strategy patch` to preserve and reconcile a customized environment. Recommend `--strategy new` when protected compatibility is impossible. Only use `--strategy force` after presenting the risk to the user or following a policy that explicitly permits it. Never bypass the manager by installing the checkout's requirement files directly. Inspect `rolled_back` and `rollback_error` after any failed candidate.

## Capability discovery

Agents can query the built-in parity map:

```bash
./comfyui-setup-manager --format json capabilities
```

This output pairs each TUI feature with its CLI command.

## Passwords and privilege

Do not place passwords in shell history. Prefer preinstalled system prerequisites or an appropriately privileged runner. When unavoidable, `--sudo-password-env VARIABLE_NAME` reads a password from an environment variable and does not place it in the command line.

## Shared asset automation

Agents should configure one external models path and one external workflows path before creating several installations:

```bash
comfyui-setup-manager --format json shared-paths configure \
  --models-dir /data/comfy/models \
  --workflows-dir /data/comfy/workflows
```

Then apply it to every managed checkout:

```bash
comfyui-setup-manager --format json shared-paths apply /data/ComfyUI-A /data/ComfyUI-B
```

Before downloading or deleting assets, list sources and installed files in JSON. Never guess a relative path:

```bash
comfyui-setup-manager --format json models sources
comfyui-setup-manager --format json models list
comfyui-setup-manager --format json workflows list
```

Destructive commands require `--yes`. Agents should show the exact target path to the user before running them.


## Bundled Agent Skills

The source tree and wheel include portable skills under `skills/<skill-id>/SKILL.md`. List and install them without manually copying directories:

```bash
./comfyui-setup-manager --format json skills list
./comfyui-setup-manager --format json skills targets
./comfyui-setup-manager skills install comfyui-setup-manager-operator --agent claude-code
```

Default targets for Claude Code, Codex, OpenCode, and OpenClaude are stored in `agents-skills.yaml`. Use `--target PATH` to override a target for one installation.

## Cached scans

Agents should use `config show runtime-state.yaml` for a fast cached view. Run a discovery/rescan only after external filesystem changes; reload YAML after configuration-only changes.

## MCP definitions

```bash
./comfyui-setup-manager --format json mcps list
./comfyui-setup-manager mcps edit-config
```

The manager does not start MCP servers automatically.
