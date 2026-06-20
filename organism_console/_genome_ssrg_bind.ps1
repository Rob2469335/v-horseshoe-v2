function Get-GenomeFitnessFromSSRG {
    param($skillId)

    $events = $global:SSRG.events | Where-Object {
        $_.from -eq $skillId -or $_.to -eq $skillId
    }

    $wins = ($events | Where-Object { $_.type -eq "WIN" }).Count
    $losses = ($events | Where-Object { $_.type -eq "LOSS" }).Count
    $total = $wins + $losses

    if ($total -eq 0) {
        return 0.5
    }

    $winRate = $wins / $total

    # activity bonus (but capped to avoid inflation)
    $activity = [math]::Min(1.0, $total / 30)

    return ($winRate * 0.7) + ($activity * 0.3)
}

function SSRG-MutateGenome {
    param($genome)

    $fitness = Get-GenomeFitnessFromSSRG $genome.skill_id

    # mutation strength depends on performance
    $mutationRate =
        if ($fitness -lt 0.4) { 0.3 }
        elseif ($fitness -lt 0.7) { 0.15 }
        else { 0.05 }

    # apply controlled drift
    $genome.confidence += (Get-Random -Minimum -$mutationRate -Maximum $mutationRate)

    # clamp
    $genome.confidence = [math]::Max(0.1, [math]::Min(1.0, $genome.confidence))

    return $genome
}

function SSRG-GenomeSelectionPressure {
    param($genomes)

    $scored = @()

    foreach ($g in $genomes) {
        $fitness = Get-GenomeFitnessFromSSRG $g.skill_id

        $score =
            ($fitness * 0.6) +
            ($g.confidence * 0.4)

        $scored += @{
            genome = $g
            score = $score
        }
    }

    return $scored | Sort-Object score -Descending
}
