@echo off
rem Start bot-team automatically at Windows logon via update.bat (pull latest, then start).
rem Undo with autostart_off.bat
set LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\bot-team.lnk
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%LNK%'); $s.TargetPath='%~dp0update.bat'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()"
echo Registered: %LNK%
pause
