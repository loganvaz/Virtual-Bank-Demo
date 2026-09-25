import pytest
from rest_framework.test import APIClient

from accounts.models import Account
from users.models import User


def make_user(email, **extra):
    return User.objects.create_user(
        username=email.split("@")[0], email=email, password="pass1234",
        first_name="Test", last_name="User", **extra,
    )


def make_account(user, number, balance=1000, currency="USD"):
    return Account.objects.create(
        user=user, name=f"{user.first_name} acct", number=number,
        balance=balance, currency=currency,
    )


@pytest.fixture
def user(db):
    return make_user("alice@example.com")


@pytest.fixture
def other_user(db):
    return make_user("bob@example.com")


@pytest.fixture
def admin_user(db):
    return make_user("admin@example.com", is_staff=True, is_superuser=True)


@pytest.fixture
def account(user):
    return make_account(user, 1234567890)


@pytest.fixture
def other_account(other_user):
    return make_account(other_user, 9876543210, currency="EUR")


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def auth_api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client
