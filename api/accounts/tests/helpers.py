import itertools
from decimal import Decimal

from django.test import override_settings
from rest_framework.test import APITestCase

from accounts.models import Account
from users.models import User

_counter = itertools.count(1)

in_memory_channels = override_settings(
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
)


def make_user(first_name="Test", last_name="User", **kwargs):
    n = next(_counter)
    return User.objects.create_user(
        username=f"acct_user{n}", email=f"acct_user{n}@example.com", password="pw12345!",
        first_name=first_name, last_name=last_name, **kwargs,
    )


def make_admin():
    return make_user("Root", "Admin", is_superuser=True, is_staff=True)


def make_account(user, name=None, balance="100.00", currency="USD", account_type="SAVINGS"):
    n = next(_counter)
    return Account.objects.create(
        user=user, name=name or f"{user.first_name} {account_type} {n}", account_type=account_type,
        balance=Decimal(balance), number=1000000000 + n, currency=currency,
    )


@in_memory_channels
class AccountAPITestCase(APITestCase):
    """Base test case: Alice (authenticated) and Bob, each owning one USD savings account."""

    def setUp(self):
        self.alice = make_user("Alice", "Anders")
        self.bob = make_user("Bob", "Brown")
        self.alice_acct = make_account(self.alice, name="Alice Savings")
        self.bob_acct = make_account(self.bob, name="Bob Savings")
        self.client.force_authenticate(self.alice)
