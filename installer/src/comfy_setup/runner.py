from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable

LogCallback = Callable[[str], None]
SecretProvider = Callable[[str], str | None]


class CommandError(RuntimeError):
    def __init__(self, command: list[str], returncode: int, output_tail: str = "") -> None:
        rendered = subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)
        message = f"Command failed with exit code {returncode}: {rendered}"
        if output_tail:
            message += f"\n\nLast output:\n{output_tail}"
        super().__init__(message)
        self.command = command
        self.returncode = returncode
        self.output_tail = output_tail


class Runner:
    """Stream every command into the Textual log without suspending the app.

    Privileged POSIX commands are authenticated through an in-app secret
    provider and use ``sudo -S``. Passwords are written directly to the child
    process and are never logged or placed in command arguments.
    """

    def __init__(
        self,
        log: LogCallback | None = None,
        secret_provider: SecretProvider | None = None,
    ) -> None:
        self.log = log or (lambda line: None)
        self.secret_provider = secret_provider
        self._sudo_password: str | None = None

    @staticmethod
    def render(command: Iterable[str]) -> str:
        values = list(command)
        if os.name == "nt":
            return subprocess.list2cmdline(values)
        return shlex.join(values)

    def _prepare_privileged_command(
        self, command: list[str], env: dict[str, str]
    ) -> tuple[list[str], str | None]:
        if not command or command[0] != "sudo" or os.name == "nt":
            return command, None

        geteuid = getattr(os, "geteuid", None)
        if geteuid is not None and geteuid() == 0:
            return command[1:], None

        if shutil.which("sudo") is None:
            raise CommandError(command, 127, "sudo is required but was not found.")

        cached = subprocess.run(
            ["sudo", "-n", "true"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if cached.returncode == 0:
            return ["sudo", "-n", *command[1:]], None

        if self._sudo_password is None:
            if self.secret_provider is None:
                raise CommandError(
                    command,
                    1,
                    "Administrator authentication is required, but no in-app password provider is available.",
                )
            self._sudo_password = self.secret_provider(
                "Administrator password required for operating-system packages"
            )
        if self._sudo_password is None:
            raise CommandError(command, 1, "Administrator authentication was cancelled.")
        return ["sudo", "-S", "-p", "", *command[1:]], self._sudo_password + "\n"

    def clear_cached_secret(self) -> None:
        self._sudo_password = None

    def run(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        check: bool = True,
        interactive: bool = False,
    ) -> int:
        # ``interactive`` remains accepted for profile compatibility, but all
        # output and authentication stay inside Textual.
        del interactive
        self.log(f"$ {self.render(command)}")

        merged_env = os.environ.copy()
        if env:
            merged_env.update({key: str(value) for key, value in env.items()})

        actual_command, stdin_text = self._prepare_privileged_command(command, merged_env)
        process = subprocess.Popen(
            actual_command,
            cwd=str(cwd) if cwd else None,
            env=merged_env,
            text=True,
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            universal_newlines=True,
        )
        if stdin_text is not None and process.stdin is not None:
            try:
                process.stdin.write(stdin_text)
                process.stdin.flush()
            finally:
                process.stdin.close()

        tail: list[str] = []
        assert process.stdout is not None
        try:
            for line in process.stdout:
                clean = line.rstrip("\r\n")
                self.log(clean)
                tail.append(clean)
                if len(tail) > 80:
                    tail.pop(0)
        finally:
            process.stdout.close()
        returncode = process.wait()
        if check and returncode != 0:
            # A stale sudo password is more confusing than prompting again.
            if command and command[0] == "sudo":
                self._sudo_password = None
            raise CommandError(command, returncode, "\n".join(tail))
        return returncode

    def capture(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        merged_env = os.environ.copy()
        if env:
            merged_env.update({key: str(value) for key, value in env.items()})
        self.log(f"$ {self.render(command)}")
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            env=merged_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        for line in completed.stdout.splitlines():
            self.log(line)
        if check and completed.returncode != 0:
            raise CommandError(command, completed.returncode, completed.stdout[-4000:])
        return completed

    def python(self, python_executable: Path, *args: str, **kwargs: object) -> int:
        return self.run([str(python_executable), *args], **kwargs)  # type: ignore[arg-type]


def executable_for_venv(venv: Path, name: str) -> Path:
    if sys.platform == "win32":
        suffix = ".exe" if not name.lower().endswith(".exe") else ""
        return venv / "Scripts" / f"{name}{suffix}"
    return venv / "bin" / name
