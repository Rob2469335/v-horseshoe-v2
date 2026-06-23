param (
    [Parameter(Mandatory=$true)]
    [string]$AudioFile
)

# Transcribe using faster-whisper (this will trigger the download again)
$cmd = "from faster_whisper import WhisperModel; model = WhisperModel('large-v3', device='cpu', compute_type='int8'); segments, info = model.transcribe('', beam_size=5); print(' '.join([segment.text for segment in segments]))"
$transcript = python -c "$cmd"

# Send to Ollama (Ensure Ollama is running in your system tray)
$polished = ollama run llama3 'Polish the following text for technical accuracy and formatting: $transcript'

Write-Host '
--- Polished Output ---
' -ForegroundColor Green
Write-Output $polished
