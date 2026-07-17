from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


class LauncherError(RuntimeError):
    """Raised when a managed ComfyUI instance cannot be launched."""


def write_local_launchers(target: Path) -> Path:
    """Create self-contained launchers inside one ComfyUI installation.

    No global PATH entry or user shell profile is modified.
    """
    root = target.expanduser().resolve()
    sh_path = root / "comfyui"
    ps_path = root / "comfyui.ps1"
    cmd_path = root / "comfyui.cmd"

    sh_path.write_text(
        '''#!/usr/bin/env sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PY="$ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "ComfyUI virtual environment was not found. Run ComfyUI Setup Manager first." >&2
  exit 1
fi
export VIRTUAL_ENV="$ROOT/.venv"
export PATH="$VIRTUAL_ENV/bin:$PATH"
export PYTHONUNBUFFERED=1
exec "$PY" -u "$ROOT/main.py" --enable-manager "$@"
''',
        encoding="utf-8",
    )
    try:
        sh_path.chmod(0o755)
    except OSError:
        pass

    ps_path.write_text(
        r'''$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "ComfyUI virtual environment was not found. Run ComfyUI Setup Manager first."
}
$env:VIRTUAL_ENV = Join-Path $Root ".venv"
$env:Path = (Join-Path $env:VIRTUAL_ENV "Scripts") + ";" + $env:Path
$env:PYTHONUNBUFFERED = "1"
& $Python -u (Join-Path $Root "main.py") --enable-manager @args
exit $LASTEXITCODE
''',
        encoding="utf-8",
    )
    cmd_path.write_text(
        "@echo off\r\n"
        "set \"ROOT=%~dp0\"\r\n"
        "if not exist \"%ROOT%.venv\\Scripts\\python.exe\" (\r\n"
        "  echo ComfyUI virtual environment was not found. Run ComfyUI Setup Manager first.\r\n"
        "  exit /b 1\r\n"
        ")\r\n"
        "set \"VIRTUAL_ENV=%ROOT%.venv\"\r\n"
        "set \"PATH=%VIRTUAL_ENV%\\Scripts;%PATH%\"\r\n"
        "set \"PYTHONUNBUFFERED=1\"\r\n"
        "\"%ROOT%.venv\\Scripts\\python.exe\" -u \"%ROOT%main.py\" --enable-manager %*\r\n",
        encoding="utf-8",
    )
    return sh_path


def launch_command(target: Path, extra_args: list[str] | None = None) -> list[str]:
    root = target.expanduser().resolve()
    extra = list(extra_args or [])
    write_local_launchers(root)
    if os.name == "nt":
        return ["cmd.exe", "/d", "/c", str(root / "comfyui.cmd"), *extra]
    return [str(root / "comfyui"), *extra]


def log_relay_command(target: Path, extra_args: list[str] | None = None) -> list[str]:
    root = target.expanduser().resolve()
    return [
        sys.executable,
        "-m",
        "comfy_setup.log_relay",
        "--root",
        str(root),
        "--",
        *launch_command(root, extra_args),
    ]


def launch_detached(target: Path, *, log_path: Path | None = None, extra_args: list[str] | None = None) -> subprocess.Popen[bytes]:
    root = target.expanduser().resolve()
    if not (root / "main.py").is_file():
        raise LauncherError(f"Not a ComfyUI installation: {root}")
    python = root / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
    if not python.is_file():
        raise LauncherError(
            "This installation has no usable .venv. Apply a setup profile to it before launching."
        )
    if log_path is not None:
        raise LauncherError(
            "Custom runtime log paths are not supported by the managed log browser. "
            "Logs are stored under the installation's .comfy-setup directory."
        )

    env = os.environ.copy()
    # Source-tree runs (including development and tests) need an absolute
    # package path because the detached relay changes cwd to the ComfyUI root.
    package_root = str(Path(__file__).resolve().parents[1])
    existing_pythonpath = env.get("PYTHONPATH", "")
    # The relay changes cwd to the target ComfyUI checkout. Resolve relative
    # development/test entries now so its dependency paths remain usable after
    # that directory change. Installed environments normally contain only
    # absolute site-package paths, but preserving both cases costs nothing.
    inherited_paths = [
        str(Path(part).expanduser().resolve()) if not Path(part).is_absolute() else part
        for part in existing_pythonpath.split(os.pathsep)
        if part
    ]
    env["PYTHONPATH"] = os.pathsep.join(
        [package_root, *inherited_paths]
    )
    kwargs: dict[str, object] = {
        "cwd": str(root),
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
        "env": env,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(log_relay_command(root, extra_args), **kwargs)  # type: ignore[arg-type]


def stop_process(process: subprocess.Popen[bytes], timeout: float = 8.0) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)
    handle = getattr(process, "_comfy_log_handle", None)
    if handle is not None:
        try:
            handle.close()
        except OSError:
            pass
