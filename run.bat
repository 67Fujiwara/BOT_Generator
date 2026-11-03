@echo off
rem Start bot-team in the background (no console window) and open the browser.
rem If it is already running, only the browser opens. Stop it with the power button in the page.
cd /d %~dp0
if exist .venv\Scripts\pythonw.exe (
  start "" .venv\Scripts\pythonw.exe app\server.py --open
  goto :eof
)
where pythonw >nul 2>&1
if %errorlevel%==0 (
  start "" pythonw app\server.py --open
) else (
  start "" /min python app\server.py --open
)
