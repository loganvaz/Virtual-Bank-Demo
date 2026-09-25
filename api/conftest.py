"""
Shared pytest fixtures for the API test-suite.

Provides authenticated clients for every supported auth scheme (JWT header,
JWT cookie via ``JWTAuthenticationMiddleware``, Basic, Session) plus model
factories with deliberately PII-looking values so log/PII assertions are
meaningful.
"""

import base64
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import random

import factory
import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import Account
from debit_cards.models import DebitCard
from debit_cards.utils import generate_valid_credit_card_number
from transactions.models import Transaction
from users.models import User

PASSWORD = "S3cure-pass!"


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User

    username = factory.Sequence(lambda n: f"user{n}")
    first_name = factory.Sequence(lambda n: f"Jane{n}")
    last_name = "Doe"
    email = factory.Sequence(lambda n: f"jane{n}@example.com")
    phone_number = 5551234567
    password = factory.PostGenerationMethodCall("set_password", PASSWORD)


class AccountFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Account

    user = factory.SubFactory(UserFactory)
    name = "Main"
    account_type = "CURRENT"
    balance = Decimal("1000.00")
    number = factory.LazyFunction(lambda: random.randint(10**11, 10**12 - 1))
    currency = "USD"


class DebitCardFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = DebitCard

    account = factory.SubFactory(AccountFactory)
    card_number = factory.LazyFunction(lambda: int(generate_valid_credit_card_number()))
    cvv = "123"
    expiration_date = factory.LazyFunction(
        lambda: datetime.now(timezone.utc).replace(day=1) + timedelta(days=400)
    )


class TransactionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Transaction

    account = factory.SubFactory(AccountFactory)
    payer = factory.SelfAttribute("account")
    payee = factory.SelfAttribute("account")
    transaction_type = "DEPOSIT"
    amount_sent = Decimal("10.00")
    amount_received = Decimal("10.00")
    currency_sent = "USD"
    currency_received = "USD"


@pytest.fixture
def user(db):
    return UserFactory()


@pytest.fixture
def other_user(db):
    return UserFactory()


@pytest.fixture
def admin_user(db):
    return UserFactory(is_staff=True, is_superuser=True)


@pytest.fixture
def account(user):
    return AccountFactory(user=user)


@pytest.fixture
def other_account(other_user):
    return AccountFactory(user=other_user, currency="EUR")


@pytest.fixture
def debit_card(other_account):
    return DebitCardFactory(account=other_account)


@pytest.fixture
def api_client():
    return APIClient()


def jwt_for(user):
    return str(RefreshToken.for_user(user).access_token)


@pytest.fixture
def auth_client(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {jwt_for(user)}")
    return client


@pytest.fixture
def other_client(other_user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {jwt_for(other_user)}")
    return client


@pytest.fixture
def admin_client(admin_user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {jwt_for(admin_user)}")
    return client


@pytest.fixture
def cookie_client(user):
    client = APIClient()
    client.cookies["vb_token"] = jwt_for(user)
    return client


@pytest.fixture
def basic_client(user):
    client = APIClient()
    token = base64.b64encode(f"{user.username}:{PASSWORD}".encode()).decode()
    client.credentials(HTTP_AUTHORIZATION=f"Basic {token}")
    return client


@pytest.fixture
def session_client(user):
    client = APIClient()
    client.force_login(user)
    return client


@pytest.fixture
def url():
    return lambda name, **kwargs: reverse(f"api:{name}", kwargs=kwargs or None)
