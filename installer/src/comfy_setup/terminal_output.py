from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TerminalStreamDecoder:
    """Convert terminal byte streams into stable, plain-text log lines.

    The decoder removes ANSI/OSC control sequences, applies backspaces, and
    converts carriage-return progress updates into ordinary readable lines.
    It is deliberately stateful so escape sequences split across file reads do
    not leak fragments such as ``[31m`` into the Textual log.
    """

    encoding: str = "utf-8"
    _line: list[str] = field(default_factory=list)
    _state: str = "normal"
    _last_emitted: str | None = None

    def reset(self) -> None:
        self._line.clear()
        self._state = "normal"
        self._last_emitted = None

    def feed(self, data: bytes | str) -> list[str]:
        text = data.decode(self.encoding, errors="replace") if isinstance(data, bytes) else data
        emitted: list[str] = []

        for character in text:
            if self._state == "escape":
                if character == "[":
                    self._state = "csi"
                elif character == "]":
                    self._state = "osc"
                else:
                    self._state = "normal"
                continue

            if self._state == "csi":
                # Final CSI bytes are in the inclusive range @ through ~.
                if "@" <= character <= "~":
                    self._state = "normal"
                continue

            if self._state == "osc":
                if character == "\a":
                    self._state = "normal"
                elif character == "\x1b":
                    self._state = "osc_escape"
                continue

            if self._state == "osc_escape":
                self._state = "normal" if character == "\\" else "osc"
                continue

            if character == "\x1b":
                self._state = "escape"
                continue
            if character == "\b":
                if self._line:
                    self._line.pop()
                continue
            if character == "\r":
                self._emit_current(emitted)
                continue
            if character == "\n":
                self._emit_current(emitted, include_empty=True)
                continue
            if character == "\t":
                self._line.append("    ")
                continue
            if ord(character) < 32 or ord(character) == 127:
                continue
            self._line.append(character)

        return emitted

    def flush(self) -> list[str]:
        emitted: list[str] = []
        self._emit_current(emitted)
        return emitted

    def _emit_current(self, emitted: list[str], *, include_empty: bool = False) -> None:
        line = "".join(self._line).rstrip()
        self._line.clear()
        if not line and not include_empty:
            return
        # Repeated carriage-return progress updates often produce identical
        # records. Suppress exact duplicates while preserving real blank lines.
        if line and line == self._last_emitted:
            return
        emitted.append(line)
        if line:
            self._last_emitted = line


def clean_terminal_lines(text: bytes | str) -> list[str]:
    decoder = TerminalStreamDecoder()
    lines = decoder.feed(text)
    lines.extend(decoder.flush())
    return lines


def read_log_tail(path: Path, *, max_bytes: int = 512 * 1024) -> tuple[list[str], int]:
    """Read a sanitized tail and return it with the current byte offset."""

    try:
        size = path.stat().st_size
    except OSError:
        return [], 0

    start = max(0, size - max_bytes)
    try:
        with path.open("rb") as handle:
            handle.seek(start)
            data = handle.read()
    except OSError:
        return [], 0

    # If the read starts in the middle of a line, discard that fragment.
    if start and b"\n" in data:
        data = data.split(b"\n", 1)[1]
    decoder = TerminalStreamDecoder()
    lines = decoder.feed(data)
    lines.extend(decoder.flush())
    return lines, size
