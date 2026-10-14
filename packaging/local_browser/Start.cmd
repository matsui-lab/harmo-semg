@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -File "%~dp0bootstrap.ps1" start
if errorlevel 1 pause
