"""Unit tests for users.serializers.UserSerializer."""

import pytest

from conftest import PASSWORD, UserFactory
from users.serializers import UserSerializer

pytestmark = [pytest.mark.validation, pytest.mark.pii, pytest.mark.django_db]


def payload(**overrides):
    data = {
        "username": "serial",
        "first_name": "Ser",
        "last_name": "Ial",
        "email": "serial@example.com",
        "password": PASSWORD,
        "phone_number": 5559990000,
    }
    data.update(overrides)
    return data


class TestUserSerializerCreate:
    """create() hashes the password and honours read-only ip_address."""

    def test_password_is_hashed_and_write_only(self):
        serializer = UserSerializer(data=payload())
        assert serializer.is_valid(), serializer.errors
        user = serializer.save()
        assert user.password != PASSWORD
        assert user.check_password(PASSWORD)
        assert "password" not in serializer.data

    def test_ip_address_is_read_only(self):
        serializer = UserSerializer(data=payload(ip_address="203.0.113.9"))
        assert serializer.is_valid(), serializer.errors
        assert serializer.save().ip_address is None

    def test_profile_picture_defaults(self):
        serializer = UserSerializer(data=payload())
        assert serializer.is_valid(), serializer.errors
        assert serializer.save().profile_picture.name == "default.png"

    @pytest.mark.parametrize("field", ["username", "email", "password"])
    def test_required_fields(self, field):
        data = payload()
        del data[field]
        serializer = UserSerializer(data=data)
        assert not serializer.is_valid()
        assert field in serializer.errors

    def test_duplicate_email_rejected(self):
        UserFactory(email="taken@example.com")
        serializer = UserSerializer(data=payload(email="taken@example.com"))
        assert not serializer.is_valid()
        assert "email" in serializer.errors

    def test_invalid_email_rejected(self):
        serializer = UserSerializer(data=payload(email="not-an-email"))
        assert not serializer.is_valid()
        assert "email" in serializer.errors


class TestUserSerializerUpdate:
    """update() re-hashes a new password and leaves it alone when absent."""

    def test_update_with_password_rehashes(self, user):
        serializer = UserSerializer(user, data={"password": "N3w-pass!", "city": "Austin"}, partial=True)
        assert serializer.is_valid(), serializer.errors
        updated = serializer.save()
        assert updated.check_password("N3w-pass!")
        assert updated.city == "Austin"

    def test_update_without_password_keeps_hash(self, user):
        before = user.password
        serializer = UserSerializer(user, data={"city": "Boston"}, partial=True)
        assert serializer.is_valid(), serializer.errors
        assert serializer.save().password == before
