# DeckLens threat model

Living document: update it as features are added. Last reviewed: week 5.

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
| Over-privileged database access | `decklens_app` role without DROP/ALTER/TRUNCATE; schema changes only via owner-run migrations | Done (verified: DROP denied) |
| Stored XSS via player names | Front end only uses textContent (a test fails the build on innerHTML, eval and similar); strict CSP with no inline script; verified with hostile names in a DOM test | Done |
| Proxy abuse / quota burning | Per-client rate limits (429 + Retry-After); upstream results cached 60s in a size-bounded cache | Done |
| Scraping the player index | Search rate-limited to 20/min, at most 5 results, at least 2 letters after normalisation, LIKE wildcards escaped | Done |
| SSRF through the card image route | Browser sends only a card id; image URLs come from the official card list and must be https on api-assets.clashroyale.com with a strict path; no redirects; separate session so the API key is never sent; PNG checked by type and signature; 256 KB cap; cached | Done |
| Third-party tracking of visitors | Card art is served from our own origin (CSP img-src 'self'), so visitors' browsers never contact Supercell's servers | Done |
| Clickjacking | frame-ancestors 'none' and X-Frame-Options: DENY | Done |
| Oversized or malformed requests | Bodies over 4 KB rejected (413); request models forbid unknown fields; decks must be 8 different known card ids | Done |
| Information disclosure from the web app | Generic error messages, no stack traces, no API docs or schema endpoints, no server header, API key errors logged server-side only | Done |
| Web app able to change data | Separate read-only database role (db/web_role.sql, db/web_grants.sql) plus read-only connections; tested that it can't insert, update, delete or truncate | Done |
| Untrusted model files | Only plain JSON models are loaded, with shape and range checks | Done |
| Vulnerable dependencies | pip-audit in CI, Dependabot weekly updates | Done |
| Overloading or abusing the upstream API | Crawler request budget, minimum pause between requests, backoff on 429, stops on 403 or repeated failures | Done |
| Untrusted values from API responses (leaderboard cursors, ids, ranks) | Location and season ids allowlisted; paging cursors validated before reuse; numbers accepted only as plain integers | Done |
| Unsafe deserialisation of model files | Models exported as plain JSON (weights, archetype centroids, win counts), never pickle/joblib, so loading one can't run code; format tag, array shapes, value ranges and finite numbers checked on load | Done |
| Misleading model results (leakage and confounding) | Train on older battles, test on strictly newer ones; card vocabulary built from training data only; player identity never used; skill at battle time included so card weights aren't inflated by who plays them; models symmetric so tag-order bias can't be exploited | Done |
| Supply chain: CI actions | Dependabot updates actions; pin to commit SHAs | Partly done |
| Over-retention of player data | Nightly crawl deletes players not seen for 90 days | Done |
