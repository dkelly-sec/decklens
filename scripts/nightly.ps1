# Nightly DeckLens crawl for Windows Task Scheduler.
# Runs from the project folder using the project's own virtual environment and
# appends output to logs\crawl-YYYY-MM-DD.log (logs\ is gitignored).

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $PSScriptRoot
Set-Location $project

New-Item -ItemType Directory -Force -Path "logs" | Out-Null
$logFile = Join-Path "logs" ("crawl-" + (Get-Date -Format "yyyy-MM-dd") + ".log")

# The database runs in Docker; start it if it isn't already running.
docker compose up -d *>> $logFile

& ".\.venv\Scripts\python.exe" -m decklens.crawl --seed-leaderboard 1000 --max-players 1500 *>> $logFile
exit $LASTEXITCODE
