param (
    [int]$RecordSeconds = 8
)

Add-Type -AssemblyName System.Windows.Forms

# ---- Command table: add more phrases here as you need them ----
$COMMANDS = @{
    "run benchmark"   = ".\benchmark_harness.ps1"
    "start swarm"     = ".\start_swarm.ps1"
    "check compile"   = "python -m py_compile runtime_v2\api\agent_service_v2.py"
}

$tempAudio = "$env:TEMP\voice_capture.wav"

Write-Host "`nRecording for $RecordSeconds seconds... speak now." -ForegroundColor Yellow

$recordTime = Measure-Command {
    $recordCmd = @"
import sounddevice as sd
from scipy.io.wavfile import write
fs = 16000
audio = sd.rec(int($RecordSeconds * fs), samplerate=fs, channels=1, dtype='int16')
sd.wait()
write(r'$tempAudio', fs, audio)
print('done')
"@
    python -c "$recordCmd" | Out-Null
}
Write-Host "Recording done. ($([math]::Round($recordTime.TotalSeconds,2))s)" -ForegroundColor DarkGray

Write-Host "Transcribing..." -ForegroundColor Cyan
$transcribeTime = Measure-Command {
    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:8001/transcribe" -Method Post -Form @{
            file = Get-Item -Path $tempAudio
        }
        $script:transcript = $response.text
    } catch {
        Write-Host "Failed to connect to Whisper server. Is it running on port 8001?" -ForegroundColor Red
        $script:transcript = ""
    }
}
$cleanTranscript = $transcript.Trim().ToLower()

Write-Host "Transcribed in $([math]::Round($transcribeTime.TotalSeconds,2))s" -ForegroundColor DarkGray
Write-Host "`n--- Raw Transcript ---`n" -ForegroundColor Cyan
Write-Output $transcript

# ---- Check for a matching command ----
$matchedCommand = $null
foreach ($phrase in $COMMANDS.Keys) {
    if ($cleanTranscript -like "*$phrase*") {
        $matchedCommand = $COMMANDS[$phrase]
        break
    }
}

if ($matchedCommand) {
    Write-Host "`nCommand detected -> running: $matchedCommand" -ForegroundColor Magenta
    Invoke-Expression $matchedCommand
}
else {
    Write-Host "`nNo command matched. Using transcript directly..." -ForegroundColor Cyan
    $polishTime = Measure-Command {
        $script:polished = $transcript.Trim()
    }
    Write-Host "Polished in $([math]::Round($polishTime.TotalSeconds,2))s" -ForegroundColor DarkGray
    Write-Host "`n--- Polished Output ---`n" -ForegroundColor Green
    Write-Output $polished

    Write-Host "`nTyping into focused window in 2 seconds - click your target window now!" -ForegroundColor Yellow
    Start-Sleep -Seconds 2
    $escaped = $polished -replace '([\{\}\(\)\+\^%~])', '{$1}'
    [System.Windows.Forms.SendKeys]::SendWait($escaped)
}

Write-Host "`n--- Total time: $([math]::Round(($recordTime.TotalSeconds + $transcribeTime.TotalSeconds),2))s (record+transcribe) ---" -ForegroundColor DarkCyan
