import hashlib
import re
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from rest_framework import status
from rest_framework.test import APIRequestFactory, APITestCase, force_authenticate

from accounts.models import Account
from users.models import User
from virtual_bank.log_redaction import REDACTED, mask_number, redact

from .models import DebitCard
from .serializers import DebitCardSerializer
from .utils import generate_cvv, generate_valid_credit_card_number, luhn_checksum
from .views import (
    DebitCardDetail,
    DebitCardList,
    UserDebitCardDetail,
    UserDebitCardList,
)

IN_MEMORY_CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
VIEWS_LOGGER = "debit_cards.views"
SERIALIZERS_LOGGER = "debit_cards.serializers"

ALICE_CARD = "5555555555554444"
ALICE_CARD_2 = "5105105105105100"
BOB_CARD = "5200828282828210"
UNKNOWN_CARD = "4111111111111111"
ALICE_CVV = "987"
BOB_CVV = "654"
EXPIRY = datetime(2030, 6, 15, tzinfo=dt_timezone.utc)
EXPIRY_DISPLAY = "06/30"


@override_settings(CHANNEL_LAYERS=IN_MEMORY_CHANNEL_LAYERS)
class DebitCardsTestBase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="admin", email="admin@example.com", password="pw",
            first_name="Ada", last_name="Admin", is_staff=True,
        )
        cls.alice = User.objects.create_user(
            username="alice", email="alice@example.com", password="pw",
            first_name="Alice", last_name="Anderson",
        )
        cls.bob = User.objects.create_user(
            username="bob", email="bob@example.com", password="pw",
            first_name="Bob", last_name="Brown",
        )
        cls.alice_usd = Account.objects.create(
            user=cls.alice, name="Alice USD", currency="USD",
            balance=Decimal("1000.00"), number=100000000001,
        )
        cls.alice_eur = Account.objects.create(
            user=cls.alice, name="Alice EUR", currency="EUR",
            balance=Decimal("500.00"), number=100000000002,
        )
        cls.bob_ngn = Account.objects.create(
            user=cls.bob, name="Bob NGN", currency="NGN",
            balance=Decimal("500000.00"), number=200000000001,
        )
        cls.alice_card = DebitCard.objects.create(
            account=cls.alice_usd, card_number=int(ALICE_CARD), cvv=ALICE_CVV,
            expiration_date=EXPIRY,
        )
        cls.alice_card_2 = DebitCard.objects.create(
            account=cls.alice_eur, card_number=int(ALICE_CARD_2), cvv=ALICE_CVV,
            expiration_date=EXPIRY,
        )
        cls.bob_card = DebitCard.objects.create(
            account=cls.bob_ngn, card_number=int(BOB_CARD), cvv=BOB_CVV,
            expiration_date=EXPIRY,
        )

    def setUp(self):
        self.factory = APIRequestFactory()

    def request(self, method, view_cls, user, data=None, **kwargs):
        request = getattr(self.factory, method)("/", data, format="json")
        if user is not None:
            force_authenticate(request, user=user)
        return view_cls.as_view()(request, **kwargs)

    def get(self, view_cls, user, **kwargs):
        return self.request("get", view_cls, user, **kwargs)

    def post(self, view_cls, user, data, **kwargs):
        return self.request("post", view_cls, user, data, **kwargs)

    def pii_values(self):
        values = [ALICE_CARD, ALICE_CARD_2, BOB_CARD, UNKNOWN_CARD]
        for user in (self.admin, self.alice, self.bob):
            values += [user.first_name, user.last_name, user.email]
        for account in (self.alice_usd, self.alice_eur, self.bob_ngn):
            values += [str(account.number), account.name]
        return values

    def assert_no_pii(self, output):
        text = "\n".join(output)
        for value in self.pii_values():
            self.assertNotIn(value, text)
        for cvv in (ALICE_CVV, BOB_CVV):
            self.assertIsNone(re.search(rf"\b{cvv}\b", text), "CVV leaked into logs")
        self.assertNotIn(EXPIRY_DISPLAY, text)
        self.assertNotIn("2030-06-15", text)

    def assert_card_payload(self, payload, card):
        self.assertEqual(payload["id"], card.id)
        self.assertEqual(payload["card_number"], str(card.card_number))
        self.assertEqual(payload["cvv"], card.cvv)
        self.assertEqual(payload["expiration_date"], EXPIRY_DISPLAY)
        self.assertEqual(payload["account"]["id"], card.account.id)
        self.assertEqual(payload["account"]["number"], str(card.account.number))
        self.assertEqual(payload["account"]["currency"], card.account.currency)
        self.assertEqual(payload["account"]["user"]["username"], card.account.user.username)
        self.assertIn("created_date", payload)


class LuhnChecksumTests(SimpleTestCase):
    def test_valid_numbers_have_zero_checksum(self):
        for number in (ALICE_CARD, ALICE_CARD_2, BOB_CARD, UNKNOWN_CARD, "79927398713"):
            with self.subTest(number=number):
                self.assertEqual(luhn_checksum(number), 0)

    def test_invalid_numbers_have_nonzero_checksum(self):
        for number in ("4111111111111112", "79927398710", "1234567812345678"):
            with self.subTest(number=number):
                self.assertNotEqual(luhn_checksum(number), 0)

    def test_checksum_is_mod_10_of_weighted_digit_sum(self):
        # 7 9 9 2 7 3 9 8 7 1 3 -> doubled evens: 9->18(9) 2->4 3->6 8->16(7) 1->2
        # odds: 3+7+9+7+9+7 = 42, evens: 9+4+6+7+2 = 28 -> 70 % 10 == 0
        self.assertEqual(luhn_checksum("79927398713"), 0)
        self.assertEqual(luhn_checksum("18"), (8 + 2) % 10)
        self.assertEqual(luhn_checksum("7"), 7)

    def test_non_digit_input_raises(self):
        with self.assertRaises(ValueError):
            luhn_checksum("4111-1111")


class GenerateCardNumberTests(SimpleTestCase):
    def test_generated_number_is_luhn_valid_and_starts_with_5(self):
        for _ in range(25):
            number = generate_valid_credit_card_number()
            self.assertTrue(number.isdigit())
            self.assertTrue(number.startswith("5"))
            self.assertEqual(luhn_checksum(number), 0)

    def test_generated_number_is_16_digits(self):
        """BUG: `generate_valid_credit_card_number` builds '5' + 13 random digits, giving a
        14-digit number, while its docstring (and real Mastercard-range cards) promise 16 digits."""
        self.assertEqual(len(generate_valid_credit_card_number()), 16)

    def test_retries_until_checksum_is_zero(self):
        invalid_then_valid = iter([1, 3, 0])
        with patch("debit_cards.utils.luhn_checksum", side_effect=lambda n: next(invalid_then_valid)) as checksum:
            number = generate_valid_credit_card_number()
        self.assertEqual(checksum.call_count, 3)
        self.assertTrue(number.startswith("5"))
        self.assertEqual(len(number), 14)


class GenerateCvvTests(SimpleTestCase):
    def test_cvv_is_three_digits_and_deterministic(self):
        cvv = generate_cvv(ALICE_CARD, EXPIRY)
        self.assertEqual(len(cvv), 3)
        self.assertTrue(cvv.isdigit())
        self.assertEqual(cvv, generate_cvv(ALICE_CARD, EXPIRY))

    def test_cvv_matches_algorithm(self):
        card = int(ALICE_CARD)
        combined = int(f"{EXPIRY.strftime('%d%m')}{card >> 5}") & card
        hashed = hashlib.sha256(str(combined).encode()).hexdigest()
        expected = "".join(c for c in hashed[::-5] if c.isdigit())[:3]
        self.assertEqual(generate_cvv(ALICE_CARD, EXPIRY), expected)

    def test_cvv_depends_on_card_and_expiry(self):
        base = generate_cvv(ALICE_CARD, EXPIRY)
        other_card = generate_cvv(BOB_CARD, EXPIRY)
        other_expiry = generate_cvv(ALICE_CARD, datetime(2031, 11, 3, tzinfo=dt_timezone.utc))
        self.assertTrue(base != other_card or base != other_expiry)

    def test_non_numeric_card_number_raises(self):
        with self.assertRaises(ValueError):
            generate_cvv("4111-1111-1111-1111", EXPIRY)

    def test_cvv_is_always_three_digits(self):
        """BUG: `generate_cvv` samples only 13 characters of the hex digest (`hashed[::-5]`) and
        keeps the digits among them; a digest with fewer than 3 digits in those positions yields a
        CVV shorter than 3 characters."""
        class FakeHash:
            def hexdigest(self):
                return "abcdef" * 10 + "abcd"

        with patch("debit_cards.utils.hashlib.sha256", return_value=FakeHash()):
            self.assertEqual(len(generate_cvv(ALICE_CARD, EXPIRY)), 3)


class DebitCardModelTests(DebitCardsTestBase):
    def test_str_includes_card_number_and_owner(self):
        self.assertEqual(str(self.alice_card), f"{ALICE_CARD} - User: Alice Anderson")

    def test_created_date_is_auto_set(self):
        self.assertIsNotNone(self.alice_card.created_date)

    def test_deleting_account_cascades_to_cards(self):
        account = Account.objects.create(
            user=self.bob, name="Bob temp", currency="NGN", number=200000000009,
        )
        card = DebitCard.objects.create(
            account=account, card_number=4000056655665556, cvv="123", expiration_date=EXPIRY,
        )
        account.delete()
        self.assertFalse(DebitCard.objects.filter(pk=card.pk).exists())


class DebitCardSerializerTests(DebitCardsTestBase):
    def test_serializes_card_with_nested_account(self):
        data = DebitCardSerializer(self.alice_card).data
        self.assert_card_payload(data, self.alice_card)
        self.assertEqual(
            list(data.keys()),
            ["id", "account", "card_number", "cvv", "expiration_date", "created_date"],
        )

    def test_expiration_date_formats_as_month_year(self):
        serializer = DebitCardSerializer()
        self.assertEqual(serializer.get_expiration_date(self.alice_card), EXPIRY_DISPLAY)
        self.alice_card.expiration_date = datetime(2027, 1, 1, tzinfo=dt_timezone.utc)
        self.assertEqual(serializer.get_expiration_date(self.alice_card), "01/27")

    def test_expiration_date_none_serializes_as_none(self):
        card = DebitCard(account=self.alice_usd, card_number=1, cvv="000", expiration_date=None)
        self.assertIsNone(DebitCardSerializer().get_expiration_date(card))

    def test_read_only_fields_are_ignored_on_input(self):
        serializer = DebitCardSerializer(data={
            "account": {"name": "New"}, "card_number": "1234", "cvv": "111",
            "expiration_date": "12/40", "created_date": "2020-01-01T00:00:00Z",
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(set(serializer.validated_data), {"account"})
        self.assertEqual(serializer.validated_data["account"]["name"], "New")

    def test_nested_account_is_validated(self):
        serializer = DebitCardSerializer(data={"account": {"currency": "XXX"}})
        self.assertFalse(serializer.is_valid())
        self.assertIn("name", serializer.errors["account"])
        self.assertIn("currency", serializer.errors["account"])

        serializer = DebitCardSerializer(data={})
        self.assertFalse(serializer.is_valid())
        self.assertIn("account", serializer.errors)

    def test_create_generates_card_number_and_cvv(self):
        card = DebitCardSerializer().create({"account": self.bob_ngn, "expiration_date": EXPIRY})
        self.assertEqual(card.account, self.bob_ngn)
        self.assertEqual(card.expiration_date, EXPIRY)
        self.assertEqual(luhn_checksum(str(card.card_number)), 0)
        self.assertEqual(card.cvv, generate_cvv(str(card.card_number), EXPIRY))
        self.assertTrue(DebitCard.objects.filter(pk=card.pk).exists())

    def test_create_accepts_nested_account_data(self):
        """BUG: `account = AccountSerializer()` is a writable nested field, but `create` passes the
        validated account dict straight to `DebitCard.objects.create`, so a valid nested payload
        raises `ValueError: Cannot assign ... must be an Account instance` instead of resolving to
        an existing account."""
        serializer = DebitCardSerializer(data={"account": {"name": self.bob_ngn.name}})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        validated = dict(serializer.validated_data, expiration_date=EXPIRY)
        card = DebitCardSerializer().create(validated)
        self.assertIsInstance(card.account, Account)

    def test_create_logs_masked_card_only(self):
        with self.assertLogs(SERIALIZERS_LOGGER, level="INFO") as cm:
            card = DebitCardSerializer().create({"account": self.alice_usd, "expiration_date": EXPIRY})
        self.assertEqual(len(cm.output), 1)
        self.assertIn(f"Debit card issued card_id={card.id} account_id={self.alice_usd.id}", cm.output[0])
        self.assertIn(f"card={mask_number(card.card_number)}", cm.output[0])
        self.assertNotIn(str(card.card_number), cm.output[0])
        self.assertNotIn(card.cvv, cm.output[0].split("card=")[0])
        self.assert_no_pii(cm.output)


class DebitCardListViewTests(DebitCardsTestBase):
    def test_admin_lists_all_cards(self):
        response = self.get(DebitCardList, self.admin)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            sorted(c["id"] for c in response.data),
            sorted([self.alice_card.id, self.alice_card_2.id, self.bob_card.id]),
        )
        self.assert_card_payload(
            next(c for c in response.data if c["id"] == self.bob_card.id), self.bob_card
        )

    def test_non_admin_is_forbidden(self):
        response = self.get(DebitCardList, self.alice)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_is_unauthorized(self):
        response = self.get(DebitCardList, None)
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_admin_can_create_card_for_account(self):
        """BUG: `expiration_date` is a read-only `SerializerMethodField`, so it never reaches
        `validated_data`, and `create` raises `KeyError: 'expiration_date'` (HTTP 500) for every
        POST. Admin card issuance through the API is impossible."""
        response = self.post(DebitCardList, self.admin, {
            "account": {"name": self.bob_ngn.name}, "expiration_date": "2030-06-15T00:00:00Z",
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(DebitCard.objects.filter(account__user=self.bob).count(), 2)

    def test_invalid_payload_is_rejected(self):
        response = self.post(DebitCardList, self.admin, {"account": {"currency": "XXX"}})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("account", response.data)
        self.assertEqual(DebitCard.objects.count(), 3)

    def test_perform_create_logs_masked_card(self):
        view = DebitCardList()
        view.request = self.factory.post("/")
        view.request.user = self.admin
        serializer = DebitCardSerializer()
        with patch.object(serializer, "save", lambda: setattr(serializer, "instance", self.bob_card)):
            with self.assertLogs(VIEWS_LOGGER, level="INFO") as cm:
                view.perform_create(serializer)
        self.assertEqual(len(cm.output), 1)
        self.assertIn(
            f"Debit card created card_id={self.bob_card.id} account_id={self.bob_ngn.id} "
            f"card={mask_number(BOB_CARD)} admin_id={self.admin.id}",
            cm.output[0],
        )
        self.assert_no_pii(cm.output)


class DebitCardDetailViewTests(DebitCardsTestBase):
    def test_admin_retrieves_any_card(self):
        response = self.get(DebitCardDetail, self.admin, pk=self.bob_card.pk)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assert_card_payload(response.data, self.bob_card)

    def test_missing_card_is_not_found(self):
        response = self.get(DebitCardDetail, self.admin, pk=999999)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_non_admin_is_forbidden(self):
        response = self.get(DebitCardDetail, self.alice, pk=self.alice_card.pk)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_patch_without_writable_fields_logs_update(self):
        with self.assertLogs(VIEWS_LOGGER, level="INFO") as cm:
            response = self.request("patch", DebitCardDetail, self.admin, {}, pk=self.bob_card.pk)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assert_card_payload(response.data, self.bob_card)
        self.assertEqual(len(cm.output), 1)
        self.assertIn(
            f"Debit card updated card_id={self.bob_card.id} account_id={self.bob_ngn.id} "
            f"card={mask_number(BOB_CARD)} admin_id={self.admin.id}",
            cm.output[0],
        )
        self.assert_no_pii(cm.output)

    def test_admin_delete_removes_card_and_logs(self):
        with self.assertLogs(VIEWS_LOGGER, level="INFO") as cm:
            response = self.request("delete", DebitCardDetail, self.admin, pk=self.alice_card_2.pk)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(DebitCard.objects.filter(pk=self.alice_card_2.pk).exists())
        self.assertEqual(DebitCard.objects.count(), 2)
        self.assertEqual(len(cm.output), 1)
        self.assertIn(
            f"Debit card deleted card_id={self.alice_card_2.id} account_id={self.alice_eur.id} "
            f"card={mask_number(ALICE_CARD_2)} admin_id={self.admin.id}",
            cm.output[0],
        )
        self.assert_no_pii(cm.output)

    def test_non_admin_cannot_delete(self):
        response = self.request("delete", DebitCardDetail, self.bob, pk=self.bob_card.pk)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(DebitCard.objects.filter(pk=self.bob_card.pk).exists())


class UserDebitCardListViewTests(DebitCardsTestBase):
    def test_lists_only_own_cards(self):
        response = self.get(UserDebitCardList, self.alice)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            sorted(c["id"] for c in response.data),
            sorted([self.alice_card.id, self.alice_card_2.id]),
        )
        self.assertNotIn(BOB_CARD, [c["card_number"] for c in response.data])

    def test_user_without_cards_gets_empty_list(self):
        response = self.get(UserDebitCardList, self.admin)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_get_queryset_filters_by_request_user(self):
        view = UserDebitCardList()
        view.request = self.factory.get("/")
        view.request.user = self.bob
        self.assertEqual(list(view.get_queryset()), [self.bob_card])

    def test_anonymous_is_rejected(self):
        response = self.get(UserDebitCardList, None)
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))


class UserDebitCardDetailViewTests(DebitCardsTestBase):
    def test_owner_retrieves_card_by_number(self):
        response = self.get(UserDebitCardDetail, self.alice, number=ALICE_CARD)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assert_card_payload(response.data, self.alice_card)

    def test_number_kwarg_may_be_int(self):
        response = self.get(UserDebitCardDetail, self.bob, number=int(BOB_CARD))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.bob_card.id)

    def test_other_users_card_is_not_found(self):
        response = self.get(UserDebitCardDetail, self.alice, number=BOB_CARD)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_unknown_card_is_not_found(self):
        response = self.get(UserDebitCardDetail, self.alice, number=UNKNOWN_CARD)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_non_numeric_card_number_is_not_found(self):
        """BUG: `get_object` passes the raw URL kwarg to a `BigIntegerField` filter, so a
        non-numeric value raises `ValueError` (HTTP 500) instead of returning 404."""
        response = self.get(UserDebitCardDetail, self.alice, number="not-a-card")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_anonymous_is_rejected(self):
        response = self.get(UserDebitCardDetail, None, number=ALICE_CARD)
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_not_found_logs_masked_number(self):
        with self.assertLogs(VIEWS_LOGGER, level="WARNING") as cm:
            response = self.get(UserDebitCardDetail, self.alice, number=BOB_CARD)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(len(cm.output), 1)
        self.assertIn(
            f"Debit card lookup rejected: not found for user user_id={self.alice.id} "
            f"card={mask_number(BOB_CARD)}",
            cm.output[0],
        )
        self.assert_no_pii(cm.output)

    def test_successful_lookup_does_not_log(self):
        with self.assertNoLogs(VIEWS_LOGGER, level="INFO"):
            self.get(UserDebitCardDetail, self.alice, number=ALICE_CARD)


class PermissionLoggingTests(DebitCardsTestBase):
    def test_non_admin_denied_is_logged(self):
        with self.assertLogs(VIEWS_LOGGER, level="WARNING") as cm:
            response = self.get(DebitCardList, self.alice)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(len(cm.output), 1)
        self.assertIn(
            f"Debit card access denied view=DebitCardList user_id={self.alice.id} authenticated=True",
            cm.output[0],
        )
        self.assert_no_pii(cm.output)

    def test_anonymous_denied_is_logged_for_each_view(self):
        cases = [
            (DebitCardList, {}), (DebitCardDetail, {"pk": self.alice_card.pk}),
            (UserDebitCardList, {}), (UserDebitCardDetail, {"number": ALICE_CARD}),
        ]
        for view_cls, kwargs in cases:
            with self.subTest(view=view_cls.__name__):
                with self.assertLogs(VIEWS_LOGGER, level="WARNING") as cm:
                    self.get(view_cls, None, **kwargs)
                self.assertEqual(len(cm.output), 1)
                self.assertIn(
                    f"access denied view={view_cls.__name__} user_id=None authenticated=False",
                    cm.output[0],
                )
                self.assert_no_pii(cm.output)

    def test_granted_access_does_not_log(self):
        with self.assertNoLogs(VIEWS_LOGGER, level="INFO"):
            self.get(DebitCardList, self.admin)
            self.get(UserDebitCardList, self.alice)


class RedactionSafetyNetTests(SimpleTestCase):
    def test_card_numbers_and_secrets_are_redacted_by_filter(self):
        line = f"card={ALICE_CARD} cvv={ALICE_CVV} expiry=06/30 owner=alice@example.com"
        cleaned = redact(line)
        self.assertNotIn(ALICE_CARD, cleaned)
        self.assertNotIn(ALICE_CVV, cleaned)
        self.assertNotIn("06/30", cleaned)
        self.assertNotIn("alice@example.com", cleaned)
        self.assertEqual(cleaned.count(REDACTED), 4)

    def test_masked_numbers_survive_redaction(self):
        masked = mask_number(ALICE_CARD)
        self.assertEqual(masked, "************4444")
        self.assertEqual(redact(f"card={masked}"), f"card={masked}")
