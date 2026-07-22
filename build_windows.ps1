$ErrorActionPreference = "Stop"

$ProjectDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDirectory

$Python = Join-Path $ProjectDirectory ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Missing .venv. Create it and install requirements first."
}

& $Python -m PyInstaller --noconfirm --clean NYCDetentionLookup.spec

Write-Host "Build complete: dist\NYCDetentionLookup.exe"

