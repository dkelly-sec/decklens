# DeckLens

A Clash Royale matchup predictor trained on top-ladder battles, built on the official API and secured like a production service.

> **Status:** week 5 of 8. Web app: player lookup, last-opponent scouting, opponent search by name and archetype matchups, behind a hardened API.

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
python -m decklens.crawl                         # full run: top 10,000 leaderboard, up to 1,500 battle logs
```

Each battle log is one API request. Runs pause between requests, back off when rate-limited, and stop at once on a 403 (bad key or IP). Battle logs only carry a player's rank for the top 500, so the nightly run also records the leaderboard to 10,000 (about 100 requests); together they give each player's rank at the time of each battle, which powers the top 200 / 1,000 tiers. The deeper leaderboard also lets name search find players down to rank 10,000. Players not seen for 90 days are deleted (retention policy).

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

## Training the model

```powershell
python -m decklens.train                  # ranked battles from the last 14 days
python -m decklens.train --days 7         # current meta only
python -m decklens.train --days 0         # everything stored
python -m decklens.train --min-league 7   # top league only
```

This writes a report to `reports/model-YYYY-MM-DD.md`, plus `models/decklens-linear-YYYY-MM-DD.json` and `models/decklens-archetypes-YYYY-MM-DD.json`.

- **Honest evaluation:** training uses older battles and testing uses strictly newer ones, the same way the model will be used for real. Results are compared against a coin flip and a card-levels-only baseline, with 95% confidence ranges.
- **Decks, not players:** features are the cards in each deck, the average card-level gap, and how highly each player was ranked when the battle was played. Player identity is never used, so the model can't learn "this player usually wins".
- **Skill is controlled for:** strong players favour certain cards, so without skill data those cards look stronger than they are. Including rank at battle time means card weights measure what a card does between equally skilled players, and a skill-only baseline shows whether decks add anything beyond skill.
- **Recent meta only:** by default only the last 14 days are used, so balance changes don't mix old and new metas.
- **Two models:** logistic regression learns one strength per card (explainable, and swapping decks exactly flips the prediction). Gradient-boosted trees see both decks side by side, so they can learn counters.
- **Deck archetypes:** decks are grouped automatically by the cards they share (no hand-kept deck list to go stale), named after their signature cards, and compared in an archetype-versus-archetype table. Pairings with few games are pulled towards each archetype's overall record, so a 3-0 start isn't read as 100%. The report lists the most played archetypes and the matchups that are clearly lopsided.
- **No overclaiming:** "do decks matter beyond skill?" is answered by comparing the best deck model with skill alone battle by battle, with a 95% range, and only says yes if the whole range is an improvement. The lopsided-matchup list is corrected for testing every pairing at once (Bonferroni), so it doesn't fill up with lucky streaks.
- **Safe model files:** models are saved as plain JSON (weights, archetype centroids and win counts), not pickle, so loading a model file can never run code.

## Running the web app

```powershell
python -m decklens.web          # then open http://127.0.0.1:8000
```

It uses the newest models in `models\` (run `python -m decklens.train` first) and searches the player index through a **read-only** database role. Create it once:

```powershell
$pw = python -c "import secrets; print(secrets.token_urlsafe(24))"
Get-Content db\web_role.sql | docker compose exec -T db psql -U decklens -d decklens -v ON_ERROR_STOP=1 -v web_password=$pw -f -
$pw | Set-Clipboard   # then set WEB_DATABASE_URL in .env (see .env.example)
```

What the page does:
- **You:** type your name (picked from the player index) or your tag to load your current deck; your tag is remembered in your browser.
- **Last opponent:** your most recent ranked opponent, their deck, and how your archetype tends to do against theirs.
- **Find an opponent:** type the letters you can see in their name; pick them from the matches (closest rank to yours first) to see their recent decks and the matchup against yours.
- **Deck details:** card art, elixir, evolutions and levels for every card, plus average elixir, 4-card cycle cost and how many cards are maxed.
- **Matchup grid:** your win rate against their deck over the last 14 days, by how closely decks must match (exact, 7+ of 8 cards, 6+ of 8 cards on both sides, or same archetype) and by tier (top 200, top 1,000, all players). A game counts in a tier only if both players were inside it at the time: rank from the battle itself for the top 500, otherwise from the leaderboard snapshot within a day of it. Every cell shows its number of games and a 95% range; small samples are flagged.

Already created the web role before tier stats existed? Give it read access to stored battles (still read-only):

```powershell
Get-Content db\web_grants.sql | docker compose exec -T db psql -U decklens -d decklens -v ON_ERROR_STOP=1 -f -
```

How it's secured:
- The API key never reaches the browser; all lookups go through the server.
- Card art is fetched by the server from Supercell's official asset host only (never a URL from the browser), checked, cached and served from this site, so the page never loads anything from elsewhere.
- Strict Content Security Policy (no inline script), and the front end never builds HTML from data, so hostile player names display as text.
- Every route is rate-limited; upstream lookups are cached; request bodies are capped at 4 KB; inputs are validated (tags, names, decks).
- Errors are generic; there are no API docs or schema endpoints and no server banner.
- The server binds to 127.0.0.1 only. Putting it on a network comes with HTTPS in the deployment step.

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
