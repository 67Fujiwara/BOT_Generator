@echo off
rem Start bot-team automatically at Windows logon (puts a shortcut in the Startup folder).
rem Undo with autostart_off.bat
set LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\bot-team.lnk
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%LNK%'); $s.TargetPath='%~dp0run.bat'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()"
echo Registered: %LNK%
pause
