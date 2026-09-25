import pytest

from transactions.serializers import (
    DebitCardTransactionSerializer,
    DepositSerializer,
    TransactionSerializer,
    TransferSerializer,
)

from conftest import TransactionFactory

pytestmark = [pytest.mark.txn, pytest.mark.validation, pytest.mark.django_db]


def test_deposit_serializer_requires_account_and_amount():
    s = DepositSerializer(data={})
    assert not s.is_valid()
    assert set(s.errors) == {"account_number", "amount"}


@pytest.mark.parametrize("amount", ["abc", "1.5", "", None])
def test_deposit_serializer_rejects_non_integer_amounts(amount):
    s = DepositSerializer(data={"account_number": "123", "amount": amount})
    assert not s.is_valid()
    assert "amount" in s.errors


def test_deposit_serializer_ignores_read_only_client_input():
    s = DepositSerializer(
        data={
            "account_number": "123",
            "amount": 5,
            "transaction_type": "TRANSFER",
            "amount_received": "999",
        }
    )
    assert s.is_valid(), s.errors
    assert "transaction_type" not in s.validated_data
    assert "amount_received" not in s.validated_data


def test_transfer_serializer_requires_both_accounts():
    s = TransferSerializer(data={"amount": 5})
    assert not s.is_valid()
    assert set(s.errors) == {"payer_account_number", "payee_account_number"}


def test_debit_card_serializer_requires_card_fields():
    s = DebitCardTransactionSerializer(data={"amount": 5})
    assert not s.is_valid()
    assert set(s.errors) == {"payee_account_number", "card_number", "cvv", "expiration_date"}


def test_debit_card_serializer_cvv_max_length():
    s = DebitCardTransactionSerializer(
        data={
            "amount": 5,
            "payee_account_number": "1",
            "card_number": "1",
            "cvv": "12345",
            "expiration_date": "12/99",
        }
    )
    assert not s.is_valid()
    assert "cvv" in s.errors


@pytest.mark.pii
def test_write_only_secrets_never_serialized():
    txn = TransactionFactory()
    for serializer in (DepositSerializer, TransferSerializer, DebitCardTransactionSerializer):
        data = serializer(txn).data
        for secret in ("card_number", "cvv", "expiration_date", "account_number", "amount"):
            assert secret not in data


def test_transaction_serializer_nests_accounts():
    txn = TransactionFactory()
    data = TransactionSerializer(txn).data
    assert data["account"]["id"] == txn.account_id
    assert data["payer"]["id"] == data["payee"]["id"] == txn.account_id
    assert data["transaction_type"] == "DEPOSIT"
