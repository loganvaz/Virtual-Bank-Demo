from datetime import datetime, timedelta

from django.test import SimpleTestCase
from django.utils import timezone

from debit_cards.serializers import DebitCardSerializer
from debit_cards.utils import generate_cvv, generate_valid_credit_card_number, luhn_checksum

from .helpers import DebitCardAPITestCase, future, make_card


class LuhnTests(SimpleTestCase):
    """Tests luhn_checksum on known-valid and known-invalid numbers."""

    def test_valid_numbers_have_zero_checksum(self):
        for number in ("4111111111111111", "79927398713", "5555555555554444"):
            self.assertEqual(luhn_checksum(number), 0, number)

    def test_invalid_number_has_nonzero_checksum(self):
        self.assertNotEqual(luhn_checksum("4111111111111112"), 0)


class GenerateCardNumberTests(SimpleTestCase):
    """Tests that generated card numbers start with 5, are all digits and pass Luhn."""

    def test_generated_numbers_are_luhn_valid(self):
        for _ in range(25):
            number = generate_valid_credit_card_number()
            self.assertTrue(number.startswith("5"))
            self.assertTrue(number.isdigit())
            self.assertEqual(len(number), 14)
            self.assertEqual(luhn_checksum(number), 0)


class GenerateCvvTests(SimpleTestCase):
    """Tests that generate_cvv is deterministic, 3 digits, and depends on card number and expiry."""

    def setUp(self):
        self.exp = datetime(2030, 6, 15)

    def test_three_digits_and_deterministic(self):
        cvv = generate_cvv("5000000000000009", self.exp)
        self.assertEqual(cvv, generate_cvv("5000000000000009", self.exp))
        for i in range(200):
            number = generate_valid_credit_card_number()
            with self.subTest(number=number):
                cvv = generate_cvv(number, self.exp + timedelta(days=i))
                self.assertEqual(len(cvv), 3)
                self.assertTrue(cvv.isdigit())

    def test_changes_with_inputs(self):
        base = generate_cvv("5000000000000009", self.exp)
        other_card = generate_cvv("5100000000000008", self.exp)
        other_exp = generate_cvv("5000000000000009", self.exp + timedelta(days=40))
        self.assertTrue(base != other_card or base != other_exp)


class DebitCardSerializerTests(DebitCardAPITestCase):
    """Tests read representation (nested account, MM/YY expiry) and write path (generated number/cvv, validation)."""

    def test_representation(self):
        data = DebitCardSerializer(self.alice_card).data
        self.assertEqual(data["account"]["id"], self.alice_acct.pk)
        self.assertEqual(data["card_number"], str(self.alice_card.card_number))
        self.assertEqual(data["expiration_date"], self.alice_card.expiration_date.strftime("%m/%y"))
        self.assertNotIn("account_id", data)
        self.assertNotIn("expires_at", data)

    def test_expiration_none_renders_none(self):
        self.alice_card.expiration_date = None
        self.assertIsNone(DebitCardSerializer(self.alice_card).data["expiration_date"])

    def test_create_generates_number_and_cvv(self):
        s = DebitCardSerializer(data={"account_id": self.alice_acct.pk, "expires_at": future().isoformat()})
        self.assertTrue(s.is_valid(), s.errors)
        card = s.save()
        self.assertEqual(card.account, self.alice_acct)
        self.assertEqual(luhn_checksum(str(card.card_number)), 0)
        self.assertEqual(card.cvv, generate_cvv(str(card.card_number), card.expiration_date))

    def test_client_cannot_set_number_or_cvv(self):
        s = DebitCardSerializer(data={
            "account_id": self.alice_acct.pk, "expires_at": future().isoformat(),
            "card_number": "4111111111111111", "cvv": "999",
        })
        self.assertTrue(s.is_valid(), s.errors)
        card = s.save()
        self.assertNotEqual(str(card.card_number), "4111111111111111")
        self.assertNotEqual(card.cvv, "999")

    def test_past_expiry_rejected(self):
        past = (timezone.now() - timedelta(days=1)).isoformat()
        s = DebitCardSerializer(data={"account_id": self.alice_acct.pk, "expires_at": past})
        self.assertFalse(s.is_valid())
        self.assertIn("expires_at", s.errors)

    def test_missing_fields_rejected(self):
        s = DebitCardSerializer(data={})
        self.assertFalse(s.is_valid())
        self.assertEqual(set(s.errors), {"account_id", "expires_at"})

    def test_unknown_account_rejected(self):
        s = DebitCardSerializer(data={"account_id": 999999, "expires_at": future().isoformat()})
        self.assertFalse(s.is_valid())
        self.assertIn("account_id", s.errors)

    def test_model_str_mentions_owner(self):
        self.assertIn("Alice Anders", str(self.alice_card))
