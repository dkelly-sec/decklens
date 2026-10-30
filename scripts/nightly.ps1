# Nightly DeckLens crawl for Windows Task Scheduler.
# Appends everything to logs\crawl-YYYY-MM-DD.log (logs\ is gitignored).
#
# Docker and Python write normal progress messages to stderr. Windows
# PowerShell treats that as an error, so programs are run through cmd.exe,
# which simply appends their output to the log as plain text.

$project = Split-Path -Parent $PSScriptRoot
Set-Location $project
New-Item -ItemType Directory -Force -Path "logs" | Out-Null
$log = "logs\crawl-" + (Get-Date -Format "yyyy-MM-dd") + ".log"

function Write-Log([string]$message) {
    Add-Content -Path $log -Value ((Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " " + $message)
}

Write-Log "nightly crawl starting"

cmd /c "docker compose up -d >> $log 2>&1"
if ($LASTEXITCODE -ne 0) {
    Write-Log "database did not start (exit $LASTEXITCODE). Is Docker Desktop running?"
    exit $LASTEXITCODE
}

cmd /c ".venv\Scripts\python.exe -m decklens.crawl --seed-leaderboard 1000 --max-players 1500 >> $log 2>&1"
$code = $LASTEXITCODE
Write-Log "nightly crawl finished (exit $code)"
exit $code
