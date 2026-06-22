param (
    [Parameter(Mandatory=$true)]
    [string]$AudioFile
)

# 1. Set the file path as an environment variable
$env:TARGET_AUDIO = (Resolve-Path $AudioFile).Path

# 2. Python will read the path from the environment variable instead of the command string
$pythonCode = "import os; from faster_whisper import WhisperModel; path = os.environ['TARGET_AUDIO']; model = WhisperModel('large-v3', device='cpu', compute_type='int8'); segments, info = model.transcribe(path, beam_size=5); print(' '.join([segment.text for segment in segments]))"

Write-Host 'Transcribing audio... this may take a moment.' -ForegroundColor Yellow
$transcript = python -c "$pythonCode"

# 3. Check if we got text
if ([string]::IsNullOrWhiteSpace($transcript)) {
    Write-Error 'Transcription failed. The model loaded but did not produce text.'
} else {
    # Send to Ollama
    $polished = ollama run llama3 'Polish the following text for technical accuracy and formatting: $transcript'
    Write-Host '
--- Polished Output ---
' -ForegroundColor Green
    Write-Output $polished
}
