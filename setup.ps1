# bot-team setup (called by setup.bat). Idempotent: safe to run again.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Test-Py([string[]]$cmd) {
    try {
        $out = & $cmd[0] @($cmd[1..($cmd.Length)] | Where-Object { $_ }) -c "import sys; print(sys.version_info >= (3, 10))" 2>$null
        return ($LASTEXITCODE -eq 0 -and "$out".Trim() -eq 'True')
    } catch { return $false }
}

function Find-Python {
    $cands = @(
        @('python'), @('py', '-3.13'), @('py', '-3.12'), @('py', '-3.11'), @('py', '-3.10'), @('py', '-3'), @('python3')
    )
    foreach ($c in $cands) { if (Test-Py $c) { return $c } }
    return $null
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
    Write-Host 'Python 3.10+ was not found. Install it from https://www.python.org/downloads/'
    Write-Host '(tick "Add python.exe to PATH"), then run setup.bat again.'
    exit 1
}
Write-Host "      Using: $($py -join ' ')"

Write-Host '[2/4] Creating .venv and installing requirements (first time takes a few minutes)...'
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    & $py[0] @($py[1..($py.Length)] | Where-Object { $_ }) -m venv .venv
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
