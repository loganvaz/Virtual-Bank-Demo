"""Self-service endpoints: UserCreate (register), UserGet (verify), UserUpdate."""

import pytest
from notifications.models import Notification

from conftest import PASSWORD, UserFactory
from users.models import User

pytestmark = [pytest.mark.django_db]


def register_payload(**overrides):
    data = {
        "username": "newbie",
        "first_name": "New",
        "last_name": "Bie",
        "email": "newbie@example.com",
        "password": PASSWORD,
        "phone_number": 5550002222,
    }
    data.update(overrides)
    return data


@pytest.mark.validation
class TestRegister:
    """POST auth/register/ is public, hashes the password, records the client IP and notifies admins."""

    def test_register_creates_user_with_ip(self, api_client, admin_user, url):
        resp = api_client.post(url("user_registration"), register_payload(), REMOTE_ADDR="198.51.100.5")
        assert resp.status_code == 201
        user = User.objects.get(username="newbie")
        assert user.check_password(PASSWORD)
        assert user.ip_address == "198.51.100.5"
        assert "password" not in resp.data
        assert resp.data["ip_address"] == "198.51.100.5"
        note = Notification.objects.get(user=admin_user)
        assert note.notification_type == "USER_NOTIFICATION"
        assert note.content == "New Bie has joined the system"

    def test_register_uses_forwarded_ip(self, api_client, url):
        resp = api_client.post(
            url("user_registration"), register_payload(), HTTP_X_FORWARDED_FOR="203.0.113.1, 10.0.0.1"
        )
        assert resp.status_code == 201
        assert User.objects.get(username="newbie").ip_address == "203.0.113.1"

    def test_register_without_names_succeeds(self, api_client, admin_user, url):
        data = register_payload()
        del data["first_name"]
        del data["last_name"]
        resp = api_client.post(url("user_registration"), data)
        assert resp.status_code == 201
        assert Notification.objects.get(user=admin_user).content == "has joined the system"

    def test_register_duplicate_username_is_400(self, api_client, user, url):
        resp = api_client.post(url("user_registration"), register_payload(username=user.username))
        assert resp.status_code == 400
        assert "username" in resp.data

    def test_register_duplicate_email_is_400(self, api_client, user, url):
        resp = api_client.post(url("user_registration"), register_payload(email=user.email))
        assert resp.status_code == 400
        assert "email" in resp.data

    def test_register_missing_password_is_400(self, api_client, url):
        data = register_payload()
        del data["password"]
        resp = api_client.post(url("user_registration"), data)
        assert resp.status_code == 400
        assert "password" in resp.data

    def test_register_ignores_supplied_auth_header(self, api_client, url):
        api_client.credentials(HTTP_AUTHORIZATION="Bearer garbage")
        assert api_client.post(url("user_registration"), register_payload()).status_code == 201


@pytest.mark.authn
class TestVerify:
    """GET auth/verify/ returns the caller's own profile for every supported auth scheme."""

    @pytest.mark.parametrize("client_fixture", ["auth_client", "cookie_client", "basic_client", "session_client"])
    def test_returns_own_profile(self, request, client_fixture, user, url):
        client = request.getfixturevalue(client_fixture)
        resp = client.get(url("user_verification"))
        assert resp.status_code == 200
        assert resp.data["id"] == user.pk
        assert resp.data["username"] == user.username
        assert "password" not in resp.data

    def test_anonymous_is_401(self, api_client, url):
        assert api_client.get(url("user_verification")).status_code == 401

    def test_bad_token_is_401(self, api_client, url):
        api_client.credentials(HTTP_AUTHORIZATION="Bearer not.a.jwt")
        assert api_client.get(url("user_verification")).status_code == 401


@pytest.mark.authz
class TestUpdate:
    """PATCH/PUT auth/update/ re-authenticates with the current password and only touches the caller."""

    def test_patch_requires_current_password(self, auth_client, user, url):
        resp = auth_client.patch(url("user_update"), {"city": "Denver"})
        assert resp.status_code == 400
        assert resp.data == {"error": "password required"}
        user.refresh_from_db()
        assert user.city is None

    def test_patch_with_wrong_password_is_401(self, auth_client, user, url):
        resp = auth_client.patch(url("user_update"), {"password": "wrong", "city": "Denver"})
        assert resp.status_code == 401
        user.refresh_from_db()
        assert user.city is None

    def test_patch_updates_own_profile(self, auth_client, user, admin_user, url):
        resp = auth_client.patch(url("user_update"), {"password": PASSWORD, "city": "Denver"})
        assert resp.status_code == 200
        assert resp.data["id"] == user.pk
        user.refresh_from_db()
        assert user.city == "Denver"
        assert user.check_password(PASSWORD)
        assert Notification.objects.filter(user=admin_user, notification_type="USER_NOTIFICATION").count() == 1

    def test_patch_cannot_change_other_user(self, auth_client, user, other_user, url):
        before = other_user.city
        resp = auth_client.patch(url("user_update"), {"password": PASSWORD, "id": other_user.pk, "city": "Denver"})
        assert resp.status_code == 200
        other_user.refresh_from_db()
        user.refresh_from_db()
        assert other_user.city == before
        assert user.city == "Denver"

    def test_put_replaces_profile(self, auth_client, user, url):
        resp = auth_client.put(
            url("user_update"),
            {"username": user.username, "email": user.email, "password": PASSWORD, "state": "TX"},
        )
        assert resp.status_code == 200
        user.refresh_from_db()
        assert user.state == "TX"

    def test_put_missing_required_fields_is_400(self, auth_client, url):
        resp = auth_client.put(url("user_update"), {"password": PASSWORD})
        assert resp.status_code == 400
        assert "username" in resp.data and "email" in resp.data

    def test_patch_to_taken_email_is_400(self, auth_client, other_user, url):
        resp = auth_client.patch(url("user_update"), {"password": PASSWORD, "email": other_user.email})
        assert resp.status_code == 400
        assert "email" in resp.data

    def test_anonymous_is_401(self, api_client, url):
        assert api_client.patch(url("user_update"), {"password": PASSWORD}).status_code == 401
