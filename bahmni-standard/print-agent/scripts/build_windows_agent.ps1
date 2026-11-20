$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectDir

# SumatraPDF prints PDFs (box manifests, receipts) silently on Windows. It is
# packaged inside the agent, so the PCs need nothing installed. The download
# is pinned: a file whose SHA-256 differs is refused.
$SumatraVersion = "3.6.1"
$SumatraZipSha256 = "98b33a518d42986856d225064b0cd2d3643ecf78cbf84ab873d26cc51877a544"
$SumatraUrl = "https://www.sumatrapdfreader.org/dl/rel/$SumatraVersion/SumatraPDF-$SumatraVersion-64.zip"
$VendorDir = Join-Path $ProjectDir "packaging\vendor"
$SumatraExe = Join-Path $VendorDir "SumatraPDF.exe"

if (-not (Test-Path -LiteralPath $SumatraExe)) {
    New-Item -ItemType Directory -Force -Path $VendorDir | Out-Null
    $zip = Join-Path $VendorDir "SumatraPDF-$SumatraVersion-64.zip"
    Write-Host "Downloading SumatraPDF $SumatraVersion ..."
    Invoke-WebRequest -Uri $SumatraUrl -OutFile $zip -UseBasicParsing
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLower()
    if ($actual -ne $SumatraZipSha256) {
        Remove-Item -LiteralPath $zip
        throw "SumatraPDF download has SHA-256 $actual, expected $SumatraZipSha256. Not packaging it."
    }
    $unpacked = Join-Path $VendorDir "unpacked"
    Expand-Archive -LiteralPath $zip -DestinationPath $unpacked -Force
    Move-Item -LiteralPath (Join-Path $unpacked "SumatraPDF-$SumatraVersion-64.exe") -Destination $SumatraExe
    Remove-Item -Recurse -Force -LiteralPath $unpacked, $zip
}

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m PyInstaller --clean packaging\bahmni-print-agent.spec

Write-Host "Built dist\BahmniPrintAgent.exe (with SumatraPDF $SumatraVersion)"
