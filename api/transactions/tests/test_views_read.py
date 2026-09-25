"""History / detail endpoints: filtering, pagination, ownership."""

import uuid

import pytest

from notifications.models import Notification

from conftest import AccountFactory, TransactionFactory

pytestmark = [pytest.mark.txn, pytest.mark.django_db]


@pytest.fixture
def ledger(account, other_account, user):
    savings = AccountFactory(user=user, balance=0)
    return {
        "deposit": TransactionFactory(account=account),
        "sent": TransactionFactory(account=account, payer=account, payee=other_account, transaction_type="TRANSFER"),
        "received": TransactionFactory(account=other_account, payer=other_account, payee=account, transaction_type="TRANSFER"),
        "card": TransactionFactory(account=account, payer=other_account, payee=account, transaction_type="DEBIT_CARD"),
        "internal": TransactionFactory(account=account, payer=account, payee=savings, transaction_type="TRANSFER"),
        "foreign": TransactionFactory(account=other_account),
        "savings": savings,
    }


def ids(resp):
    return {r["identifier"] for r in resp.json()["results"]}


@pytest.mark.parametrize(
    "endpoint,params,expected",
    [
        ("transaction_history", {}, {"deposit", "sent", "received", "card", "internal"}),
        ("transaction_history", {"role": "payer"}, {"deposit", "sent", "internal"}),
        ("transaction_history", {"role": "payee"}, {"deposit", "received", "card", "internal"}),
        ("transfer_history", {}, {"sent", "received", "internal"}),
        ("transfer_history", {"role": "payer"}, {"sent", "internal"}),
        ("transfer_history", {"role": "payee"}, {"received", "internal"}),
        ("debit_card_transactions_history", {}, {"card"}),
        ("debit_card_transactions_history", {"role": "payer"}, set()),
        ("debit_card_transactions_history", {"role": "payee"}, {"card"}),
        ("deposit_list", {}, {"deposit"}),
    ],
)
def test_history_role_filters(auth_client, url, ledger, endpoint, params, expected):
    resp = auth_client.get(url(endpoint), params)
    assert resp.status_code == 200
    assert ids(resp) == {str(ledger[k].identifier) for k in expected}


@pytest.mark.parametrize("endpoint", ["transaction_history", "transfer_history", "debit_card_transactions_history"])
@pytest.mark.parametrize("role", [None, "payer", "payee"])
def test_history_account_number_filter(auth_client, url, ledger, endpoint, role):
    savings = ledger["savings"]
    params = {"account_number": savings.number}
    if role:
        params["role"] = role
    resp = auth_client.get(url(endpoint), params)
    assert resp.status_code == 200
    if endpoint == "debit_card_transactions_history" or role == "payer":
        assert ids(resp) == set()
    else:
        assert ids(resp) == {str(ledger["internal"].identifier)}


def test_history_pagination(auth_client, account, url):
    for _ in range(5):
        TransactionFactory(account=account)
    page = auth_client.get(url("transaction_history"), {"size": 2, "page": 2}).json()
    assert page["count"] == 5 and len(page["results"]) == 2
    assert page["next"] and page["previous"]
    assert auth_client.get(url("transaction_history"), {"size": 2, "page": 9}).status_code == 404


# ------------------------------------------------------------------ details


def test_transaction_detail_owner(auth_client, ledger, url):
    txn = ledger["deposit"]
    resp = auth_client.get(url("transaction_detail", identifier=txn.identifier))
    assert resp.status_code == 200 and resp.json()["identifier"] == str(txn.identifier)


def test_transaction_detail_404(auth_client, url):
    assert auth_client.get(url("transaction_detail", identifier=uuid.uuid4())).status_code == 404


@pytest.mark.authz
def test_transaction_detail_view_by_other_party_raises_security_notification(other_client, ledger, url):
    txn = ledger["deposit"]
    other_client.get(url("transaction_detail", identifier=txn.identifier))
    assert Notification.objects.filter(user=txn.account.user, notification_type="SECURITY_NOTIFICATION").exists()


@pytest.mark.authz
def test_transaction_detail_view_by_admin_labels_administrator(admin_client, ledger, url):
    txn = ledger["deposit"]
    admin_client.get(url("transaction_detail", identifier=txn.identifier))
    note = Notification.objects.get(user=txn.account.user, notification_type="SECURITY_NOTIFICATION")
    assert note.content.startswith("Virtual-Bank administrator")


def test_deposit_detail_owner(auth_client, ledger, url):
    resp = auth_client.get(url("deposit_detail", identifier=ledger["deposit"].identifier))
    assert resp.status_code == 200
    assert Notification.objects.count() == 0


def test_deposit_detail_404_for_non_deposit(auth_client, ledger, url):
    assert auth_client.get(url("deposit_detail", identifier=ledger["sent"].identifier)).status_code == 404


@pytest.mark.parametrize("key,endpoint", [("sent", "transfer_detail"), ("card", "debit_card_transaction_detail")])
def test_transfer_and_card_detail_visible_to_both_parties(auth_client, other_client, ledger, url, key, endpoint):
    txn = ledger[key]
    for client in (auth_client, other_client):
        resp = client.get(url(endpoint, identifier=txn.identifier))
        assert resp.status_code == 200
    assert Notification.objects.count() == 0


@pytest.mark.parametrize("endpoint", ["transfer_detail", "debit_card_transaction_detail"])
def test_transfer_and_card_detail_404(auth_client, ledger, url, endpoint):
    assert auth_client.get(url(endpoint, identifier=uuid.uuid4())).status_code == 404
    assert auth_client.get(url(endpoint, identifier=ledger["deposit"].identifier)).status_code == 404
