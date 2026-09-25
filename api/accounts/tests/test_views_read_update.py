"""Account list/detail/update/delete endpoints: scoping, field handling, admin views."""

import pytest

from accounts.models import Account
from notifications.models import Notification

from conftest import AccountFactory

pytestmark = [pytest.mark.django_db]


def list_numbers(resp):
    data = resp.json()
    items = data["results"] if isinstance(data, dict) else data
    return {a["number"] for a in items}


def test_list_returns_only_callers_accounts(auth_client, user, other_account, url):
    mine = [AccountFactory(user=user, name=f"A{i}") for i in range(2)]
    resp = auth_client.get(url("account_list"))
    assert resp.status_code == 200
    assert list_numbers(resp) == {str(a.number) for a in mine}


def test_detail_returns_own_account(auth_client, account, url):
    resp = auth_client.get(url("account_detail", number=account.number))
    assert resp.status_code == 200
    body = resp.json()
    assert body["number"] == str(account.number)
    assert body["name"] == account.name
    assert body["account_type"] == account.account_type
    assert body["currency"] == account.currency
    assert "password" not in body["user"]


def test_update_own_account_name(auth_client, user, url):
    account = AccountFactory(user=user, name="Old", account_type="SAVINGS", currency="USD")
    resp = auth_client.put(
        url("account_detail", number=account.number),
        {"name": "Renamed", "balance": "999999", "account_type": "CURRENT", "currency": "JPY"},
    )
    assert resp.status_code == 200
    account.refresh_from_db()
    assert account.name == "Renamed"
    # balance / account_type / currency in the payload are ignored
    assert str(account.balance) != "999999"
    assert account.account_type == "SAVINGS"
    assert account.currency == "USD"


def test_update_keeping_same_name_is_allowed(auth_client, account, url):
    resp = auth_client.put(url("account_detail", number=account.number), {"name": account.name})
    assert resp.status_code == 200


def test_rename_to_another_own_accounts_name_is_rejected(auth_client, user, url):
    AccountFactory(user=user, name="Taken")
    account = AccountFactory(user=user, name="Free")
    resp = auth_client.put(url("account_detail", number=account.number), {"name": "Taken"})
    assert resp.status_code == 403


def test_delete_own_account(auth_client, account, user, url):
    resp = auth_client.delete(url("account_detail", number=account.number))
    assert resp.status_code == 204
    assert not Account.objects.filter(pk=account.pk).exists()
    assert Notification.objects.filter(user=user, notification_type="ACCOUNT_NOTIFICATION").exists()


def test_detail_unknown_number_is_404(auth_client, account, url):
    assert auth_client.get(url("account_detail", number=999999999999)).status_code == 404


def test_admin_can_manage_any_account(admin_client, account, url):
    assert admin_client.get(url("admin_account_detail", pk=account.pk)).status_code == 200
    resp = admin_client.put(url("admin_account_detail", pk=account.pk), {"name": "Admin Renamed"})
    assert resp.status_code == 200
    account.refresh_from_db()
    assert account.name == "Admin Renamed"
    assert admin_client.delete(url("admin_account_detail", pk=account.pk)).status_code == 204
