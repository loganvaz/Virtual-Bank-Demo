"""Login / refresh / logout flows and cookie handling (Login, RefreshTokenView, Logout)."""

import pytest
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken

from conftest import PASSWORD

pytestmark = [pytest.mark.authn, pytest.mark.django_db]


class TestLogin:
    """POST auth/login/ issues tokens and mirrors them into HttpOnly cookies."""

    def test_valid_credentials_set_cookies(self, api_client, user, url):
        resp = api_client.post(url("user_login"), {"username": user.username, "password": PASSWORD})
        assert resp.status_code == 200
        assert resp.data["access"] and resp.data["refresh"]
        assert resp.cookies["vb_token"].value == resp.data["access"]
        assert resp.cookies["vb_rtoken"].value == resp.data["refresh"]
        assert resp.cookies["vb_token"]["httponly"]
        assert resp.cookies["vb_rtoken"]["httponly"]

    def test_wrong_password_is_401_without_cookies(self, api_client, user, url):
        resp = api_client.post(url("user_login"), {"username": user.username, "password": "nope"})
        assert resp.status_code == 401
        assert "vb_token" not in resp.cookies

    def test_unknown_user_is_401(self, api_client, url):
        resp = api_client.post(url("user_login"), {"username": "ghost", "password": PASSWORD})
        assert resp.status_code == 401

    def test_missing_fields_is_400(self, api_client, url):
        resp = api_client.post(url("user_login"), {})
        assert resp.status_code == 400
        assert "vb_token" not in resp.cookies

    def test_cookie_token_authenticates_via_middleware(self, api_client, user, url):
        login = api_client.post(url("user_login"), {"username": user.username, "password": PASSWORD})
        api_client.cookies["vb_token"] = login.cookies["vb_token"].value
        resp = api_client.get(url("user_verification"))
        assert resp.status_code == 200
        assert resp.data["id"] == user.pk


class TestRefresh:
    """POST auth/token/refresh/ rotates the pair, blacklists the old refresh token and re-sets cookies."""

    def test_valid_refresh_rotates_and_sets_cookies(self, api_client, user, url):
        old = RefreshToken.for_user(user)
        resp = api_client.post(url("token_refresh"), {"refresh": str(old)})
        assert resp.status_code == 200
        assert resp.data["access"]
        assert resp.data["refresh"] != str(old)
        assert resp.cookies["vb_token"].value == resp.data["access"]
        assert resp.cookies["vb_rtoken"].value == resp.data["refresh"]
        assert BlacklistedToken.objects.filter(token__jti=old["jti"]).exists()

    def test_reusing_rotated_refresh_token_is_401(self, api_client, user, url):
        old = RefreshToken.for_user(user)
        assert api_client.post(url("token_refresh"), {"refresh": str(old)}).status_code == 200
        resp = api_client.post(url("token_refresh"), {"refresh": str(old)})
        assert resp.status_code == 401
        assert "vb_token" not in resp.cookies

    def test_garbage_token_is_401(self, api_client, url):
        resp = api_client.post(url("token_refresh"), {"refresh": "not.a.token"})
        assert resp.status_code == 401

    def test_missing_token_is_400(self, api_client, url):
        assert api_client.post(url("token_refresh"), {}).status_code == 400


class TestLogout:
    """GET auth/logout/ blacklists the refresh cookie and clears both cookies."""

    def test_logout_blacklists_and_clears_cookies(self, auth_client, user, url):
        refresh = RefreshToken.for_user(user)
        auth_client.cookies["vb_rtoken"] = str(refresh)
        resp = auth_client.get(url("user_logout"))
        assert resp.status_code == 200
        assert resp.data == {"details": "success"}
        assert BlacklistedToken.objects.filter(token__jti=refresh["jti"]).exists()
        assert resp.cookies["vb_token"].value == ""
        assert resp.cookies["vb_rtoken"].value == ""

    def test_logout_without_refresh_cookie_still_clears_cookies(self, auth_client, user, url):
        resp = auth_client.get(url("user_logout"))
        assert resp.status_code == 200
        assert resp.cookies["vb_token"].value == ""
        assert not BlacklistedToken.objects.filter(token__user=user).exists()

    def test_logout_with_already_blacklisted_token_reports_failure(self, auth_client, user, url):
        refresh = RefreshToken.for_user(user)
        refresh.blacklist()
        auth_client.cookies["vb_rtoken"] = str(refresh)
        resp = auth_client.get(url("user_logout"))
        assert resp.data == {"details": "failed"}

    def test_logout_requires_authentication(self, api_client, url):
        assert api_client.get(url("user_logout")).status_code == 401
