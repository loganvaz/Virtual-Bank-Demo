"""
Defects surfaced while writing the suite. Marked xfail(strict=True) so they
document current behaviour and start failing loudly once fixed.
"""

import pytest
from notifications.models import Notification

from conftest import PASSWORD

pytestmark = [pytest.mark.django_db]


@pytest.mark.authn
@pytest.mark.xfail(strict=True, reason="Logout without a vb_rtoken cookie mints and blacklists a brand-new token (RefreshToken(None)) and reports success")
def test_logout_without_refresh_cookie_is_a_client_error(auth_client, url):
    assert auth_client.get(url("user_logout")).status_code == 400


@pytest.mark.authn
@pytest.mark.xfail(strict=True, reason="Logout swallows every exception and answers 200 {'details': 'failed'}")
def test_logout_with_invalid_refresh_cookie_is_a_client_error(auth_client, url):
    auth_client.cookies["vb_rtoken"] = "junk"
    assert auth_client.get(url("user_logout")).status_code == 400


@pytest.mark.audit
@pytest.mark.xfail(strict=True, reason="UserUpdate notifies admins of a 'successful' update before validation runs")
def test_failed_update_does_not_emit_success_notification(auth_client, admin_user, other_user, url):
    resp = auth_client.patch(url("user_update"), {"password": PASSWORD, "email": other_user.email})
    assert resp.status_code == 400
    assert not Notification.objects.filter(user=admin_user).exists()


@pytest.mark.authz
@pytest.mark.xfail(strict=True, reason="UserList.create mutates request.data, which is an immutable QueryDict for form-encoded bodies")
def test_admin_create_accepts_form_encoded_body(admin_client, url):
    resp = admin_client.post(
        url("admin_user_list"),
        {"username": "formy", "email": "formy@example.com", "password": PASSWORD},
    )
    assert resp.status_code == 201


@pytest.mark.authn
@pytest.mark.xfail(strict=True, reason="Auth cookies are set without secure/samesite flags")
def test_login_cookies_are_secure(api_client, user, url):
    resp = api_client.post(url("user_login"), {"username": user.username, "password": PASSWORD})
    assert resp.cookies["vb_token"]["secure"]
