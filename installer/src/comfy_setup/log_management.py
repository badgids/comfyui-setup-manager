from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .configuration import manager_config, save_editable_config


RETENTION_PRESETS: dict[str, int] = {
    "daily": 1,
    "weekly": 7,
    "monthly": 30,
}


@dataclass(frozen=True, slots=True)
class LogRetention:
    mode: str = "monthly"
    days: int = 30
    auto_cleanup: bool = True

    @property
    def effective_days(self) -> int:
        if self.mode in RETENTION_PRESETS:
            return RETENTION_PRESETS[self.mode]
        return max(1, int(self.days))


@dataclass(frozen=True, slots=True)
class RuntimeLogFile:
    path: Path
    active: bool
    size: int
    modified: float

    @property
    def label(self) -> str:
        state = "active" if self.active else "archived"
        stamp = datetime.fromtimestamp(self.modified).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        return f"{self.path.name} · {state} · {self.size:,} bytes · {stamp}"


def state_directory(root: Path) -> Path:
    return root.expanduser().resolve() / ".comfy-setup"


def logs_directory(root: Path) -> Path:
    directory = state_directory(root) / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def active_log_path(root: Path) -> Path:
    path = state_directory(root) / "runtime.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def rotation_request_path(root: Path) -> Path:
    return state_directory(root) / "rotate-log.request"


def _archive_name(root: Path, *, reason: str = "session") -> Path:
    safe_reason = re.sub(r"[^a-z0-9-]+", "-", reason.lower()).strip("-") or "session"
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    directory = logs_directory(root)
    candidate = directory / f"runtime-{stamp}-{safe_reason}.log"
    counter = 2
    while candidate.exists():
        candidate = directory / f"runtime-{stamp}-{safe_reason}-{counter}.log"
        counter += 1
    return candidate


def rotate_runtime_log(root: Path, *, reason: str = "manual") -> Path | None:
    """Archive the active log and create a fresh runtime.log.

    Call this only when no external process is holding the active log open. A
    running log relay should receive a rotation request instead.
    """
    active = active_log_path(root)
    archived: Path | None = None
    try:
        if active.is_file() and active.stat().st_size > 0:
            archived = _archive_name(root, reason=reason)
            active.replace(archived)
    except OSError:
        # Cross-device or platform-specific rename failures get a copy+truncate
        # fallback while preserving the previous log.
        if active.is_file() and active.stat().st_size > 0:
            archived = _archive_name(root, reason=reason)
            shutil.copy2(active, archived)
            active.write_bytes(b"")
    active.touch(exist_ok=True)
    return archived


def request_log_rotation(root: Path, *, reason: str = "manual") -> Path:
    marker = rotation_request_path(root)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(reason.strip() or "manual", encoding="utf-8")
    return marker


def consume_rotation_request(root: Path) -> str | None:
    marker = rotation_request_path(root)
    try:
        reason = marker.read_text(encoding="utf-8").strip() or "manual"
    except FileNotFoundError:
        return None
    except OSError:
        return None
    try:
        marker.unlink()
    except OSError:
        pass
    return reason


def list_runtime_logs(root: Path) -> list[RuntimeLogFile]:
    root = root.expanduser().resolve()
    active = active_log_path(root)
    candidates: list[tuple[Path, bool]] = [(active, True)]
    directory = logs_directory(root)
    candidates.extend((path, False) for path in directory.glob("*.log") if path.is_file())
    records: list[RuntimeLogFile] = []
    seen: set[Path] = set()
    for path, is_active in candidates:
        try:
            resolved = path.resolve()
            if resolved in seen or not resolved.is_file():
                continue
            seen.add(resolved)
            stat = resolved.stat()
        except OSError:
            continue
        records.append(RuntimeLogFile(resolved, is_active, stat.st_size, stat.st_mtime))
    records.sort(key=lambda item: (not item.active, -item.modified, item.path.name.lower()))
    return records


def read_log_text(path: Path, *, max_bytes: int | None = None) -> str:
    resolved = path.expanduser().resolve()
    if max_bytes is None:
        return resolved.read_text(encoding="utf-8", errors="replace")
    with resolved.open("rb") as handle:
        size = resolved.stat().st_size
        if size > max_bytes:
            handle.seek(size - max_bytes)
        data = handle.read()
    return data.decode("utf-8", errors="replace")


def search_runtime_logs(root: Path, query: str) -> list[RuntimeLogFile]:
    records = list_runtime_logs(root)
    needle = query.strip().casefold()
    if not needle:
        return records
    matches: list[RuntimeLogFile] = []
    for record in records:
        if needle in record.path.name.casefold():
            matches.append(record)
            continue
        try:
            text = read_log_text(record.path, max_bytes=2 * 1024 * 1024)
        except OSError:
            continue
        if needle in text.casefold():
            matches.append(record)
    return matches


def delete_runtime_log(root: Path, path: Path) -> bool:
    root = root.expanduser().resolve()
    candidate = path.expanduser().resolve()
    active = active_log_path(root).resolve()
    archive_root = logs_directory(root).resolve()
    if candidate == active:
        raise ValueError("The active runtime log cannot be deleted. Use Clear output to rotate it safely.")
    try:
        candidate.relative_to(archive_root)
    except ValueError as exc:
        raise ValueError("Only archived logs inside this installation may be deleted.") from exc
    try:
        candidate.unlink()
    except FileNotFoundError:
        return False
    return True


def load_log_retention() -> LogRetention:
    payload = manager_config().get("logs", {})
    mode = str(payload.get("retention_mode", "monthly")).strip().lower()
    if mode not in {*RETENTION_PRESETS, "custom"}:
        mode = "monthly"
    try:
        days = max(1, int(payload.get("retention_days", RETENTION_PRESETS.get(mode, 30))))
    except (TypeError, ValueError):
        days = 30
    return LogRetention(
        mode=mode,
        days=days,
        auto_cleanup=bool(payload.get("auto_cleanup", True)),
    )


def save_log_retention(retention: LogRetention) -> Path:
    payload = manager_config()
    payload["logs"] = {
        "auto_cleanup": bool(retention.auto_cleanup),
        "retention_mode": retention.mode,
        "retention_days": retention.effective_days if retention.mode != "custom" else max(1, retention.days),
    }
    return save_editable_config("manager-config.yaml", payload)


def cleanup_runtime_logs(root: Path, retention: LogRetention | None = None, *, now: float | None = None) -> list[Path]:
    policy = retention or load_log_retention()
    if not policy.auto_cleanup:
        return []
    cutoff = (time.time() if now is None else now) - policy.effective_days * 86400
    removed: list[Path] = []
    for record in list_runtime_logs(root):
        if record.active or record.modified >= cutoff:
            continue
        try:
            record.path.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            continue
        removed.append(record.path)
    return removed


def cleanup_all_installation_logs(roots: Iterable[Path], retention: LogRetention | None = None) -> list[Path]:
    removed: list[Path] = []
    for root in roots:
        removed.extend(cleanup_runtime_logs(root, retention))
    return removed
