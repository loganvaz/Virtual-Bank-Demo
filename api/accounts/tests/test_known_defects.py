"""Defects surfaced while writing the suite. Marked xfail(strict=True) so they
document current behaviour and start failing loudly once fixed."""

import pytest

from conftest import AccountFactory

pytestmark = [pytest.mark.django_db]


@pytest.mark.xfail(strict=True, reason="generate_account_number collision propagates IntegrityError as a 500 instead of a 4xx")
def test_account_number_collision_returns_client_error(auth_client, account, url, monkeypatch):
    monkeypatch.setattr("accounts.serializers.generate_account_number", lambda: str(account.number))
    resp = auth_client.post(url("account_creation"), {"name": "Collision"})
    assert resp.status_code in (400, 409)


@pytest.mark.xfail(strict=True, reason="admin AccountList.create cannot supply the required Account.user -> IntegrityError/500")
def test_admin_account_list_post_returns_client_error(admin_client, user, url):
    resp = admin_client.post(url("admin_account_list"), {"name": "AdminMade", "user": user.pk})
    assert resp.status_code in (400, 403)
