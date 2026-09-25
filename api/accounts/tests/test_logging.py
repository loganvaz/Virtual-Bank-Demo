"""Audit logging: lifecycle events carry surrogate IDs only, rejections carry reasons."""

import logging

import pytest

from accounts.models import Account
from debit_cards.models import DebitCard
from virtual_bank.logging import PIIRedactingFilter

from conftest import AccountFactory

pytestmark = [pytest.mark.audit, pytest.mark.pii, pytest.mark.django_db]


def messages(caplog):
    return [r.getMessage() for r in caplog.records]


def assert_no_pii(text, account):
    card = DebitCard.objects.filter(account=account).first()
    for secret in (
        account.name,
        str(account.number),
        account.user.email,
        account.user.username,
        str(card.card_number) if card else "",
    ):
        if secret:
            assert secret not in text, f"{secret!r} leaked into log: {text}"


def test_create_current_emits_created_and_card_events(auth_client, user, url, caplog):
    with caplog.at_level(logging.INFO, logger="accounts.views"):
        resp = auth_client.post(url("account_creation"), {"name": "Audited", "account_type": "CURRENT"})
    account = Account.objects.get(pk=resp.json()["id"])
    card = DebitCard.objects.get(account=account)
    joined = "\n".join(messages(caplog))
    assert f"account.created actor_id={user.pk} account_id={account.pk}" in joined
    assert f"account.debit_card.issued actor_id={user.pk} account_id={account.pk} card_id={card.pk}" in joined
    assert_no_pii(joined, account)


def test_duplicate_create_logs_rejection(auth_client, user, url, caplog):
    AccountFactory(user=user, name="Dupe")
    with caplog.at_level(logging.WARNING, logger="accounts.views"):
        auth_client.post(url("account_creation"), {"name": "Dupe"})
    assert any("account.create.rejected" in m and "reason='duplicate_name'" in m for m in messages(caplog))


def test_other_users_detail_logs_rejection(other_client, account, url, caplog):
    with caplog.at_level(logging.WARNING, logger="accounts.views"):
        other_client.get(url("account_detail", number=account.number))
    assert any("account.detail.rejected" in m and "reason='not_found_or_not_owner'" in m for m in messages(caplog))


def test_update_and_delete_log_events(auth_client, user, url, caplog):
    account = AccountFactory(user=user, name="Lifecycle")
    with caplog.at_level(logging.INFO, logger="accounts.views"):
        auth_client.put(url("account_detail", number=account.number), {"name": "Lifecycle2"})
        auth_client.delete(url("account_detail", number=account.number))
    joined = "\n".join(messages(caplog))
    assert f"account.updated actor_id={user.pk} account_id={account.pk}" in joined
    assert f"account.deleted actor_id={user.pk} account_id={account.pk}" in joined
    assert_no_pii(joined, account)


def test_redaction_filter_catches_accidental_pii_in_accounts_logger():
    """Even if a developer logs PII directly, the handler-level filter scrubs it."""
    account = AccountFactory()
    logger = logging.getLogger("accounts.views")
    handler = logging.StreamHandler()
    handler.addFilter(PIIRedactingFilter())
    captured = []
    handler.emit = lambda record: captured.append(handler.format(record))
    logger.addHandler(handler)
    try:
        logger.warning("acct %s mail %s", account.number, account.user.email)
    finally:
        logger.removeHandler(handler)
    assert captured and str(account.number) not in captured[0] and account.user.email not in captured[0]
