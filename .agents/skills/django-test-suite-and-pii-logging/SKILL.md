---
name: django-test-suite-and-pii-logging
description: Add a behavior-focused DRF test suite to a Django app in Virtual-Bank-Demo (or similar Django/DRF repos), expose latent bugs without fixing them, and add PII-redacted logging with unit tests. Use when asked to "add tests for app X", "measure coverage", or "make logging PII-safe".
---

# Django/DRF test suite + PII-redacted logging

Distilled from building `api/transactions/tests.py` + `api/virtual_bank/log_redaction.py`
(PR loganvaz/Virtual-Bank-Demo#1: 65 tests, coverage 44% -> 95%, 7 bug-exposing failures kept on purpose).

## 0. Clarify before you start (one message, non-blocking)

Flag contradictions in the request up front instead of resolving them silently. For example:
"ensure `manage.py test <app>` passes" + "expose latent bugs" + "don't fix failing tests" cannot
all be true. Ask which one wins and default to **keep bug-exposing tests failing and document them**.
Decide the scope question (just this app, or the whole repo, e.g. `print()` calls in other apps)
before you touch anything. Use a `user_question`. If there's no answer, don't expand scope. Say in
the final message that the question is still open.

If the user asks to "let me know X before you write tests", that is a checkpoint. Send the plan and
the baseline coverage, and wait for them if they asked for approval rather than just information.

## 1. Environment (known pitfalls in this repo)

Future sessions get this from the blueprint, but here is what's needed and why:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt coverage "setuptools<81"   # drf_yasg imports pkg_resources
docker start vb-pg 2>/dev/null || docker run -d --name vb-pg -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_USER=postgres -e POSTGRES_DB=virtualbank -p 5432:5432 postgres:16
cd api && export SECRET_KEY=test-secret DB_NAME=virtualbank DB_USER=postgres \
  DB_PASSWORD=postgres DB_HOST=localhost DB_PORT=5432
../.venv/bin/python manage.py makemigrations --noinput   # migrations/ are gitignored; never commit them
```

- `ValueError: Dependency on app with no migrations: users` means you forgot `makemigrations`.
- `ModuleNotFoundError: pkg_resources` means you need `setuptools<81`.
- `.github/django.yml` is **not** under `.github/workflows/`, so the PR runs no CI. Local runs are
  the only verification. Say so explicitly and don't claim CI passed.

## 2. Baseline first

Run this before writing any test and report it to the user:

```bash
../.venv/bin/coverage run --source=<app> manage.py test <app> --noinput; ../.venv/bin/coverage report -m
```

Import-time lines inflate an empty app to about 40%. Report the per-file numbers (views/utils)
and say that the function bodies are at 0%.

## 3. Read the code, then write down the bug list

Read the whole views/serializers/models/utils for the app. Note any latent bugs before you write
tests, for example:
- attribute access that can't work (`obj.transaction.date` on a `Transaction`),
- type-mismatched comparisons (`Account != User`) and `or` where `and` was meant,
- missing validation (negative amounts, non-numeric card numbers reaching `int()`).

Each bug gets **one** clearly named test that asserts the *correct* behavior and has a
docstring starting `BUG:` that explains the cause. Leave it failing. Don't use `expectedFailure`
unless the user asks, because it hides the defect from the pass rate.

## 4. Test harness pattern

- `APITestCase` with `setUpTestData` for users, accounts (in different currencies) and a card.
  Use `SimpleTestCase` for pure functions and redaction helpers.
- `urls.py` is empty, so call views directly:
  ```python
  request = APIRequestFactory().post("/", data, format="json")
  force_authenticate(request, user=user)
  response = ViewCls.as_view()(request, **kwargs)
  ```
- Patch side effects where they're *looked up* (`patch("transactions.views.process_notifications")`),
  not where they're defined.
- `@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})`
  so the `post_save` websocket signals don't need Redis.
- Cover each branch of each `perform_create`, `get_queryset` and `get_object`. For list views, test
  role=payer/payee/default × account filter, and check that another user's rows never show up.
- Check balances with `refresh_from_db()` and exact `Decimal` math. Don't use approximate floats.

## 5. PII-safe logging

- Put the shared helper in `virtual_bank/log_redaction.py`: `mask_number` (last 4 digits),
  `redact` (10–19 digit runs, emails, `cvv/expiry/password/token` key-value pairs in both `k=v`
  and `'k': 'v'` forms), a `RedactingFilter`, and `get_redacted_logger(name)`. Wire the filter into
  `settings.LOGGING` on the handler.
- In the code: log **ids, masked numbers, amounts, currencies, identifiers** only. Never log names,
  emails, full account or card numbers, CVV, expiry or raw request payloads. Pre-mask values at the
  call site, and treat the filter as a second line of defence.
- Tests: for every log call, use `assertLogs(logger, level=...)` to check that the event is emitted.
  Then run a shared `assert_no_pii(cm.output)` that checks the fixture names, emails, full
  numbers, CVV and expiry are all absent. Also unit-test `redact`/`mask_number`/the filter directly,
  including quoted-key forms (the first regex missed `'cvv': '987'`).
- Audit the rest of the repo (`rg -n "print\(|logger\.|logging\." api/`) and report PII-leaking
  calls outside the scope. Fix them only if the user confirms the wider scope.

## 6. Verify, report, PR

- Run the full app suite with coverage. Report passes/total, pass rate, and per-file coverage
  **excluding** files with no executable logic (for example an empty `urls.py`), and say that you
  excluded them.
- Lint only the files you changed (`pyflakes`). Leave pre-existing warnings alone and mention them.
- The PR body lists each deliberately failing test → root cause, plus the exact local test command.
- Final message: PR link, pass rate, baseline → final coverage, and any open scope question.
  Don't bundle `message_user` in the same parallel block as a tool whose result it depends on.

## What went badly last time (avoid)

- The repo wasn't pre-cloned. Clone to `~/repos/<name>` right away rather than searching.
- Env failures (pkg_resources, missing migrations) cost several iterations. Do step 1 first.
- The "tests must pass" vs "don't fix failures" conflict wasn't raised explicitly. Do step 0.
- The plan checkpoint was treated as informational and work went ahead without waiting.
  Confirm whether the user wants to approve the plan.
- The final message was rejected because it was sent in parallel with a blueprint update.
