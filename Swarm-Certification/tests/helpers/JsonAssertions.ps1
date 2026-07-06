function Read-JsonResult {
  param(
    [Parameter(Mandatory=$false)][string]$JsonText,
    [Parameter(Mandatory=$false)][string]$JsonPath
  )

  if ($JsonPath) {
    if (-not (Test-Path $JsonPath)) {
      throw "JSON file not found: $JsonPath"
    }
    $JsonText = Get-Content $JsonPath -Raw
  }

  if (-not $JsonText) {
    throw "No JSON input provided."
  }

  if (-not ($JsonText | Test-Json)) {
    throw "Invalid JSON content."
  }

  return ($JsonText | ConvertFrom-Json)
}

function Assert-HasProperties {
  param(
    [Parameter(Mandatory=$true)]$Object,
    [Parameter(Mandatory=$true)][string[]]$Properties
  )

  $missing = @()
  foreach ($p in $Properties) {
    if ($null -eq $Object.PSObject.Properties[$p]) {
      $missing += $p
    }
  }

  if ($missing.Count -gt 0) {
    throw "Missing required properties: $($missing -join ', ')"
  }
}

function Assert-StatusSuccess {
  param(
    [Parameter(Mandatory=$true)]$Object
  )

  if ($Object.status -ne "success") {
    throw "Status was not success: $($Object.status)"
  }
}

function Assert-TextContains {
  param(
    [Parameter(Mandatory=$true)][string]$Text,
    [Parameter(Mandatory=$true)][string[]]$Terms
  )

  $missing = @()
  $lower = $Text.ToLowerInvariant()

  foreach ($t in $Terms) {
    if ($lower -notmatch [regex]::Escape($t.ToLowerInvariant())) {
      $missing += $t
    }
  }

  if ($missing.Count -gt 0) {
    throw "Missing required terms: $($missing -join ', ')"
  }
}

function Assert-ArrayMinCount {
  param(
    [Parameter(Mandatory=$true)]$Value,
    [Parameter(Mandatory=$true)][string]$Name,
    [Parameter(Mandatory=$true)][int]$MinCount
  )

  if ($null -eq $Value) {
    throw "$Name was null"
  }

  $count = @($Value).Count
  if ($count -lt $MinCount) {
    throw "$Name must contain at least $MinCount item(s); found $count"
  }
}
