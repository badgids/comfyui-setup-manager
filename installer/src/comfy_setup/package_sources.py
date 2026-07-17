from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse

PYPI_SIMPLE = "https://pypi.org/simple"
PUBLIC_PACKAGE_INDEX_HOSTS = {
    "pypi.org",
    "files.pythonhosted.org",
    "download.pytorch.org",
    "pypi.nvidia.com",
}
PUBLIC_GITHUB_HOSTS = {"github.com", "www.github.com"}
BLOCKED_HOST_MARKERS = (
    "internal",
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    ".local",
    ".lan",
)
PACKAGE_ENV_KEYS = {
    "PIP_INDEX_URL",
    "PIP_EXTRA_INDEX_URL",
    "PIP_TRUSTED_HOST",
    "PIP_CONFIG_FILE",
    "UV_INDEX",
    "UV_EXTRA_INDEX_URL",
    "UV_INDEX_URL",
    "UV_DEFAULT_INDEX",
    "UV_CONFIG_FILE",
}


class PackageSourceError(ValueError):
    pass


def _host(url: str) -> str:
    parsed = urlparse(url)
    return (parsed.hostname or "").lower()


def _reject_private_host(url: str, purpose: str) -> str:
    value = str(url).strip()
    if not value:
        raise PackageSourceError(f"Missing URL for {purpose}.")
    parsed = urlparse(value)
    if parsed.scheme != "https":
        raise PackageSourceError(
            f"{purpose} must use HTTPS; refusing {value!r}."
        )
    host = _host(value)
    if not host or any(marker in host for marker in BLOCKED_HOST_MARKERS):
        raise PackageSourceError(
            f"{purpose} uses a private or unsupported host: {host or value}."
        )
    return value


def validate_package_index(url: str) -> str:
    value = _reject_private_host(url, "package index")
    host = _host(value)
    if host not in PUBLIC_PACKAGE_INDEX_HOSTS:
        raise PackageSourceError(
            "Package indexes are restricted to official PyPI, Python hosted files, "
            "official PyTorch wheels, and NVIDIA PyPI. Refusing: " + value
        )
    return value.rstrip("/")


def validate_github_repository(url: str) -> str:
    value = str(url).strip()
    if value.startswith("git+"):
        value = value[4:]
    value = _reject_private_host(value, "Git repository")
    if _host(value) not in PUBLIC_GITHUB_HOSTS:
        raise PackageSourceError(
            "Remote source repositories must be public GitHub HTTPS URLs. Refusing: "
            + value
        )
    return value


def validate_requirement_line(line: str) -> None:
    """Reject requirement directives or URLs that bypass the public source policy."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return
    lowered = stripped.lower()
    blocked_directives = (
        "--index-url",
        "--extra-index-url",
        "--trusted-host",
        "--find-links",
        "-i ",
        "-f ",
    )
    if lowered.startswith(blocked_directives):
        raise PackageSourceError(
            f"Requirement file attempted to override package sources: {stripped}"
        )

    urls = re.findall(r"(?:git\+)?https?://[^\s;]+", stripped, flags=re.IGNORECASE)
    for url in urls:
        clean = url.rstrip(",)]}\'\"")
        normalized = clean[4:] if clean.lower().startswith("git+") else clean
        if normalized.lower().startswith("http://"):
            raise PackageSourceError(f"Remote package requirements must use HTTPS: {clean}")
        validate_github_repository(clean)


def clean_package_environment(
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return an environment that cannot inherit private pip/uv indexes."""
    env = os.environ.copy()
    for key in PACKAGE_ENV_KEYS:
        env.pop(key, None)
    env["PIP_CONFIG_FILE"] = os.devnull
    env["PIP_INDEX_URL"] = PYPI_SIMPLE
    env["UV_NO_CONFIG"] = "1"
    env["UV_DEFAULT_INDEX"] = PYPI_SIMPLE
    if extra:
        env.update({key: str(value) for key, value in extra.items()})
    return env




def enforce_package_environment() -> None:
    """Sanitize package-source variables in the current manager process."""
    clean = clean_package_environment()
    for key in PACKAGE_ENV_KEYS:
        os.environ.pop(key, None)
    for key in ("PIP_CONFIG_FILE", "PIP_INDEX_URL", "UV_NO_CONFIG", "UV_DEFAULT_INDEX"):
        os.environ[key] = clean[key]


def uv_install_command(
    uv: Path,
    python: Path,
    *arguments: str,
    index: str | None = None,
) -> list[str]:
    command = [
        str(uv),
        "pip",
        "install",
        "--python",
        str(python),
        "--no-config",
        "--default-index",
        PYPI_SIMPLE,
    ]
    if index:
        command.extend(["--index", validate_package_index(index)])
    command.extend(arguments)
    return command
