from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable

import yaml

from platformdirs import user_config_path
from textual.theme import Theme

from .configuration import ensure_editable_config

APP_NAME = "comfyui-setup-manager"
THEME_STATE_FILE = "themes.yaml"
LEGACY_THEME_STATE_FILE = "themes.json"


class ThemeImportError(ValueError):
    """Raised when a Vim/Neovim colorscheme cannot be converted safely."""


GRUVBOX_THEME = Theme(
    name="gruvbox",
    primary="#d79921",
    secondary="#b8bb26",
    accent="#fe8019",
    foreground="#ebdbb2",
    background="#282828",
    surface="#32302f",
    panel="#3c3836",
    success="#b8bb26",
    warning="#fabd2f",
    error="#fb4934",
    dark=True,
    variables={
        "muted": "#a89984",
        "border": "#665c54",
        "header-bg": "#1d2021",
        "input-bg": "#3c3836",
        "button-text": "#282828",
        "focus": "#fe8019",
        "card-title": "#fabd2f",
        "footer-key-foreground": "#fabd2f",
        "footer-description-foreground": "#a89984",
        "input-selection-background": "#d79921 45%",
    },
)

DARK_THEME = Theme(
    name="setup-dark",
    primary="#60a5fa",
    secondary="#a78bfa",
    accent="#22d3ee",
    foreground="#e5e7eb",
    background="#0b0f14",
    surface="#111827",
    panel="#172033",
    success="#34d399",
    warning="#fbbf24",
    error="#fb7185",
    dark=True,
    variables={
        "muted": "#94a3b8",
        "border": "#334155",
        "header-bg": "#080b10",
        "input-bg": "#111827",
        "button-text": "#07111f",
        "focus": "#22d3ee",
        "card-title": "#67e8f9",
        "footer-key-foreground": "#67e8f9",
        "footer-description-foreground": "#94a3b8",
        "input-selection-background": "#60a5fa 40%",
    },
)

LIGHT_THEME = Theme(
    name="setup-light",
    primary="#2563eb",
    secondary="#7c3aed",
    accent="#0891b2",
    foreground="#1f2937",
    background="#f8fafc",
    surface="#eef2f7",
    panel="#ffffff",
    success="#15803d",
    warning="#b45309",
    error="#b91c1c",
    dark=False,
    variables={
        "muted": "#64748b",
        "border": "#cbd5e1",
        "header-bg": "#e2e8f0",
        "input-bg": "#ffffff",
        "button-text": "#ffffff",
        "focus": "#0891b2",
        "card-title": "#0f5f73",
        "footer-key-foreground": "#0f5f73",
        "footer-description-foreground": "#64748b",
        "input-selection-background": "#2563eb 25%",
    },
)

BUILTIN_THEME_LABELS = {
    "gruvbox": "Gruvbox (default)",
    "setup-dark": "Dark",
    "setup-light": "Light",
}


def builtin_themes() -> tuple[Theme, ...]:
    return GRUVBOX_THEME, DARK_THEME, LIGHT_THEME


def config_directory() -> Path:
    return user_config_path(APP_NAME, appauthor=False, ensure_exists=True)


def theme_state_path() -> Path:
    return config_directory() / THEME_STATE_FILE


def _theme_to_dict(theme: Theme) -> dict[str, Any]:
    return {
        "name": theme.name,
        "primary": theme.primary,
        "secondary": theme.secondary,
        "warning": theme.warning,
        "error": theme.error,
        "success": theme.success,
        "accent": theme.accent,
        "foreground": theme.foreground,
        "background": theme.background,
        "surface": theme.surface,
        "panel": theme.panel,
        "boost": theme.boost,
        "dark": theme.dark,
        "luminosity_spread": theme.luminosity_spread,
        "text_alpha": theme.text_alpha,
        "variables": dict(theme.variables),
        "ansi": theme.ansi,
    }


def _theme_from_dict(payload: dict[str, Any]) -> Theme:
    allowed = {
        "name",
        "primary",
        "secondary",
        "warning",
        "error",
        "success",
        "accent",
        "foreground",
        "background",
        "surface",
        "panel",
        "boost",
        "dark",
        "luminosity_spread",
        "text_alpha",
        "variables",
        "ansi",
    }
    values = {key: value for key, value in payload.items() if key in allowed}
    if not values.get("name") or not values.get("primary"):
        raise ThemeImportError("Saved theme is missing its name or primary color.")
    return Theme(**values)


def load_theme_state() -> tuple[str, list[Theme]]:
    path = theme_state_path()
    legacy = config_directory() / LEGACY_THEME_STATE_FILE
    if not path.is_file() and legacy.is_file():
        try:
            import json
            payload = json.loads(legacy.read_text(encoding="utf-8"))
            path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
        except Exception:
            pass
    if not path.is_file():
        try:
            path = ensure_editable_config(THEME_STATE_FILE)
        except Exception:
            return "gruvbox", []
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        selected = str(payload.get("selected") or "gruvbox")
        custom = [_theme_from_dict(item) for item in payload.get("custom", [])]
        return selected, custom
    except Exception:
        return "gruvbox", []


def save_theme_state(selected: str, custom: Iterable[Theme]) -> Path:
    path = theme_state_path()
    payload = {
        "schema_version": 1,
        "selected": selected,
        "custom": [_theme_to_dict(theme) for theme in custom],
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
    return path


def display_name(theme_name: str) -> str:
    if theme_name in BUILTIN_THEME_LABELS:
        return BUILTIN_THEME_LABELS[theme_name].replace(" (default)", "")
    return theme_name.removeprefix("vim-").replace("-", " ").title()


def _hex(value: int) -> str:
    return f"#{value & 0xFFFFFF:06x}"


def _rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))


def _blend(left: str, right: str, amount: float) -> str:
    a = _rgb(left)
    b = _rgb(right)
    values = [round(x + (y - x) * amount) for x, y in zip(a, b)]
    return "#" + "".join(f"{value:02x}" for value in values)


def _is_dark(color: str) -> bool:
    red, green, blue = _rgb(color)
    luminance = (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255
    return luminance < 0.52


VIM_NAMED_COLORS = {
    "black": "#000000",
    "darkblue": "#000080",
    "darkgreen": "#008000",
    "darkcyan": "#008080",
    "darkred": "#800000",
    "darkmagenta": "#800080",
    "brown": "#808000",
    "darkyellow": "#808000",
    "lightgray": "#c0c0c0",
    "lightgrey": "#c0c0c0",
    "gray": "#808080",
    "grey": "#808080",
    "darkgray": "#808080",
    "darkgrey": "#808080",
    "blue": "#0000ff",
    "lightblue": "#add8e6",
    "green": "#00ff00",
    "lightgreen": "#90ee90",
    "cyan": "#00ffff",
    "lightcyan": "#e0ffff",
    "red": "#ff0000",
    "lightred": "#ff8080",
    "magenta": "#ff00ff",
    "lightmagenta": "#ff80ff",
    "yellow": "#ffff00",
    "lightyellow": "#ffffe0",
    "white": "#ffffff",
}


def _xterm_color(index: int) -> str:
    ansi = [
        "#000000", "#800000", "#008000", "#808000", "#000080", "#800080", "#008080", "#c0c0c0",
        "#808080", "#ff0000", "#00ff00", "#ffff00", "#0000ff", "#ff00ff", "#00ffff", "#ffffff",
    ]
    if index < 16:
        return ansi[max(0, index)]
    if index < 232:
        value = index - 16
        red, green, blue = value // 36, (value % 36) // 6, value % 6
        levels = [0, 95, 135, 175, 215, 255]
        return f"#{levels[red]:02x}{levels[green]:02x}{levels[blue]:02x}"
    gray = 8 + (max(232, min(index, 255)) - 232) * 10
    return f"#{gray:02x}{gray:02x}{gray:02x}"


def _normalize_color(value: str | int | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, int):
        return _hex(value)
    text = str(value).strip().strip("'").strip('"')
    if not text or text.lower() in {"none", "bg", "fg"}:
        return None
    if re.fullmatch(r"#[0-9a-fA-F]{6}", text):
        return text.lower()
    if re.fullmatch(r"#[0-9a-fA-F]{3}", text):
        return "#" + "".join(ch * 2 for ch in text[1:].lower())
    if text.isdigit():
        return _xterm_color(int(text))
    return VIM_NAMED_COLORS.get(text.lower())


def _first(groups: dict[str, dict[str, str | int]], names: Iterable[str], key: str) -> str | None:
    for name in names:
        group = groups.get(name.lower())
        if not group:
            continue
        value = _normalize_color(group.get(key))
        if value:
            return value
    return None


def _theme_from_groups(groups: dict[str, dict[str, str | int]], source_name: str) -> Theme:
    normal = groups.get("normal", {})
    foreground = _normalize_color(normal.get("fg")) or "#d8dee9"
    background = _normalize_color(normal.get("bg")) or "#20242b"
    dark = _is_dark(background)

    primary = _first(groups, ("function", "identifier", "statement", "keyword"), "fg") or (
        "#88c0d0" if dark else "#2563eb"
    )
    secondary = _first(groups, ("type", "preproc", "constant", "number"), "fg") or (
        "#a3be8c" if dark else "#7c3aed"
    )
    accent = _first(groups, ("special", "operator", "title", "delimiter"), "fg") or primary
    success = _first(groups, ("diffadd", "string", "healthsuccess"), "fg") or secondary
    warning = _first(groups, ("warningmsg", "todo", "diagnosticwarn"), "fg") or (
        "#ebcb8b" if dark else "#b45309"
    )
    error = _first(groups, ("error", "errormsg", "diagnosticerror", "diffdelete"), "fg") or (
        "#bf616a" if dark else "#b91c1c"
    )
    visual = _first(groups, ("visual",), "bg")
    surface = _blend(background, foreground, 0.06 if dark else 0.035)
    panel = _blend(background, foreground, 0.11 if dark else 0.07)
    border = _blend(background, foreground, 0.28 if dark else 0.22)
    muted = _blend(foreground, background, 0.38)
    safe_name = re.sub(r"[^a-z0-9]+", "-", source_name.lower()).strip("-") or "custom"

    return Theme(
        name=f"vim-{safe_name}",
        primary=primary,
        secondary=secondary,
        accent=accent,
        foreground=foreground,
        background=background,
        surface=surface,
        panel=panel,
        success=success,
        warning=warning,
        error=error,
        dark=dark,
        variables={
            "muted": muted,
            "border": border,
            "header-bg": _blend(background, foreground, 0.025 if dark else 0.05),
            "input-bg": surface,
            "button-text": background if dark else "#ffffff",
            "focus": accent,
            "card-title": primary,
            "footer-key-foreground": accent,
            "footer-description-foreground": muted,
            "input-selection-background": f"{visual or primary} 40%",
            "vim-colorscheme": source_name,
        },
    )


def parse_vim_colorscheme(path: Path) -> Theme:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise ThemeImportError(f"Vim colorscheme does not exist: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    groups: dict[str, dict[str, str | int]] = {}
    links: dict[str, str] = {}
    highlight = re.compile(r"^\s*(?:hi|highlight)(?:!)?\s+(?:default\s+)?([\w@.-]+)\s+(.+)$", re.I)
    link = re.compile(r"^\s*(?:hi|highlight)(?:!)?\s+(?:default\s+)?link\s+([\w@.-]+)\s+([\w@.-]+)", re.I)
    attribute = re.compile(r"\b(gui|cterm)(fg|bg)=([^\s]+)", re.I)

    for raw_line in text.splitlines():
        line = raw_line.split('"', 1)[0].strip()
        if not line:
            continue
        match = link.match(line)
        if match:
            links[match.group(1).lower()] = match.group(2).lower()
            continue
        match = highlight.match(line)
        if not match:
            continue
        name, body = match.group(1).lower(), match.group(2)
        values = groups.setdefault(name, {})
        attrs = {(kind.lower(), channel.lower()): value for kind, channel, value in attribute.findall(body)}
        for channel in ("fg", "bg"):
            value = attrs.get(("gui", channel)) or attrs.get(("cterm", channel))
            if value is not None:
                values[channel] = value

    for group, target in links.items():
        visited = set()
        while target in links and target not in visited:
            visited.add(target)
            target = links[target]
        if target in groups and group not in groups:
            groups[group] = dict(groups[target])

    if "normal" not in groups:
        raise ThemeImportError(
            f"{path.name} does not expose a parseable 'highlight Normal' definition. "
            "Try importing it by colorscheme name with Neovim installed."
        )
    return _theme_from_groups(groups, path.stem)


def _common_color_paths(name: str) -> list[Path]:
    filename = name if name.endswith(".vim") else f"{name}.vim"
    home = Path.home()
    paths = [
        home / ".vim" / "colors" / filename,
        home / ".config" / "nvim" / "colors" / filename,
        home / ".local" / "share" / "nvim" / "site" / "colors" / filename,
    ]
    vimruntime = os.environ.get("VIMRUNTIME")
    if vimruntime:
        paths.append(Path(vimruntime) / "colors" / filename)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        base = Path(local)
        paths.extend([
            base / "nvim" / "colors" / filename,
            base / "nvim-data" / "site" / "colors" / filename,
        ])
    for base in (Path("/usr/share/vim"), Path("/usr/local/share/vim"), Path("/opt/homebrew/share/vim")):
        if base.is_dir():
            paths.extend(sorted(base.glob(f"vim*/colors/{filename}"), reverse=True))
    return paths


def _query_neovim(name: str) -> Theme | None:
    executable = shutil.which("nvim")
    if not executable or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        return None
    groups = [
        "Normal", "Function", "Identifier", "Statement", "Keyword", "Type", "PreProc",
        "Constant", "Number", "Special", "Operator", "Title", "Delimiter", "DiffAdd",
        "String", "WarningMsg", "Todo", "DiagnosticWarn", "Error", "ErrorMsg",
        "DiagnosticError", "DiffDelete", "Visual",
    ]
    lua_names = "{" + ",".join(json.dumps(group) for group in groups) + "}"
    lua = (
        "local ok,err=pcall(vim.cmd.colorscheme," + json.dumps(name) + ");"
        "if not ok then io.stderr:write(tostring(err)); vim.cmd('cq') end;"
        "local names=" + lua_names + "; local out={};"
        "for _,g in ipairs(names) do out[g]=vim.api.nvim_get_hl(0,{name=g,link=false}) end;"
        "print(vim.json.encode(out))"
    )
    try:
        result = subprocess.run(
            [executable, "--headless", "-c", f"lua {lua}", "-c", "qa!"],
            text=True,
            capture_output=True,
            timeout=12,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    payload: dict[str, Any] | None = None
    for line in reversed(result.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if not payload:
        return None
    normalized: dict[str, dict[str, str | int]] = {}
    for group_name, values in payload.items():
        if not isinstance(values, dict):
            continue
        normalized[group_name.lower()] = {
            key: value for key, value in values.items() if key in {"fg", "bg"}
        }
    if not normalized.get("normal"):
        return None
    return _theme_from_groups(normalized, name)


def _query_vim(name: str) -> Theme | None:
    executable = shutil.which("vim")
    if not executable or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        return None
    groups = [
        "Normal", "Function", "Identifier", "Statement", "Keyword", "Type", "PreProc",
        "Constant", "Number", "Special", "Operator", "Title", "Delimiter", "DiffAdd",
        "String", "WarningMsg", "Todo", "Error", "ErrorMsg", "DiffDelete", "Visual",
    ]
    import tempfile

    try:
        with tempfile.TemporaryDirectory(prefix="comfyui-theme-") as temp:
            output = Path(temp) / "highlights.txt"
            commands = [executable, "-n", "-es"]
            commands.extend(["-c", f"colorscheme {name}"])
            commands.extend(["-c", f"redir! > {output}"])
            for group in groups:
                commands.extend(["-c", f"silent highlight {group}"])
            commands.extend(["-c", "redir END", "-c", "qa!"])
            result = subprocess.run(
                commands,
                text=True,
                capture_output=True,
                timeout=12,
                check=False,
            )
            if result.returncode != 0 or not output.is_file():
                return None
            text = output.read_text(encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return None

    parsed: dict[str, dict[str, str | int]] = {}
    links: dict[str, str] = {}
    line_pattern = re.compile(r"^\s*([\w@.-]+)\s+xxx\s+(.+)$", re.I)
    attribute = re.compile(r"\b(gui|cterm)(fg|bg)=([^\s]+)", re.I)
    for raw in text.splitlines():
        match = line_pattern.match(raw)
        if not match:
            continue
        group, body = match.group(1).lower(), match.group(2)
        linked = re.search(r"links to\s+([\w@.-]+)", body, re.I)
        if linked:
            links[group] = linked.group(1).lower()
            continue
        attrs = {(kind.lower(), channel.lower()): value for kind, channel, value in attribute.findall(body)}
        values: dict[str, str | int] = {}
        for channel in ("fg", "bg"):
            value = attrs.get(("gui", channel)) or attrs.get(("cterm", channel))
            if value is not None:
                values[channel] = value
        if values:
            parsed[group] = values
    for group, target in links.items():
        if target in parsed and group not in parsed:
            parsed[group] = dict(parsed[target])
    if "normal" not in parsed:
        return None
    return _theme_from_groups(parsed, name)


def import_vim_theme(source: str) -> Theme:
    value = source.strip()
    if not value:
        raise ThemeImportError("Enter a Vim/Neovim colorscheme name or a .vim file path.")
    candidate = Path(os.path.expandvars(os.path.expanduser(value)))
    if candidate.is_file():
        return parse_vim_colorscheme(candidate)

    name = value.removesuffix(".vim")
    queried = _query_neovim(name) or _query_vim(name)
    if queried is not None:
        return queried

    for path in _common_color_paths(name):
        if path.is_file():
            return parse_vim_colorscheme(path)

    searched = "\n".join(f"  - {path}" for path in _common_color_paths(name)[:8])
    raise ThemeImportError(
        f"Could not find or load the Vim/Neovim colorscheme '{value}'.\n"
        "Install it in Vim/Neovim, provide its .vim file directly, or use one of the built-in themes.\n"
        f"Searched examples:\n{searched}"
    )
