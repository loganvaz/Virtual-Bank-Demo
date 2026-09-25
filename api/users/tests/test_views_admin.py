"""Admin-only user management: UserList (admin/users/) and UserDetail (admin/users/<pk>/)."""

import pytest

from conftest import PASSWORD, UserFactory
from users.models import User

pytestmark = [pytest.mark.authz, pytest.mark.django_db]


def admin_payload(**overrides):
    data = {
        "username": "staffmade",
        "email": "staffmade@example.com",
        "password": PASSWORD,
        "first_name": "Staff",
        "last_name": "Made",
    }
    data.update(overrides)
    return data


class TestUserListPermissions:
    """Only staff may list or create users through the admin endpoint."""

    def test_anonymous_is_401(self, api_client, url):
        assert api_client.get(url("admin_user_list")).status_code == 401

    def test_regular_user_is_403(self, auth_client, url):
        assert auth_client.get(url("admin_user_list")).status_code == 403
        assert auth_client.post(url("admin_user_list"), admin_payload(), format="json").status_code == 403

    def test_staff_without_superuser_is_allowed(self, api_client, url):
        staff = UserFactory(is_staff=True)
        api_client.force_authenticate(staff)
        assert api_client.get(url("admin_user_list")).status_code == 200


class TestUserList:
    """Admins can list every user and create one; the creator's IP is not written to the new record."""

    def test_lists_all_users(self, admin_client, admin_user, user, other_user, url):
        resp = admin_client.get(url("admin_user_list"))
        assert resp.status_code == 200
        assert {row["id"] for row in resp.data} == {admin_user.pk, user.pk, other_user.pk}
        assert all("password" not in row for row in resp.data)

    def test_create_user_as_admin(self, admin_client, url):
        resp = admin_client.post(url("admin_user_list"), admin_payload(), format="json", REMOTE_ADDR="192.0.2.10")
        assert resp.status_code == 201
        created = User.objects.get(username="staffmade")
        assert created.check_password(PASSWORD)
        assert created.ip_address is None

    def test_create_invalid_user_is_400(self, admin_client, url):
        resp = admin_client.post(url("admin_user_list"), admin_payload(email="bad"), format="json")
        assert resp.status_code == 400
        assert "email" in resp.data


class TestUserDetail:
    """Admins can retrieve, update and delete any user by id; non-admins cannot."""

    def test_regular_user_cannot_read_other_by_id(self, auth_client, other_user, url):
        assert auth_client.get(url("admin_user_detail", pk=other_user.pk)).status_code == 403

    def test_regular_user_cannot_read_self_by_id(self, auth_client, user, url):
        assert auth_client.get(url("admin_user_detail", pk=user.pk)).status_code == 403

    def test_anonymous_is_401(self, api_client, user, url):
        assert api_client.get(url("admin_user_detail", pk=user.pk)).status_code == 401

    def test_admin_retrieves_user(self, admin_client, user, url):
        resp = admin_client.get(url("admin_user_detail", pk=user.pk))
        assert resp.status_code == 200
        assert resp.data["username"] == user.username

    def test_admin_404_for_unknown_id(self, admin_client, url):
        assert admin_client.get(url("admin_user_detail", pk=999999)).status_code == 404

    def test_admin_patches_user(self, admin_client, user, url):
        resp = admin_client.patch(url("admin_user_detail", pk=user.pk), {"country": "CA"})
        assert resp.status_code == 200
        user.refresh_from_db()
        assert user.country == "CA"

    def test_admin_resets_password(self, admin_client, user, url):
        resp = admin_client.patch(url("admin_user_detail", pk=user.pk), {"password": "Reset-me-1!"})
        assert resp.status_code == 200
        user.refresh_from_db()
        assert user.check_password("Reset-me-1!")

    def test_admin_deletes_user(self, admin_client, user, url):
        assert admin_client.delete(url("admin_user_detail", pk=user.pk)).status_code == 204
        assert not User.objects.filter(pk=user.pk).exists()
