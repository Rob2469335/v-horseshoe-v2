$ErrorActionPreference = "Continue"
$root = "C:\Users\rober\Projects\v-horseshoe-v2"

$conn = Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue
foreach ($c in $conn) {
    $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
    if ($p -and $p.ProcessName -eq "python") {
        Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        Write-Host "Stopped old backend PID $($p.Id)"
    }
}
# Also kill any other uvicorn serving swarm_os.app.main (multiple instances can
# stack up and the last one to bind wins port 8000).
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*uvicorn*swarm_os.app.main*" -and $_.ProcessId -ne $PID } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "Killed stray uvicorn PID $($_.ProcessId)"
    }
Start-Sleep -Seconds 2

$dotenvPath = Join-Path $root ".env"
if (Test-Path $dotenvPath) {
    Get-Content $dotenvPath | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#") -or $line -notmatch "=") { return }
        $name, $value = $line -split '=', 2
        $name = $name.Trim()
        $value = $value.Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        Set-Item -Path "Env:$name" -Value $value
    }
}

$pythonPath = if (Test-Path "$root\.venv\Scripts\python.exe") { "$root\.venv\Scripts\python.exe" } else { "python" }
$env:PYTHONPATH = $root

Start-Process -FilePath $pythonPath `
    -ArgumentList @("-m", "uvicorn", "swarm_os.app.main:app", "--host", "127.0.0.1", "--port", "8000") `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput "$root\logs\backend_restart.out.log" `
    -RedirectStandardError "$root\logs\backend_restart.err.log" `
    -PassThru | ForEach-Object { Write-Host "Started backend PID $($_.Id)" }

for ($i = 0; $i -lt 30; $i++) {
    try {
        Invoke-RestMethod "http://127.0.0.1:8000/health" | Out-Null
        Write-Host "Backend healthy after $i s"
        break
    } catch {
        Start-Sleep 1
    }
}
