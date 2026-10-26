# Installs the single project dependency already approved for Ada Poker.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$adaPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $adaPython)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
}
& $adaPython -m ensurepip --upgrade
if ($LASTEXITCODE -ne 0) { throw 'Could not prepare pip in the project environment.' }
& $adaPython -m pip install --disable-pip-version-check --no-deps --only-binary=:all: -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Could not install the recording component.' }
Write-Output 'Ada Poker is ready. Run .\start-recorder.ps1 to open it.'
