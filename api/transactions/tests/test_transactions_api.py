"""UI/attacker-facing tests for the transactions endpoints (deposit, transfer, history)."""
from decimal import Decimal

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from notifications.models import Notification
from transactions.models import Transaction
from transactions.utils import convert_currency

pytestmark = pytest.mark.django_db

DEPOSIT = reverse("api:deposit_creation")
TRANSFER = reverse("api:transfer_creation")


# --- authentication -------------------------------------------------------

@pytest.mark.parametrize("name", ["deposit_creation", "transfer_creation", "transaction_history",
                                  "transfer_history", "deposit_list"])
def test_anonymous_is_rejected(api, name):
    assert api.get(reverse(f"api:{name}")).status_code in (401, 403)
    assert api.post(reverse(f"api:{name}"), {}).status_code in (401, 403)


def test_non_admin_cannot_use_admin_endpoints(auth_api):
    assert auth_api.get("/admin/transactions/").status_code == 403


def test_admin_can_list_transactions(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    assert client.get("/admin/transactions/").status_code == 200


# --- deposits -------------------------------------------------------------

def test_deposit_happy_path(auth_api, account):
    r = auth_api.post(DEPOSIT, {"account_number": account.number, "amount": 250})
    assert r.status_code == 201
    account.refresh_from_db()
    assert account.balance == Decimal("1250")
    assert Transaction.objects.filter(transaction_type="DEPOSIT").count() == 1


@pytest.mark.parametrize("amount", [0, -50, "abc", ""])
def test_deposit_rejects_invalid_amounts(auth_api, account, amount):
    r = auth_api.post(DEPOSIT, {"account_number": account.number, "amount": amount})
    assert r.status_code == 400
    account.refresh_from_db()
    assert account.balance == Decimal("1000")


def test_deposit_into_someone_elses_account_is_forbidden(auth_api, other_account):
    r = auth_api.post(DEPOSIT, {"account_number": other_account.number, "amount": 10})
    assert r.status_code == 403
    other_account.refresh_from_db()
    assert other_account.balance == Decimal("1000")


def test_deposit_unknown_account(auth_api):
    assert auth_api.post(DEPOSIT, {"account_number": 1, "amount": 10}).status_code == 404


# --- transfers ------------------------------------------------------------

def transfer(client, payer, payee, amount):
    return client.post(TRANSFER, {"payer_account_number": payer, "payee_account_number": payee, "amount": amount})


def test_transfer_happy_path_with_currency_conversion(auth_api, account, other_account):
    r = transfer(auth_api, account.number, other_account.number, 100)
    assert r.status_code == 201
    account.refresh_from_db(); other_account.refresh_from_db()
    assert account.balance == Decimal("900")
    expected = convert_currency(100, "USD", "EUR")[0].quantize(Decimal("0.01"))
    assert other_account.balance == Decimal("1000") + expected
    assert Notification.objects.filter(user=other_account.user, notification_type="TRANSACTION_NOTIFICATION").exists()


def test_transfer_insufficient_funds(auth_api, account, other_account):
    r = transfer(auth_api, account.number, other_account.number, 5000)
    assert r.status_code == 403
    account.refresh_from_db()
    assert account.balance == Decimal("1000")


@pytest.mark.parametrize("amount", [0, -100])
def test_transfer_negative_or_zero_amount_cannot_drain_payee(auth_api, account, other_account, amount):
    r = transfer(auth_api, account.number, other_account.number, amount)
    assert r.status_code == 400
    other_account.refresh_from_db()
    assert other_account.balance == Decimal("1000")


def test_transfer_from_someone_elses_account_is_flagged(auth_api, account, other_account):
    r = transfer(auth_api, other_account.number, account.number, 10)
    assert r.status_code == 403
    other_account.refresh_from_db()
    assert other_account.balance == Decimal("1000")
    assert Notification.objects.filter(user=other_account.user, notification_type="SECURITY_NOTIFICATION").exists()


def test_transfer_to_self_rejected(auth_api, account):
    assert transfer(auth_api, account.number, account.number, 10).status_code == 403


def test_transfer_unknown_payer_and_payee(auth_api, account):
    assert transfer(auth_api, 1, account.number, 10).status_code == 404
    assert transfer(auth_api, account.number, 1, 10).status_code == 404


def test_transfer_missing_fields(auth_api):
    assert auth_api.post(TRANSFER, {}).status_code == 400


# --- history / detail ----------------------------------------------------

def test_history_only_shows_own_transactions(auth_api, account, other_account, other_user):
    transfer(auth_api, account.number, other_account.number, 10)
    bob = APIClient(); bob.force_authenticate(user=other_user)
    # both parties see the transfer, but alice's deposit is hers alone
    auth_api.post(DEPOSIT, {"account_number": account.number, "amount": 5})
    assert auth_api.get(reverse("api:transaction_history")).data["count"] == 2
    assert bob.get(reverse("api:transaction_history")).data["count"] == 1
    assert bob.get(reverse("api:transaction_history"), {"role": "payee"}).data["count"] == 1
    assert bob.get(reverse("api:transaction_history"), {"role": "payer"}).data["count"] == 0
    assert auth_api.get(reverse("api:transaction_history"), {"role": "payer", "account_number": account.number}).data["count"] == 2
    assert auth_api.get(reverse("api:deposit_list")).data["count"] == 1
    assert auth_api.get(reverse("api:transfer_history"), {"role": "payer"}).data["count"] == 1
    assert bob.get(reverse("api:transfer_history"), {"role": "payee"}).data["count"] == 1


def test_detail_unknown_identifier_is_404(auth_api):
    import uuid
    assert auth_api.get(reverse("api:transaction_detail", args=[uuid.uuid4()])).status_code == 404
    assert auth_api.get(reverse("api:deposit_detail", args=[uuid.uuid4()])).status_code == 404
    assert auth_api.get(reverse("api:transfer_detail", args=[uuid.uuid4()])).status_code == 404


def test_detail_of_own_transaction(auth_api, account, other_account):
    transfer(auth_api, account.number, other_account.number, 10)
    tx = Transaction.objects.get()
    assert auth_api.get(reverse("api:transaction_detail", args=[tx.identifier])).status_code == 200
    assert auth_api.get(reverse("api:transfer_detail", args=[tx.identifier])).status_code == 200


# --- utils ----------------------------------------------------------------

def test_convert_currency_invalid_code():
    with pytest.raises(ValueError):
        convert_currency(1, "USD", "XXX")


def test_convert_currency_same_currency_is_identity():
    assert convert_currency(Decimal(50), "GBP", "GBP")[0] == Decimal(50)
