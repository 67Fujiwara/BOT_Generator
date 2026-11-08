# Used by the logon autostart shortcut. Runs update.bat (pull latest, then start) through cmd.exe with no window.
# Launching cmd.exe (a system program) instead of the .bat itself avoids the "publisher could not be verified"
# dialog that Windows shows for batch files that came from a download (mark of the web).
$bat = Join-Path $PSScriptRoot 'update.bat'
Get-ChildItem -LiteralPath $PSScriptRoot -Recurse -Include *.bat,*.ps1,*.cmd | Unblock-File -ErrorAction SilentlyContinue
Start-Process -FilePath $env:ComSpec -ArgumentList '/c', ('"' + $bat + '"') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
