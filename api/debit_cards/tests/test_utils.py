"""Unit tests for debit_cards card-number / CVV utilities."""

from datetime import datetime
from unittest.mock import patch

import pytest

from debit_cards.utils import (
    generate_cvv,
    generate_valid_credit_card_number,
    luhn_checksum,
)

pytestmark = [pytest.mark.validation]


@pytest.mark.parametrize(
    "number",
    ["4539578763621486", "5555555555554444"],
)
def test_luhn_checksum_returns_zero_for_valid_numbers(number):
    assert luhn_checksum(number) == 0


def test_luhn_checksum_is_non_zero_for_invalid_number():
    assert luhn_checksum("4539578763621487") != 0


def test_generate_valid_credit_card_number_produces_luhn_valid_14_digit_number():
    for _ in range(20):
        number = generate_valid_credit_card_number()
        assert len(number) == 14
        assert number.startswith("5")
        assert luhn_checksum(number) == 0


def test_generate_valid_credit_card_number_retries_until_checksum_is_zero():
    with patch("debit_cards.utils.luhn_checksum", side_effect=[1, 0]):
        number = generate_valid_credit_card_number()
    assert len(number) == 14
    assert number.startswith("5")


def test_generate_cvv_returns_three_digits():
    cvv = generate_cvv("5555555555554444", datetime(2030, 5, 1))
    assert len(cvv) == 3
    assert cvv.isdigit()


def test_generate_cvv_is_deterministic_for_same_inputs():
    expiration = datetime(2030, 5, 1)
    assert generate_cvv("5555555555554444", expiration) == generate_cvv(
        "5555555555554444", expiration
    )


def test_generate_cvv_differs_for_different_expiration_dates():
    assert generate_cvv("5555555555554444", datetime(2030, 5, 1)) != generate_cvv(
        "5555555555554444", datetime(2031, 6, 1)
    )
