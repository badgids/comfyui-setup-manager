from __future__ import annotations

import pytest
pytest.importorskip("textual")

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from rich.style import Style

from comfy_setup.app import ComfySetupApp, HomeScreen, ThemeScreen
from comfy_setup.selectable_widgets import text_area_theme
from comfy_setup.themes import (
    GRUVBOX_THEME,
    builtin_themes,
    import_vim_theme,
    load_theme_state,
    save_theme_state,
)


def test_text_syntax_palette_is_derived_from_active_theme() -> None:
    palette = text_area_theme(GRUVBOX_THEME)
    assert palette.syntax_styles["log.error"] == Style(color=GRUVBOX_THEME.error, bold=True)
    assert palette.syntax_styles["log.warning"] == Style(color=GRUVBOX_THEME.warning, bold=True)
    assert palette.syntax_styles["log.success"] == Style(color=GRUVBOX_THEME.success)
    assert palette.syntax_styles["yaml.field"] == Style(color=GRUVBOX_THEME.primary, bold=True)


def test_builtin_theme_set_and_default() -> None:
    themes = {theme.name: theme for theme in builtin_themes()}
    assert set(themes) == {"gruvbox", "setup-dark", "setup-light"}
    assert themes["gruvbox"].dark is True
    assert themes["setup-light"].dark is False


def test_vim_colorscheme_file_is_mapped_to_textual_theme() -> None:
    with tempfile.TemporaryDirectory() as temp:
        colors = Path(temp) / "portable.vim"
        colors.write_text(
            """
            highlight Normal guifg=#ebdbb2 guibg=#282828
            highlight Function guifg=#fabd2f
            highlight Type guifg=#b8bb26
            highlight Special guifg=#fe8019
            highlight String guifg=#b8bb26
            highlight WarningMsg guifg=#d79921
            highlight ErrorMsg guifg=#fb4934
            highlight Visual guibg=#504945
            """,
            encoding="utf-8",
        )
        theme = import_vim_theme(str(colors))
        assert theme.name == "vim-portable"
        assert theme.background == "#282828"
        assert theme.foreground == "#ebdbb2"
        assert theme.primary == "#fabd2f"
        assert theme.success == "#b8bb26"
        assert theme.error == "#fb4934"
        assert theme.dark is True


def test_theme_state_round_trip() -> None:
    with tempfile.TemporaryDirectory() as temp, patch.dict(
        os.environ, {"XDG_CONFIG_HOME": temp}, clear=False
    ):
        custom = list(builtin_themes())[:1]
        save_theme_state("gruvbox", custom)
        selected, loaded = load_theme_state()
        assert selected == "gruvbox"
        assert [theme.name for theme in loaded] == ["gruvbox"]


def test_home_is_compact_and_theme_screen_is_reachable() -> None:
    async def exercise() -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"XDG_CONFIG_HOME": temp}, clear=False
        ):
            app = ComfySetupApp()
            async with app.run_test(size=(100, 34)) as pilot:
                await pilot.pause()
                assert isinstance(app.screen, HomeScreen)
                assert app.theme == "gruvbox"
                wizard = app.screen.query_one("#wizard")
                assert wizard.region.height > 0
                assert wizard.virtual_size.height >= wizard.region.height
                # At compact terminal sizes the dashboard is intentionally
                # scrollable. Validate only controls currently inside the
                # viewport; off-screen controls are covered by the dedicated
                # small-window scrolling test.
                for button in app.screen.query("Button"):
                    region = button.region
                    if region.bottom <= 0 or region.y >= 34:
                        continue
                    assert region.x >= 0
                    assert region.right <= 100
                    assert region.bottom <= 34
                    assert region.width >= min(len(str(button.label)) + 2, 12)
                await pilot.press("t")
                await pilot.pause()
                assert isinstance(app.screen, ThemeScreen)
                app.apply_setup_theme("setup-light")
                assert app.theme == "setup-light"

    asyncio.run(exercise())
