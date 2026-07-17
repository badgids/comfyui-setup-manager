# Textual interface guide

[Documentation home](index.md) · [CLI reference](cli-reference.md)

Run `./comfyui-setup-manager` or `./comfyui-setup-manager tui`.

## Fast startup and rescans

The application shell renders before expensive probing begins. On first launch, a progress dialog reports platform detection, installation discovery, node indexing, workflow indexing, shared-asset indexing, and YAML-cache writing. Later launches read `runtime-state.yaml` instead of repeating the scan.

Use **Rescan** after installations, nodes, workflows, models, or LoRAs change outside the manager. Use **Reload YAML** after editing manager configuration only.

Returning Home after an installation also refreshes the inventory because a new
or partially created ComfyUI directory may need to be added. The dashboard is
shown first, then **REFRESHING INSTALLATION INVENTORY** reports the background
scan stages. Buttons, keyboard navigation, and terminal controls remain
responsive throughout the scan. After completion, a successful installation is
selected automatically in Setup and Launch & Manage.

## Installation failures

An installation error keeps the embedded console on screen and enables the
recovery shell, **Retry installation**, **Return home**, and **Quit**. Unexpected
worker or callback failures use the same recovery state. Leaving the screen also
cancels any pending password or wheel-source question so shutdown cannot wait on
an invisible prompt.

## Main tabs

The fixed order is:

1. **Launch & Manage** — instance lifecycle, updates, snapshots, metadata, uninstall, and runtime logs.
2. **Setup & Install** — profiles, system summary, installation target, import/export, profile removal, YAML reload, and installation wizard.
3. **Nodes & Plugins** — one installation's custom-node/plugin inventory and related actions only.
4. **Workflows** — workflow library, packs, catalogs, tasks, and sources only.
5. **Models** — non-LoRA model inventory, catalog, import/export/download/delete, tasks, and sources only.
6. **LoRAs** — LoRA inventory, catalog, import/export/download/delete, tasks, and sources only.
7. **Agents & Skills** — bundled skills, configured agent targets, installation, and agent-skill YAML only.
8. **MCPs** — configured MCP definitions and MCP YAML only.

When cached installations exist, **Launch & Manage** opens first. Otherwise **Setup & Install** opens first.

## Responsive Setup layout

Setup cards and quick actions reflow from four columns to two and then one. Their containers grow with the controls. The **Continue** action remains in its own action bar rather than sharing a clipped fixed-height grid. **Review Installation** uses a dedicated stable-scrollbar viewport, so the complete plan remains readable while Back and Start installation stay visible.


## Runtime logs

Launch & Manage streams the active runtime log. **Clear output** archives the current file and starts a fresh active log without deleting prior output. **Browse logs** opens a dedicated TUI browser that can search filenames and contents, view large logs, copy a selected log, delete archived files, refresh the list, and configure Daily, Weekly, Monthly (30-day default), or custom retention. The active log is protected from deletion.

**View contents** also uses a dedicated stable-scrollbar viewport for long node and workflow inventories.

## Path autocomplete and file browser

Filesystem fields suggest matching files and directories as you type; press `Tab` to accept the visible completion. Every file or directory field also has a **Browse…** button that opens a Textual browser. The browser can move to the parent directory, home directory, or manager project root and can select existing inputs or type a new export filename.

Profile exports begin in `<project-root>/profiles/`. The old `setups/` directory is migrated without overwriting same-named files. Imported and exported setup profiles are reloaded and selected immediately.

Profile labels include a normalized PEP 440 version and a Python/accelerator/PyTorch ABI tag. The Profile Library expands that tag into exact Python, CUDA/ROCm/CPU/MPS, and PyTorch build values while retaining the export creation date.

For an imported `.comfyuisetup`, **Edit profile files** opens the archive's UTF-8 files with language-aware highlighting. Save rebuilds and validates a separate candidate before replacing the archive; generated metadata and binary payloads stay protected.

## Update review

**Launch & Manage → Update** performs preflight in the background, then shows changed ComfyUI files, changed direct core libraries, protected packages, `pip check`, and resolver status. Already-current and file-only updates report that protected resolution is not needed. Required library changes start selected; compatible manifest-only changes are optional and do not trigger pip by themselves. The fixed action dock above the footer contains **Safe update**, **Try to patch current setup**, **Continue anyway**, **Create new install**, and **Abort update**. Patch and Continue remain available for a pending update after a dry-run failure. A failed candidate is rolled back automatically, and the result says whether the restored source and package state passed validation. Successful updates launch a visible inventory-refresh progress screen.

## Selecting and copying text

All path/file/command inputs and all textual output surfaces use a real selection model. Focus the field or output pane, then use the mouse or keyboard:

| Input | Action |
|---|---|
| Mouse drag | Select part of the focused text |
| `Shift+Arrow` | Extend or contract the selection |
| `Ctrl+A` / `Cmd+A` | Select all text in the focused field or output pane |
| `Ctrl+C` / `Cmd+C` | Copy the selected text |
| Arrow keys, Page Up/Down, mouse wheel | Move through or scroll long output |

Live console output does not force-scroll while a selection is active, so debugging text can be selected and copied before more output arrives. Log levels, commands, package requirements, paths, URLs, inventory names, sizes, source labels, YAML, JSON, and supported source languages use colors derived from the active manager theme. Highlight styles exist only at render time: copied text and the clipboard/cache contain plain sanitized text with no ANSI, OSC, Rich markup, or color codes.

## Navigation

| Input | Action |
|---|---|
| `Tab` / `Shift+Tab` | Move between controls |
| Arrow keys or mouse wheel | Move or scroll |
| `h`, `j`, `k`, `l` | Scroll when not typing |
| `Esc` | Go back |
| `Ctrl+Q` | Quit |
| `F1` | Help |
| `t` | Theme screen |

## Themes and consoles

Gruvbox is the default; Dark, Light, and imported Vim/Neovim themes are available. Every selectable output/editor palette is rebuilt from the chosen theme. Installation, update, rollback, recovery, and runtime output stay inside the TUI; terminal control codes are removed from stored/copied text while semantic color remains visible on screen.
