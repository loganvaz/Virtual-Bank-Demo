"""Serialization contract for DebitCardSerializer."""

import pytest

from debit_cards.models import DebitCard
from debit_cards.serializers import DebitCardSerializer

from conftest import DebitCardFactory

pytestmark = [pytest.mark.pii, pytest.mark.validation, pytest.mark.django_db]


def test_serialized_debit_card_has_expected_fields(debit_card):
    data = DebitCardSerializer(debit_card).data
    assert set(data) == {
        "id",
        "account",
        "card_number",
        "cvv",
        "expiration_date",
        "created_date",
    }


def test_expiration_date_is_formatted_mm_yy(debit_card):
    data = DebitCardSerializer(debit_card).data
    assert data["expiration_date"] == debit_card.expiration_date.strftime("%m/%y")


def test_get_expiration_date_returns_none_when_unset():
    obj = DebitCard(expiration_date=None)
    assert DebitCardSerializer().get_expiration_date(obj) is None


def test_nested_account_contains_id_number_and_currency(debit_card):
    account = DebitCardSerializer(debit_card).data["account"]
    assert {"id", "number", "currency"} <= set(account)


def test_str_renders_card_number_in_plaintext(debit_card):
    """Documents current behaviour: __str__ embeds the full card number (PII)."""
    assert str(debit_card.card_number) in str(debit_card)


def test_generated_card_number_is_luhn_valid(debit_card):
    from debit_cards.utils import luhn_checksum

    assert luhn_checksum(str(debit_card.card_number)) == 0
