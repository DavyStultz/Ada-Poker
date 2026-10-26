$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$adaPython = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $adaPython)) {
    throw 'Run setup.ps1 first to prepare the recorder.'
}
Start-Process -FilePath $adaPython -ArgumentList '-m', 'ada_poker' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
