# Name-index refresh for Windows Task Scheduler (every 6 hours).
# Re-reads the global top 1,000 and every country's top 1,000 so players who
# climbed today can be found by name. No battle logs are fetched.
# Appends to logs\index-YYYY-MM-DD.log (logs\ is gitignored).

$project = Split-Path -Parent $PSScriptRoot
Set-Location $project
New-Item -ItemType Directory -Force -Path "logs" | Out-Null
$log = "logs\index-" + (Get-Date -Format "yyyy-MM-dd") + ".log"

function Write-Log([string]$message) {
    Add-Content -Path $log -Value ((Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " " + $message)
}

Write-Log "index refresh starting"

cmd /c "docker compose up -d >> $log 2>&1"
if ($LASTEXITCODE -ne 0) {
    Write-Log "database did not start (exit $LASTEXITCODE). Is Docker Desktop running?"
    exit $LASTEXITCODE
}

cmd /c ".venv\Scripts\python.exe -m decklens.crawl --seed-leaderboard 1000 --countries 1000 --max-players 0 --retention-days 0 >> $log 2>&1"
$code = $LASTEXITCODE
Write-Log "index refresh finished (exit $code)"
exit $code
