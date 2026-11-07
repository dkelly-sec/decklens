# Verified security sweep: local-and-supply-chain, 2026-11-07

## Context
Scope: code that runs on the owner's PC outside the web server, model files, and the build and dependency
chain. `src/decklens/watcher.py`, `src/decklens/desktop.py`, `src/decklens/wincapture.py`, `src/decklens/model.py`,
`src/decklens/train.py`, `src/decklens/dataset.py`, `src/decklens/features.py`, the file loading in
`src/decklens/archetypes.py`, `scripts/nightly.ps1`, `scripts/refresh-index.ps1`, `scripts/install-desktop.ps1`,
`.github/workflows/ci.yml`, `.github/dependabot.yml`, `.pre-commit-config.yaml`, `pyproject.toml`,
`docker-compose.yml`, `.env.example`, `.gitignore`, and the full git history (89 commits).
Trigger: owner request for a full sweep. First run: no earlier report.
Commit read: `bb6c40f737fab65d9553300a5b7fd84867ab84ff`.

## Delegation
One read-only reviewer (general-purpose agent, fresh context) with the common brief. Skills loaded and
applied: securitymaxxing:security-review-method, securitymaxxing:deps, securitymaxxing:harden,
securitymaxxing:secrets, securitymaxxing:secure-coding, owasp-security-audit:security-audit. Tools run:
bandit, pip-audit (installed set, lowest allowed versions, watcher extra), and a git history scan.
The lead checked the `httpx2` dependency on PyPI before delegating and re-ran the watcher reproduction live.

## Verification
| Finding | Zone | Verdict | Claimed | Verified | Reservations | Proof |
|---|---|---|---|---|---|---|
| F1 the watcher's "plain http only to this computer" check is bypassed with a backslash, sending the password to another host | local-and-supply-chain | VERIFIED REAL | Medium | Medium | needs someone to influence the watcher's `--url` or `DECKLENS_URL` setting | `tests/test_audit_watcher_url.py::test_backslash_tricks_cannot_pass_for_this_computer`, ran, fails as expected; guard `test_this_computer_and_https_servers_are_still_accepted`, ran, passes; live: a listener on 127.0.0.2 received the login POST with the password |
| R2 watcher POSTs follow redirects | local-and-supply-chain | OVER-RATED | Low | Low | needs the configured server to answer 307 or 308 | `src/decklens/watcher.py:419` |
| R3 model loader checks incomplete (leaf feature range, weight shape, archetype numbers) | local-and-supply-chain | OVER-RATED | Low | Low | only the owner writes the models folder; no request picks a file; cannot loop or run code | `src/decklens/model.py:308` |
| R4 secrets in git history | local-and-supply-chain | FALSE POSITIVE | - | - | lens claim correctness: all 89 commits, no deleted files, no real token or password | lead and reviewer history scan |
| R5 `httpx2` is a typosquat | local-and-supply-chain | FALSE POSITIVE | - | - | lens claim correctness: pydantic's fork, repository exists, Starlette's TestClient imports it | `pyproject.toml:28` |
| R6 CI secret scan does not cover the full history | local-and-supply-chain | OVER-RATED | Low | Low | the history was scanned by hand in this sweep and is clean | `.github/workflows/ci.yml:53` |
| R7 database container receives every secret from `.env` | local-and-supply-chain | OVER-RATED | Low | Low | readable only by someone who can already run `docker inspect` on the PC | `docker-compose.yml:6` |

## Confirmed defects

### F1 (Medium): a backslash URL passes the watcher's local-only check and sends the password elsewhere
- Location: `src/decklens/watcher.py:407`. Class: Local clients / outbound requests (CWE-436).
- Evidence chain:
  1. `src/decklens/watcher.py:407` decides "this computer" from `urlsplit(...).hostname`, and
     `src/decklens/watcher.py:410` builds the base URL from the raw netloc.
  2. The URL comes from `--url` or `DECKLENS_URL` and is used for `login()`, which posts the username and
     password to it. For `http://other-host\@127.0.0.1:8000`, `urlsplit` reads the host as 127.0.0.1, but
     requests (urllib3) treats the backslash as the start of the path and connects to `other-host`.
  3. THREAT_MODEL.md's control ("refuses plain http to anything but this computer") therefore does not hold;
     the existing test (`tests/test_watcher.py:279`) tries only a plain foreign host, ftp and javascript URLs.
- Scenario: a setup guide or a pasted `.env` line gives `DECKLENS_URL=http://their-host\@127.0.0.1:8000`. It
  looks local, the watcher accepts it, and the DeckLens password goes in clear text to their host, the same
  password that will unlock the internet-facing site after deployment.
- Fix approach: refuse any netloc containing a backslash, `@`, a username or a password, and rebuild the base
  from `hostname` and `port`, in the exact-host style of `safe_asset_url` in `src/decklens/web/cards.py`.
- Reproduction (`tests/test_audit_watcher_url.py`):
  - `test_backslash_tricks_cannot_pass_for_this_computer` (two URLs): expected to fail; ran, fails as expected.
  - `test_this_computer_and_https_servers_are_still_accepted` (127.0.0.1, localhost, [::1], an https host):
    expected to pass; ran, passes.
  - Live observation (lead): with `http://127.0.0.2:<port>\@127.0.0.1:8000`, a listener on 127.0.0.2 received
    `POST /%5C@127.0.0.1:8000/api/login` with `Host: 127.0.0.2:<port>` and the password in the JSON body.

## Refused or unconfirmed candidates
- R1, DNS rebinding reaching the desktop quit route with logins off: out of zone, web-auth. The defect is in
  `src/decklens/web/security.py:100` and was confirmed and reproduced in this run as web-auth F1
  (`2026-11-07-web-auth.md`); the desktop app serves the same app, so the Host allowlist closes both. Not
  counted in this zone's yield.
- R2, watcher POSTs follow redirects: refused as a finding (over-rated to Low), lens exploitability. `requests` re-sends the JSON
  body on 307 or 308; only the configured server can send one. Pass `allow_redirects=False` as
  `api_client.py` and `cards.py` already do.
- R3, model loader gaps: refused as a finding (over-rated to Low), lens exploitability. A leaf node's feature index is not
  range-checked (`src/decklens/model.py:308`), linear weights are not checked to be one-dimensional, and the
  archetype loader does not check `wins`, `games` and `prior_strength` are finite, contrary to
  THREAT_MODEL.md. A crafted file raises an error or gives wrong numbers; it cannot loop or run code, and
  only the owner writes the models folder.
- R4, secrets in history: refused, lens claim correctness. All refs, all 89 commits, deleted files, stashes and
  unreachable objects checked; `.env` was never committed; every database URL is a placeholder or test value.
  Nothing to rotate.
- R5, `httpx2` typosquat: refused, lens claim correctness. PyPI links it to github.com/pydantic/httpx2, which
  exists; Starlette 1.10's TestClient imports `httpx2`.
- R6, CI secret scan scope: refused as a finding (over-rated to Low). The reviewer reports that gitleaks-action v3 scans only the
  pushed range on push and pull_request events, contrary to README.md's "CI scans the full git history";
  not re-verified by the lead. Adding a scheduled run makes it scan everything.
- R7, secrets in the database container: refused as a finding (over-rated to Low), lens exploitability. `env_file: .env` passes the
  API key and session secret into the Postgres container; pass only the `POSTGRES_*` variables.

## Coverage
Read in full by the reviewer: `src/decklens/watcher.py`, `src/decklens/desktop.py`, `src/decklens/wincapture.py`,
`src/decklens/model.py`, `src/decklens/train.py`, `src/decklens/dataset.py`, `src/decklens/features.py`, the three
PowerShell scripts, `.github/workflows/ci.yml`, `.github/dependabot.yml`, `.pre-commit-config.yaml`,
`pyproject.toml`, `docker-compose.yml`, `.env.example`, `.gitignore`. Read in part: `src/decklens/archetypes.py`
(loading only). Read by the lead: the watcher client in `src/decklens/watcher.py`, `pyproject.toml`, `ci.yml`.
Tool results: bandit exit 0, no issues; pip-audit found no known vulnerabilities in the installed set, at the
lowest allowed versions, or in the watcher extra.
Checked, no defect: desktop subprocess commands are fixed lists; the server is fixed to 127.0.0.1; models load
as plain JSON only; CI uses `push` and `pull_request` with read-only permissions; the database port is bound
to localhost.
Not checked: whether Edge on the owner's PC prompts before a public page reaches localhost; GitHub's own
secret scanning and push protection settings.

## Hand-off
- Proposed change: strict host parsing in `DeckLensClient`, with `tests/test_audit_watcher_url.py` in the same
  change (or its cases merged into the parametrised test in `tests/test_watcher.py`).
- Low items to schedule: `allow_redirects=False` on watcher POSTs; complete the model loader checks the
  threat model already claims; a scheduled full-history gitleaks run; pass only `POSTGRES_*` to the database
  container; pin `postgres:16` and the CI actions by digest (the actions are a documented TODO); add the
  `[watcher]` extra to CI's pip-audit.
- Informational: older commits carry a personal email address as author; later commits use a GitHub
  noreply address.
- The reproduction tests are committed marked `xfail(strict=True)` so CI stays green while each
  defect is open; the fix removes the mark. Run them as plain tests with `pytest --runxfail`.
