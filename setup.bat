@echo off
rem One-time setup on a new PC: find Python 3.10+, create .venv, install requirements,
rem register autostart at logon, and start bot-team now. Safe to run again.
cd /d %~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
pause
