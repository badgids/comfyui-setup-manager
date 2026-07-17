from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from .configuration import load_editable_config

AGENT_CONFIG_FILE = "agents-skills.yaml"


class AgentSkillError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AgentSkill:
    id: str
    name: str
    description: str
    path: Path


def _project_root() -> Path | None:
    configured = os.environ.get("COMFYUI_SETUP_PROJECT_ROOT")
    if configured:
        root = Path(configured).expanduser().resolve()
        if root.is_dir():
            return root
    try:
        root = Path(__file__).resolve().parents[3]
    except IndexError:
        return None
    return root if (root / "skills").is_dir() else None


def bundled_skills_root() -> Path:
    root = _project_root()
    if root is not None:
        return root / "skills"
    packaged = resources.files("comfy_setup").joinpath("skills")
    return Path(packaged)


def _frontmatter(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AgentSkillError(f"Could not read skill metadata: {path}: {exc}") from exc
    if not text.startswith("---\n"):
        raise AgentSkillError(f"Skill is missing YAML frontmatter: {path}")
    end = text.find("\n---", 4)
    if end < 0:
        raise AgentSkillError(f"Skill frontmatter is not closed: {path}")
    try:
        payload = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as exc:
        raise AgentSkillError(f"Invalid skill frontmatter in {path}: {exc}") from exc
    return payload if isinstance(payload, dict) else {}


def list_bundled_skills() -> list[AgentSkill]:
    root = bundled_skills_root()
    if not root.is_dir():
        return []
    output: list[AgentSkill] = []
    for directory in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        manifest = directory / "SKILL.md"
        if not directory.is_dir() or not manifest.is_file():
            continue
        payload = _frontmatter(manifest)
        skill_id = directory.name
        output.append(
            AgentSkill(
                id=skill_id,
                name=str(payload.get("name") or skill_id),
                description=str(payload.get("description") or "ComfyUI Setup Manager agent skill."),
                path=directory,
            )
        )
    return output


def load_agent_skill_config() -> dict[str, Any]:
    return load_editable_config(AGENT_CONFIG_FILE)


def configured_agent_targets() -> dict[str, Path]:
    payload = load_agent_skill_config()
    agents = payload.get("agents", {}) if isinstance(payload, dict) else {}
    output: dict[str, Path] = {}
    if not isinstance(agents, dict):
        return output
    for agent_id, record in agents.items():
        if not isinstance(record, dict) or not record.get("enabled", True):
            continue
        raw = record.get("skills_directory")
        if not raw:
            continue
        expanded = os.path.expandvars(str(raw))
        if "$" in expanded or "%" in expanded:
            fallback = record.get("fallback_skills_directory")
            if not fallback:
                continue
            expanded = os.path.expandvars(str(fallback))
        output[str(agent_id)] = Path(expanded).expanduser().resolve()
    return output


def install_skill(
    skill_id: str,
    agent_id: str,
    *,
    target_root: Path | None = None,
    overwrite: bool = False,
) -> Path:
    skills = {item.id: item for item in list_bundled_skills()}
    skill = skills.get(skill_id)
    if skill is None:
        raise AgentSkillError(f"Bundled skill not found: {skill_id}")
    if target_root is None:
        target_root = configured_agent_targets().get(agent_id)
    if target_root is None:
        raise AgentSkillError(f"No skills directory is configured for agent: {agent_id}")
    root = target_root.expanduser().resolve()
    destination = (root / skill.id).resolve()
    if root != destination.parent:
        raise AgentSkillError("Unsafe skill destination.")
    root.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if not overwrite:
            raise AgentSkillError(f"Skill is already installed: {destination}")
        if destination.is_dir():
            shutil.rmtree(destination)
        else:
            destination.unlink()
    shutil.copytree(skill.path, destination)
    return destination
