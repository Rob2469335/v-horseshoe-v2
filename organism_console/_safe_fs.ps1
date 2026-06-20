function Safe-WriteFile {
    param (
        [string]$Path,
        [string]$Content
    )

    $backupRoot = ".backup\latest"

    # ensure backup folder exists
    New-Item -ItemType Directory -Force $backupRoot | Out-Null

    # only backup if file exists
    if (Test-Path $Path) {
        $name = Split-Path $Path -Leaf
        Copy-Item $Path "$backupRoot\$name" -Force
        Write-Host "[backup] saved $name" -ForegroundColor Yellow
    }

    # write new content
    Set-Content $Path -Value $Content -Encoding utf8
    Write-Host "[write] updated $Path" -ForegroundColor Green
}
