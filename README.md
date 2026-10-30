# DeckLens

A Clash Royale matchup predictor trained on top-ladder battles, built on the official API and secured like a production service.

> **Status:** week 1 of 8. Data pipeline in progress: API client, battle-log collector and database schema.

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
   The dry run prints which battle types were skipped. Check that ranked battles appear as kept, and adjust `KEPT_BATTLE_TYPES` in `src/decklens/battles.py` if the API uses a different name.

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
