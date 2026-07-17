[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ManagerArguments
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ScriptDir "installer\.venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvUv = Join-Path $VenvDir "Scripts\uv.exe"
$PyPiIndex = "https://pypi.org/simple"
$ExpectedVersion = "0.8.7"

$Installed = (Test-Path $VenvPython)
if ($Installed) {
    & $VenvPython -c "import comfy_setup,sys; sys.exit(0 if comfy_setup.__version__ == '0.8.7' else 1)" 2>$null
    $Installed = ($LASTEXITCODE -eq 0)
}

if (-not $Installed) {
    Write-Host "ComfyUI Setup Manager is not installed yet; running the local installer..." -ForegroundColor Cyan
    & (Join-Path $ScriptDir "install.ps1") @ManagerArguments
    exit $LASTEXITCODE
}

@(
    "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_TRUSTED_HOST", "PIP_CONFIG_FILE",
    "UV_INDEX", "UV_EXTRA_INDEX_URL", "UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_CONFIG_FILE"
) | ForEach-Object { Remove-Item "Env:$_" -ErrorAction SilentlyContinue }
$env:PIP_CONFIG_FILE = "NUL"
$env:PIP_INDEX_URL = $PyPiIndex
$env:UV_NO_CONFIG = "1"
$env:UV_DEFAULT_INDEX = $PyPiIndex
$env:COMFY_INSTALLER_UV = $VenvUv
$env:COMFYUI_SETUP_CONFIG_DIR = Join-Path $ScriptDir "config"
$env:COMFYUI_SETUP_PROJECT_ROOT = $ScriptDir
New-Item -ItemType Directory -Force -Path $env:COMFYUI_SETUP_CONFIG_DIR | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $ScriptDir "profiles") | Out-Null

& $VenvPython -m comfy_setup @ManagerArguments
exit $LASTEXITCODE
