[CmdletBinding()]
param(
    [string]$ComfyUiDirectory,
    [string]$OutputArchive
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Collector = Join-Path $ScriptDir "collect_comfyui_inventory.py"

$Python = Get-Command py -ErrorAction SilentlyContinue
if ($Python) {
    $Arguments = @("-3", $Collector)
}
else {
    $Python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $Python) {
        throw "Python 3 is required to run the inventory collector."
    }
    $Arguments = @($Collector)
}

if ($ComfyUiDirectory) {
    $Arguments += $ComfyUiDirectory
}
if ($OutputArchive) {
    $Arguments += $OutputArchive
}

& $Python.Source @Arguments
exit $LASTEXITCODE
