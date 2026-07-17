from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path
from typing import Any

from .runner import Runner


_DEPENDENCY_AUDIT_CODE = textwrap.dedent(
    r"""
    import importlib.metadata as md
    import json
    from packaging.markers import default_environment
    from packaging.requirements import Requirement

    env = default_environment()
    env["extra"] = ""
    issues = []
    for dist in md.distributions():
        owner = dist.metadata.get("Name") or "unknown"
        for raw in dist.requires or []:
            try:
                requirement = Requirement(raw)
            except Exception:
                continue
            try:
                if requirement.marker and not requirement.marker.evaluate(env):
                    continue
            except Exception:
                continue
            try:
                installed = md.version(requirement.name)
            except md.PackageNotFoundError:
                issues.append({
                    "owner": owner,
                    "name": requirement.name,
                    "requirement": str(requirement),
                    "installed": "",
                    "kind": "missing",
                })
                continue
            if requirement.specifier and installed not in requirement.specifier:
                issues.append({
                    "owner": owner,
                    "name": requirement.name,
                    "requirement": str(requirement),
                    "installed": installed,
                    "kind": "incompatible",
                })
    print(json.dumps(issues))
    """
).strip()


def inspect_dependency_issues(python_executable: Path, runner: Runner) -> list[dict[str, str]]:
    completed = runner.capture([str(python_executable), "-c", _DEPENDENCY_AUDIT_CODE])
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise RuntimeError("Could not inspect installed package dependency metadata.") from exc
    if not isinstance(payload, list):
        raise RuntimeError("Dependency audit returned an unexpected result.")
    return [
        {str(key): str(value) for key, value in item.items()}
        for item in payload
        if isinstance(item, dict)
    ]


def missing_module_names(output: str) -> list[str]:
    matches = re.findall(r"No module named ['\"]([^'\"]+)['\"]", output)
    return sorted(set(matches))


def comfyui_startup_failures(output: str) -> list[str]:
    patterns = (
        re.compile(r"Cannot import .*? custom nodes: .*", re.IGNORECASE),
        re.compile(r"\d+(?:\.\d+)? seconds \(IMPORT FAILED\): .*", re.IGNORECASE),
        re.compile(r"Critical Import Error: .*", re.IGNORECASE),
        re.compile(r"Training node missing dependencies: .*", re.IGNORECASE),
    )
    failures: list[str] = []
    for raw in output.splitlines():
        line = raw.strip()
        if any(pattern.search(line) for pattern in patterns):
            failures.append(line)
    return list(dict.fromkeys(failures))


def reconcile_requirements_text(
    text: str,
    verified_versions: dict[str, str],
) -> tuple[str, list[dict[str, str]]]:
    """Reconcile a requirements document with a verified working environment.

    Requirement files describe what a repository *currently requests*; they do
    not prove what was installed in the working source environment.  A portable
    setup exported from a running installation may legitimately contain an
    older compatible package than a newly edited ``requirements.txt``. Passing
    that manifest unchanged together with an exact constraint creates an
    unsatisfiable pair (for example ``comfy-kitchen==0.2.21`` plus a verified
    ``comfy-kitchen==0.2.20`` lock).

    This function retains comments, options, URLs, and already-compatible
    requirements. Only an ordinary package requirement whose specifier rejects
    the verified installed version is replaced with that exact verified
    version. The replacement is explicit and auditable in both the generated
    file and the returned override records.
    """

    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.utils import canonicalize_name

    normalized_versions = {
        canonicalize_name(str(name)): str(version).strip()
        for name, version in verified_versions.items()
        if str(name).strip() and str(version).strip()
    }
    output: list[str] = []
    overrides: list[dict[str, str]] = []

    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            output.append(raw)
            continue

        requirement_text = re.split(r"\s+#", stripped, maxsplit=1)[0].strip()
        try:
            requirement = Requirement(requirement_text)
        except InvalidRequirement:
            output.append(raw)
            continue

        name = canonicalize_name(requirement.name)
        verified = normalized_versions.get(name)
        # Direct references encode provenance, not only a version range. They
        # remain untouched and are resolved by the environment-lock lifecycle.
        if not verified or requirement.url:
            output.append(raw)
            continue
        try:
            accepts_verified = not requirement.specifier or verified in requirement.specifier
        except Exception:
            accepts_verified = True
        if accepts_verified:
            output.append(raw)
            continue

        extras = f"[{','.join(sorted(requirement.extras))}]" if requirement.extras else ""
        replacement = f"{requirement.name}{extras}=={verified}"
        if requirement.marker:
            replacement += f"; {requirement.marker}"
        output.append(
            f"# comfyui-setup-manager: source manifest requested {requirement}; "
            f"the verified working environment used {name}=={verified}."
        )
        output.append(replacement)
        overrides.append(
            {
                "name": name,
                "requested": str(requirement),
                "verified_version": verified,
                "replacement": replacement,
            }
        )

    return "\n".join(output) + "\n", overrides
