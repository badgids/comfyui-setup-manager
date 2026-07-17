@echo off
setlocal
set "ROOT=%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%ROOT%comfyui-setup-manager.ps1" %*
exit /b %ERRORLEVEL%
