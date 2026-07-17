from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from .discovery import is_comfyui_directory
from .launchers import launch_command, log_relay_command, write_local_launchers
from .log_management import active_log_path


class InstanceControlError(RuntimeError):
    """Raised when a managed ComfyUI process cannot be controlled safely."""


@dataclass(slots=True)
class ProcessRecord:
    pid: int
    started_at: float
    command: list[str]
    log_path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _state_path(root: Path) -> Path:
    return root / ".comfy-setup" / "runtime-process.yaml"


def _load_record(root: Path) -> ProcessRecord | None:
    path = _state_path(root)
    if not path.is_file():
        return None
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return ProcessRecord(
            pid=int(payload["pid"]),
            started_at=float(payload.get("started_at", 0.0)),
            command=[str(item) for item in payload.get("command", [])],
            log_path=str(payload.get("log_path", "")),
        )
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
        return None


def _save_record(root: Path, record: ProcessRecord) -> Path:
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(record.to_dict(), sort_keys=False), encoding="utf-8")
    return path


def _remove_record(root: Path) -> None:
    try:
        _state_path(root).unlink()
    except FileNotFoundError:
        pass


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0 and str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def instance_status(target: Path) -> dict[str, Any]:
    root = target.expanduser().resolve()
    record = _load_record(root)
    alive = bool(record and process_alive(record.pid))
    if record and not alive:
        _remove_record(root)
        record = None
    return {
        "path": str(root),
        "running": alive,
        "pid": record.pid if record else None,
        "started_at": record.started_at if record else None,
        "command": record.command if record else None,
        "log_path": record.log_path if record else str(root / ".comfy-setup" / "runtime.log"),
    }


def launch_instance(
    target: Path,
    *,
    foreground: bool = False,
    extra_args: list[str] | None = None,
    log_path: Path | None = None,
) -> dict[str, Any]:
    root = target.expanduser().resolve()
    if not is_comfyui_directory(root):
        raise InstanceControlError(f"Not a ComfyUI installation: {root}")
    python = root / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
    if not python.is_file():
        raise InstanceControlError(f"ComfyUI virtual environment is missing: {python}")
    current = instance_status(root)
    if current["running"]:
        raise InstanceControlError(f"ComfyUI is already running with PID {current['pid']}.")

    write_local_launchers(root)
    command = launch_command(root, extra_args)
    if foreground:
        completed = subprocess.run(command, cwd=root, check=False)
        return {"path": str(root), "foreground": True, "returncode": completed.returncode}

    log = (log_path.expanduser().resolve() if log_path is not None else active_log_path(root))
    log.parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {
        "cwd": str(root),
        "stdin": subprocess.DEVNULL,
    }
    if log_path is None:
        command = log_relay_command(root, extra_args)
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    else:
        handle = log.open("ab", buffering=0)
        kwargs["stdout"] = handle
        kwargs["stderr"] = subprocess.STDOUT
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)
    if log_path is not None:
        handle.close()
    record = ProcessRecord(
        pid=process.pid,
        started_at=time.time(),
        command=command,
        log_path=str(log),
    )
    _save_record(root, record)
    return {**record.to_dict(), "path": str(root), "running": True}


def stop_instance(target: Path, *, timeout: float = 10.0, force: bool = False) -> dict[str, Any]:
    root = target.expanduser().resolve()
    record = _load_record(root)
    if record is None or not process_alive(record.pid):
        _remove_record(root)
        return {"path": str(root), "stopped": False, "reason": "not-running"}

    pid = record.pid
    if os.name == "nt":
        command = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            command.append("/F")
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0 and process_alive(pid):
            raise InstanceControlError(completed.stderr.strip() or f"Could not stop PID {pid}.")
    else:
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + timeout
        while process_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        if process_alive(pid):
            if not force:
                raise InstanceControlError(
                    f"PID {pid} did not stop within {timeout:.1f}s. Re-run with --force."
                )
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    _remove_record(root)
    return {"path": str(root), "stopped": True, "pid": pid}
