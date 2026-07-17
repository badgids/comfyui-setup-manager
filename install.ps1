[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$InstallerArguments
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$InstallerDir = Join-Path $ScriptDir "installer"
$VenvDir = Join-Path $InstallerDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvUv = Join-Path $VenvDir "Scripts\uv.exe"
$PyPiIndex = "https://pypi.org/simple"

function Find-CompatiblePython {
    $Candidates = @(
        @{ Command = "py"; Args = @("-3") },
        @{ Command = "python"; Args = @() },
        @{ Command = "python3"; Args = @() }
    )
    foreach ($Candidate in $Candidates) {
        $Found = Get-Command $Candidate.Command -ErrorAction SilentlyContinue
        if (-not $Found) { continue }
        $ProbeArgs = @($Candidate.Args) + @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)")
        & $Found.Source @ProbeArgs 2>$null
        if ($LASTEXITCODE -eq 0) {
            return @{ Executable = $Found.Source; Prefix = $Candidate.Args }
        }
    }
    return $null
}

function Install-WithWinget([string]$Id) {
    $Winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $Winget) {
        throw "winget is required to install missing prerequisite $Id. Install it from Microsoft App Installer, then rerun this script."
    }
    Write-Host "Installing missing prerequisite: $Id" -ForegroundColor Cyan
    & $Winget.Source install --id $Id --exact --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw "winget failed while installing $Id" }
}

$Python = Find-CompatiblePython
if (-not $Python) {
    Install-WithWinget "Python.Python.3.12"
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
    $Python = Find-CompatiblePython
    if (-not $Python) { throw "Python 3.10 or newer is still unavailable after installation." }
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Install-WithWinget "Git.Git"
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
}

if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating the private manager environment..." -ForegroundColor Cyan
    & $Python.Executable @($Python.Prefix) -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "Python could not create $VenvDir" }
}

@(
    "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_TRUSTED_HOST", "PIP_CONFIG_FILE",
    "UV_INDEX", "UV_EXTRA_INDEX_URL", "UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_CONFIG_FILE"
) | ForEach-Object { Remove-Item "Env:$_" -ErrorAction SilentlyContinue }
$env:PIP_CONFIG_FILE = "NUL"
$env:PIP_INDEX_URL = $PyPiIndex
$env:UV_NO_CONFIG = "1"
$env:UV_DEFAULT_INDEX = $PyPiIndex

Write-Host "Installing or updating ComfyUI Setup Manager from the included project and public Python package index..." -ForegroundColor Cyan
& $VenvPython -m pip install --disable-pip-version-check --no-cache-dir --index-url $PyPiIndex --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$BundledWheel = Get-ChildItem -Path (Join-Path $InstallerDir "dist") -Filter "comfyui_setup_manager-*.whl" -File -ErrorAction SilentlyContinue |
    Sort-Object Name | Select-Object -Last 1
if ($BundledWheel) {
    & $VenvPython -m pip install --disable-pip-version-check --no-cache-dir --index-url $PyPiIndex --upgrade $BundledWheel.FullName
} else {
    & $VenvPython -m pip install --disable-pip-version-check --no-cache-dir --index-url $PyPiIndex --no-build-isolation --upgrade $InstallerDir
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not (Test-Path $VenvUv)) { throw "uv was not installed into $VenvDir" }

$env:COMFY_INSTALLER_UV = $VenvUv
$env:COMFYUI_SETUP_CONFIG_DIR = Join-Path $ScriptDir "config"
$env:COMFYUI_SETUP_PROJECT_ROOT = $ScriptDir
New-Item -ItemType Directory -Force -Path $env:COMFYUI_SETUP_CONFIG_DIR | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $ScriptDir "profiles") | Out-Null
Write-Host ""
Write-Host "Installation complete." -ForegroundColor Green
Write-Host "Interactive interface: .\comfyui-setup-manager.ps1"
Write-Host "Automation CLI help: .\comfyui-setup-manager.ps1 --help"
Write-Host ""
& $VenvPython -m comfy_setup @InstallerArguments
exit $LASTEXITCODE
