@echo off
rem Update bot-team to the latest version and (re)start it. Used by the logon autostart too.
rem Local edits to tracked files are discarded (settings, inbox, samples, tasks and keys are untouched).
cd /d %~dp0
rem Remove the "downloaded file" mark from the scripts so Windows stops asking "do you want to run this software?"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%~dp0' -Recurse -Include *.bat,*.ps1,*.cmd | Unblock-File -ErrorAction SilentlyContinue" >nul 2>&1
if not exist .git (
  echo This folder is not a git clone, so it cannot be updated. Starting as is.
  call run.bat
  timeout /t 5 >nul
  exit /b 0
)
git checkout -- . >nul 2>&1
git pull
if errorlevel 1 (
  echo.
  echo git pull failed (offline?). Starting the current version as is.
  call run.bat
  timeout /t 8 >nul
  exit /b 0
)
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe -m pip install -q -r requirements.txt
) else (
  python -m pip install -q -r requirements.txt
)
call run.bat
echo Updated. Reload the page in the browser if it was already open.
timeout /t 5 >nul
