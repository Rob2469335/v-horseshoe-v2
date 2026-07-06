function Invoke-ModelCall {
    param(
        [Parameter(Mandatory)] [hashtable]$Model,
        [Parameter(Mandatory)] [hashtable]$Task,
        [int]$TimeoutSec = 120,
        [int]$MaxRetries = 1
    )

    $prompt = @"
You are an evaluation model for a benchmark system.

TASK:
$($Task.description)

Return only the solution/output.
"@

    $attempt = 0
    $lastError = $null

    while ($attempt -le $MaxRetries) {
        $attempt++
        $start = Get-Date
        try {
            if ($Model.provider -eq "ollama") {
                $body = @{ model = $Model.name; prompt = $prompt; stream = $false } | ConvertTo-Json -Depth 5
                $result = Invoke-RestMethod -Uri "http://localhost:11434/api/generate" -Method Post `
                    -Body $body -ContentType "application/json" -TimeoutSec $TimeoutSec
                $elapsed = ((Get-Date) - $start).TotalMilliseconds
                return @{
                    Output = $result.response
                    Error = $null
                    RuntimeMs = $elapsed
                    TokenUsage = $result.eval_count
                    PromptEval = $result.prompt_eval_count
                    TokensPerSec = if ($result.eval_duration -gt 0) { [math]::Round(($result.eval_count / ($result.eval_duration / 1e9)), 2) } else { 0 }
                    Fatal = $false
                }
            }
            elseif ($Model.provider -eq "openrouter") {
                if (-not $env:OPENROUTER_API_KEY) {
                    return @{ Output = ""; Error = "FATAL: OPENROUTER_API_KEY not set"; RuntimeMs = 0; TokenUsage = 0; TokensPerSec = 0; PromptEval = 0; Fatal = $true }
                }

                $headers = @{
                    "Authorization" = "Bearer $env:OPENROUTER_API_KEY"
                    "Content-Type"  = "application/json"
                }

                $body = @{
                    model = $Model.name
                    messages = @(
                        @{ role = "user"; content = $prompt }
                    )
                    temperature = 0.2
                } | ConvertTo-Json -Depth 10

                try {
                    $result = Invoke-RestMethod -Uri "https://openrouter.ai/api/v1/chat/completions" -Method Post `
                        -Headers $headers -Body $body -TimeoutSec $TimeoutSec
                }
                catch {
                    $statusCode = $null
                    try { $statusCode = $_.Exception.Response.StatusCode.value__ } catch {}
                    if ($statusCode -in 401, 403) {
                        return @{ Output = ""; Error = "FATAL: Auth error ($statusCode) - check OPENROUTER_API_KEY"; RuntimeMs = 0; TokenUsage = 0; TokensPerSec = 0; PromptEval = 0; Fatal = $true }
                    }
                    if ($statusCode -eq 402) {
                        return @{ Output = ""; Error = "FATAL: Payment/quota error (402)"; RuntimeMs = 0; TokenUsage = 0; TokensPerSec = 0; PromptEval = 0; Fatal = $true }
                    }
                    throw
                }

                $elapsed = ((Get-Date) - $start).TotalMilliseconds
                return @{
                    Output = $result.choices[0].message.content
                    Error = $null
                    RuntimeMs = $elapsed
                    TokenUsage = $result.usage.total_tokens
                    PromptEval = $result.usage.prompt_tokens
                    TokensPerSec = if ($elapsed -gt 0) { [math]::Round(($result.usage.completion_tokens / ($elapsed / 1000)), 2) } else { 0 }
                    Fatal = $false
                }
            }
            else {
                return @{ Output = ""; Error = "FATAL: Unknown provider $($Model.provider)"; RuntimeMs = 0; TokenUsage = 0; TokensPerSec = 0; PromptEval = 0; Fatal = $true }
            }
        }
        catch {
            $lastError = $_.Exception.Message
            Start-Sleep -Seconds 2
        }
    }

    return @{
        Output = ""
        Error = "MODEL_CALL_FAILED after $MaxRetries retries: $lastError"
        RuntimeMs = 0
        TokenUsage = 0
        TokensPerSec = 0
        PromptEval = 0
        Fatal = $false
    }
}

function Get-AllTasks {
    param([hashtable]$BenchmarkConfig)

    $allTasks = @()
    foreach ($file in $BenchmarkConfig.task_files) {
        if (-not (Test-Path $file)) {
            throw "Task file not found: $file"
        }

        $data = Get-Content $file -Raw | ConvertFrom-Json -AsHashtable
        if (-not $data.ContainsKey('tasks') -or $data.tasks.Count -eq 0) {
            throw "Task file '$file' is malformed or has no tasks defined."
        }

        $allTasks += $data.tasks
    }

    if ($allTasks.Count -eq 0) {
        throw "No tasks loaded. Check config/benchmark.json task_files list."
    }

    return $allTasks
}

function Invoke-BenchmarkRun {
    param(
        [string]$OutputRoot = "benchmark/outputs",
        [int]$MaxOpenRouterCalls = 25,
        [int]$ModelTimeoutSec = 240,
        [switch]$Force
    )

    if (-not (Test-Path $OutputRoot)) {
        New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
        Write-Host "Created missing output directory: $OutputRoot"
    }

    $modelsConfig    = Get-Content "config/models.json" -Raw | ConvertFrom-Json -AsHashtable
    $benchmarkConfig = Get-Content "config/benchmark.json" -Raw | ConvertFrom-Json -AsHashtable
    $tasks = Get-AllTasks -BenchmarkConfig $benchmarkConfig

    $openRouterCallCount = 0
    $circuitBroken = $false
    $skipped = 0
    $ran = 0

    foreach ($model in $modelsConfig.models) {
        if ($circuitBroken) { break }

        foreach ($task in $tasks) {
            if ($task.category -notin $model.roles) { continue }

            $safeModelName = $model.name -replace '[:/\\]', '_'
            $outFile = "$OutputRoot/${safeModelName}_$($task.id).json"

            if ((Test-Path $outFile) -and -not $Force) {
                $existing = Get-Content $outFile -Raw | ConvertFrom-Json
                if (-not $existing.Error) {
                    $skipped++
                    Write-Host "SKIP (already done): $($model.name) / $($task.id)" -ForegroundColor DarkGray
                    continue
                }
            }

            if ($model.provider -eq "openrouter") {
                if ($openRouterCallCount -ge $MaxOpenRouterCalls) {
                    Write-Warning "CIRCUIT BREAKER: Hit MaxOpenRouterCalls ($MaxOpenRouterCalls). Stopping all further cloud calls."
                    $circuitBroken = $true
                    break
                }
                $openRouterCallCount++
            }

            Write-Host "Running $($model.name) on $($task.id)... [$ran run, $skipped skipped, $openRouterCallCount/$MaxOpenRouterCalls cloud calls]"
            $result = Invoke-ModelCall -Model $model -Task $task -TimeoutSec $ModelTimeoutSec
            $ran++

            $record = @{
                TaskId       = $task.id
                Model        = $model.name
                Category     = $task.category
                Output       = $result.Output
                Error        = $result.Error
                RuntimeMs    = $result.RuntimeMs
                TokenUsage   = $result.TokenUsage
                TokensPerSec = $result.TokensPerSec
                PromptEval   = $result.PromptEval
                TestCommand  = $task.test_command
                Timestamp    = (Get-Date).ToString("o")
            }

            if ($result.Error) {
                Write-Warning "Failure on $($model.name) / $($task.id): $($result.Error)"
                if ($result.Fatal -and $model.provider -eq "openrouter") {
                    Write-Warning "FATAL OpenRouter error. Tripping circuit breaker."
                    $circuitBroken = $true
                }
            }
            else {
                Write-Host "  OK ($([math]::Round($result.RuntimeMs))ms, $($result.TokensPerSec) tok/s)" -ForegroundColor Green
            }

            $record | ConvertTo-Json -Depth 5 | Set-Content -Path $outFile -Encoding UTF8

            if ($circuitBroken) { break }
        }
    }

    Write-Host "`n=== Run summary: $ran executed, $skipped skipped (resumed), $openRouterCallCount OpenRouter calls used ==="
}
