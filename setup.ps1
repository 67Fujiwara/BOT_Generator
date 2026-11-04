# bot-team setup (called by setup.bat). Idempotent: safe to run again.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

# A candidate is a plain string such as 'python' or 'py -3.12' (kept as a string: PowerShell
# unrolls one-element arrays returned from functions, which once turned 'python' into 'p').
function Invoke-Py([string]$cmd, [string[]]$extra) {
    $parts = @($cmd -split ' ')
    $exe = $parts[0]
    $all = @()
    if ($parts.Length -gt 1) { $all += $parts[1..($parts.Length - 1)] }
    if ($extra) { $all += $extra }
    & $exe $all
}

function Test-Py([string]$cmd) {
    try {
        $out = Invoke-Py $cmd @('-c', 'import sys; print(sys.version_info >= (3, 10))') 2>$null
        return (($LASTEXITCODE -eq 0) -and ("$out".Trim() -eq 'True'))
    } catch { return $false }
}

function Find-Python {
    foreach ($c in @('python', 'py -3.13', 'py -3.12', 'py -3.11', 'py -3.10', 'py -3', 'python3')) {
        if (Test-Py $c) { return $c }
    }
    return $null
}

if (-not (Test-Path '.git')) {
    Write-Host 'NOTE: this folder is not a git clone (probably a downloaded ZIP). It will run, but update.bat'
    Write-Host '      cannot update it. Prefer: git clone https://github.com/67Fujiwara/BOT_Generator.git'
    Write-Host ''
}

Write-Host '[1/4] Looking for Python 3.10 or newer...'
$py = Find-Python
if (-not $py) {
    Write-Host '      Not found. Trying to install Python 3.12 with winget...'
    try {
        winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
        $py = Find-Python
    } catch {}
}
if (-not $py) {
    Write-Host ''
    Write-Host 'Python 3.10+ was not found. What each command returned:'
    foreach ($c in @('python', 'py -3', 'python3')) {
        try { $o = Invoke-Py $c @('--version') 2>&1 | Out-String } catch { $o = $_.Exception.Message }
        Write-Host ("  {0,-8} -> {1}" -f $c, ($o.Trim() -replace "`r?`n", ' | '))
    }
    Write-Host ''
    Write-Host 'If "python" opens the Microsoft Store or prints nothing: Settings > Apps > Advanced app settings >'
    Write-Host 'App execution aliases > turn OFF "python.exe" and "python3.exe", then run setup.bat again.'
    Write-Host 'Otherwise install from https://www.python.org/downloads/ (tick "Add python.exe to PATH") and run setup.bat again.'
    exit 1
}
Write-Host "      Using: $py"

Write-Host '[2/4] Creating .venv and installing requirements (first time takes a few minutes)...'
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Invoke-Py $py @('-m', 'venv', '.venv')
    if (-not (Test-Path '.venv\Scripts\python.exe')) { Write-Host 'Could not create .venv. Check the messages above.'; exit 1 }
}
& '.venv\Scripts\python.exe' -m pip install --quiet --upgrade pip
& '.venv\Scripts\python.exe' -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Host 'pip install failed. Check the messages above.'; exit 1 }

Write-Host '[3/4] Registering autostart at Windows logon...'
$lnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\bot-team.lnk'
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$s.TargetPath = Join-Path $PSScriptRoot 'run.bat'
$s.WorkingDirectory = $PSScriptRoot
$s.WindowStyle = 7
$s.Save()
Write-Host "      $lnk"

Write-Host '[4/4] Starting bot-team now...'
Start-Process -FilePath (Join-Path $PSScriptRoot 'run.bat') -WindowStyle Hidden
Write-Host ''
Write-Host 'Done. The browser opens http://127.0.0.1:8765 in a moment.'
Write-Host 'Save your API key with the gear button (top-left). To undo autostart, run autostart_off.bat.'
