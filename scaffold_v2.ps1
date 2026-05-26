# scaffold_v2.ps1
# Run from PowerShell: cd C:\Users\rober\Projects; .\scaffold_v2.ps1
# Creates the full v-horseshoe-v2 project structure

$root = "C:\Users\rober\Projects\v-horseshoe-v2"

$folders = @(
    "core\agents",
    "core\runtime",
    "features\chat_search",
    "features\upwork_analyzer",
    "features\vscode_automation",
    "lib\mcp",
    "lib\vector",
    "blueprints\file_folder_automation",
    "blueprints\web_research",
    "blueprints\upwork_scanner",
    "api\routes",
    "api\websocket",
    "app\static\css",
    "app\static\js",
    "app\templates",
    "config\prompts",
    "data\logs",
    "data\sessions",
    "data\qdrant",
    "data\cache",
    "data\exports",
    "scripts",
    "tests\unit",
    "tests\integration"
)

Write-Host "`n  v-horseshoe-v2 scaffold" -ForegroundColor Cyan
Write-Host "  Location: $root`n" -ForegroundColor Cyan

foreach ($folder in $folders) {
    $path = Join-Path $root $folder
    if (!(Test-Path $path)) {
        New-Item -Path $path -ItemType Directory -Force | Out-Null
        Write-Host "  [+] $folder" -ForegroundColor Green
    } else {
        Write-Host "  [=] $folder (exists)" -ForegroundColor DarkGray
    }
}

# Python files to create
$pyFiles = @(
    "bootstrap.py",
    "config\settings.py",
    "config\__init__.py",
    "core\__init__.py",
    "core\runtime\__init__.py",
    "core\runtime\task_queue.py",
    "core\event_bus.py",
    "core\router.py",
    "core\agents\__init__.py",
    "core\agents\base.py",
    "core\agents\swarm_adapter.py",
    "features\__init__.py",
    "features\chat_search\__init__.py",
    "features\chat_search\handler.py",
    "features\upwork_analyzer\__init__.py",
    "features\upwork_analyzer\handler.py",
    "features\vscode_automation\__init__.py",
    "features\vscode_automation\handler.py",
    "lib\__init__.py",
    "lib\mcp\__init__.py",
    "lib\mcp\registry.py",
    "lib\mcp\playwright.py",
    "lib\mcp\filesystem.py",
    "lib\mcp\context7.py",
    "lib\vector\__init__.py",
    "lib\vector\qdrant_store.py",
    "lib\vector\reranker.py",
    "api\__init__.py",
    "api\routes\__init__.py",
    "api\routes\blueprints.py",
    "api\routes\features.py",
    "api\routes\health.py",
    "api\websocket\__init__.py",
    "api\websocket\console.py",
    "tests\__init__.py",
    "tests\unit\__init__.py",
    "tests\integration\__init__.py"
)

# Config / data files
$dataFiles = @(
    "config\prompts\planner.md",
    "config\prompts\executor.md",
    "config\prompts\critic.md",
    "blueprints\file_folder_automation\manifest.json",
    "blueprints\file_folder_automation\automate.py",
    "blueprints\file_folder_automation\prompt.md",
    "blueprints\web_research\manifest.json",
    "blueprints\web_research\automate.py",
    "blueprints\web_research\prompt.md",
    "blueprints\upwork_scanner\manifest.json",
    "blueprints\upwork_scanner\automate.py",
    "blueprints\upwork_scanner\prompt.md",
    ".env.example",
    "requirements.txt",
    "README.md"
)

Write-Host "`n  Creating Python files..." -ForegroundColor Yellow
foreach ($file in $pyFiles) {
    $path = Join-Path $root $file
    if (!(Test-Path $path)) {
        New-Item -Path $path -ItemType File -Force | Out-Null
        Write-Host "  [+] $file" -ForegroundColor DarkCyan
    }
}

Write-Host "`n  Creating config and blueprint files..." -ForegroundColor Yellow
foreach ($file in $dataFiles) {
    $path = Join-Path $root $file
    if (!(Test-Path $path)) {
        New-Item -Path $path -ItemType File -Force | Out-Null
        Write-Host "  [+] $file" -ForegroundColor DarkCyan
    }
}

Write-Host "`n  Scaffold complete." -ForegroundColor Cyan
Write-Host "  Next: copy config\settings.py and bootstrap.py into place, then:" -ForegroundColor White
Write-Host "    cd $root" -ForegroundColor White
Write-Host "    pip install -r requirements.txt" -ForegroundColor White
Write-Host "    uvicorn bootstrap:app --reload --port 8100`n" -ForegroundColor White
