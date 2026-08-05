Write-Host "=== SELF-HEALING MEMORY LOOP START ===" -ForegroundColor Cyan

py organism_console\memory\rebuild_from_journal.py

Write-Host "=== MEMORY REBUILD COMPLETE ===" -ForegroundColor Green
