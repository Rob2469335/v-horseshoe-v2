Write-Host "=== CRITIC MEMORY EVOLUTION START ===" -ForegroundColor Cyan

py -c "from organism_console.learning.critic_engine import CriticEngine; CriticEngine().evolve()"

Write-Host "=== EVOLUTION COMPLETE ===" -ForegroundColor Green
