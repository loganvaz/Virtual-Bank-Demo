import itertools
from datetime import datetime, timedelta
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
        username=f"user{n}", email=f"user{n}@example.com", password="pw12345!",
        first_name=first_name, last_name=last_name, **kwargs,
    )


def make_account(user, balance="100.00", currency="USD", account_type="SAVINGS"):
    n = next(_counter)
    return Account.objects.create(
        user=user, name=f"{user.first_name} {account_type}", account_type=account_type,
        balance=Decimal(balance), number=1000000000 + n, currency=currency,
    )


def make_card(account, months_ahead=24):
    exp = timezone.now() + timedelta(days=30 * months_ahead)
    return DebitCard.objects.create(
        account=account, card_number=int(generate_valid_credit_card_number()),
        cvv="123", expiration_date=exp,
    )


def card_expiry(card):
    return card.expiration_date.strftime("%m/%y")


@in_memory_channels
class TransactionAPITestCase(APITestCase):
    """Base test case: two users (Alice authenticated, Bob) each with one USD account."""

    def setUp(self):
        self.alice = make_user("Alice", "Anders")
        self.bob = make_user("Bob", "Brown")
        self.alice_acct = make_account(self.alice, balance="100.00")
        self.bob_acct = make_account(self.bob, balance="50.00")
        self.client.force_authenticate(self.alice)

    def refresh(self, *objs):
        for o in objs:
            o.refresh_from_db()
