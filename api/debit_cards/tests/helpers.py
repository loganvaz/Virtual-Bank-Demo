import itertools
from datetime import timedelta
from decimal import Decimal

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import Account
from debit_cards.models import DebitCard
from debit_cards.utils import generate_valid_credit_card_number
from users.models import User

_counter = itertools.count(1)

in_memory_channels = override_settings(
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
)


def make_user(first_name="Test", last_name="User", **kwargs):
    n = next(_counter)
    return User.objects.create_user(
        username=f"card_user{n}", email=f"card_user{n}@example.com", password="pw12345!",
        first_name=first_name, last_name=last_name, **kwargs,
    )


def make_admin():
    return make_user("Root", "Admin", is_staff=True, is_superuser=True)


def make_account(user, balance="100.00", currency="USD", account_type="CURRENT"):
    n = next(_counter)
    return Account.objects.create(
        user=user, name=f"{user.first_name} {account_type}", account_type=account_type,
        balance=Decimal(balance), number=2000000000 + n, currency=currency,
    )


def future(months=24):
    return timezone.now() + timedelta(days=30 * months)


def make_card(account, months_ahead=24):
    return DebitCard.objects.create(
        account=account, card_number=int(generate_valid_credit_card_number()),
        cvv="123", expiration_date=future(months_ahead),
    )


@in_memory_channels
class DebitCardAPITestCase(APITestCase):
    """Base test case: Alice (authenticated) and Bob, each with one CURRENT account and one debit card."""

    def setUp(self):
        self.alice = make_user("Alice", "Anders")
        self.bob = make_user("Bob", "Brown")
        self.alice_acct = make_account(self.alice)
        self.bob_acct = make_account(self.bob)
        self.alice_card = make_card(self.alice_acct)
        self.bob_card = make_card(self.bob_acct)
        self.client.force_authenticate(self.alice)
