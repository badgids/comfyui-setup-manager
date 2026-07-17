# ComfyUI Setup Manager agent skills

These portable Agent Skills teach coding agents how to operate, maintain, and release ComfyUI Setup Manager. Each skill is a self-contained directory with a `SKILL.md` manifest.

The TUI **Agents & Skills** tab and the `skills` CLI commands can list and copy them into a configured agent skills directory. Default destinations are editable in `config/agents-skills.yaml`.

Bundled skills:

- `comfyui-setup-manager-operator`: use the manager safely through its CLI and TUI.
- `comfyui-setup-manager-developer`: modify the codebase while following `PRD.md`, architecture rules, and tests.
- `comfyui-setup-manager-release`: validate metadata, build the wheel, and prepare release artifacts.

The canonical copies live in this directory. Packaged copies under `installer/src/comfy_setup/skills/` allow the skills to remain available when the manager is installed as a wheel.
