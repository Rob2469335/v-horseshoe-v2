param(
    [string]$File,
    [string]$FunctionName,
    [string]$ReplacementFile
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($File)) { throw "Missing -File" }
if ([string]::IsNullOrWhiteSpace($FunctionName)) { throw "Missing -FunctionName" }
if ([string]::IsNullOrWhiteSpace($ReplacementFile)) { throw "Missing -ReplacementFile" }

if (-not (Test-Path $File)) { throw "File not found: $File" }
if (-not (Test-Path $ReplacementFile)) { throw "Replacement not found: $ReplacementFile" }

$code = Get-Content $File -Raw
$replacement = Get-Content $ReplacementFile -Raw

Copy-Item $File "$File.bak" -Force

$pattern = "(def\s+$FunctionName\s*\(.*?\):(?:\n(?:\s{4}.*|\s*)*)?)"

$match = [regex]::Match($code, $pattern)

if (-not $match.Success) {
    throw "Function not found: $FunctionName"
}

$code = $code.Replace($match.Value, $replacement)

Set-Content -Path $File -Value $code -Encoding UTF8

Write-Host "Patch applied." -ForegroundColor Green

python -m py_compile $File
Write-Host "✔ Syntax OK" -ForegroundColor Green
