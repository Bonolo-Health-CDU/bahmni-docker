param(
    [Parameter(Mandatory=$true)]
    [string]$PrinterName,

    [Parameter(Mandatory=$true)]
    [Alias("Path")]
    [string]$File,

    [string]$SumatraPath = $env:CDU_SUMATRA_PDF
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $File)) {
    Write-Error "PDF file was not found: $File"
    exit 2
}

if (-not $SumatraPath) {
    $candidates = @(
        "$env:ProgramFiles\SumatraPDF\SumatraPDF.exe",
        "${env:ProgramFiles(x86)}\SumatraPDF\SumatraPDF.exe",
        "$PSScriptRoot\SumatraPDF.exe"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            $SumatraPath = $candidate
            break
        }
    }
}

if (-not $SumatraPath -or -not (Test-Path -LiteralPath $SumatraPath)) {
    Write-Error "SumatraPDF.exe was not found. Install SumatraPDF or set CDU_SUMATRA_PDF."
    exit 3
}

$arguments = @(
    "-silent",
    "-print-to",
    $PrinterName,
    $File
)

$process = Start-Process `
    -FilePath $SumatraPath `
    -ArgumentList $arguments `
    -Wait `
    -PassThru `
    -WindowStyle Hidden

exit $process.ExitCode
