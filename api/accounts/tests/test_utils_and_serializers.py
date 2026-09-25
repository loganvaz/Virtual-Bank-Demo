from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from accounts.models import Account
from accounts.serializers import AccountCreateSerializer, AccountSerializer
from accounts.utils import currency_to_unicode, generate_account_number

from .helpers import make_account, make_user


class UtilsTests(SimpleTestCase):
    """Tests for generate_account_number and currency_to_unicode."""

    def test_generate_account_number_is_12_digits(self):
        for _ in range(20):
            number = generate_account_number()
            self.assertEqual(len(number), 12)
            self.assertTrue(number.isdigit())

    def test_currency_symbols(self):
        self.assertEqual(currency_to_unicode("USD"), "$")
        self.assertEqual(currency_to_unicode("EUR"), "€")
        self.assertEqual(currency_to_unicode("GBP"), "£")
        self.assertEqual(currency_to_unicode("NGN"), "₦")
        self.assertEqual(currency_to_unicode("JPY"), "¥")

    def test_unknown_currency_is_empty_string(self):
        self.assertEqual(currency_to_unicode("XXX"), "")
        self.assertEqual(currency_to_unicode(None), "")


class AccountModelTests(TestCase):
    """Tests for Account defaults and __str__."""

    def test_defaults_and_str(self):
        user = make_user("Alice", "Anders")
        acct = Account.objects.create(user=user, name="Main", number=123)
        self.assertEqual((acct.account_type, acct.currency, acct.balance), ("SAVINGS", "NGN", 0))
        self.assertEqual(str(acct), "Savings - 123 - User: Alice Anders")


class AccountCreateSerializerTests(TestCase):
    """Tests that the user-facing create serializer validates choices and ignores client-supplied balance/number."""

    def setUp(self):
        self.user = make_user()

    def test_valid_payload_creates_account_with_generated_number(self):
        s = AccountCreateSerializer(data={"name": "Main", "account_type": "CURRENT", "currency": "EUR"})
        self.assertTrue(s.is_valid(), s.errors)
        acct = s.save(user=self.user)
        self.assertEqual(len(str(acct.number)), 12)
        self.assertEqual((acct.account_type, acct.currency, acct.balance), ("CURRENT", "EUR", 0))

    def test_balance_and_number_are_read_only(self):
        s = AccountCreateSerializer(data={"name": "Main", "balance": "9999.00", "number": "1"})
        self.assertTrue(s.is_valid(), s.errors)
        self.assertNotIn("balance", s.validated_data)
        self.assertNotIn("number", s.validated_data)
        self.assertEqual(s.save(user=self.user).balance, Decimal("0"))

    def test_invalid_choices_and_missing_name(self):
        s = AccountCreateSerializer(data={"name": "x", "account_type": "GOLD", "currency": "ZZZ"})
        self.assertFalse(s.is_valid())
        self.assertEqual(set(s.errors), {"account_type", "currency"})
        s = AccountCreateSerializer(data={"account_type": "SAVINGS"})
        self.assertFalse(s.is_valid())
        self.assertIn("name", s.errors)

    def test_name_too_long(self):
        s = AccountCreateSerializer(data={"name": "n" * 51})
        self.assertFalse(s.is_valid())
        self.assertIn("name", s.errors)


class AccountSerializerTests(TestCase):
    """Tests for the admin/detail serializer: nested user output, write-only user_id, required user on create."""

    def setUp(self):
        self.user = make_user("Alice", "Anders")
        self.acct = make_account(self.user)

    def test_output_shape(self):
        data = AccountSerializer(self.acct).data
        self.assertEqual(set(data), {"id", "user", "name", "account_type", "balance", "number", "currency", "created_date"})
        self.assertEqual(data["user"]["username"], self.user.username)
        self.assertNotIn("password", data["user"])
        self.assertEqual(data["number"], str(self.acct.number))

    def test_create_requires_user(self):
        s = AccountSerializer(data={"name": "Orphan"})
        self.assertTrue(s.is_valid(), s.errors)
        with self.assertRaises(Exception) as cm:
            s.save()
        self.assertIn("user_id", str(cm.exception))

    def test_create_with_user_id(self):
        s = AccountSerializer(data={"name": "Admin made", "user_id": self.user.pk, "balance": "5.00"})
        self.assertTrue(s.is_valid(), s.errors)
        acct = s.save()
        self.assertEqual((acct.user, acct.balance), (self.user, Decimal("5.00")))

    def test_create_with_unknown_user_id_is_invalid(self):
        s = AccountSerializer(data={"name": "x", "user_id": 999999})
        self.assertFalse(s.is_valid())
        self.assertIn("user_id", s.errors)
