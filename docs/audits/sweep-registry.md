# Sweep registry: what exists, what ran, what it is worth

Proposals for sweeps are written here, not in conversation. The `Last run` column is derived
from report filenames by the sweep's `sweep_status.py`; the other columns are written by hand.
Yield is confirmed / verified candidates.

## Candidates

| Zone | Argument | Last run | Yield |
|---|---|---|---|
| `web-auth` | Login, sessions, CSRF, headers, rate limiter, route wiring, account tool and DB roles: the gate for every other route. Guarded by `tests/test_auth.py` and `tests/test_web.py`; no Host allowlist yet. | 2026-11-07 | 3/10 |
| `web-features` | Feature routes and services behind the gate: search, clan lookup, card art proxy, watch board, tiers, meta, tweaks, deck links, front end. Highest churn in the repo (app.js, app.py, services.py); re-run after the next feature burst. | 2026-11-07 | 1/8 |
| `upstream-data` | API client, crawler, collector, storage, battles, names, rankings: the API key, SQL writes and hostile upstream data. Integration tests use autocommit, so the crawl's transaction mode had no guard. | 2026-11-07 | 1/6 |
| `local-and-supply-chain` | Screen watcher, desktop launcher, window capture, model files, PowerShell scripts, CI, dependencies, git history. History clean at 89 commits. | 2026-11-07 | 1/7 |

## Proposed, not yet run

| Zone | Argument | Premise to re-verify before starting |
|---|---|---|
| `deployment` | The HTTPS deployment (roadmap weeks 7-8): reverse proxy, Host and X-Forwarded-For handling, HSTS, cookie scope, server resources for scrypt. Several 2026-11-07 findings change severity once the site is public. | that the deployment exists and the web-auth fixes have landed |

## Candidates verified and refused

Written here so they are not rediscovered. A refused candidate without a written reason comes back.

| Candidate | Zone | Reason | Report |
|---|---|---|---|
| Lockout bypass by case, Unicode or whitespace | web-auth | key and username normalised the same way | 2026-11-07-web-auth.md R6 |
| Flood `clear()` resets lockouts | web-auth | slower than waiting out the lockout | 2026-11-07-web-auth.md R5 |
| Web role can write | web-auth | SELECT only; integration test passes | 2026-11-07-web-auth.md R7 |
| SQL injection in search and tiers | web-features | parameterised, LIKE escaped | 2026-11-07-web-features.md R4 |
| Card image SSRF or poisoning | web-features | exact host and path, separate session, no redirects | 2026-11-07-web-features.md R5 |
| DOM XSS in app.js | web-features | textContent only; fixed-prefix hrefs | 2026-11-07-web-features.md R6 |
| Cross-user watcher reports | web-features | keyed on the signed-in username | 2026-11-07-web-features.md R7 |
| API key disclosure | upstream-data | repr=False, private session, no redirects | 2026-11-07-upstream-data.md R3 |
| URL injection into upstream requests | upstream-data | validated, percent-encoded segments | 2026-11-07-upstream-data.md R4 |
| Secrets in git history | local-and-supply-chain | 89 commits scanned, none real | 2026-11-07-local-and-supply-chain.md R4 |
| `httpx2` typosquat | local-and-supply-chain | pydantic's fork, imported by Starlette | 2026-11-07-local-and-supply-chain.md R5 |
