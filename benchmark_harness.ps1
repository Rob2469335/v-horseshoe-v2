param()
$ErrorActionPreference = "Continue"

$Models = @(
    "llama3-groq-tool-use:8b",
    "MFDoom/deepseek-r1-tool-calling:8b",
    "gemma4:e4b",
    "MFDoom/deepseek-r1-tool-calling:8b",
    "llama3-groq-tool-use:8b",
    "qwen3:8b-q4_K_M",
    "qwen2.5-coder:7b"
)

$Roles = @("coordinator","planner","executor","coder","tool-runner","debugger","reviewer")
$TeacherModel = "qwen2.5-coder:7b"
$OutputDir = Join-Path $PWD "output"
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$OutputCsv = Join-Path $OutputDir "agent_role_eval_results.csv"
$WinnerCsv = Join-Path $OutputDir "agent_role_winners.csv"

$teacherSchema = @{
    type = "object"
    properties = @{
        score = @{ type = "integer"; minimum = 1; maximum = 5 }
        feedback = @{ type = "string" }
    }
    required = @("score", "feedback")
    additionalProperties = $false
}

$Scenarios = @{
    coordinator = @(
        @{ name = "simple";    prompt = "You have a user request that requires a web search and a file update. Delegate the work and do not do it yourself." }
        @{ name = "realistic"; prompt = "A user wants a code change, a docs update, and verification. Split the work into delegated steps and keep control of the workflow." }
        @{ name = "messy";     prompt = "The user request is ambiguous, may require research, and may need code changes. Resolve the ambiguity by delegating the right work in order." }
    )
    planner = @(
        @{ name = "simple";    prompt = "Turn this request into a 3-step plan with clear dependencies: search web, edit file, run tests." }
        @{ name = "realistic"; prompt = "Break a multi-agent software task into ordered stages with risks, checkpoints, and handoffs." }
        @{ name = "messy";     prompt = "Plan a task where requirements are incomplete, one step may fail, and you need a fallback path." }
    )
    executor = @(
        @{ name = "simple";    prompt = "Perform the next concrete action for this task using one tool and return the result." }
        @{ name = "realistic"; prompt = "Execute a contained work item: read the relevant file, make the smallest safe change, and report what changed." }
        @{ name = "messy";     prompt = "You have partial instructions and a likely broken dependency. Pick the next concrete action and keep the work moving." }
    )
    coder = @(
        @{ name = "simple";    prompt = "Patch a small bug in a file and preserve behavior. Show the exact edit you would make." }
        @{ name = "realistic"; prompt = "Refactor a function into a helper, keep the API stable, and avoid regressions." }
        @{ name = "messy";     prompt = "A file has duplicated logic and one stale variable bug. Fix only the necessary lines and explain why." }
    )
    "tool-runner" = @(
        @{ name = "simple";    prompt = "Run the requested check and return only the verification result." }
        @{ name = "realistic"; prompt = "Execute tests, report failures, and do not edit code." }
        @{ name = "messy";     prompt = "A verification run failed. Determine whether it is an environment issue or a code issue, then report." }
    )
    debugger = @(
        @{ name = "simple";    prompt = "Tests failed with: AssertionError: expected 4 got 5. Diagnose and route the fix." }
        @{ name = "realistic"; prompt = "Tool-runner reports: FileNotFoundError on path swarm_os/docs/output.md. Determine if this is a code bug or environment issue and route accordingly." }
        @{ name = "messy";     prompt = "Two failures: one is a missing import, one is a logic error in the patched function. Prioritize and route both." }
    )
    reviewer = @(
        @{ name = "simple";    prompt = "Review a change for correctness and give a concise verdict." }
        @{ name = "realistic"; prompt = "Review a multi-file agent change for regressions, missing cases, and maintainability issues." }
        @{ name = "messy";     prompt = "Review a patch where one fix is good but another introduced a subtle bug. Identify both." }
    )
}

function Invoke-TeacherGrade {
    param([string]$Rationale, [string]$Model, [hashtable]$Schema)
    $rubric = "Grade this response 1-5 (5=perfect logic): `"$Rationale`""
    $body = @{
        model = $Model
        messages = @(
            @{ role = "system"; content = "Grade rationale quality. Output only valid JSON." }
            @{ role = "user";   content = $rubric }
        )
        stream = $false
        format = $Schema
        options = @{ temperature = 0 }
    } | ConvertTo-Json -Depth 10 -Compress
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/chat" -Method POST -Body $body -ContentType "application/json" -TimeoutSec 120
        return $r.message.content
    } catch { return '{"score":0,"feedback":"grade failed"}' }
}

$results = [System.Collections.Generic.List[PSCustomObject]]::new()

$totalTests = $Models.Count * $Roles.Count * 3
$current = 0

foreach ($model in $Models) {
    foreach ($role in $Roles) {
        $scenarioList = $Scenarios[$role]
        foreach ($scenario in $scenarioList) {
            $current++
            Write-Host "[$current/$totalTests] $model | $role | $($scenario.name)" -ForegroundColor Cyan

            $scantron = 0; $teacherScore = 0; $teacherFeedback = ""; $raw = ""; $jsonValid = 0; $rationale = ""

            try {
                $body = @{
                    model = $model
                    messages = @(
                        @{ role = "system"; content = "You are an agent. Respond thoughtfully with your rationale." }
                        @{ role = "user";   content = $scenario.prompt }
                    )
                    stream = $false
                    keep_alive = 0
                    options = @{ temperature = 0.1; num_predict = -1 }
                } | ConvertTo-Json -Depth 10 -Compress

                $resp = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/chat" -Method POST -Body $body -ContentType "application/json" -TimeoutSec 300
                $raw = $resp.message.content
                # Strip thinking tokens
                $raw = $raw -replace '(?s)<think>.*?</think>', ''
                $raw = $raw.Trim()

                if ($raw.Length -gt 10) {
                    $scantron = 1
                    $jsonValid = 1
                    $rationale = $raw.Substring(0, [Math]::Min(500, $raw.Length))
                }

                if ($rationale.Length -gt 5) {
                    $teacherRaw = Invoke-TeacherGrade -Rationale $rationale -Model $TeacherModel -Schema $teacherSchema
                    try {
                        $td = $teacherRaw | ConvertFrom-Json -ErrorAction Stop
                        $teacherScore = [int]$td.score
                        $teacherFeedback = [string]$td.feedback
                    } catch { $teacherFeedback = "parse failed" }
                }

            } catch {
                $raw = "ERROR: $($_.Exception.Message)"
                $teacherFeedback = "model call failed"
            }

            $results.Add([PSCustomObject]@{
                Model         = $model
                Role          = $role
                Scenario      = $scenario.name
                JSONValid     = $jsonValid
                ScantronScore = $scantron
                TeacherScore  = $teacherScore
                TotalScore    = ($scantron + $teacherScore)
                RawOutput     = $raw.Substring(0, [Math]::Min(300, $raw.Length))
                Feedback      = $teacherFeedback
            })

            Write-Host "  ScantronScore=$scantron TeacherScore=$teacherScore Total=$($scantron+$teacherScore)" -ForegroundColor $(if(($scantron+$teacherScore) -ge 4){"Green"}elseif(($scantron+$teacherScore) -ge 2){"Yellow"}else{"Red"})
        }
    }
}

$results | Export-Csv -Path $OutputCsv -NoTypeInformation -Encoding UTF8
Write-Host "`nSaved: $OutputCsv" -ForegroundColor Green

# Winners per role
Write-Host "`n=== WINNER PER ROLE ===" -ForegroundColor Magenta
$winners = foreach ($roleGroup in ($results | Group-Object Role)) {
    $best = $roleGroup.Group |
        Group-Object Model |
        ForEach-Object {
            [PSCustomObject]@{
                Role     = $roleGroup.Name
                Model    = $_.Name
                AvgScore = [math]::Round(($_.Group | Measure-Object TotalScore -Average).Average, 2)
            }
        } |
        Sort-Object AvgScore -Descending |
        Select-Object -First 1
    $best
}
$winners | Format-Table -AutoSize
$winners | Export-Csv -Path $WinnerCsv -NoTypeInformation -Encoding UTF8

Write-Host "`n=== OVERALL MODEL RANKING ===" -ForegroundColor Magenta
$results | Group-Object Model | ForEach-Object {
    [PSCustomObject]@{
        Model    = $_.Name
        AvgScore = [math]::Round(($_.Group | Measure-Object TotalScore -Average).Average, 2)
        Runs     = $_.Count
    }
} | Sort-Object AvgScore -Descending | Format-Table -AutoSize



