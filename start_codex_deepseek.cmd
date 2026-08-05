@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_codex_deepseek.ps1" %*
exit /b %ERRORLEVEL%
