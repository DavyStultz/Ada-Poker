# Prepares Python and installs the checksum-verified local FFmpeg runtime.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$adaPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $adaPython)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
}
& $adaPython (Join-Path $PSScriptRoot 'scripts\setup_ffmpeg.py')
if ($LASTEXITCODE -ne 0) { throw 'Could not install the recording component.' }
Write-Output 'Ada Poker is ready. Run .\start-recorder.ps1 to open it.'
