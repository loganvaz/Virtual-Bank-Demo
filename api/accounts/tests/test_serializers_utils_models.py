"""Unit-level coverage for accounts serializers, utils and the Account model."""

import random
from decimal import Decimal

import pytest

from accounts.models import Account
from accounts.serializers import AccountCreateSerializer, AccountSerializer
from accounts.utils import currency_to_unicode, generate_account_number

from conftest import AccountFactory

pytestmark = [pytest.mark.django_db]


def test_generate_account_number_is_a_twelve_digit_string():
    number = generate_account_number()
    assert isinstance(number, str)
    assert len(number) == 12
    assert number.isdigit()


def test_generate_account_number_differs_between_calls():
    random.seed(1)
    first = generate_account_number()
    random.seed(2)
    assert generate_account_number() != first


@pytest.mark.parametrize(
    "code,symbol",
    [("USD", "$"), ("EUR", "€"), ("GBP", "£"), ("NGN", "₦"), ("JPY", "¥"), ("XXX", "")],
)
def test_currency_to_unicode(code, symbol):
    assert currency_to_unicode(code) == symbol


def test_account_str_and_choices(account):
    assert str(account) == f"{account.get_account_type_display()} - {account.number} - User: {account.user.first_name} {account.user.last_name}"
    assert {c[0] for c in Account.ACCOUNT_TYPES} == {"SAVINGS", "CURRENT"}
    assert {c[0] for c in Account.CURRENCY_CHOICES} == {"USD", "EUR", "GBP", "NGN", "JPY"}


def test_account_serializer_create_assigns_number(user):
    serializer = AccountSerializer(data={"name": "New", "account_type": "SAVINGS", "balance": "10.00", "currency": "USD"})
    assert serializer.is_valid(), serializer.errors
    account = serializer.save(user=user)
    assert str(account.number).isdigit()


@pytest.mark.validation
def test_create_serializer_rejects_negative_balance():
    serializer = AccountCreateSerializer(data={"name": "Neg", "balance": "-0.01"})
    assert not serializer.is_valid()
    assert "balance" in serializer.errors


@pytest.mark.validation
def test_create_serializer_accepts_zero_balance():
    serializer = AccountCreateSerializer(data={"name": "Zero", "balance": "0"})
    assert serializer.is_valid(), serializer.errors
