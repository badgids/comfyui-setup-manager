from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_tui_has_exact_workspace_order_and_domain_specific_controls() -> None:
    source = (ROOT / "installer" / "src" / "comfy_setup" / "app.py").read_text(encoding="utf-8")
    tabs = [
        'TabPane("Launch & Manage", id="manage-tab")',
        'TabPane("Setup & Install", id="setup-tab")',
        'TabPane("Nodes & Plugins", id="nodes-tab")',
        'TabPane("Workflows", id="workflows-tab")',
        'TabPane("Models", id="models-tab")',
        'TabPane("LoRAs", id="loras-tab")',
        'TabPane("Agents & Skills", id="agents-skills-tab")',
        'TabPane("MCPs", id="mcps-tab")',
    ]
    offsets = [source.index(item) for item in tabs]
    assert offsets == sorted(offsets)
    assert "Installed models and LoRAs" not in source
    assert 'Button("Export model", id="models-export")' in source
    assert 'Button("Export LoRA", id="loras-export")' in source
    assert 'Button("Tasks", id="workflows-tasks")' in source
    assert 'Button("Rescan nodes", id="nodes-refresh"' in source
    assert 'Button("Install skill", id="skills-install"' in source
    assert 'Button("Edit MCP config", id="mcps-edit-config"' in source


def test_tui_uses_cached_state_and_visible_background_scan() -> None:
    source = (ROOT / "installer" / "src" / "comfy_setup" / "app.py").read_text(encoding="utf-8")
    assert "class ScanProgressScreen" in source
    assert "self.runtime_state = load_runtime_state()" in source
    assert '@work(thread=True, exclusive=True, group="runtime-scan")' in source
    constructor = source[source.index("class ComfySetupApp"):source.index("    def reload_profiles", source.index("class ComfySetupApp"))]
    assert "discover_installations(" not in constructor
    assert "detect_platform(" not in constructor


def test_setup_layout_allocates_all_actions_and_keeps_continue_in_flow() -> None:
    css = (ROOT / "installer" / "src" / "comfy_setup" / "styles.tcss").read_text(encoding="utf-8")
    assert ".quick-actions-grid" in css
    assert "grid-size: 4;" in css
    assert "HomeScreen.-medium .quick-actions-card" in css
    assert "HomeScreen.-compact .quick-actions-card" in css
    assert "#setup-layout" in css
    assert ".home-actionbar" in css
