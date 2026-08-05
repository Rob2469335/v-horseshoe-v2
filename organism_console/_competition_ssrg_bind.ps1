function _Get-SSRGStatsForSkill {
    param($skillId)

    $events = $global:SSRG.events | Where-Object {
        $_.from -eq $skillId -or $_.to -eq $skillId
    }

    $wins = ($events | Where-Object { $_.type -eq "WIN" }).Count
    $losses = ($events | Where-Object { $_.type -eq "LOSS" }).Count
    $total = $wins + $losses

    if ($total -eq 0) {
        return @{
            winRate = 0.5
            activity = 0
        }
    }

    return @{
        winRate = $wins / $total
        activity = $total
    }
}

function _ScoreWithSSRG {
    param($skill, $baseScore)

    $stats = _Get-SSRGStatsForSkill $skill.id

    $confidence = $skill.confidence
    $success = $skill.success_count
    $failure = $skill.failure_count

    $localScore =
        ($baseScore * 0.4) +
        ($confidence * 0.3) +
        (($success / [math]::Max(1, ($success + $failure))) * 0.3)

    # SSRG GLOBAL ADJUSTMENT
    $globalAdjustment =
        ($stats.winRate * 0.6) +
        ([math]::Min(1, $stats.activity / 20) * 0.4)

    return ($localScore * 0.7) + ($globalAdjustment * 0.3)
}

function Update-SSRGCompetition {
    param($winner, $candidates, $task)

    $winnerId = $winner.id

    foreach ($c in $candidates) {
        $sid = $c[0].id

        if ($sid -eq $winnerId) {
            Add-SSRGEvent $sid "competition" "WIN" $task "" 1.0
        }
        else {
            Add-SSRGEvent $sid "competition" "LOSS" $task "" 0.0
        }
    }
}
