$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectDir

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m PyInstaller --clean packaging\bahmni-print-agent.spec

Write-Host "Built dist\BahmniPrintAgent.exe"
