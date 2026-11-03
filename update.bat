@echo off
rem Update bot-team to the latest version and (re)start it.
rem Local edits to tracked files are discarded (settings, inbox, samples and keys are untouched).
cd /d %~dp0
git checkout -- .
git pull
if errorlevel 1 (
  echo.
  echo git pull failed. Copy the message above and ask Claude.
  pause
  exit /b 1
)
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe -m pip install -q -r requirements.txt
) else (
  python -m pip install -q -r requirements.txt
)
call run.bat
echo Updated. Press Ctrl+F5 in the browser to reload the page.
timeout /t 5 >nul
