# Test Coverage Plan: Auth Security & PII-safe Logging

Baseline (before this work): 13 `tests.py` files, 9 empty; 3 of the 4 non-empty ones
fail on import (they call a live server with `requests`). Effective test count: 1.

## Track 1 — Authentication

### Findings from audit (`api/`)
| # | Finding | Severity | Status |
|---|---------|----------|--------|
| A1 | `vb_token` / `vb_rtoken` cookies set `httponly` but not `secure` / `samesite` | Med | Fixed: `Secure` when `DEBUG=False`, `SameSite=Lax` |
| A2 | `Logout` swallows every exception and returns HTTP 200 `{"details": "failed"}` | Low | Fixed: returns 400 |
| A3 | Access token lifetime is 2 days; refresh 15 days | Med | Documented only (product decision) |
| A4 | `BasicAuthentication` enabled globally alongside JWT | Low | Documented only |
| A5 | `CORS_ALLOW_ALL_ORIGINS = True` with `CORS_ALLOW_CREDENTIALS = True` | Med | Documented only |
| A6 | Admin endpoints correctly use `IsAdminUser`; user endpoints scope querysets by `request.user` | OK | Regression tests added |
| A7 | `PUT/PATCH /auth/update/` always 500s (`UserUpdate` had no `queryset`/`get_object`) | High | Fixed: `get_object` returns `request.user` |
| A8 | `Logout` with no `vb_rtoken` cookie called `RefreshToken(None)`, minting and blacklisting a fresh token and returning 200 | Med | Fixed: 400 when cookie missing |
| A9 | `JWTAuthenticationMiddleware` overwrote an explicit `Authorization` header with the cookie, so a stale cookie breaks header-based API clients | Med | Fixed: header takes precedence |

### Tests (`api/users/tests/test_auth.py`)
- Login: success sets both HttpOnly cookies with flags; wrong password -> 401, no cookies.
- Middleware: `vb_token` cookie is promoted to `Authorization: Bearer`; garbage cookie -> 401.
- Protected endpoints: 401 anonymous; `IsAdminUser` endpoints -> 403 for normal users, 200 for staff.
- Object scoping: user B cannot read user A's account (404).
- Refresh: rotates tokens, old refresh token is blacklisted (`ROTATE_REFRESH_TOKENS` + `BLACKLIST_AFTER_ROTATION`).
- Logout: blacklists refresh token, clears cookies; missing cookie -> 400.
- Profile update requires current password; serializer never returns `password`.

## Track 2 — Logging & PII

### Findings
| # | Finding | Status |
|---|---------|--------|
| L1 | No `LOGGING` config; nothing about auth is logged | Fixed: `LOGGING` with `virtual_bank.auth` / `virtual_bank.security` loggers |
| L2 | `print(self.user)` / `print(data)` / `print(notification)` dump user + notification content to stdout | Fixed: replaced with `logger.debug` (still redacted) |
| L3 | No safeguard against PII entering log lines | Fixed: `PIIRedactionFilter` on every handler |

### Logging policy
- **Log**: event name, `user_id`, client IP, HTTP status, token `jti`. Never usernames, emails, phone numbers, passwords, raw tokens, card numbers, CVVs.
- `PIIRedactionFilter` redacts emails, phone numbers, 13–19 digit card numbers, JWTs and `password=`/`token=` key/value pairs from the formatted message as a last line of defence.

### Tests (`api/users/tests/test_logging.py`)
- Filter unit tests for every redaction pattern.
- Login success/failure and logout emit exactly one audit line each; the line contains `user_id` and never the password, email or token.

## Parallelism
- `manage.py test --parallel auto` (Django's multiprocess runner; `tblib` added for tracebacks).
- `coverage` configured with `concurrency = multiprocessing` + `parallel = true`, combined by `run_tests.sh`.
- `run_tests.sh` writes `htmlcov/` for the coverage UI.

## Next candidates (not in this PR)
1. `api/transactions/views.py` (27%, all money movement).
2. Replace the 3 broken live-server tests in `clients/` with in-process tests.
3. Enforce `--fail-under` in CI once transactions coverage lands.
