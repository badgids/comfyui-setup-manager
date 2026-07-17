# Command-line reference

[Documentation home](index.md) · [Automation and agents](automation-and-agents.md)

The command line exposes the complete feature set without opening Textual.

## Global syntax

```text
comfyui-setup-manager [GLOBAL OPTIONS] COMMAND SUBCOMMAND [OPTIONS]
```

Global options:

| Option | Meaning |
|---|---|
| `--format text` | Human-readable output, default |
| `--format json` | Structured JSON output |
| `--format yaml` | Structured YAML output |
| `--config-dir PATH` | Use a different editable YAML configuration directory |
| `--traceback` | Show a full Python traceback on errors |
| `--version` | Print the version |

List the complete feature map:

```bash
comfyui-setup-manager capabilities
```

## System

```bash
comfyui-setup-manager system info
comfyui-setup-manager system check [--source-builds]
comfyui-setup-manager system install-prerequisites [--source-builds] [--yes] [--dry-run]
```

## Installations

```bash
comfyui-setup-manager installations discover [--scan-root PATH]
comfyui-setup-manager installations show PATH
comfyui-setup-manager installations contents PATH
comfyui-setup-manager installations status PATH
comfyui-setup-manager installations launch PATH [--foreground] [--arg VALUE] [--log FILE]
comfyui-setup-manager installations stop PATH [--timeout SECONDS] [--force]
comfyui-setup-manager installations logs PATH [--lines N] [--follow | --list-files | --search TEXT | --clear | --cleanup | --retention MODE [--days N] | --delete FILE --yes]
comfyui-setup-manager installations edit PATH [--name TEXT] [--description TEXT]
comfyui-setup-manager installations uninstall PATH --confirm UNINSTALL
```

`instances` is an alias for `installations`.

## Nodes and plugins

```bash
comfyui-setup-manager nodes list PATH
```

## Complete installation

Plan:

```bash
comfyui-setup-manager install plan --profile PROFILE --target PATH [OPTIONS]
```

Run:

```bash
comfyui-setup-manager install run --profile PROFILE --target PATH [OPTIONS]
```

Important options:

| Option | Meaning |
|---|---|
| `--repository-mode existing` | Keep the existing checkout and origin |
| `--repository-mode official` | Use official ComfyUI |
| `--repository-mode profile` | Use the profile-recommended repository |
| `--repository-mode custom --repository URL` | Use a public custom repository |
| `--branch NAME` | Git branch |
| `--accelerator auto|nvidia|rocm|mps|cpu` | Requested acceleration backend |
| `--nodes default|all|none|ID,ID` | Node selection |
| `--exclude-node ID` | Exclude one node; repeatable |
| `--acceleration-packages ...` | Compiled package selection |
| `--no-install-system` | Do not install OS packages |
| `--no-source-builds` | Cancel instead of compiling when no wheel exists |
| `--backup-builds --backup-dir PATH` | Save downloaded or built wheels |
| `--update-nodes` | Pull existing node Git checkouts |
| `--sudo-password-env NAME` | Read an administrator password from an environment variable |

For automation, prefer running as an account that already has required system packages. Avoid putting passwords directly in command arguments.

## Profiles

```bash
comfyui-setup-manager profiles list
comfyui-setup-manager profiles show PROFILE
comfyui-setup-manager profiles import FILE.comfyuisetup
comfyui-setup-manager profiles export PROFILE [OUTPUT.comfyuisetup]
comfyui-setup-manager profiles files PROFILE
comfyui-setup-manager profiles read-file PROFILE MEMBER
comfyui-setup-manager profiles edit-file PROFILE MEMBER --from-file PATH_OR_DASH [--allow-id-change]
comfyui-setup-manager profiles remove PROFILE --yes
comfyui-setup-manager profiles from-installation PATH --name NAME [--output FILE.comfyuisetup] [--omit-node COMPLETE_PATH ...]
comfyui-setup-manager profiles from-inventory FILE.tgz --name NAME [--output FILE.comfyuisetup]
```

When an output is omitted, profile exports use `<project-root>/profiles/`. Non-conflicting files in the former `setups/` directory are migrated automatically. Exported profiles are added to the profile library immediately.

`profiles edit-file` accepts only UTF-8 text members and rebuilds the archive transactionally. Use `--from-file -` for standard input. Profile IDs are stable for in-library edits; `--allow-id-change` is for an external copy that will be imported as a new profile.

If duplicate custom-node identities are found, an interactive terminal prints every full source path and asks which path number(s) to omit, leaving one copy. For scripts and structured output, repeat `--omit-node /complete/path/to/node` until every duplicate identity has one remaining source.

## Workflows

```bash
comfyui-setup-manager workflows libraries [--comfyui-dir PATH]
comfyui-setup-manager workflows list PATH [--user NAME]
comfyui-setup-manager workflows inspect ARTIFACT
comfyui-setup-manager workflows import ARTIFACT --comfyui-dir PATH [OPTIONS]
comfyui-setup-manager workflows export SOURCE.json OUTPUT.json
comfyui-setup-manager workflows pack SOURCE... --name NAME --output FILE.comfyworkflows [OPTIONS]
```

Import destinations may be a ComfyUI workflow library or an exact custom directory. Workflow packs preserve nested paths.

## Updates and snapshots

```bash
comfyui-setup-manager updates check PATH [--no-fetch]
comfyui-setup-manager updates run PATH [--strategy safe|patch|force|new|abort]
  [--package NAME ... | --all-core-packages]
comfyui-setup-manager snapshots list PATH
comfyui-setup-manager snapshots show PATH SNAPSHOT_ID
comfyui-setup-manager snapshots rollback PATH SNAPSHOT_ID
comfyui-setup-manager snapshots delete PATH SNAPSHOT_ID --yes
```

`updates check` reports core file changes, selectable direct core package changes, protected packages, current `pip check`, and protected dry-run resolution only when a core package must change. Already-current and file-only updates do not invoke uv. Duplicate normalized installed metadata is reduced to Python's effective version. `updates run` never executes upstream requirement files independently. The deprecated `--force` flag is an alias for `--strategy force`. A failed candidate that changed source or packages is rolled back automatically.

## Themes

```bash
comfyui-setup-manager themes list
comfyui-setup-manager themes current
comfyui-setup-manager themes select THEME
comfyui-setup-manager themes import-vim NAME_OR_PATH [--select]
```

## Wheel sources

```bash
comfyui-setup-manager wheel-sources path
comfyui-setup-manager wheel-sources list
comfyui-setup-manager wheel-sources show SOURCE_ID
comfyui-setup-manager wheel-sources add --package PACKAGE --label LABEL --kind KIND --location LOCATION
comfyui-setup-manager wheel-sources enable SOURCE_ID
comfyui-setup-manager wheel-sources disable SOURCE_ID
comfyui-setup-manager wheel-sources remove SOURCE_ID --yes
comfyui-setup-manager wheel-sources register-local PACKAGE DIRECTORY
comfyui-setup-manager wheel-sources validate
comfyui-setup-manager wheel-sources edit
```

`sources` is an alias for `wheel-sources`.

## Configuration

```bash
comfyui-setup-manager config list
comfyui-setup-manager config path FILE.yaml
comfyui-setup-manager config show FILE.yaml
comfyui-setup-manager config edit FILE.yaml
comfyui-setup-manager config set FILE.yaml dotted.key YAML_VALUE
comfyui-setup-manager config validate [FILE.yaml]
```

Example:

```bash
comfyui-setup-manager config set manager-config.yaml resolver.merge_new_default_sources true
```

## Exit codes

| Code | Meaning |
|---:|---|
| 0 | Success |
| 2 | Invalid arguments or unsafe confirmation missing |
| 3 | Requested file, profile, source, or snapshot not found |
| 5 | Operation failed |
| 130 | Cancelled with Ctrl+C |

## Shared external paths

```bash
comfyui-setup-manager shared-paths status
comfyui-setup-manager shared-paths configure --models-dir PATH --workflows-dir PATH [--migrate-existing]
comfyui-setup-manager shared-paths apply COMFYUI_DIR [COMFYUI_DIR ...]
comfyui-setup-manager shared-paths inspect COMFYUI_DIR
comfyui-setup-manager shared-paths edit
```

`assets` is an alias for `shared-paths`.

## Models

```bash
comfyui-setup-manager models list
comfyui-setup-manager models sources
comfyui-setup-manager models edit-sources
comfyui-setup-manager models tasks
comfyui-setup-manager models retry-task TASK_ID [--overwrite]
comfyui-setup-manager models clear-tasks [--all]
comfyui-setup-manager models add-source --id ID --name NAME --kind KIND [--base-url URL] [--label LABEL]
comfyui-setup-manager models add-entry --id ID --name NAME --source-id SOURCE --url URL [--destination DIR] [--filename NAME]
comfyui-setup-manager models download ENTRY_ID [--overwrite]
comfyui-setup-manager models import FILE [--destination DIR] [--overwrite]
comfyui-setup-manager models export RELATIVE_PATH OUTPUT [--overwrite]
comfyui-setup-manager models delete RELATIVE_PATH --yes
comfyui-setup-manager models remove-entry ENTRY_ID
```


## LoRAs

The `loras` group mirrors model catalog operations but is rooted at the dedicated shared LoRA directory:

```bash
comfyui-setup-manager loras list
comfyui-setup-manager loras sources
comfyui-setup-manager loras import FILE [--overwrite]
comfyui-setup-manager loras download ENTRY_ID [--overwrite]
comfyui-setup-manager loras export RELATIVE_PATH OUTPUT [--overwrite]
comfyui-setup-manager loras delete RELATIVE_PATH --yes
```

## Agent Skills and MCPs

```bash
comfyui-setup-manager skills list
comfyui-setup-manager skills targets
comfyui-setup-manager skills install SKILL_ID --agent AGENT_ID [--target PATH] [--overwrite]
comfyui-setup-manager skills edit-config
comfyui-setup-manager mcps list
comfyui-setup-manager mcps edit-config
```

## Workflow catalogs and tasks

In addition to native workflow import/export and packs:

```bash
comfyui-setup-manager workflows sources
comfyui-setup-manager workflows edit-sources
comfyui-setup-manager workflows tasks
comfyui-setup-manager workflows retry-task TASK_ID [--overwrite]
comfyui-setup-manager workflows clear-tasks [--all]
comfyui-setup-manager workflows add-source --id ID --name NAME --kind KIND [--base-url URL] [--label LABEL]
comfyui-setup-manager workflows add-entry --id ID --name NAME --source-id SOURCE --url URL [--destination DIR] [--filename NAME]
comfyui-setup-manager workflows download ENTRY_ID [--overwrite]
comfyui-setup-manager workflows delete RELATIVE_PATH --yes
```

## Portable setup extension

The primary setup extension is `.comfyuisetup`. Legacy `.comfysetup`, YAML, and JSON profiles remain readable for migration.
