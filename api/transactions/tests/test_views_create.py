"""Deposit / transfer / debit-card creation: happy paths and every error arm."""

from datetime import datetime
from decimal import Decimal

import pytest

from notifications.models import Notification
from transactions.models import Transaction

from conftest import AccountFactory, DebitCardFactory

pytestmark = [pytest.mark.txn, pytest.mark.django_db]


def future_expiry():
    now = datetime.now()
    return f"{now.month:02d}/{(now.year + 2) % 100:02d}"


# ---------------------------------------------------------------- deposits


def test_deposit_credits_balance_and_records_transaction(auth_client, account, url):
    resp = auth_client.post(url("deposit_creation"), {"account_number": account.number, "amount": 250})
    assert resp.status_code == 201, resp.json()
    account.refresh_from_db()
    assert account.balance == Decimal("1250.00")
    txn = Transaction.objects.get()
    assert (txn.transaction_type, txn.amount_sent, txn.amount_received, txn.rate) == ("DEPOSIT", 250, 250, 1)
    assert txn.payer == txn.payee == txn.account == account
    assert resp.json()["amount_received"] == "250.00"
    assert Notification.objects.filter(user=account.user, notification_type="TRANSACTION_NOTIFICATION").count() == 1


def test_deposit_unknown_account_404(auth_client, url):
    resp = auth_client.post(url("deposit_creation"), {"account_number": 999999999999, "amount": 1})
    assert resp.status_code == 404
    assert Transaction.objects.count() == 0


@pytest.mark.authz
def test_deposit_into_someone_elses_account_403(auth_client, other_account, url):
    resp = auth_client.post(url("deposit_creation"), {"account_number": other_account.number, "amount": 1})
    assert resp.status_code == 403
    other_account.refresh_from_db()
    assert other_account.balance == Decimal("1000.00")


@pytest.mark.validation
@pytest.mark.parametrize("payload", [{}, {"amount": 1}, {"account_number": "1"}, {"account_number": "1", "amount": "x"}])
def test_deposit_validation_errors_400(auth_client, url, payload):
    assert auth_client.post(url("deposit_creation"), payload).status_code == 400


# ---------------------------------------------------------------- transfers


def transfer(client, url, payer, payee, amount, **extra):
    return client.post(
        url("transfer_creation"),
        {"payer_account_number": payer.number, "payee_account_number": payee.number, "amount": amount, **extra},
    )


def test_transfer_same_currency_moves_funds(auth_client, account, other_user, url):
    payee = AccountFactory(user=other_user, currency="USD", balance=0)
    resp = transfer(auth_client, url, account, payee, 300, description="rent")
    assert resp.status_code == 201, resp.json()
    account.refresh_from_db()
    payee.refresh_from_db()
    assert (account.balance, payee.balance) == (Decimal("700.00"), Decimal("300.00"))
    txn = Transaction.objects.get()
    assert txn.transaction_type == "TRANSFER" and txn.rate == 1 and txn.description == "rent"
    # both parties notified
    assert Notification.objects.filter(user=account.user).count() == 1
    assert Notification.objects.filter(user=other_user).count() == 1


def test_transfer_cross_currency_applies_rate(auth_client, account, other_account, url):
    resp = transfer(auth_client, url, account, other_account, 100)  # USD -> EUR
    assert resp.status_code == 201
    other_account.refresh_from_db()
    assert other_account.balance == Decimal("1085.00")
    txn = Transaction.objects.get()
    assert (txn.currency_sent, txn.currency_received) == ("USD", "EUR")
    assert txn.amount_received == Decimal("85.00")
    assert txn.rate.quantize(Decimal("0.01")) == Decimal("1.18")


def test_transfer_between_own_accounts_uses_self_notification(auth_client, account, user, url):
    savings = AccountFactory(user=user, account_type="SAVINGS", balance=0)
    assert transfer(auth_client, url, account, savings, 10).status_code == 201
    assert Notification.objects.filter(user=user).count() == 1


def test_transfer_to_same_account_rejected(auth_client, account, url):
    resp = transfer(auth_client, url, account, account, 10)
    assert resp.status_code == 403
    assert "identical" in resp.json()["detail"]
    assert Notification.objects.filter(user=account.user).exists()


def test_transfer_unknown_payer_404(auth_client, other_account, url):
    ghost = type("A", (), {"number": 111111111111})
    assert transfer(auth_client, url, ghost, other_account, 10).status_code == 404


@pytest.mark.authz
def test_transfer_from_someone_elses_account_403_and_flags_owner(auth_client, account, other_account, url):
    resp = transfer(auth_client, url, other_account, account, 10)
    assert resp.status_code == 403
    assert Notification.objects.filter(user=other_account.user, notification_type="SECURITY_NOTIFICATION").exists()
    other_account.refresh_from_db()
    assert other_account.balance == Decimal("1000.00")


def test_transfer_unknown_payee_404(auth_client, account, url):
    ghost = type("A", (), {"number": 222222222222})
    resp = transfer(auth_client, url, account, ghost, 10)
    assert resp.status_code == 404
    assert Notification.objects.filter(user=account.user).exists()


def test_transfer_insufficient_funds_403_no_side_effects(auth_client, account, other_account, url):
    resp = transfer(auth_client, url, account, other_account, 1001)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Insufficient funds"
    account.refresh_from_db()
    assert account.balance == Decimal("1000.00")
    assert Transaction.objects.count() == 0


def test_transfer_exact_balance_allowed(auth_client, account, other_account, url):
    assert transfer(auth_client, url, account, other_account, 1000).status_code == 201
    account.refresh_from_db()
    assert account.balance == 0


# ---------------------------------------------------------------- debit cards


def pay(client, url, account, card, amount=50, **overrides):
    data = {
        "payee_account_number": account.number,
        "card_number": str(card.card_number),
        "cvv": card.cvv,
        "expiration_date": card.expiration_date.strftime("%m/%y"),
        "amount": amount,
    }
    data.update(overrides)
    return client.post(url("debit_card_payment"), data)


def test_debit_card_payment_moves_funds_cross_currency(auth_client, account, debit_card, url):
    resp = pay(auth_client, url, account, debit_card, 85)  # EUR card -> USD account
    assert resp.status_code == 201, resp.json()
    account.refresh_from_db()
    debit_card.account.refresh_from_db()
    assert debit_card.account.balance == Decimal("915.00")
    assert account.balance == Decimal("1100.00")
    txn = Transaction.objects.get()
    assert txn.transaction_type == "DEBIT_CARD" and txn.payer == debit_card.account and txn.payee == account
    assert Notification.objects.filter(user=account.user).count() == 1
    assert Notification.objects.filter(user=debit_card.account.user).count() == 1


def test_debit_card_payment_to_own_other_account(auth_client, account, user, url):
    savings = AccountFactory(user=user, balance=500)
    card = DebitCardFactory(account=savings)
    assert pay(auth_client, url, account, card, 20).status_code == 201
    assert Notification.objects.filter(user=user).count() == 1


def test_debit_card_unknown_payee_404(auth_client, debit_card, url):
    ghost = type("A", (), {"number": 333333333333})
    assert pay(auth_client, url, ghost, debit_card).status_code == 404


@pytest.mark.authz
def test_debit_card_payee_not_owned_403(auth_client, other_account, debit_card, url):
    assert pay(auth_client, url, other_account, debit_card).status_code == 403


@pytest.mark.validation
@pytest.mark.parametrize(
    "expiry,detail",
    [
        ("13/30", "Invalid month"),
        ("00/30", "Invalid month"),
        ("01/20", "Card has expired"),
        ("12/100", "Invalid year"),
        ("garbage", "Invalid expiry date"),
        ("1230", "Invalid expiry date"),
        ("aa/bb", "Invalid expiry date"),
    ],
)
def test_debit_card_expiry_validation(auth_client, account, debit_card, url, expiry, detail):
    resp = pay(auth_client, url, account, debit_card, expiration_date=expiry)
    assert resp.status_code == 403
    assert resp.json()["detail"] == detail


def test_debit_card_expired_this_year_earlier_month(auth_client, account, debit_card, url):
    now = datetime.now()
    if now.month == 1:
        pytest.skip("no earlier month in January")
    expiry = f"{now.month - 1:02d}/{now.year % 100:02d}"
    resp = pay(auth_client, url, account, debit_card, expiration_date=expiry)
    assert resp.json()["detail"] == "Card has expired"


@pytest.mark.validation
def test_debit_card_luhn_failure(auth_client, account, debit_card, url):
    resp = pay(auth_client, url, account, debit_card, card_number="4111111111111112")
    assert resp.status_code == 403 and resp.json()["detail"] == "Invalid card number"


@pytest.mark.parametrize("field,value", [("cvv", "999"), ("card_number", "4111111111111111"), ("expiration_date", future_expiry())])
def test_debit_card_details_mismatch(auth_client, account, debit_card, url, field, value):
    resp = pay(auth_client, url, account, debit_card, **{field: value})
    assert resp.status_code == 403 and resp.json()["detail"] == "Invalid card"


def test_debit_card_same_account_rejected(auth_client, account, url):
    card = DebitCardFactory(account=account)
    resp = pay(auth_client, url, account, card)
    assert resp.status_code == 403 and "cannot be the same" in resp.json()["detail"]


def test_debit_card_insufficient_funds(auth_client, account, debit_card, url):
    resp = pay(auth_client, url, account, debit_card, amount=5000)
    assert resp.status_code == 403 and resp.json()["detail"] == "Insufficient funds"
    assert Notification.objects.filter(user=account.user).count() == 1
    assert Notification.objects.filter(user=debit_card.account.user).count() == 1
    assert Transaction.objects.count() == 0
