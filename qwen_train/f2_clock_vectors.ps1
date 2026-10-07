<#
.SYNOPSIS
  PowerShell clock-vector harness (Windows PowerShell 5.1 compatible).

.DESCRIPTION
  Executes qwen_train/f2_clock_vectors.json through the SAME algorithm as
  qwen_train/f2_clock_guard.py. Exits 0 iff every vector matches. This proves the
  guest-side PowerShell implementation agrees with the Python authority.

.EXAMPLE
  powershell.exe -NoProfile -File .\f2_clock_vectors.ps1
#>
[CmdletBinding()]
param(
    [string]$VectorsPath = ""
)

$ErrorActionPreference = "Stop"
if (-not $VectorsPath) { $VectorsPath = Join-Path $PSScriptRoot "f2_clock_vectors.json" }
$inv = [System.Globalization.CultureInfo]::InvariantCulture
$styles = [System.Globalization.DateTimeStyles]::AdjustToUniversal

function Convert-ToUtc {
    param([object]$Value)
    if ($null -eq $Value) { return "MISSING" }
    if ($Value -is [string]) {
        $t = $Value.Trim()
        if ($t -eq "") { return "MISSING" }
        try { return [datetime]::Parse($t, $inv, $styles) } catch { return "MALFORMED" }
    }
    if ($Value -is [int] -or $Value -is [long] -or $Value -is [double]) {
        try {
            $epoch = New-Object System.DateTime(1970, 1, 1, 0, 0, 0, [System.DateTimeKind]::Utc)
            return $epoch.AddSeconds([double]$Value)
        } catch { return "MALFORMED" }
    }
    return "MALFORMED"
}

$data = Get-Content -LiteralPath $VectorsPath -Raw | ConvertFrom-Json
$tol = [double]$data.tolerance_seconds
$defaultRef = $data.default_reference_utc
$mismatches = 0
$ran = 0

foreach ($case in $data.cases) {
    $ran++
    $refRaw = $defaultRef
    if ($case.PSObject.Properties.Name -contains "reference") { $refRaw = $case.reference }
    $ref = Convert-ToUtc $refRaw
    $guest = Convert-ToUtc $case.guest
    $status = "OK"; $reason = ""

    if ($ref -eq "MISSING") { $status = "FAIL"; $reason = "HOST_REFERENCE_MISSING" }
    elseif ($ref -eq "MALFORMED") { $status = "FAIL"; $reason = "HOST_REFERENCE_MALFORMED" }
    elseif ($guest -eq "MALFORMED" -or $guest -eq "MISSING") { $status = "FAIL"; $reason = "GUEST_TIMESTAMP_MALFORMED" }
    else {
        $skew = ($guest - $ref).TotalSeconds
        if ([math]::Abs($skew) -gt $tol) { $status = "FAIL"; $reason = "GUEST_CLOCK_OUTSIDE_ALLOWED_WINDOW" }
    }

    if ($status -ne $case.expect_status -or $reason -ne $case.expect_reason) {
        $mismatches++
        Write-Output ("MISMATCH {0}: got {1}/{2} want {3}/{4}" -f $case.name, $status, $reason, $case.expect_status, $case.expect_reason)
    }
}

if ($mismatches -eq 0) {
    Write-Output ("PS51 VECTORS: OK ({0} cases)" -f $ran)
    exit 0
} else {
    Write-Output ("PS51 VECTORS: {0} MISMATCHES of {1}" -f $mismatches, $ran)
    exit 1
}
