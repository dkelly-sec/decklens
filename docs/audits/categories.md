# Audit categories

Categories the verified security sweep checks in DeckLens. Categories, not patterns: a new pattern
lands under an existing category. "Covered by" names the project's own control (from README.md and
THREAT_MODEL.md); "Enforced mechanically?" says whether a test or tool fails when the control is
removed, or whether it is only a sentence. When text and guard diverge, the guard is right.

| Category | Covered by | Enforced mechanically? |
|---|---|---|
| Authentication and sessions | scrypt hashes, HMAC-signed cookie with per-user revocation, lockout after 5 failures, `__Host-` and Secure over HTTPS (`src/decklens/web/auth.py`) | yes: `tests/test_auth.py` |
| Access control | `signed_in` dependency on every API route and card image; desktop quit route only inside the desktop app | partly: `tests/test_web.py` |
| Cross-site request forgery | Origin/Referer must match on every POST; SameSite=Lax cookies (`src/decklens/web/security.py`) | yes: `tests/test_web.py` |
| Input validation | tag allowlist regex, request models forbid unknown fields, deck ids checked, 4 KB body cap | yes: `tests/test_validation.py`, `tests/test_web.py` |
| SQL construction | parameterised queries everywhere; LIKE wildcards escaped | sentence only |
| Database privilege | read-only `decklens_web` role; `decklens_app` without DROP/ALTER/TRUNCATE (`db/*.sql`) | yes, with a database: `tests/test_storage_integration.py` |
| Outbound requests (SSRF) | fixed base URL, percent-encoded validated path parts, no redirects; card art only from the official asset host | yes: `tests/test_api_client.py`, `tests/test_web.py` |
| Secrets | API key and session secret only from environment, excluded from repr and logs; gitleaks pre-commit and CI history scan | partly: `tests/test_config.py`, gitleaks in CI |
| Output encoding (XSS) | front end builds DOM with textContent only; strict CSP without inline script; names in `<bdi>` | yes: a test fails on innerHTML and similar |
| Security headers | CSP, frame-ancestors none, X-Frame-Options, no server banner (`src/decklens/web/security.py`) | yes: `tests/test_web.py` |
| Rate limiting and quota | per-client limits per route; upstream results cached 60 s in a size-bounded cache | partly: `tests/test_web.py` |
| Untrusted data from the upstream API | player names hostile; ids, cursors and ranks validated before reuse | partly |
| Deserialisation of model files | plain JSON models, shape and range checks, forward-only tree nodes | yes: `tests/test_model.py` |
| Local clients (watcher, desktop app) | watcher refuses plain http to non-local hosts; fixed subprocess commands; server bound to 127.0.0.1 | partly: `tests/test_watcher.py`, `tests/test_desktop.py` |
| Supply chain and CI | pip-audit, Dependabot, gitleaks; actions pinned to tags (SHA pinning is a TODO) | partly |
| Concurrency | in-memory rate limiter, watch board and caches shared across requests | sentence only |
| Data retention | players not seen for 90 days deleted by the nightly crawl | sentence only |
