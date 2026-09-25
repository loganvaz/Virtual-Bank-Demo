"""Unit tests for users.utils, users.models and users.forms."""

import pytest
from django.test import RequestFactory

from conftest import UserFactory
from users.forms import UserRegisterForm
from users.models import User
from users.utils import get_client_ip

pytestmark = [pytest.mark.validation]


class TestGetClientIP:
    """get_client_ip prefers X-Forwarded-For's first hop, else REMOTE_ADDR."""

    def test_uses_first_forwarded_hop(self):
        request = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="203.0.113.7, 10.0.0.1")
        assert get_client_ip(request) == "203.0.113.7"

    def test_falls_back_to_remote_addr(self):
        request = RequestFactory().get("/", REMOTE_ADDR="198.51.100.2")
        assert get_client_ip(request) == "198.51.100.2"


@pytest.mark.django_db
class TestUserModel:
    """User.save auto-generates a username when none is supplied."""

    def test_username_generated_when_blank(self):
        user = User.objects.create(email="blank@example.com")
        assert user.username == "users_1"

    def test_explicit_username_preserved(self):
        user = UserFactory(username="explicit")
        assert User.objects.get(pk=user.pk).username == "explicit"

    def test_email_must_be_unique(self):
        UserFactory(email="dup@example.com")
        with pytest.raises(Exception):
            User.objects.create(username="second", email="dup@example.com")


@pytest.mark.django_db
class TestUserRegisterForm:
    """UserRegisterForm requires contact fields and matching passwords."""

    def _data(self, **overrides):
        data = {
            "username": "formuser",
            "first_name": "Form",
            "last_name": "User",
            "phone_number": "5550001111",
            "email": "form@example.com",
            "password1": "Str0ng-pass-word!",
            "password2": "Str0ng-pass-word!",
            "address": "1 Main St",
            "city": "Austin",
            "state": "TX",
            "country": "US",
            "date_of_birth": "1990-01-01",
        }
        data.update(overrides)
        return data

    def test_valid_form_saves_user(self):
        form = UserRegisterForm(data=self._data())
        assert form.is_valid(), form.errors
        user = form.save()
        assert user.check_password("Str0ng-pass-word!")

    @pytest.mark.parametrize("missing", ["email", "first_name", "last_name", "phone_number", "address", "date_of_birth"])
    def test_required_fields(self, missing):
        form = UserRegisterForm(data=self._data(**{missing: ""}))
        assert not form.is_valid()
        assert missing in form.errors

    def test_phone_number_must_be_numeric(self):
        form = UserRegisterForm(data=self._data(phone_number="555-CALL"))
        assert not form.is_valid()
        assert "phone_number" in form.errors

    def test_password_mismatch(self):
        form = UserRegisterForm(data=self._data(password2="different"))
        assert not form.is_valid()
        assert "password2" in form.errors


def test_app_urlconf_is_empty_routes_live_in_api_urls():
    from users import urls

    assert urls.app_name == "users"
    assert urls.urlpatterns == []
