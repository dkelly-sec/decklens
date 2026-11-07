# Verified security sweep: web-features, 2026-11-07

## Context
Scope: the feature routes behind the login gate and the front end. `src/decklens/web/services.py`,
`src/decklens/web/search.py`, `src/decklens/web/cards.py`, `src/decklens/web/watch.py`, `src/decklens/web/tiers.py`,
`src/decklens/web/meta.py`, `src/decklens/web/tweaks.py`, the feature handlers in `src/decklens/web/app.py`,
`src/decklens/web/static/app.js`, `src/decklens/web/static/index.html`, `src/decklens/web/static/style.css`,
`db/migrations/005_clan_search.sql`, `db/migrations/006_fuzzy_search.sql`.
Trigger: owner request for a full sweep. This is the highest-churn code in the repository (app.js 17 commits,
app.py and services.py 11 each in the last three days). First run: no earlier report.
Commit read: `bb6c40f737fab65d9553300a5b7fd84867ab84ff`.

## Delegation
One read-only reviewer (general-purpose agent, fresh context) with the common brief (pre-read of README.md,
THREAT_MODEL.md and `docs/audits/categories.md`, severity calibration, disqualifications, at most ten findings).
Skills loaded and applied: securitymaxxing:security-review-method, securitymaxxing:injection,
securitymaxxing:api, securitymaxxing:secure-coding, securitymaxxing:authz, owasp-security-audit:security-audit.
Verification and reproduction by the sweep lead.

## Verification
| Finding | Zone | Verdict | Claimed | Verified | Reservations | Proof |
|---|---|---|---|---|---|---|
| F1 one live clan lookup makes up to 41 upstream calls, not the documented 6 | web-features | VERIFIED REAL | Medium | Medium | needs a signed-in account; the larger cap was a deliberate change (commit 42643a4, asserted by `tests/test_web.py`), so the defect is that the documented budget no longer holds | `tests/test_audit_clan_lookup_budget.py::test_one_lookup_stays_within_the_documented_upstream_budget`, ran, fails as expected (41 calls); guard `test_a_lookup_still_finds_a_member`, ran, passes |
| R1 card ids are not range-checked | web-features | OVER-RATED | Low | Low | ids only become `str(int)` after a fixed https prefix | `src/decklens/web/services.py:604` |
| R2 422 responses echo the submitted input | web-features | OVER-RATED | Low | Low | echoes the caller's own input, nothing else | lead probe: `/api/clan-lookup?clan=x` returned the input in the body |
| R3 `/api/health` needs no login | web-features | OVER-RATED | Low | Low | reveals only whether a model and search are configured | lead probe: 200 without a session |
| R4 SQL injection in search, tiers and clan lookup | web-features | FALSE POSITIVE | - | - | lens existing mitigation: every statement parameterised, LIKE escaped | `src/decklens/web/search.py:137` |
| R5 SSRF or cache poisoning through the card image route | web-features | FALSE POSITIVE | - | - | lens existing mitigation: exact host, strict path, no redirects, own session | `src/decklens/web/cards.py:47` |
| R6 DOM XSS in app.js | web-features | FALSE POSITIVE | - | - | lens existing mitigation: textContent only; the only hrefs are fixed-prefix deck links and blob URLs | `src/decklens/web/static/app.js:232` |
| R7 one user reading another's watcher reports | web-features | FALSE POSITIVE | - | - | lens claim correctness: every read and write keys on the signed-in username | `src/decklens/web/app.py:359` |

## Confirmed defects

### F1 (Medium): one live clan lookup can make 41 upstream API calls, not the documented 6
- Location: `src/decklens/web/services.py:472`. Class: Rate limiting and quota (CWE-770).
- Evidence chain:
  1. `src/decklens/web/services.py:472` sets `CLANS_TO_CHECK_WITH_NAME = 40`; when a name is given, the
     batch loop fetches a member list for each of up to 40 clans and stops early only when it finds an online
     player whose name matches well.
  2. A signed-in user sends `GET /api/clan-lookup?clan=<common name>&name=<a name nobody has>`
     (`src/decklens/web/app.py:322`); each member list goes upstream through `cached_call`.
  3. The documented control does not hold: THREAT_MODEL.md promises at most 1 clan search and 5 member lists
     per lookup, and `RATE_LIMITS` (`src/decklens/web/app.py:54`) says up to 6 calls. The 6-a-minute limit
     counts lookups, not upstream calls, and a different clan name each time misses the 60-second cache.
- Scenario: a signed-in user, or a script with one stolen session, cycles 6 common clan names a minute with
  a nonsense name: about 246 calls a minute on the same key the nightly crawl uses, more when the API starts
  answering 429 and each call is retried. The asset at risk is the API key (THREAT_MODEL.md, Assets).
- Fix approach: decide the budget, then make code and docs agree. Either charge the `clan_lookup` bucket per
  member list fetched, as `limit("clan_lookup")` already charges per lookup, or lower the cap; then correct
  THREAT_MODEL.md and the `RATE_LIMITS` comment.
- Reproduction (`tests/test_audit_clan_lookup_budget.py`):
  - `test_one_lookup_stays_within_the_documented_upstream_budget`: expected to fail; ran, fails as expected
    (41 upstream calls for one lookup).
  - `test_a_lookup_still_finds_a_member`: expected to pass; ran, passes.

## Refused or unconfirmed candidates
- R1, card ids not range-checked: refused as a finding, lens exploitability; kept as Low. Unknown base ids and
  very large form ids pass `validate_deck` (`src/decklens/web/services.py:604`), but `deck_link` only writes
  integers after a fixed https prefix. Bounding ids to `0 < id < 10**9`, as `card_image` already does, closes it.
- R2, 422 responses echo the input: refused as a finding, lens exploitability; kept as Low. FastAPI's default
  validation handler (`src/decklens/web/app.py:117` builds the app without an override) returns the field and
  the caller's own value, which contradicts README.md's "errors are generic" but discloses nothing new.
- R3, `/api/health` without a login: refused as a finding, lens exploitability; kept as Low.
  `src/decklens/web/app.py:177` returns only `model` and `search` booleans; THREAT_MODEL.md says every API
  route needs a login.
- R4, SQL injection: refused, lens existing mitigation. All statements in `src/decklens/web/search.py` and
  `src/decklens/web/tiers.py` use placeholders; intervals use `make_interval(days => %s)` with bounded days; LIKE
  patterns are escaped at `src/decklens/web/search.py:137`.
- R5, card image SSRF or poisoning: refused, lens existing mitigation. URLs come only from the card list and
  must match the exact host and path (`src/decklens/web/cards.py:47`); a separate session without the API key,
  no redirects, PNG signature and 256 KB cap.
- R6, DOM XSS: refused, lens existing mitigation. `src/decklens/web/static/app.js` builds every node with
  textContent; the deck link href (`src/decklens/web/static/app.js:232`) is always `DECK_LINK` plus integers.
- R7, cross-user watcher access: refused, lens claim correctness. `watch_key` (`src/decklens/web/app.py:359`)
  takes the username from the signed session; the desktop key cannot be a valid username.

## Coverage
Read in full by the reviewer: `src/decklens/web/app.py`, `src/decklens/web/services.py`, `src/decklens/web/search.py`,
`src/decklens/web/cards.py`, `src/decklens/web/watch.py`, `src/decklens/web/tiers.py`, `src/decklens/web/meta.py`,
`src/decklens/web/tweaks.py`, `src/decklens/web/security.py`, `src/decklens/web/static/app.js`,
`db/migrations/005_clan_search.sql`, `db/migrations/006_fuzzy_search.sql`. Skimmed: `src/decklens/web/static/index.html`,
`src/decklens/web/static/style.css`, `tests/test_web.py` (clan lookup tests).
Read by the lead: the clan lookup code in `src/decklens/web/services.py` and `src/decklens/web/app.py`, and the
commit that raised the cap.
Checked, no defect: login gate on every feature route; request models forbid unknown fields; error text
never carries upstream bodies; cache keys hold only public data; no remote content in the page.
Lenses applied: exploitability, claim correctness, existing mitigation. Not checked: query plans on a real
player index (similarity scans were judged from the GIN indexes, not measured).

## Hand-off
- Proposed change: an upstream-call budget for the clan lookup, plus corrected THREAT_MODEL.md and
  `RATE_LIMITS` comment, with `tests/test_audit_clan_lookup_budget.py` in the same change (adjust
  `DOCUMENTED_BUDGET` there if the owner decides on a different number).
- Low items to schedule: bound card ids; a generic 422 handler; put `/api/health` behind the login or
  change the threat model's wording.
- Not a security issue, noted for the owner: `id(table)` in the meta and tweaks cache keys can be reused by a
  new table object, serving old stats for up to 60 seconds.
- The reproduction tests are committed marked `xfail(strict=True)` so CI stays green while each
  defect is open; the fix removes the mark. Run them as plain tests with `pytest --runxfail`.
