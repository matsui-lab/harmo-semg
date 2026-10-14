@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -File "%~dp0bootstrap.ps1" check
pause
