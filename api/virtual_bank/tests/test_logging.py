"""Audit-logging + PII-redaction controls."""

import logging
import re
from decimal import Decimal

import pytest
from django.conf import settings

from virtual_bank.logging import PIIRedactingFilter, RequestIDFilter, get_request_id, redact
from virtual_bank.logging.pii import REDACTED

from conftest import AccountFactory, DebitCardFactory

pytestmark = [pytest.mark.audit, pytest.mark.pii]

SSN = "123-45-6789"
CARD = "4111111111111111"  # Luhn-valid
ACCOUNT = "987654321012"
EMAIL = "jane.doe@example.com"
PHONE = "(555) 123-4567"


# --------------------------------------------------------------- redact()


@pytest.mark.parametrize(
    "text,expected",
    [
        (f"ssn {SSN} end", f"ssn {REDACTED} end"),
        (f"card {CARD}", "card ****1111"),
        ("card 4111 1111 1111 1111", "card ****1111"),
        ("card 4111-1111-1111-1111", "card ****1111"),
        (f"acct {ACCOUNT}", f"acct {REDACTED}"),
        (f"mail {EMAIL}", f"mail {REDACTED}"),
        (f"tel {PHONE}", f"tel {REDACTED}"),
        ("tel 555-123-4567", f"tel {REDACTED}"),
        ("digits 1234567890123 (not luhn)", f"digits {REDACTED} (not luhn)"),
    ],
)
def test_redact_masks_pii(text, expected):
    assert redact(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "transfer.created actor_id=42 transaction_id=1 amount_sent=1000.00 currency_sent=USD",
        "rate=1.176470 amount=85.00",
        "2025-01-31 12:00:00 INFO",
        "account_id=123456789",  # 9 digits: an internal PK, not an account number
        "reason='insufficient_funds'",
        "transaction_id=73670aaa-547b-4277-b7ff-6994397108af",  # uuid segments are not account numbers
    ],
)
def test_redact_leaves_non_pii_alone(text):
    assert redact(text) == text


def test_redact_coerces_non_strings():
    assert redact(Decimal("12.50")) == "12.50"
    assert redact(None) == "None"


def test_filter_redacts_integer_args_that_look_like_numbers():
    record = make_record("card %s acct %s pk %s flag %s", (int(CARD), int(ACCOUNT), 42, True))
    PIIRedactingFilter().filter(record)
    assert record.getMessage() == f"card ****1111 acct {REDACTED} pk 42 flag True"


# ------------------------------------------------------------ the filter


def make_record(msg, args=(), **extra):
    record = logging.LogRecord("t", logging.INFO, __file__, 1, msg, args, None)
    for k, v in extra.items():
        setattr(record, k, v)
    return record


def test_filter_scrubs_message_args_and_extra():
    record = make_record(
        "user %s paid %s",
        (EMAIL, CARD),
        customer={"ssn": SSN, "tags": [PHONE], "ok": 5},
        note=f"acct {ACCOUNT}",
    )
    assert PIIRedactingFilter().filter(record) is True
    assert record.getMessage() == f"user {REDACTED} paid ****1111"
    assert record.customer == {"ssn": REDACTED, "tags": [REDACTED], "ok": 5}
    assert record.note == f"acct {REDACTED}"


def test_filter_handles_dict_args():
    record = make_record("ssn=%(ssn)s", ({"ssn": SSN},))
    PIIRedactingFilter().filter(record)
    assert record.getMessage() == f"ssn={REDACTED}"


def test_filter_leaves_standard_attrs_untouched():
    record = make_record("x")
    record.pathname = f"/tmp/{ACCOUNT}/file.py"
    PIIRedactingFilter().filter(record)
    assert record.pathname == f"/tmp/{ACCOUNT}/file.py"


# -------------------------------------------------------- configuration


def test_logging_config_attaches_redaction_to_every_handler():
    for name, handler in settings.LOGGING["handlers"].items():
        assert "pii_redact" in handler["filters"], name
        assert "request_id" in handler["filters"], name
    assert settings.MIDDLEWARE[0] == "virtual_bank.logging.RequestIDMiddleware"


def test_live_root_handlers_have_redaction_filter():
    handlers = [h for h in logging.getLogger().handlers if not type(h).__module__.startswith("_pytest")]
    assert len(handlers) == 2
    for handler in handlers:
        assert any(isinstance(f, PIIRedactingFilter) for f in handler.filters), handler


def test_request_id_filter_defaults_to_dash_outside_request():
    record = make_record("x")
    RequestIDFilter().filter(record)
    assert record.request_id == "-" == get_request_id()


# ---------------------------------------------------------- middleware


@pytest.mark.django_db
def test_request_id_middleware_generates_and_echoes_header(auth_client, url):
    resp = auth_client.get(url("transaction_history"))
    assert re.fullmatch(r"[0-9a-f]{32}", resp["X-Request-ID"])
    resp = auth_client.get(url("transaction_history"), HTTP_X_REQUEST_ID="abc-123")
    assert resp["X-Request-ID"] == "abc-123"
    assert get_request_id() == "-"  # context reset after request


# --------------------------------------------- transaction audit events


@pytest.mark.txn
@pytest.mark.django_db
def test_deposit_emits_created_event_with_ids_only(auth_client, account, url, caplog):
    with caplog.at_level(logging.INFO, logger="transactions"):
        auth_client.post(url("deposit_creation"), {"account_number": account.number, "amount": 5})
    record = next(r for r in caplog.records if "deposit.created" in r.getMessage())
    msg = record.getMessage()
    assert f"actor_id={account.user.pk}" in msg and f"payer_id={account.pk}" in msg
    assert "amount_sent=5" in msg and "currency_sent=USD" in msg
    _assert_no_pii(msg, account)


@pytest.mark.txn
@pytest.mark.django_db
def test_transfer_rejections_emit_warning_with_reason(auth_client, account, other_account, url, caplog):
    with caplog.at_level(logging.WARNING, logger="transactions"):
        auth_client.post(
            url("transfer_creation"),
            {"payer_account_number": account.number, "payee_account_number": other_account.number, "amount": 10**6},
        )
        auth_client.post(
            url("transfer_creation"),
            {"payer_account_number": other_account.number, "payee_account_number": account.number, "amount": 1},
        )
    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("transfer.rejected" in m and "reason='insufficient_funds'" in m for m in messages)
    assert any("transfer.rejected" in m and "reason='not_owner'" in m for m in messages)
    for m in messages:
        _assert_no_pii(m, account)
        _assert_no_pii(m, other_account)


@pytest.mark.txn
@pytest.mark.django_db
def test_debit_card_events_never_include_card_number(auth_client, account, debit_card, url, caplog):
    with caplog.at_level(logging.INFO, logger="transactions"):
        auth_client.post(
            url("debit_card_payment"),
            {
                "payee_account_number": account.number,
                "card_number": str(debit_card.card_number),
                "cvv": debit_card.cvv,
                "expiration_date": debit_card.expiration_date.strftime("%m/%y"),
                "amount": 5,
            },
        )
    messages = [r.getMessage() for r in caplog.records]
    assert any("debit_card.created" in m and f"card_id={debit_card.pk}" in m for m in messages)
    for m in messages:
        assert str(debit_card.card_number) not in m
        _assert_no_pii(m, account)


@pytest.mark.django_db
def test_redaction_filter_catches_accidental_pii_in_transactions_logger(caplog):
    """Even if a developer logs PII directly, the handler-level filter scrubs it."""
    account = AccountFactory()
    card = DebitCardFactory(account=account)
    logger = logging.getLogger("transactions.leak")
    handler = logging.StreamHandler()
    handler.addFilter(PIIRedactingFilter())
    captured = []
    handler.emit = lambda record: captured.append(handler.format(record))
    logger.addHandler(handler)
    try:
        logger.warning("user %s card %s acct %s", account.user.email, card.card_number, account.number)
    finally:
        logger.removeHandler(handler)
    assert captured and account.user.email not in captured[0]
    assert str(card.card_number) not in captured[0]
    assert str(account.number) not in captured[0]


def _assert_no_pii(message, account):
    user = account.user
    for secret in (user.email, user.first_name, user.last_name, user.username, str(account.number)):
        assert secret not in message, f"{secret!r} leaked into log: {message}"
