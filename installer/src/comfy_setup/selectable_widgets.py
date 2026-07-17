from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

from rich.style import Style
from rich.text import Text
from textual.binding import Binding
from textual._text_area_theme import TextAreaTheme
from textual.widgets import Input as TextualInput
from textual.widgets import TextArea as TextualTextArea

from .terminal_output import clean_terminal_lines


ACTIVE_TEXT_THEME = "comfy-setup-active"

LOG_ERROR_RE = re.compile(
    r"(?:\berror\b|\bfailed\b|\bfailure\b|traceback|exception|fatal|❌|\b×\b)", re.I
)
LOG_WARNING_RE = re.compile(r"(?:\bwarn(?:ing)?\b|deprecated|retry|attention|⚠)", re.I)
LOG_SUCCESS_RE = re.compile(
    r"(?:\bsuccess(?:ful(?:ly)?)?\b|\bcomplete(?:d)?\b|\bpassed\b|\bready\b|✓|✅)", re.I
)
LOG_COMMAND_RE = re.compile(r"^\s*(?:[$>#]|(?:python|python3|pip|uv|git|comfyui-setup-manager)\b)", re.I)
LOG_TIMESTAMP_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ][0-9:.+-Z]+)?\b|\[[0-9:., -]+\]")
LOG_PACKAGE_RE = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9_.-]*(?:==|!=|~=|>=|<=|>|<)[^\s,;]+")
INVENTORY_IDENTIFIER_RE = re.compile(r"^\s*([^\s].*?)(?=\s{2,}|\s+[—–]\s+|$)")
INVENTORY_SIZE_RE = re.compile(r"\([^()]*\b(?:bytes?|KiB|MiB|GiB|KB|MB|GB)\b[^()]*\)", re.I)
INVENTORY_STATE_RE = re.compile(
    r"\[[^\]]*(?:enabled|installed|active|current|verified|exact)[^\]]*\]", re.I
)
INVENTORY_SOURCE_RE = re.compile(r"(?<=[—–])\s+.*$")
PATH_RE = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|~?[\\/])[^\s\])}>]+")
URL_RE = re.compile(r"https?://[^\s\])}>]+")


def _theme_color(theme: Any, name: str, fallback: str) -> str:
    value = getattr(theme, name, None)
    return str(value or fallback)


def text_area_theme(theme: Any) -> TextAreaTheme:
    """Build syntax colors from the currently selected application theme.

    Textual's stock editor themes are fixed palettes.  The manager instead
    derives every syntax role from the active setup theme so imported Vim
    colorschemes and the built-in light/dark themes also color logs, inventory
    panes, YAML, JSON, and source files consistently.
    """

    foreground = _theme_color(theme, "foreground", "#e5e7eb")
    muted = str(getattr(theme, "variables", {}).get("muted") or foreground)
    primary = _theme_color(theme, "primary", foreground)
    secondary = _theme_color(theme, "secondary", primary)
    accent = _theme_color(theme, "accent", primary)
    success = _theme_color(theme, "success", secondary)
    warning = _theme_color(theme, "warning", accent)
    error = _theme_color(theme, "error", warning)
    syntax_styles = {
        "string": Style(color=success),
        "string.documentation": Style(color=success),
        "comment": Style(color=muted, italic=True),
        "heading.marker": Style(color=muted),
        "keyword": Style(color=secondary, bold=True),
        "conditional": Style(color=secondary, bold=True),
        "keyword.function": Style(color=secondary, bold=True),
        "keyword.return": Style(color=secondary, bold=True),
        "keyword.operator": Style(color=secondary),
        "repeat": Style(color=secondary, bold=True),
        "exception": Style(color=error, bold=True),
        "include": Style(color=secondary),
        "operator": Style(color=foreground),
        "number": Style(color=warning),
        "float": Style(color=warning),
        "class": Style(color=accent, bold=True),
        "type": Style(color=accent),
        "type.class": Style(color=accent, bold=True),
        "type.builtin": Style(color=accent),
        "function": Style(color=primary),
        "function.call": Style(color=primary),
        "method": Style(color=primary),
        "method.call": Style(color=primary),
        "constructor": Style(color=primary),
        "boolean": Style(color=secondary, bold=True),
        "constant.builtin": Style(color=secondary, bold=True),
        "json.null": Style(color=muted, italic=True),
        "tag": Style(color=accent),
        "yaml.field": Style(color=primary, bold=True),
        "json.label": Style(color=primary, bold=True),
        "toml.type": Style(color=primary, bold=True),
        "toml.datetime": Style(color=warning),
        "css.property": Style(color=primary),
        "heading": Style(color=accent, bold=True),
        "bold": Style(bold=True),
        "italic": Style(italic=True),
        "strikethrough": Style(strike=True),
        "link.uri": Style(color=secondary, underline=True),
        "link.label": Style(color=primary),
        "list.marker": Style(color=muted),
        "inline_code": Style(color=success),
        "info_string": Style(color=success, bold=True),
        "punctuation.bracket": Style(color=foreground),
        "punctuation.delimiter": Style(color=foreground),
        "punctuation.special": Style(color=accent),
        # Manager-specific semantic roles, populated by SelectableLog.
        "log.error": Style(color=error, bold=True),
        "log.warning": Style(color=warning, bold=True),
        "log.success": Style(color=success),
        "log.timestamp": Style(color=muted),
        "log.command": Style(color=accent, bold=True),
        "log.path": Style(color=primary),
        "log.url": Style(color=secondary, underline=True),
        "log.package": Style(color=accent),
        "inventory.identifier": Style(color=accent, bold=True),
        "inventory.path": Style(color=primary),
        "inventory.size": Style(color=warning),
        "inventory.source": Style(color=secondary),
        "inventory.state": Style(color=success, bold=True),
    }
    return TextAreaTheme(name=ACTIVE_TEXT_THEME, syntax_styles=syntax_styles)


class SelectableInput(TextualInput):
    """Input with conventional desktop selection/copy shortcuts.

    Textual reserves ``Ctrl+A`` for moving to the beginning of an input.  That
    is surprising in a setup manager where paths and commands are commonly
    copied wholesale, so this widget deliberately follows the desktop/editor
    convention on Linux, Windows, and macOS.
    """

    BINDINGS = [
        Binding("ctrl+a,super+a", "select_all", "Select all", show=False, priority=True),
    ]


class SelectableTextArea(TextualTextArea):
    """Text area with Ctrl/Cmd+A in addition to Textual's native selection.

    Textual already implements mouse drag selection, Shift+Arrow selection,
    Ctrl/Cmd+C, and clipboard integration.  This subclass only normalizes the
    select-all shortcut so every editable and read-only text field behaves the
    same way.
    """

    BINDINGS = [
        Binding("ctrl+a,super+a", "select_all", "Select all", show=False, priority=True),
    ]

    def __init__(self, text: str = "", *, syntax_mode: str | None = None, **kwargs: Any) -> None:
        self.syntax_mode = syntax_mode
        super().__init__(text, **kwargs)

    def _sync_application_theme(self) -> None:
        if not self.is_attached:
            return
        self.register_theme(text_area_theme(self.app.current_theme))
        # Re-applying the same named TextArea theme is intentional: its palette
        # may have changed because the user selected another setup theme.
        self._set_theme(ACTIVE_TEXT_THEME)
        self.theme = ACTIVE_TEXT_THEME
        self._line_cache.clear()
        self.refresh()

    def on_mount(self) -> None:
        super().on_mount()
        self._sync_application_theme()

    def _app_theme_changed(self) -> None:
        self._sync_application_theme()

    @staticmethod
    def _byte_span(line: str, start: int, end: int) -> tuple[int, int]:
        return len(line[:start].encode("utf-8")), len(line[:end].encode("utf-8"))

    def _add_pattern(self, row: int, line: str, pattern: re.Pattern[str], style: str) -> None:
        for match in pattern.finditer(line):
            start, end = self._byte_span(line, match.start(), match.end())
            self._highlights[row].append((start, end, style))

    def _build_highlight_map(self) -> None:
        super()._build_highlight_map()
        if self.syntax_mode not in {"log", "inventory"}:
            return
        for row, line in enumerate(self.text.split("\n")):
            encoded_length = len(line.encode("utf-8"))
            if self.syntax_mode == "log":
                if LOG_ERROR_RE.search(line):
                    self._highlights[row].append((0, encoded_length, "log.error"))
                elif LOG_WARNING_RE.search(line):
                    self._highlights[row].append((0, encoded_length, "log.warning"))
                elif LOG_SUCCESS_RE.search(line):
                    self._highlights[row].append((0, encoded_length, "log.success"))
                if LOG_COMMAND_RE.match(line):
                    self._highlights[row].append((0, encoded_length, "log.command"))
                self._add_pattern(row, line, LOG_TIMESTAMP_RE, "log.timestamp")
                self._add_pattern(row, line, LOG_PACKAGE_RE, "log.package")
            else:
                match = INVENTORY_IDENTIFIER_RE.match(line)
                if match:
                    start, end = self._byte_span(line, match.start(1), match.end(1))
                    self._highlights[row].append((start, end, "inventory.identifier"))
                self._add_pattern(row, line, INVENTORY_SIZE_RE, "inventory.size")
                self._add_pattern(row, line, INVENTORY_STATE_RE, "inventory.state")
                self._add_pattern(row, line, INVENTORY_SOURCE_RE, "inventory.source")
            self._add_pattern(
                row,
                line,
                PATH_RE,
                "inventory.path" if self.syntax_mode == "inventory" else "log.path",
            )
            # URL styling is applied last so its // segment is not mistaken for
            # a Unix path by the more general path matcher.
            self._add_pattern(row, line, URL_RE, "log.url")


class SelectableLog(SelectableTextArea):
    """Read-only, selectable replacement for :class:`textual.widgets.RichLog`.

    Console and inventory output must be scrollable, mouse-selectable, and
    keyboard-selectable.  RichLog is excellent for rendering but intentionally
    has no text-selection model.  This compatibility widget exposes the small
    RichLog API used by the application (``write``/``clear``/``scroll_end``)
    while storing plain terminal text in TextArea's selectable document.
    """

    def __init__(
        self,
        text: str = "",
        *,
        wrap: bool = False,
        markup: bool = False,
        highlight: bool = False,
        max_lines: int | None = None,
        min_width: int | None = None,
        auto_scroll: bool = False,
        syntax_mode: str = "log",
        **kwargs: Any,
    ) -> None:
        # ``markup`` and ``highlight`` are accepted for drop-in compatibility;
        # subprocess output is intentionally retained as plain text.
        self.markup = bool(markup)
        self.highlight = bool(highlight)
        self.auto_scroll = bool(auto_scroll)
        del min_width
        kwargs.setdefault("read_only", True)
        kwargs.setdefault("show_cursor", True)
        kwargs.setdefault("show_line_numbers", False)
        kwargs.setdefault("soft_wrap", wrap)
        kwargs.setdefault("tab_behavior", "focus")
        kwargs.setdefault("highlight_cursor_line", False)
        super().__init__(text, syntax_mode=syntax_mode, **kwargs)
        self.max_lines = max_lines

    @property
    def lines(self) -> list[Any]:
        """Compatibility view used by existing integrations and tests."""

        return [SimpleNamespace(text=line) for line in self.text.splitlines()]

    @staticmethod
    def _plain(value: Any) -> str:
        if isinstance(value, Text):
            plain = value.plain
        else:
            plain = str(value)
        # The document model contains only sanitized text. Syntax colors live
        # in render-time highlight spans, so clipboard/cache output can never
        # contain ANSI, OSC hyperlinks, Rich markup, or color escape codes.
        return "\n".join(clean_terminal_lines(plain))

    def write(self, value: Any, *, scroll_end: bool = False, **_: Any) -> None:
        """Append one rendered record as selectable plain text."""

        self.write_lines([value], scroll_end=scroll_end)

    def write_lines(self, values: Any, *, scroll_end: bool = False) -> None:
        """Append several records in one syntax-highlighted document edit."""

        incoming: list[str] = []
        for value in values:
            plain = self._plain(value)
            incoming.extend(plain.splitlines() or [""])
        if not incoming:
            return
        addition = "\n".join(incoming)
        if self.text:
            addition = "\n" + addition
        self.insert(addition, self.document.end, maintain_selection_offset=True)
        if self.max_lines and self.document.line_count > self.max_lines:
            excess = self.document.line_count - self.max_lines
            self.replace("", (0, 0), (excess, 0), maintain_selection_offset=True)
        if (scroll_end or self.auto_scroll) and self.selection.is_empty:
            super().scroll_end(animate=False, immediate=True)

    def clear(self) -> None:  # type: ignore[override]
        self.load_text("")


# Friendly aliases used throughout the TUI.
Input = SelectableInput
TextArea = SelectableTextArea
RichLog = SelectableLog


class SelectableDocument(SelectableTextArea):
    """Read-only selectable document used for review and diagnostic pages."""

    def __init__(self, text: str = "", **kwargs: Any) -> None:
        text = self._plain_document(text)
        kwargs.setdefault("read_only", True)
        kwargs.setdefault("show_cursor", True)
        kwargs.setdefault("show_line_numbers", False)
        kwargs.setdefault("soft_wrap", True)
        kwargs.setdefault("tab_behavior", "focus")
        kwargs.setdefault("highlight_cursor_line", False)
        kwargs.setdefault("language", "markdown")
        super().__init__(text, **kwargs)

    @staticmethod
    def _plain_document(content: Any) -> str:
        value = content.plain if isinstance(content, Text) else str(content)
        return "\n".join(clean_terminal_lines(value))

    def update(self, content: Any = "") -> None:
        self.load_text(self._plain_document(content))


Markdown = SelectableDocument
