# Prerequisites and platform preparation

[Documentation home](index.md) · [Getting started](getting-started.md) · [Troubleshooting](troubleshooting.md)

This page is the canonical prerequisite reference for **ComfyUI Setup Manager itself**. A setup profile may add operating-system tools or hardware requirements; those belong to that profile's documentation and are not universal manager requirements. See [Badgids Complete profile](badgids-complete-profile.md) for the bundled custom profile.

## Core requirements

| Requirement | Minimum or condition | Why it is needed |
|---|---|---|
| Supported operating system | Windows 10 version 1809 or newer, Windows 11, Linux, WSL2, or macOS | Runs the bootstrap scripts, Textual interface, and managed ComfyUI processes |
| Python | 3.10 or newer, with `venv` and `pip` support | Creates the private manager environment and runs the application |
| Git | Current supported release | Clones and updates ComfyUI and public custom-node repositories |
| Internet access | Required for initial setup and public downloads | Retrieves Python packages, Git repositories, source catalogs, models, workflows, and wheels |
| Local write access | The extracted project directory and every chosen install/library directory | Creates virtual environments, configuration, logs, profiles, installations, and shared libraries |
| Free disk space | Enough for the manager environment plus the selected ComfyUI profile, packages, models, and workflows | AI models and compiled packages can require many gigabytes |

The manager installs `uv` inside `installer/.venv`. A global `uv` installation is **not** required. A global ComfyUI installation is also not required.

## Windows preparation

The Windows bootstrap is `install.ps1`; `install.cmd` simply launches it through `powershell.exe`.

### When WinGet is required

`install.ps1` checks for Python 3.10+ and Git. When either is missing, the script uses **WinGet** to install it. Therefore:

- WinGet is required for the automatic first-run bootstrap when Python or Git is missing.
- WinGet is not required after both compatible Python and Git are already installed and visible in a newly opened terminal.
- The bootstrap cannot install WinGet for you because WinGet is the package manager it needs to perform the missing-tool installation.

WinGet is distributed with Microsoft's **App Installer** and is supported on Windows 10 version 1809 (build 17763) or later, Windows 11, and Windows Server 2025. Microsoft installation and repair guidance: [Use WinGet to install and manage applications](https://learn.microsoft.com/windows/package-manager/winget/).

Check it before running the installer:

```powershell
winget --version
```

If the command is missing, install or update **App Installer**, close the terminal, open a new PowerShell window, and run the check again. On managed, Store-disabled, Sandbox, or Server systems, follow Microsoft's documented standalone/repair method.

### Manual alternative to WinGet

You can avoid using WinGet by installing these yourself before running `install.ps1`:

1. Python 3.10 or newer, including `venv` and `pip`.
2. Git for Windows.
3. A new PowerShell or Command Prompt window so the refreshed `PATH` is visible.

The bootstrap accepts Python through `py -3`, `python`, or `python3`. Verify the manual installation:

```powershell
py -3 --version
python --version
git --version
```

Only one compatible Python command is required. The Microsoft Store execution aliases for `python.exe` can open the Store instead of Python; disable those aliases or use the Python launcher (`py -3`) when that happens.

### PowerShell execution policy

No permanent policy change is required. The documented command changes policy only for the current PowerShell process:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```

Running `install.cmd` also invokes the script with a process-local bypass.

## Linux and WSL2 preparation

The bootstrap requires a POSIX-compatible `sh`. It can install missing Python/Git packages through `apt-get`, `dnf`, `pacman`, `zypper`, or Homebrew when the package manager is present.

Automatic system-package installation normally requires:

- `sudo` access when the current user is not root;
- working package repositories;
- Python's virtual-environment package on distributions that split it out, such as `python3-venv` on Debian/Ubuntu;
- Python's pip package on distributions that split it out, such as `python3-pip`.

Verify manually:

```bash
python3 --version
git --version
python3 -m venv --help >/dev/null
```

On WSL2, install Linux packages inside the WSL distribution. Windows-side Python, Git, CUDA tools, and package managers do not replace their Linux-side counterparts.

## macOS preparation

Install Python 3.10+ and Git before running `install.sh`, or install Homebrew so the bootstrap can obtain missing packages. Source builds may also require Apple's Xcode Command Line Tools.

Verify:

```bash
python3 --version
git --version
xcode-select -p   # required only for native source builds
```

## Source-build prerequisites

Normal installations prefer compatible wheels. These tools become necessary when a selected package has no compatible wheel or a profile explicitly allows a source fallback:

| Tool | Windows | Linux/WSL2 | macOS |
|---|---|---|---|
| C/C++ compiler | Visual Studio 2022 Build Tools with the Desktop development with C++/VCTools workload and recommended Windows SDK components | GCC/G++ or Clang plus the distribution's development packages | Xcode Command Line Tools |
| CMake | `cmake` on `PATH` | Distribution package | Homebrew or another supported package source |
| Ninja | `ninja` on `PATH` | Usually `ninja-build` or `ninja` | Homebrew or another supported package source |
| Python headers | Supplied by standard Windows/macOS Python distributions | Often `python3-dev` or `python3-devel` | Supplied by the selected Python distribution |
| Accelerator development toolkit | CUDA toolkit for NVIDIA builds; verify `nvcc` | CUDA toolkit or ROCm development stack matching the selected build | Not applicable to CUDA/ROCm; use MPS-compatible wheels |
| Optional build helpers | Rust (`cargo`, `rustc`) and `pkg-config` when a dependency requires them | Same | Same |

A compatible NVIDIA driver may be enough for prebuilt PyTorch CUDA wheels, but compiling CUDA extensions requires a matching CUDA toolkit and `nvcc`. Likewise, ROCm source builds require the appropriate development stack, not only a runtime driver.

Read [Wheels, accelerated PyTorch, and source builds](wheels-and-builds.md) before enabling source builds.

## Profile-specific operating-system dependencies

Profiles can declare tools such as FFmpeg, SoX, Tesseract OCR, compilers, or other native utilities. The installation review reports missing profile dependencies and can offer package-manager commands when the current platform is supported.

These are **not automatically universal prerequisites**. Install only what the selected profile and enabled components require. The bundled [Badgids Complete profile](badgids-complete-profile.md) has its own dedicated compatibility and dependency guide.

## Network, proxy, and certificate requirements

The bootstrap intentionally uses the public Python Package Index for the manager environment. Later operations may use PyPI, official PyTorch indexes, GitHub, Comfy Registry/Manager catalogs, and user-configured asset sources.

Corporate proxies, TLS interception, firewall rules, or custom certificate stores can block these operations. Configure Git, Python/pip, and the operating system's trusted certificates according to your network policy. Do not disable TLS verification globally.

## Verify before installation

Before the manager environment exists, use the operating-system commands above. After installation, use the built-in checks:

```bash
./comfyui-setup-manager system check
./comfyui-setup-manager system check --source-builds
./comfyui-setup-manager --format yaml system install-prerequisites --source-builds --dry-run
```

On Windows, replace the launcher with `comfyui-setup-manager.ps1` when running directly from PowerShell.
