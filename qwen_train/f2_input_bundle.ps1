<#
.SYNOPSIS
  Build the F2 offline input bundle (read-only VHDX) + SHA-256 manifest.

.DESCRIPTION
  Chosen mechanism: a small dedicated input VHDX attached to the guest
  READ-ONLY. Rationale vs an ISO: no Microsoft ADK / oscdimg install and no
  download, inherent read-only attach semantics (the guest cannot write back),
  SHA-256 manifest verification, and no extra tooling. The input VHDX carries the
  artifacts AND the bundle manifest (which embeds host_reference_utc for the
  guest clock startup check).

  PLAN ONLY by default: it enumerates + hashes artifacts and prints the planned
  bundle WITHOUT mutating anything. Only -Execute creates the VHDX. It never
  downloads, never executes input artifacts, and never touches secrets.

.EXAMPLE
  ./qwen_train/f2_input_bundle.ps1 -InputDir D:\f2\inputs
#>
[CmdletBinding()]
param(
    [switch]$Execute,
    [Parameter(Mandatory = $true)][string]$InputDir,
    [string]$OutputVhdx = "C:\ProgramData\Microsoft\Windows\Hyper-V\F2\f2-input.vhdx",
    [int64]$SizeBytes = 2GB,
    [string]$ManifestPath = "",
    [string]$HostReferenceUtc = "",
    [string[]]$ExpectedArtifacts = @()
)

$ErrorActionPreference = "Stop"
function Write-Plan($m) { Write-Host "[plan] $m" }
function Write-Step($m) { Write-Host "[exec] $m" }

if (-not (Test-Path -LiteralPath $InputDir)) {
    throw "F2 input bundle refused: -InputDir not found: $InputDir"
}
$root = (Resolve-Path -LiteralPath $InputDir).Path
if (-not $HostReferenceUtc) {
    $HostReferenceUtc = (Get-Date).ToUniversalTime().ToString("o")
}

$files = @(Get-ChildItem -LiteralPath $root -File -Recurse | Sort-Object FullName)
if ($files.Count -eq 0) {
    throw "F2 input bundle refused: no input artifacts found under $root"
}

$artifacts = foreach ($f in $files) {
    [pscustomobject][ordered]@{
        filename = $f.FullName.Substring($root.Length).TrimStart('\')
        size     = $f.Length
        sha256   = (Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash.ToLower()
        source   = "operator-provided"
    }
}

if ($ExpectedArtifacts.Count -gt 0) {
    $got = @($artifacts.filename | Sort-Object)
    $want = @($ExpectedArtifacts | Sort-Object)
    if ($got.Count -ne $want.Count -or (Compare-Object $got $want)) {
        throw "F2 input bundle refused: artifact membership != expected exact set."
    }
}

$manifest = [ordered]@{
    bundle_version     = "f2-input-bundle/1"
    created_utc        = (Get-Date).ToUniversalTime().ToString("o")
    host_reference_utc = $HostReferenceUtc
    artifact_count     = @($artifacts).Count
    artifacts          = @($artifacts)
}
$manifestJson = ($manifest | ConvertTo-Json -Depth 6)
if (-not $ManifestPath) { $ManifestPath = Join-Path $root "f2_bundle_manifest.json" }

if (-not $Execute) {
    Write-Plan "PLAN ONLY (no mutation)."
    Write-Plan "Artifacts ($($manifest.artifact_count)):"
    $artifacts | ForEach-Object { Write-Plan ("  {0}  {1} bytes  {2}" -f $_.filename, $_.size, $_.sha256) }
    Write-Plan "host_reference_utc: $HostReferenceUtc"
    Write-Plan "Would create input VHDX: $OutputVhdx ($([math]::Round($SizeBytes/1GB,2)) GB; attached READ-ONLY)"
    Write-Plan "Would write manifest: $ManifestPath"
    Write-Host $manifestJson
    exit 0
}

# --- -Execute (NOT run during preflight): create + populate the input VHDX ----
Write-Step "Creating input VHDX $OutputVhdx"
New-VHD -Path $OutputVhdx -SizeBytes $SizeBytes -Dynamic | Out-Null
$disk = Mount-VHD -Path $OutputVhdx -Passthru
$part = $disk | Get-Disk | Get-Partition | Where-Object { $_.DriveLetter } | Select-Object -First 1
if (-not $part) {
    throw "F2 input bundle: VHDX has no formatted volume; partition/format it first (operator step)."
}
$vol = "$($part.DriveLetter):\"
Set-Content -LiteralPath (Join-Path $vol "f2_bundle_manifest.json") -Value $manifestJson -Encoding utf8
foreach ($a in $artifacts) {
    $dst = Join-Path $vol $a.filename
    New-Item -ItemType Directory -Force -Path (Split-Path $dst) | Out-Null
    Copy-Item -LiteralPath (Join-Path $root $a.filename) -Destination $dst
}
Dismount-VHD -Path $OutputVhdx
Write-Step "Input bundle VHDX created: $OutputVhdx (attach READ-ONLY in the guest)"
