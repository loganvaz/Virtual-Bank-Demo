"""Audit-logging and PII controls for the debit_cards views."""

import logging

import pytest

from conftest import DebitCardFactory

pytestmark = [pytest.mark.audit, pytest.mark.pii, pytest.mark.django_db]


def test_viewing_own_card_emits_viewed_event_with_ids_only(auth_client, account, url, caplog):
    card = DebitCardFactory(account=account)
    with caplog.at_level(logging.INFO, logger="debit_cards.views"):
        resp = auth_client.get(url("debit_card_detail", number=card.card_number))
    assert resp.status_code == 200
    record = next(r for r in caplog.records if "debit_card.viewed" in r.getMessage())
    msg = record.getMessage()
    assert f"actor_id={account.user.pk}" in msg
    assert f"card_id={card.pk}" in msg


def test_non_owner_lookup_emits_security_not_found(auth_client, user, debit_card, url, caplog):
    with caplog.at_level(logging.WARNING, logger="security"):
        resp = auth_client.get(url("debit_card_detail", number=debit_card.card_number))
    assert resp.status_code == 404
    record = next(
        r
        for r in caplog.records
        if r.name == "security" and "debit_card.not_found" in r.getMessage()
    )
    assert f"actor_id={user.pk}" in record.getMessage()


def test_listing_emits_listed_event_with_count(auth_client, account, url, caplog):
    DebitCardFactory(account=account)
    DebitCardFactory(account=account)
    with caplog.at_level(logging.INFO, logger="debit_cards.views"):
        resp = auth_client.get(url("debit_card_list"))
    assert resp.status_code == 200
    record = next(r for r in caplog.records if "debit_card.listed" in r.getMessage())
    msg = record.getMessage()
    assert f"actor_id={account.user.pk}" in msg
    assert "count=2" in msg


def test_debit_card_logs_never_include_card_or_account_numbers(auth_client, other_client, account, other_account, url, caplog):
    own_card = DebitCardFactory(account=account)
    other_card = DebitCardFactory(account=other_account)
    with caplog.at_level(logging.INFO):
        auth_client.get(url("debit_card_list"))
        auth_client.get(url("debit_card_detail", number=own_card.card_number))
        other_client.get(url("debit_card_detail", number=own_card.card_number))
    assert str(own_card.card_number) not in caplog.text
    assert str(other_card.card_number) not in caplog.text
    assert str(account.number) not in caplog.text
    assert str(other_account.number) not in caplog.text


def test_debit_card_logs_never_include_user_email(auth_client, other_client, account, other_account, url, caplog):
    own_card = DebitCardFactory(account=account)
    with caplog.at_level(logging.INFO):
        auth_client.get(url("debit_card_list"))
        auth_client.get(url("debit_card_detail", number=own_card.card_number))
        other_client.get(url("debit_card_detail", number=own_card.card_number))
    for user in (account.user, other_account.user):
        assert user.email not in caplog.text
        assert user.first_name not in caplog.text
        assert user.last_name not in caplog.text
