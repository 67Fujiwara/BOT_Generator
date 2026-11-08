@echo off
rem Start bot-team in the background (no console window) and open the browser.
rem If it is already running, only the browser opens. Stop it with the power button in the page.
cd /d %~dp0
rem Remove the "downloaded file" mark from the scripts so Windows stops asking "do you want to run this software?"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%~dp0' -Recurse -Include *.bat,*.ps1,*.cmd | Unblock-File -ErrorAction SilentlyContinue" >nul 2>&1
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
