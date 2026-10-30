# DeckLens threat model

Living document: update it as features are added. Last reviewed: week 1.

## Assets

| Asset | Why it matters |
| --- | --- |
| Clash Royale API key | Lets anyone call the API as DeckLens; abuse could get the key revoked |
| Database credentials | Full access to stored battles and the player index |
| Player index | Names and tags of players DeckLens has seen; public data, but must not be scraped wholesale or kept forever |
| Server | Runs everything with a whitelisted IP |

## Trust boundaries

1. **Browser → backend:** everything from the browser is untrusted.
2. **Backend → Clash Royale API:** responses are external data. Player names in them are attacker-controlled.
3. **Backend → database:** local only, least-privilege role.

## Threats and mitigations

| Threat | Mitigation | Status |
| --- | --- | --- |
| API key committed to git | `.env` gitignored, gitleaks pre-commit hook, CI history scan | Done |
| API key in logs or errors | Excluded from `repr()`, never logged, error messages tested for leaks | Done |
| Token forwarded on redirect | `allow_redirects=False` on every request | Done |
| Tag input altering upstream URL (SSRF / path traversal) | Fixed base URL, allowlist regex, percent-encoding; tested with traversal payloads | Done |
| SQL injection via names or tags | Parameterised queries throughout | Done |
| Database exposed to the network | Docker port bound to 127.0.0.1 | Done |
| Over-privileged database access | `decklens_app` role without DROP/ALTER/TRUNCATE | Done (run `db/roles.sql`) |
| Stored XSS via player names | Escape on output, CSP | Planned (week 5) |
| Proxy abuse / quota burning | Per-IP rate limits, caching | Planned (week 5) |
| Scraping the player index | Rate-limited search, capped results, minimum query length | Planned (week 5) |
| Vulnerable dependencies | pip-audit in CI | Done |
| Over-retention of player data | Drop players not seen for 90 days | Planned |
