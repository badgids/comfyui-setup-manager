from __future__ import annotations

import argparse
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from .log_management import active_log_path, consume_rotation_request, rotate_runtime_log


def _session_header() -> bytes:
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    return f"\n===== ComfyUI session started {stamp} =====\n".encode("utf-8", errors="replace")


def _rotation_header(reason: str) -> bytes:
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    return f"\n===== Runtime log rotated {stamp} ({reason}) =====\n".encode("utf-8", errors="replace")


def _terminate_child(child: subprocess.Popen[bytes]) -> None:
    if child.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(child.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    child.terminate()
    try:
        child.wait(timeout=8)
    except subprocess.TimeoutExpired:
        child.kill()


def relay(root: Path, command: list[str]) -> int:
    root = root.expanduser().resolve()
    if not command:
        raise ValueError("A child command is required.")

    # Every launch begins a distinct file while preserving the previous log.
    rotate_runtime_log(root, reason="previous-session")
    active = active_log_path(root)
    handle = active.open("ab", buffering=0)
    handle.write(_session_header())

    kwargs: dict[str, object] = {
        "cwd": str(root),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    child = subprocess.Popen(command, **kwargs)  # type: ignore[arg-type]

    stop_requested = threading.Event()

    def stop_handler(_signum: int, _frame: object) -> None:
        stop_requested.set()

    for name in ("SIGTERM", "SIGINT"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), stop_handler)

    output_queue: queue.Queue[bytes | None] = queue.Queue()

    def reader() -> None:
        stream = child.stdout
        if stream is None:
            output_queue.put(None)
            return
        try:
            # Consume unbuffered child output line-by-line so the TUI receives
            # progress immediately instead of waiting for a large block or EOF.
            for data in iter(stream.readline, b""):
                output_queue.put(data)
        finally:
            try:
                stream.close()
            except OSError:
                pass
            output_queue.put(None)

    threading.Thread(target=reader, daemon=True).start()
    reader_finished = False
    try:
        while True:
            if stop_requested.is_set():
                _terminate_child(child)
                stop_requested.clear()

            reason = consume_rotation_request(root)
            if reason is not None:
                handle.close()
                rotate_runtime_log(root, reason=reason)
                handle = active_log_path(root).open("ab", buffering=0)
                handle.write(_rotation_header(reason))

            try:
                item = output_queue.get(timeout=0.1)
            except queue.Empty:
                item = b""
            if item is None:
                reader_finished = True
            elif item:
                handle.write(item)

            if reader_finished and child.poll() is not None and output_queue.empty():
                break
    finally:
        if child.poll() is None:
            _terminate_child(child)
        handle.close()
    return int(child.returncode or 0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Persistent ComfyUI runtime log relay")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    try:
        return relay(args.root, command)
    except Exception as exc:
        print(f"log relay failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
