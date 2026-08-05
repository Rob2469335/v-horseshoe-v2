param(
    [Parameter(Mandatory = $true)]
    [string]$Query
)

$ErrorActionPreference = "Stop"

$root = "C:\Users\rober\Projects\v-horseshoe-v2"

if ([string]::IsNullOrWhiteSpace($Query)) {
    Write-Host "Query cannot be empty" -ForegroundColor Red
    exit 1
}

Write-Host "Searching codebase for: $Query" -ForegroundColor Cyan

# --- FIND BEST MATCH ---
$match = Get-ChildItem -Path $root -Recurse -File -Filter *.py -ErrorAction SilentlyContinue |
    ForEach-Object {
        $hit = Select-String -Path $_.FullName -Pattern $Query -SimpleMatch -ErrorAction SilentlyContinue
        if ($hit) {
            [PSCustomObject]@{
                File  = $_.FullName
                Count = $hit.Count
            }
        }
    } |
    Where-Object { $_ -ne $null } |
    Sort-Object Count -Descending |
    Select-Object -First 1

if (-not $match -or -not $match.File) {
    Write-Host "No matches found" -ForegroundColor Yellow
    exit 1
}

$file = $match.File
Write-Host "Target: $file" -ForegroundColor Green

# --- READ FILE SAFELY ---
$src = Get-Content $file -Raw

if ([string]::IsNullOrWhiteSpace($src)) {
    Write-Host "File empty or unreadable" -ForegroundColor Red
    exit 1
}

# --- PREVIEW ---
Write-Host "`n--- CONTEXT (first 50 lines) ---" -ForegroundColor DarkGray
($src -split "`n")[0..[Math]::Min(50, ($src -split "`n").Count - 1)]

# --- GET FIX ---
$replacement = Read-Host "Paste replacement block (or press Enter to cancel)"

if ([string]::IsNullOrWhiteSpace($replacement)) {
    Write-Host "Cancelled" -ForegroundColor Yellow
    exit 0
}

# --- BACKUP ---
Copy-Item $file "$file.bak" -Force

# --- APPLY FIX ---
$updated = $src.Replace($Query, $replacement)
Set-Content $file $updated -Encoding UTF8

# --- VALIDATE ---
python -m py_compile $file

if ($LASTEXITCODE -ne 0) {
    Write-Host "Syntax error detected - rolling back" -ForegroundColor Red
    Copy-Item "$file.bak" $file -Force
    exit 1
}

Write-Host "PATCH SUCCESS" -ForegroundColor Green
