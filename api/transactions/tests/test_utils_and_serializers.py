from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from transactions.models import Transaction
from transactions.serializers import DebitCardTransactionSerializer, DepositSerializer, TransferSerializer
from transactions.utils import convert_currency

from .helpers import in_memory_channels, make_account, make_user


class ConvertCurrencyTests(SimpleTestCase):
    """Unit tests for the convert_currency exchange-rate helper."""

    def test_same_currency_is_identity(self):
        amount, src, dst = convert_currency(Decimal("10"), "USD", "USD")
        self.assertEqual(amount, Decimal("10"))
        self.assertEqual(src, dst)

    def test_usd_to_eur(self):
        amount, _, _ = convert_currency(Decimal("100"), "USD", "EUR")
        self.assertEqual(amount, Decimal("85.00"))

    def test_cross_rate_eur_to_ngn(self):
        amount, _, _ = convert_currency(Decimal("85"), "EUR", "NGN")
        self.assertEqual(amount, Decimal("75000.00"))

    def test_rates_are_exact_decimals(self):
        _, _, eur = convert_currency(Decimal("1"), "USD", "EUR")
        self.assertEqual(eur, Decimal("0.85"))

    def test_invalid_currency_raises(self):
        with self.assertRaises(ValueError):
            convert_currency(Decimal("1"), "USD", "XXX")
        with self.assertRaises(ValueError):
            convert_currency(Decimal("1"), "XXX", "USD")


@in_memory_channels
class TransactionModelTests(TestCase):
    """Tests for Transaction model defaults, uniqueness and ordering."""

    def test_identifier_is_unique_and_str_includes_type(self):
        user = make_user()
        acct = make_account(user)
        t1 = Transaction.objects.create(account=acct, payer=acct, payee=acct, amount_sent=1, amount_received=1,
                                        currency_sent="USD", currency_received="USD")
        t2 = Transaction.objects.create(account=acct, payer=acct, payee=acct, amount_sent=1, amount_received=1,
                                        currency_sent="USD", currency_received="USD")
        self.assertNotEqual(t1.identifier, t2.identifier)
        self.assertIn("Deposit", str(t1))
        self.assertEqual(list(Transaction.objects.all()), [t2, t1])


class DepositSerializerTests(SimpleTestCase):
    """Validation tests for the deposit input serializer."""

    def test_valid_payload(self):
        s = DepositSerializer(data={"account_number": "1234567890", "amount": "10.50"})
        self.assertTrue(s.is_valid(), s.errors)
        self.assertEqual(s.validated_data["amount"], Decimal("10.50"))

    def test_zero_and_negative_amount_rejected(self):
        for amount in ("0", "-5"):
            s = DepositSerializer(data={"account_number": "1234567890", "amount": amount})
            self.assertFalse(s.is_valid())
            self.assertIn("amount", s.errors)

    def test_non_numeric_account_number_rejected(self):
        s = DepositSerializer(data={"account_number": "12ab", "amount": "1"})
        self.assertFalse(s.is_valid())
        self.assertIn("account_number", s.errors)

    def test_read_only_fields_ignored(self):
        s = DepositSerializer(data={"account_number": "1", "amount": "1", "transaction_type": "TRANSFER"})
        self.assertTrue(s.is_valid())
        self.assertNotIn("transaction_type", s.validated_data)


class TransferSerializerTests(SimpleTestCase):
    """Validation tests for the transfer input serializer."""

    def test_requires_both_account_numbers(self):
        s = TransferSerializer(data={"amount": "1"})
        self.assertFalse(s.is_valid())
        self.assertIn("payer_account_number", s.errors)
        self.assertIn("payee_account_number", s.errors)

    def test_negative_amount_rejected(self):
        s = TransferSerializer(data={"payer_account_number": "1", "payee_account_number": "2", "amount": "-1"})
        self.assertFalse(s.is_valid())


class DebitCardSerializerTests(SimpleTestCase):
    """Validation tests for the debit-card payment input serializer."""

    def test_cvv_max_length_enforced(self):
        s = DebitCardTransactionSerializer(data={
            "payee_account_number": "1", "card_number": "4111111111111111", "cvv": "12345",
            "expiration_date": "12/30", "amount": "1",
        })
        self.assertFalse(s.is_valid())
        self.assertIn("cvv", s.errors)

    def test_card_fields_are_write_only(self):
        fields = DebitCardTransactionSerializer().fields
        for name in ("card_number", "cvv", "expiration_date"):
            self.assertTrue(fields[name].write_only, name)
