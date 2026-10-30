# DeckLens

A Clash Royale matchup predictor trained on top-ladder battles, built on the official API and secured like a production service.

> **Status:** week 2 of 8. Data pipeline: Ranked leaderboard seeding, opponent crawling, rank-at-battle-time tracking and a nightly schedule.

## Security from the first commit

- **The API key never leaves the server.** It lives only in environment variables, is excluded from `repr()` and logs, and is sent only in the `Authorization` header. Redirects are refused so it can't be forwarded to another host.
- **Secrets can't be committed.** `.env` is gitignored, a gitleaks pre-commit hook blocks secrets locally, and CI scans the full git history.
- **Untrusted input is allowlisted.** Player tags must match `#[0289PYLQGRJCUV]{3,15}` before they reach a URL or a query. The base URL is fixed, so input can't redirect requests (SSRF).
- **SQL is parameterised** everywhere, and the database runs with a [least-privilege app role](db/roles.sql), bound to localhost only.
- **Player names are treated as hostile.** They are stored as-is for display (and must be escaped when rendered), plus a normalised search key that strips emoji, fancy fonts, accents and common look-alike characters.
- **CI runs** tests, bandit (static analysis), pip-audit (known CVEs) and gitleaks on every push.

See [THREAT_MODEL.md](THREAT_MODEL.md) for the full threat model as it develops.

## Setup

1. **Get an API key.** Sign in at [developer.clashroyale.com](https://developer.clashroyale.com), create a key, and whitelist your current public IP (run `curl ifconfig.me` to find it). Keys only work from whitelisted IPs.
2. **Configure secrets.**
   ```bash
   cp .env.example .env    # then edit .env: API token and a long random DB password
   ```
3. **Install** (Python 3.11+):
   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -e ".[dev]"
   pre-commit install
   ```
4. **Start the database:**
   ```bash
   docker compose up -d
   ```
5. **Try the collector** with your own tag:
   ```bash
   python -m decklens.collector --tag "#YOURTAG" --dry-run   # fetch and summarise only
   python -m decklens.collector --tag "#YOURTAG"             # fetch and store
   ```
   The dry run prints which battle types were skipped. Ranked battles are type `pathOfLegend`; event challenges (`trail`) and friendlies (`clanMate`) are skipped.

## Collecting at scale

The crawler seeds from the Ranked leaderboard, then follows opponents best-ranked first. Top players mostly face each other, so each battle log reveals more of them.

```powershell
python -m decklens.crawl --probe                 # check response shapes; stores nothing
python -m decklens.crawl --max-players 100       # small first run
python -m decklens.crawl                         # full run: top 1,000 seeds, up to 1,500 battle logs
```

Each battle log is one API request. Runs pause between requests, back off when rate-limited, and stop at once on a 403 (bad key or IP). Each player's leaderboard rank at the time of every battle is stored, which powers the Ultimate Champion / top 1,000 / top 200 tiers. Players not seen for 90 days are deleted (retention policy).

**Upgrading an existing database** (run once, as the owner):

```powershell
Get-Content db\migrations\002_ranks_and_crawl.sql | docker compose exec -T db psql -U decklens -d decklens -v ON_ERROR_STOP=1 -f -
```

### Nightly schedule (Windows)

`scripts/nightly.ps1` starts the database if needed, runs the crawl, and writes to `logs\crawl-YYYY-MM-DD.log`. Register it once:

```powershell
$action  = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PWD\scripts\nightly.ps1`""
$trigger = New-ScheduledTaskTrigger -Daily -At 3am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
Register-ScheduledTask -TaskName "DeckLens nightly crawl" -Action $action -Trigger $trigger -Settings $settings
```

The PC must be on, Docker Desktop running, and your public IP must still match the API key.

## Development

```bash
pytest                 # tests
bandit -r src -q       # static analysis
pip-audit              # dependency vulnerabilities
```

## Roadmap

1. **Data pipeline** (weeks 1–2): collector, Postgres, leaderboard seeding, nightly runs
2. **Model** (weeks 3–4): deck encoding, logistic regression baseline, boosted trees, time-split evaluation
3. **App and hardening** (weeks 5–6): FastAPI proxy, opponent lookup, rate limits, security headers
4. **Ship** (weeks 7–8): HTTPS deployment, threat model write-up, ZAP scan

---

This content is not affiliated with, endorsed, sponsored, or specifically approved by Supercell and Supercell is not responsible for it. For more information see [Supercell's Fan Content Policy](https://supercell.com/en/fan-content-policy/).
