"""Account creation: happy paths, defaults, validation and duplicates."""

import datetime
import re
from decimal import Decimal

import pytest

from accounts.models import Account
from debit_cards.models import DebitCard
from debit_cards.utils import luhn_checksum
from notifications.models import Notification

from conftest import AccountFactory

pytestmark = [pytest.mark.django_db]


def create(client, url, **payload):
    return client.post(url("account_creation"), payload)


def account_notifications(user):
    return Notification.objects.filter(user=user, notification_type="ACCOUNT_NOTIFICATION")


def test_create_savings_account(auth_client, user, url):
    resp = create(auth_client, url, name="Everyday", account_type="SAVINGS", currency="USD")
    assert resp.status_code == 201
    body = resp.json()
    assert re.fullmatch(r"\d{11,12}", body["number"])  # generated 12-digit str (may lose a leading zero)
    assert body["user"]["id"] == user.pk or body["user"]["username"] == user.username
    account = Account.objects.get(pk=body["id"])
    assert account.user_id == user.pk
    assert not DebitCard.objects.filter(account=account).exists()
    assert account_notifications(user).count() == 1


def test_create_current_account_issues_debit_card(auth_client, user, url):
    resp = create(auth_client, url, name="Current", account_type="CURRENT")
    assert resp.status_code == 201
    account = Account.objects.get(pk=resp.json()["id"])
    card = DebitCard.objects.get(account=account)
    assert re.fullmatch(r"\d{14,16}", str(card.card_number))
    assert luhn_checksum(str(card.card_number)) == 0
    assert card.cvv
    delta = card.expiration_date.date() - datetime.date.today() if hasattr(card.expiration_date, "date") else card.expiration_date - datetime.date.today()
    assert datetime.timedelta(days=365 * 2) < delta < datetime.timedelta(days=365 * 4)
    assert account_notifications(user).count() == 2


def test_defaults_are_savings_and_ngn(auth_client, url):
    resp = create(auth_client, url, name="Defaults")
    assert resp.status_code == 201
    body = resp.json()
    assert body["account_type"] == "SAVINGS"
    assert body["currency"] == "NGN"
    account = Account.objects.get(pk=body["id"])
    assert not DebitCard.objects.filter(account=account).exists()


@pytest.mark.authz
def test_duplicate_name_for_same_user_is_rejected(auth_client, user, url):
    AccountFactory(user=user, name="Main")
    before = Account.objects.count()
    resp = create(auth_client, url, name="Main")
    assert resp.status_code == 403
    assert Account.objects.count() == before


def test_same_name_for_a_different_user_is_allowed(auth_client, other_account, url):
    resp = create(auth_client, url, name=other_account.name)
    assert resp.status_code == 201


@pytest.mark.validation
def test_invalid_account_type_is_rejected(auth_client, url):
    assert create(auth_client, url, name="Bad", account_type="CRYPTO").status_code == 400


@pytest.mark.validation
def test_invalid_currency_is_rejected(auth_client, url):
    assert create(auth_client, url, name="Bad", currency="BTC").status_code == 400


@pytest.mark.validation
def test_missing_name_is_rejected(auth_client, url):
    assert create(auth_client, url, account_type="SAVINGS").status_code == 400


@pytest.mark.validation
def test_negative_balance_is_rejected(auth_client, url):
    assert create(auth_client, url, name="Neg", balance="-10").status_code == 400


def test_explicit_balance_is_stored(auth_client, url):
    resp = create(auth_client, url, name="Funded", balance="7500")
    assert resp.status_code == 201
    account = Account.objects.get(pk=resp.json()["id"])
    assert account.balance == Decimal("7500.00")


def test_client_supplied_number_and_user_are_ignored(auth_client, other_account, user, url):
    resp = create(auth_client, url, name="Forged", number="123456789012", user=other_account.user_id)
    assert resp.status_code == 201
    account = Account.objects.get(pk=resp.json()["id"])
    assert str(account.number) != "123456789012"
    assert account.user_id == user.pk
