function Invoke-DeterministicPass {
    param([Parameter(Mandatory)] [hashtable]$TaskResult, [Parameter(Mandatory)] [hashtable]$ScoringConfig)
    $checks = @{}
    $output = $TaskResult.Output
    $checks.non_empty_output = -not [string]::IsNullOrWhiteSpace($output)
    $checks.non_boilerplate  = ($output -notmatch "^(TODO|N/A|not implemented)$")
    if ($TaskResult.ContainsKey("TestCommand") -and $TaskResult.TestCommand) {
        Invoke-Expression $TaskResult.TestCommand
        $checks.tests_pass = ($LASTEXITCODE -eq 0)
    }
    if ($TaskResult.ContainsKey("RuntimeMs") -and $ScoringConfig.dimensions.Efficiency.max_runtime_ms) {
        $checks.runtime_under_threshold = $TaskResult.RuntimeMs -le $ScoringConfig.dimensions.Efficiency.max_runtime_ms
    }
    if ($TaskResult.ContainsKey("TokenUsage") -and $ScoringConfig.dimensions.Efficiency.max_tokens) {
        $checks.token_usage_under_budget = $TaskResult.TokenUsage -le $ScoringConfig.dimensions.Efficiency.max_tokens
    }
    $failedChecks = $checks.GetEnumerator() | Where-Object { $_.Value -eq $false }
    return @{
        Checks          = $checks
        FailedChecks    = $failedChecks
        RequiresJudge   = ($TaskResult.RequiresSubjectiveReview -eq $true)
        DeterministicOk = ($failedChecks.Count -eq 0)
    }
}

function Convert-ChecksToScores {
    param([hashtable]$Checks, [hashtable]$ScoringConfig)
    $scores = @{}
    foreach ($dim in $ScoringConfig.dimensions.Keys) {
        $dimChecks = $ScoringConfig.dimensions[$dim].checks
        $applicable = $dimChecks | Where-Object { $Checks.ContainsKey($_) }
        if ($applicable.Count -eq 0) { continue }
        $passed = ($applicable | Where-Object { $Checks[$_] -eq $true }).Count
        $scores[$dim] = [math]::Round($passed / $applicable.Count, 3)
    }
    return $scores
}

