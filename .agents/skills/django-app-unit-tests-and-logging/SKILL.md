---
name: django-app-unit-tests-and-logging
description: Add unit tests, structured PII-redacting logging, and before/after coverage reports to a Django/DRF app in this repo (api/ or clients/). Use when asked to test an app, raise coverage, add logging, or produce a coverage/pass-rate PR. Reference implementation: api/transactions (PR #9).
---

# Django app: unit tests + logging + coverage reports

Follow the steps in order. Reference implementation: `api/transactions/tests/`,
`api/virtual_bank/logging.py`, `api/run_tests_with_reports.sh`.

## 1. Baseline (record before touching code)

```bash
docker compose up -d db redis api        # stack must be running; tests run inside the api container
docker compose exec api ./run_tests_with_reports.sh <app>
```

Record: test count, pass rate, coverage % from `api/reports/coverage.txt`.
A stub `tests.py` (3-line Django template) counts as **0 tests / 0%**. Legacy tests that
`ModuleNotFoundError` on import also count as 0 passing — note it in the PR.

## 2. Audit the app

Read `views.py`, `serializers.py`, `models.py`, `utils.py`. Build a branch list:

- every create/reject path (happy, insufficient funds, wrong owner, not found, invalid input)
- every auth/authz check: `IsAuthenticated`, `IsAdminUser`, owner vs. participant, superuser
- crash risks: `Account != User` comparisons, `.transaction.date`-style bad attrs,
  `int(x)` on unvalidated input, `IntegerField` amounts accepting `<= 0`,
  non-atomic multi-row balance updates
- detail views reachable by UUID → check for IDOR (any authenticated user can read)

Tag each item **tests-only** or **bug** (needs a code fix). Fix bugs minimally in the same PR
(guard/validation/403), never refactor business logic.

## 3. Test scaffold

```
git rm <app>/tests.py
<app>/tests/__init__.py
<app>/tests/helpers.py                     # factories + base TestCase
<app>/tests/test_utils_and_serializers.py
<app>/tests/test_create_views.py
<app>/tests/test_read_views_and_auth.py
<app>/tests/test_logging.py
```

Rules:
- Django `TestCase` / DRF `APITestCase` + `APIClient` only. **No** live-server `requests`,
  no `../json/*.json` fixtures, no hard-coded credentials.
- Factories in `helpers.py` (`make_user`, `make_account`, `make_card`, ...) with an
  `itertools.count` for unique usernames/account numbers.
- Base class decorated with the in-memory Channels layer (creating models fires
  WebSocket signals that otherwise need Redis):
  ```python
  in_memory_channels = override_settings(
      CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
  @in_memory_channels
  class <App>APITestCase(APITestCase):
      """One-line description of fixtures the base sets up."""
  ```
- **Every class gets a one-line docstring** saying what it covers (user requirement).
- Use `self.assertLogs("transactions", "WARNING")` / `("security", ...)` to assert events.
- Auth tests: unauthenticated → 401/403, wrong user → 403 (IDOR regression), admin → 200,
  JWT bearer + `vb_token` cookie both accepted, invalid JWT rejected, and a meta-test that
  each view declares explicit `permission_classes`.
- Target ≥90% line coverage on the app. Don't chase 100% on defensive branches.

## 4. Logging

Use the shared module, do not create per-app configs:

```python
from virtual_bank.logging import get_logger
logger = get_logger("<app>")          # business events
security_logger = get_logger("security")  # auth/authz rejections
```

- Add a `_reject(request, event, reason, exc_class, detail, security=False, **fields)`
  helper: logs `WARNING` with `extra={"event": f"{event}.rejected", "reason": ..., "user_id": ...}`
  then raises. Log `INFO` `event=<action>.created` on success.
- Allowed fields (see `STRUCTURED_FIELDS`): `event reason user_id account_id payee_account_id
  txn_id amount currency currency_sent currency_received rate`. Only PKs/UUIDs/amounts.
- **Never** pass account `number`, card number/CVV/expiry, names, emails, `description`,
  notification text, or credentials. `PIIRedactingFilter` on the handler is defense in depth,
  not a licence.
- Replace any `print()` in the app with logger calls.
- If a new logger name is used, add it to `build_logging_config()` in `virtual_bank/logging.py`.
- For the `clients` project: import `build_logging_config` into `clients/virtual_bank/settings.py`
  (`LOGGING = build_logging_config(...)`) — it is not wired there yet.

## 5. Reports ("after" and "before")

```bash
docker compose exec api ./run_tests_with_reports.sh <app>
# -> api/reports/coverage_html/index.html, test_results.html, coverage.txt, test-logs.jsonl, PII hit count
```

"Before" failures report = new suite against the **original** source (shows which tests
catch the bugs). **Commit your changes first** — the restore step below is `checkout HEAD`,
so uncommitted edits to those files would be lost:

```bash
git checkout main -- api/<app>/views.py api/<app>/serializers.py api/<app>/utils.py
docker compose exec -e TEST_REPORT_DIR=reports_before api ./run_tests_with_reports.sh <app>
git checkout HEAD -- api/<app>/views.py api/<app>/serializers.py api/<app>/utils.py
```

Confirm the PII scan prints `hits: 0`; if not, find the offending log call — do not widen the
regex to hide it. Zip `coverage_html/` and attach both `test_results.html` files to the user.

## 6. PR

Body must contain a before/after table: tests, pass %, coverage % (app + logging module),
log-statement count, PII hits; then bug fixes the tests forced; then an explicit
out-of-scope list. Attach the HTML reports in the chat, not the repo (`api/reports*/` is
gitignored).

## Gotchas

- `coverage`/`xmlrunner`/`junit2html` are in `requirements.txt`; if the container predates
  them, `docker compose build api` or `pip install` inside it. Always call `python -m coverage`.
- `TEST_REPORT_DIR` env switches `TEST_RUNNER` to `XMLTestRunner` (see `settings.py`); without it
  `manage.py test` runs normally.
- `Decimal` amounts render as `"5.00"` in logs — assert on the formatted string.
- Build exchange rates etc. from strings: `Decimal("0.85")`, never `Decimal(0.85)`.
