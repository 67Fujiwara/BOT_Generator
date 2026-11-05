@echo off
rem Start bot-team automatically at Windows logon, silently (no window, no prompt):
rem  - removes the "downloaded file" mark from the scripts so Windows does not ask for confirmation
rem  - puts a Startup shortcut that runs update.bat hidden (pull latest, then start)
rem Undo with autostart_off.bat
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-ChildItem -Path '%~dp0' -Include *.bat,*.ps1 -Recurse | Unblock-File -ErrorAction SilentlyContinue;" ^
  "$lnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\bot-team.lnk';" ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk);" ^
  "$s.TargetPath = 'powershell.exe';" ^
  "$s.Arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command \"Start-Process -WindowStyle Hidden -FilePath ''%~dp0update.bat''\"';" ^
  "$s.WorkingDirectory = '%~dp0';" ^
  "$s.WindowStyle = 7;" ^
  "$s.IconLocation = 'shell32.dll,43';" ^
  "$s.Save();" ^
  "Write-Host ('Registered: ' + $lnk)"
pause
