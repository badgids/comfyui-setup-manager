#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if command -v python3 >/dev/null 2>&1; then
    exec python3 "$SCRIPT_DIR/collect_comfyui_inventory.py" "$@"
fi

if command -v python >/dev/null 2>&1; then
    exec python "$SCRIPT_DIR/collect_comfyui_inventory.py" "$@"
fi

printf '\nERROR: Python 3 is required to run the inventory collector.\n' >&2
exit 1
