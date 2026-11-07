# Verified security sweep: upstream-data, 2026-11-07

## Context
Scope: the Clash Royale API client, the crawler and the database writers. `src/decklens/api_client.py`,
`src/decklens/config.py`, `src/decklens/crawl.py`, `src/decklens/collector.py`, `src/decklens/storage.py`,
`src/decklens/battles.py`, `src/decklens/names.py`, `src/decklens/validation.py`, `src/decklens/rankings.py`,
`src/decklens/reindex.py`, `db/schema.sql`, `db/migrations/002_ranks_and_crawl.sql`, `db/migrations/003_player_rating.sql`.
Trigger: owner request for a full sweep. First run: no earlier report.
Commit read: `bb6c40f737fab65d9553300a5b7fd84867ab84ff`.

## Delegation
One read-only reviewer (general-purpose agent, fresh context) with the common brief. Skills loaded and
applied: securitymaxxing:security-review-method, securitymaxxing:secrets, securitymaxxing:injection,
securitymaxxing:secure-coding, securitymaxxing:crypto, owasp-security-audit:security-audit. The reviewer
ran its own throwaway Postgres; the lead re-ran the reproduction on a separate one.

## Verification
| Finding | Zone | Verdict | Claimed | Verified | Reservations | Proof |
|---|---|---|---|---|---|---|
| F1 the whole crawl is one transaction: an abort discards every battle stored that run and skips retention | upstream-data | VERIFIED REAL | Medium | Medium | the trigger is ordinary (a public IP change gives a 403, ten failed requests), not an attacker; data loss, no disclosure | `tests/test_audit_upstream_data.py::test_battles_stored_before_a_403_survive_the_abort`, ran against Postgres 16, fails as expected (0 battles kept); guard `test_a_clean_run_stores_its_battles`, ran, passes |
| R1 numbers from the battle log not limited to bounded plain integers | upstream-data | OVER-RATED | Medium | Low | the source is the TLS-verified official API, not a player | `src/decklens/battles.py:144` |
| R2 a NUL or out-of-range value in a player name aborts every nightly run | upstream-data | NEEDS DEEPER REVIEW | Medium | - | whether the game allows NUL or lone surrogates in names is unknown | `src/decklens/storage.py:122` |
| R3 API key leaks through logs, repr, redirects or another host | upstream-data | FALSE POSITIVE | - | - | lens existing mitigation: `repr=False`, private session, `allow_redirects=False` on the only call | `src/decklens/api_client.py:181` |
| R4 path or query injection from tags, clan tags, ids or cursors | upstream-data | FALSE POSITIVE | - | - | lens existing mitigation: every segment validated and percent-encoded | `src/decklens/validation.py:42` |
| R5 negative `--retention-days` deletes every player | upstream-data | OVER-RATED | Low | Low | only the owner passes it | `src/decklens/crawl.py:370` |

## Confirmed defects

### F1 (Medium): a crawl run is one transaction, so any abort throws away the night's battles
- Location: `src/decklens/storage.py:205`. Class: Data retention / integrity (CWE-460).
- Evidence chain:
  1. `players_due_for_crawl` runs a bare SELECT (`src/decklens/storage.py:205`) on the crawl's
     non-autocommit connection, so psycopg opens an implicit transaction and leaves it open.
  2. `main()` opens that connection at `src/decklens/crawl.py:409` and calls `players_due_for_crawl` at
     `src/decklens/crawl.py:435` before `crawl()`. From then on every `with conn.transaction()` in `save_battles`,
     `mark_crawled` and the rest is only a savepoint inside the open transaction.
  3. `CrawlAborted` on a 403 (`src/decklens/crawl.py:123`) or after ten failures unwinds through the connection
     block, which rolls back on an exception; the `except` that logs the abort is outside it, and
     `prune_stale_players` never runs. The integration tests connect with `autocommit=True`, so they never see
     the production transaction mode.
- Scenario: the PC's public IP changes during the 3am run, the key starts answering 403, and the task logs
  "stopped". Every battle, crawl mark and profile rank stored in the previous hours is silently discarded, the
  90-day retention delete is skipped, and the next night starts again from the same players.
- Fix approach: end the read transaction before crawling, by wrapping the SELECT in `with conn.transaction():`
  as `mark_crawled` and every other function in `storage.py` already do; add an integration test with a
  non-autocommit connection.
- Reproduction (`tests/test_audit_upstream_data.py`, needs `DECKLENS_TEST_DATABASE_URL`; replays `main()`'s
  connection block with the real `crawl()` and `_PgStore`):
  - `test_battles_stored_before_a_403_survive_the_abort`: expected to fail; ran against Postgres 16, fails as
    expected (0 of 1 stored battles kept).
  - `test_a_clean_run_stores_its_battles`: expected to pass; ran, passes.

## Refused or unconfirmed candidates
- R1, unbounded numbers from the battle log: refused as a finding (over-rated to Low), lens exploitability. `int()` at
  `src/decklens/battles.py:144` accepts booleans, numeric strings and floats, and huge values raise at the
  insert; but the response comes from the TLS-verified official API and a player cannot set these fields.
  Worth fixing for robustness with `opt_int` and ranges, as `storage._rating` does, and a per-player
  `except` in `crawl()` so one bad log costs one player.
- R2, a hostile name that aborts every run: needs deeper review. PostgreSQL refuses NUL in text, so a NUL in
  a name would raise at the battle insert (`src/decklens/storage.py:122`) and, through F1, discard the run every
  night while that battle stays in the log. Deciding needs to know whether the game lets a player put NUL or
  lone surrogates in a name; no source in the repository says.
- R3, API key disclosure: refused, lens existing mitigation. The key is read only in `config.py`, kept in a
  `repr=False` field, set only on the client's own session, and the only request (`src/decklens/api_client.py:181`)
  passes `allow_redirects=False`; card art uses a separate session without it.
- R4, URL injection: refused, lens existing mitigation. Tags go through `encode_tag` (`src/decklens/validation.py:42`),
  location and season ids and cursors are allowlisted before reuse, clan names only go in the query string.
- R5, negative retention days: refused as a finding (over-rated to Low), lens exploitability. `src/decklens/crawl.py:370` is the only
  numeric option without a range check; a typo from the owner would empty the player index.

## Coverage
Read in full by the reviewer: `src/decklens/api_client.py`, `src/decklens/config.py`, `src/decklens/crawl.py`,
`src/decklens/collector.py`, `src/decklens/storage.py`, `src/decklens/battles.py`, `src/decklens/names.py`,
`src/decklens/validation.py`, `src/decklens/rankings.py`, `src/decklens/reindex.py`, `db/schema.sql`,
`db/migrations/002_ranks_and_crawl.sql`, `db/migrations/003_player_rating.sql`, and the guarding tests.
Read by the lead: the crawl's connection block in `src/decklens/crawl.py`, `src/decklens/storage.py`
(transactions), `src/decklens/battles.py` (player parsing).
Checked, no defect: SQL parameterised everywhere; retention predicate; no catastrophic regex in `names.py`;
nothing secret or line-forging in crawl logs.
Lenses applied: exploitability, claim correctness, existing mitigation.

## Hand-off
- Proposed change: commit the crawl's read before crawling, with `tests/test_audit_upstream_data.py` in the
  same change.
- Low items to schedule: bounded integer parsing for battle-log numbers and a per-player `except` in
  `crawl()`; a range check on `--retention-days`; `response.json()` inside the client's error handling; an
  ASCII-only `Retry-After` parse.
- Decision pending for the owner: battle rows keep player tags and decks after the 90-day retention deletes
  the player's name (`db/schema.sql` has no retention for `battle_players`); README.md's retention sentence
  reads as if all player data goes.
- Decision pending: whether the game allows NUL or control characters in names (R2), which decides whether
  F1 has a player-controlled, repeating trigger.
- The reproduction tests are committed marked `xfail(strict=True)` so CI stays green while each
  defect is open; the fix removes the mark. Run them as plain tests with `pytest --runxfail`.
