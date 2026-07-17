#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INSTALLER_DIR="$SCRIPT_DIR/installer"
VENV_DIR="$INSTALLER_DIR/.venv"
PYPI_INDEX="https://pypi.org/simple"

say() { printf '\n%s\n' "$*"; }

install_system_bootstrap() {
    missing="$1"
    if command -v apt-get >/dev/null 2>&1; then
        say "Installing missing prerequisite packages with apt-get: $missing"
        sudo apt-get update || true
        sudo apt-get install -y $missing
    elif command -v dnf >/dev/null 2>&1; then
        say "Installing missing prerequisite packages with dnf: $missing"
        sudo dnf install -y $missing
    elif command -v pacman >/dev/null 2>&1; then
        say "Installing missing prerequisite packages with pacman: $missing"
        sudo pacman -S --needed --noconfirm $missing
    elif command -v zypper >/dev/null 2>&1; then
        say "Installing missing prerequisite packages with zypper: $missing"
        sudo zypper --non-interactive install $missing
    elif command -v brew >/dev/null 2>&1; then
        say "Installing missing prerequisite packages with Homebrew: $missing"
        brew install $missing
    else
        printf '\nERROR: No supported system package manager was found.\n' >&2
        printf 'Install Python 3.10 or newer, Python venv support, pip, and Git, then rerun this script.\n' >&2
        exit 1
    fi
}

find_python() {
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 && \
           "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

if ! PYTHON=$(find_python); then
    if command -v apt-get >/dev/null 2>&1; then
        install_system_bootstrap "python3 python3-venv python3-pip"
    elif command -v dnf >/dev/null 2>&1; then
        install_system_bootstrap "python3 python3-pip"
    elif command -v pacman >/dev/null 2>&1; then
        install_system_bootstrap "python python-pip"
    elif command -v brew >/dev/null 2>&1; then
        install_system_bootstrap "python"
    else
        install_system_bootstrap "python3"
    fi
    PYTHON=$(find_python) || { echo "ERROR: Python 3.10 or newer is still unavailable." >&2; exit 1; }
fi

if ! command -v git >/dev/null 2>&1; then
    install_system_bootstrap "git"
fi

if [ ! -x "$VENV_DIR/bin/python" ]; then
    say "Creating the private manager environment with $PYTHON..."
    if ! "$PYTHON" -m venv "$VENV_DIR"; then
        if command -v apt-get >/dev/null 2>&1; then
            install_system_bootstrap "python3-venv"
            "$PYTHON" -m venv "$VENV_DIR"
        else
            echo "ERROR: Python virtual-environment support is missing." >&2
            exit 1
        fi
    fi
fi

VENV_PYTHON="$VENV_DIR/bin/python"
VENV_UV="$VENV_DIR/bin/uv"

unset PIP_INDEX_URL PIP_EXTRA_INDEX_URL PIP_TRUSTED_HOST PIP_CONFIG_FILE || true
unset UV_INDEX UV_EXTRA_INDEX_URL UV_INDEX_URL UV_DEFAULT_INDEX UV_CONFIG_FILE || true
export PIP_CONFIG_FILE=/dev/null
export PIP_INDEX_URL="$PYPI_INDEX"
export UV_NO_CONFIG=1
export UV_DEFAULT_INDEX="$PYPI_INDEX"

say "Installing or updating ComfyUI Setup Manager from the included project and public Python package index..."
"$VENV_PYTHON" -m pip install --disable-pip-version-check --no-cache-dir --index-url "$PYPI_INDEX" --upgrade pip setuptools wheel

BUNDLED_WHEEL=$(find "$INSTALLER_DIR/dist" -maxdepth 1 -type f -name 'comfyui_setup_manager-*.whl' 2>/dev/null | sort | tail -n 1 || true)
if [ -n "$BUNDLED_WHEEL" ]; then
    "$VENV_PYTHON" -m pip install --disable-pip-version-check --no-cache-dir --index-url "$PYPI_INDEX" --upgrade "$BUNDLED_WHEEL"
else
    "$VENV_PYTHON" -m pip install --disable-pip-version-check --no-cache-dir --index-url "$PYPI_INDEX" --no-build-isolation --upgrade "$INSTALLER_DIR"
fi

[ -x "$VENV_UV" ] || { echo "ERROR: uv was not installed in $VENV_DIR" >&2; exit 1; }

export COMFY_INSTALLER_UV="$VENV_UV"
export COMFYUI_SETUP_CONFIG_DIR="$SCRIPT_DIR/config"
export COMFYUI_SETUP_PROJECT_ROOT="$SCRIPT_DIR"
mkdir -p "$COMFYUI_SETUP_CONFIG_DIR" "$SCRIPT_DIR/profiles"

say "Installation complete."
printf 'Interactive interface: %s/comfyui-setup-manager\n' "$SCRIPT_DIR"
printf 'Automation CLI help: %s/comfyui-setup-manager --help\n\n' "$SCRIPT_DIR"

exec "$VENV_PYTHON" -m comfy_setup "$@"
