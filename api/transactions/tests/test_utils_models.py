from decimal import Decimal

import pytest

from transactions.models import Transaction
from transactions.paginations import TransactionPagination
from transactions.utils import convert_currency

from conftest import TransactionFactory

pytestmark = [pytest.mark.txn, pytest.mark.validation]


@pytest.mark.parametrize(
    "amount,src,dst,expected",
    [
        (Decimal("100"), "USD", "USD", Decimal("100")),
        (Decimal("100"), "USD", "EUR", Decimal("85")),
        (Decimal("85"), "EUR", "USD", Decimal("100")),
        (Decimal("0"), "USD", "NGN", Decimal("0")),
        (Decimal("1"), "GBP", "JPY", Decimal("110.50") / Decimal("0.75")),
    ],
)
def test_convert_currency(amount, src, dst, expected):
    converted, src_rate, dst_rate = convert_currency(amount, src, dst)
    assert converted.quantize(Decimal("0.0001")) == expected.quantize(Decimal("0.0001"))
    assert (src_rate, dst_rate) == (
        convert_currency(1, src, src)[1],
        convert_currency(1, dst, dst)[1],
    )


@pytest.mark.parametrize("src,dst", [("XXX", "USD"), ("USD", "XXX"), ("", "")])
def test_convert_currency_rejects_unknown_codes(src, dst):
    with pytest.raises(ValueError, match="Invalid currency code"):
        convert_currency(Decimal("1"), src, dst)


@pytest.mark.django_db
def test_transaction_str_and_defaults():
    txn = TransactionFactory(transaction_type="DEBIT_CARD", amount_received=Decimal("12.50"))
    assert str(txn) == f"Transaction ID: {txn.pk} - Type: Debit Card - Amount: 12.50"
    assert txn.identifier is not None
    assert txn.rate == 1
    assert txn.description == ""


@pytest.mark.django_db
def test_transactions_ordered_newest_first():
    first = TransactionFactory()
    second = TransactionFactory(account=first.account)
    assert list(Transaction.objects.all()) == [second, first]


@pytest.mark.pii
@pytest.mark.django_db
def test_transaction_str_contains_no_owner_pii():
    txn = TransactionFactory()
    user = txn.account.user
    text = str(txn)
    for secret in (user.email, user.first_name, str(txn.account.number)):
        assert secret not in text


def test_pagination_bounds():
    assert TransactionPagination.page_size == 100
    assert TransactionPagination.max_page_size == 1000
    assert TransactionPagination.page_size_query_param == "size"
