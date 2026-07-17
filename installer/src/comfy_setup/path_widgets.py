from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Literal

from textual import on
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.suggester import Suggester
from textual.widgets import Button, DirectoryTree, Static

from .selectable_widgets import Input

from .configuration import project_root_directory

PathMode = Literal["file", "directory", "any"]


def _expand_path(value: str) -> Path:
    return Path(os.path.expandvars(value)).expanduser()


def _typed_parent(value: str) -> str:
    slash = max(value.rfind("/"), value.rfind("\\"))
    return value[: slash + 1] if slash >= 0 else ""


class FilesystemPathSuggester(Suggester):
    """Suggest the first matching filesystem entry for a path input.

    Textual accepts the visible suggestion with the normal completion key. The
    lookup deliberately avoids recursive scanning and only reads one directory,
    keeping completion responsive even in large installations.
    """

    def __init__(self) -> None:
        super().__init__(use_cache=False, case_sensitive=True)

    async def get_suggestion(self, value: str) -> str | None:
        raw = value.strip()
        if not raw or raw.startswith(("http://", "https://", "git@")):
            return None

        expanded = os.path.expandvars(os.path.expanduser(raw))
        trailing_separator = raw.endswith(("/", "\\"))
        parent_text = expanded if trailing_separator else os.path.dirname(expanded) or "."
        prefix = "" if trailing_separator else os.path.basename(expanded)
        parent = Path(parent_text)
        try:
            entries = sorted(
                parent.iterdir(),
                key=lambda item: (not item.is_dir(), item.name.casefold()),
            )
        except (OSError, ValueError):
            return None

        compare_prefix = prefix.casefold() if os.name == "nt" else prefix
        for entry in entries:
            compare_name = entry.name.casefold() if os.name == "nt" else entry.name
            if not compare_name.startswith(compare_prefix) or compare_name == compare_prefix:
                continue
            suggestion = _typed_parent(raw) + entry.name
            if entry.is_dir():
                suggestion += os.sep
            return suggestion
        return None


class PathInput(Input):
    """Input with lightweight local filesystem completion."""

    def __init__(self, value: str | None = None, **kwargs: object) -> None:
        kwargs.setdefault("suggester", FilesystemPathSuggester())
        super().__init__(value, **kwargs)  # type: ignore[arg-type]


class PathBrowserScreen(ModalScreen[str | None]):
    """Small, keyboard-friendly TUI file and directory picker."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(
        self,
        value: str,
        *,
        mode: PathMode,
        must_exist: bool,
        extensions: Iterable[str] = (),
        start_path: Path | None = None,
        title: str = "SELECT PATH",
    ) -> None:
        super().__init__()
        self.path_mode = mode
        self.must_exist = must_exist
        self.extensions = tuple(
            extension.lower() if extension.startswith(".") else f".{extension.lower()}"
            for extension in extensions
        )
        self.browser_title = title
        self.initial_value = value.strip()
        self.start_path = self._existing_start(start_path or (_expand_path(value) if value.strip() else Path.cwd()))

    @staticmethod
    def _existing_start(path: Path) -> Path:
        candidate = path.expanduser()
        if candidate.is_file():
            candidate = candidate.parent
        while not candidate.exists() and candidate != candidate.parent:
            candidate = candidate.parent
        return candidate if candidate.is_dir() else Path.cwd()

    def compose(self) -> ComposeResult:
        initial = self.initial_value or str(self.start_path)
        with Container(id="path-browser-dialog"):
            yield Static(self.browser_title, id="path-browser-title")
            yield Static(
                "Use the tree or type a path. Tab accepts an autocomplete suggestion.",
                id="path-browser-help",
            )
            yield DirectoryTree(self.start_path, id="path-browser-tree")
            yield PathInput(initial, id="path-browser-value")
            yield Static("Ready.", id="path-browser-status")
            with Horizontal(classes="path-browser-nav"):
                yield Button("Up", id="path-browser-up")
                yield Button("Home", id="path-browser-home")
                yield Button("Project", id="path-browser-project")
            with Horizontal(classes="path-browser-actions"):
                yield Button("Cancel", id="path-browser-cancel")
                yield Button("Use selected path", id="path-browser-select", classes="primary")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _set_root(self, path: Path) -> None:
        directory = self._existing_start(path)
        tree = self.query_one("#path-browser-tree", DirectoryTree)
        tree.path = directory
        self.query_one("#path-browser-value", Input).value = str(directory)
        self.query_one("#path-browser-status", Static).update(f"Browsing: {directory}")

    @on(DirectoryTree.FileSelected, "#path-browser-tree")
    def file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self.query_one("#path-browser-value", Input).value = str(event.path)
        self.query_one("#path-browser-status", Static).update(f"Selected file: {event.path.name}")

    @on(DirectoryTree.DirectorySelected, "#path-browser-tree")
    def directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        self.query_one("#path-browser-value", Input).value = str(event.path)
        self.query_one("#path-browser-status", Static).update(f"Selected directory: {event.path}")

    @on(Button.Pressed, "#path-browser-up")
    def go_up(self) -> None:
        tree = self.query_one("#path-browser-tree", DirectoryTree)
        self._set_root(Path(tree.path).parent)

    @on(Button.Pressed, "#path-browser-home")
    def go_home(self) -> None:
        self._set_root(Path.home())

    @on(Button.Pressed, "#path-browser-project")
    def go_project(self) -> None:
        self._set_root(project_root_directory())

    @on(Button.Pressed, "#path-browser-cancel")
    def cancel(self) -> None:
        self.dismiss(None)

    def _validate(self, path: Path) -> str | None:
        if self.path_mode == "file":
            if self.must_exist and not path.is_file():
                return "Choose an existing file."
            if not self.must_exist and path.exists() and path.is_dir():
                return "Enter a file name, not only a directory."
        elif self.path_mode == "directory":
            if self.must_exist and not path.is_dir():
                return "Choose an existing directory."
            if path.exists() and not path.is_dir():
                return "Choose a directory, not a file."
        elif self.must_exist and not path.exists():
            return "Choose an existing file or directory."

        if not self.must_exist:
            parent = path if self.path_mode == "directory" else path.parent
            existing_parent = parent
            while not existing_parent.exists() and existing_parent != existing_parent.parent:
                existing_parent = existing_parent.parent
            if not existing_parent.is_dir():
                return "The selected path has no accessible parent directory."

        if self.extensions and self.path_mode != "directory":
            lower_name = path.name.lower()
            if not any(lower_name.endswith(extension) for extension in self.extensions):
                allowed = ", ".join(self.extensions)
                return f"Choose a file ending in: {allowed}"
        return None

    @on(Button.Pressed, "#path-browser-select")
    def choose(self) -> None:
        raw = self.query_one("#path-browser-value", Input).value.strip()
        if not raw:
            self.query_one("#path-browser-status", Static).update("Enter or select a path.")
            return
        path = _expand_path(raw)
        error = self._validate(path)
        if error:
            self.query_one("#path-browser-status", Static).update(error)
            return
        self.dismiss(str(path.resolve(strict=False)))


class PathField(Container):
    """A PathInput paired with a TUI Browse button.

    The input keeps the caller-provided id, so existing ``query_one(..., Input)``
    code continues to work without special accessors.
    """

    def __init__(
        self,
        value: str | None = None,
        *,
        input_id: str,
        placeholder: str = "",
        mode: PathMode = "any",
        must_exist: bool = True,
        extensions: Iterable[str] = (),
        browser_root: Path | None = None,
        browser_title: str = "SELECT PATH",
        disabled: bool = False,
        classes: str | None = None,
    ) -> None:
        super().__init__(classes=f"path-field {classes or ''}".strip())
        self.input_value = value or ""
        self.input_id = input_id
        self.placeholder = placeholder
        self.path_mode = mode
        self.must_exist = must_exist
        self.extensions = tuple(extensions)
        self.browser_root = browser_root
        self.browser_title = browser_title
        self.field_disabled = disabled
        self.browse_id = f"{input_id}-browse"

    def compose(self) -> ComposeResult:
        yield PathInput(
            self.input_value,
            placeholder=self.placeholder,
            id=self.input_id,
            disabled=self.field_disabled,
        )
        yield Button("Browse…", id=self.browse_id, classes="path-browse", disabled=self.field_disabled)

    def set_field_disabled(self, disabled: bool) -> None:
        self.field_disabled = disabled
        self.query_one(f"#{self.input_id}", Input).disabled = disabled
        self.query_one(f"#{self.browse_id}", Button).disabled = disabled

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != self.browse_id:
            return
        event.stop()
        field = self.query_one(f"#{self.input_id}", Input)
        self.app.push_screen(
            PathBrowserScreen(
                field.value,
                mode=self.path_mode,
                must_exist=self.must_exist,
                extensions=self.extensions,
                start_path=self.browser_root,
                title=self.browser_title,
            ),
            self._selected,
        )

    def _selected(self, selected: str | None) -> None:
        if selected:
            field = self.query_one(f"#{self.input_id}", Input)
            field.value = selected
            field.focus()
