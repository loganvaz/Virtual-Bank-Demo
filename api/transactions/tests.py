import logging
import re
import uuid
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, override_settings
from rest_framework import status
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, APITestCase, force_authenticate

from accounts.models import Account
from debit_cards.models import DebitCard
from users.models import User
from virtual_bank.log_redaction import (
    REDACTED,
    RedactingFilter,
    get_redacted_logger,
    mask_number,
    redact,
)

from .models import Transaction
from .paginations import TransactionPagination
from .utils import convert_currency
from .views import (
    CreateDebitCardTransaction,
    CreateDepositTransaction,
    CreateTransferTransaction,
    DebitCardDetails,
    DebitCardHistory,
    DepositDetail,
    DepositList,
    TransactionDetail,
    TransactionHistory,
    TransferDetails,
    TransferHistory,
)

IN_MEMORY_CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
VIEWS_LOGGER = "transactions.views"

CARD_NUMBER = "4111111111111111"
CARD_CVV = "987"
CARD_EXPIRY = "06/30"
LUHN_INVALID_CARD = "4111111111111112"
UNREGISTERED_VALID_CARD = "5555555555554444"
UNKNOWN_ACCOUNT = "999999999999"


@override_settings(CHANNEL_LAYERS=IN_MEMORY_CHANNEL_LAYERS)
class TransactionsTestBase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(
            username="alice", email="alice@example.com", password="pw",
            first_name="Alice", last_name="Anderson",
        )
        cls.bob = User.objects.create_user(
            username="bob", email="bob@example.com", password="pw",
            first_name="Bob", last_name="Brown",
        )
        cls.carol = User.objects.create_user(
            username="carol", email="carol@example.com", password="pw",
            first_name="Carol", last_name="Clark",
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
        cls.carol_gbp = Account.objects.create(
            user=cls.carol, name="Carol GBP", currency="GBP",
            balance=Decimal("100.00"), number=300000000001,
        )
        cls.bob_card = DebitCard.objects.create(
            account=cls.bob_ngn, card_number=int(CARD_NUMBER), cvv=CARD_CVV,
            expiration_date=datetime(2030, 6, 15, tzinfo=dt_timezone.utc),
        )

    def setUp(self):
        self.factory = APIRequestFactory()
        patcher = patch("transactions.views.process_notifications")
        self.notify = patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, view_cls, user, data):
        request = self.factory.post("/", data, format="json")
        force_authenticate(request, user=user)
        return view_cls.as_view()(request)

    def get(self, view_cls, user, params=None, **kwargs):
        request = self.factory.get("/", params or {})
        force_authenticate(request, user=user)
        return view_cls.as_view()(request, **kwargs)

    @staticmethod
    def make_transaction(account, payer, payee, transaction_type, amount=Decimal("10.00")):
        return Transaction.objects.create(
            account=account, payer=payer, payee=payee, transaction_type=transaction_type,
            amount_sent=amount, amount_received=amount,
            currency_sent=payer.currency, currency_received=payee.currency,
        )

    def notified_users(self):
        return [c.args[0] for c in self.notify.call_args_list]

    def pii_values(self):
        values = [CARD_NUMBER, LUHN_INVALID_CARD, UNREGISTERED_VALID_CARD, UNKNOWN_ACCOUNT]
        for user in (self.alice, self.bob, self.carol):
            values += [user.first_name, user.last_name, user.email]
        for account in (self.alice_usd, self.alice_eur, self.bob_ngn, self.carol_gbp):
            values += [str(account.number), account.name]
        return values

    def assert_no_pii(self, output):
        text = "\n".join(output)
        for value in self.pii_values():
            self.assertNotIn(value, text)
        self.assertIsNone(re.search(rf"\b{CARD_CVV}\b", text), "CVV leaked into logs")
        self.assertNotIn(CARD_EXPIRY, text)


class ConvertCurrencyTests(SimpleTestCase):
    def assertConverted(self, amount, source, target, expected):
        converted, source_rate, target_rate = convert_currency(amount, source, target)
        self.assertEqual(converted.quantize(Decimal("0.01")), Decimal(expected))
        return converted, source_rate, target_rate

    def test_same_currency_is_identity(self):
        converted, source_rate, target_rate = self.assertConverted(100, "USD", "USD", "100.00")
        self.assertEqual(source_rate, target_rate)

    def test_currency_pairs(self):
        cases = [
            (100, "USD", "EUR", "85.00"),
            (85, "EUR", "USD", "100.00"),
            (10, "USD", "NGN", "7500.00"),
            (7500, "NGN", "USD", "10.00"),
            (7500, "NGN", "EUR", "8.50"),
            (Decimal("8.50"), "EUR", "NGN", "7500.00"),
            (75, "GBP", "JPY", "11050.00"),
            (1105, "JPY", "GBP", "7.50"),
        ]
        for amount, source, target, expected in cases:
            with self.subTest(source=source, target=target):
                self.assertConverted(amount, source, target, expected)

    def test_rate_tuple_math(self):
        converted, source_rate, target_rate = convert_currency(100, "USD", "EUR")
        self.assertEqual(source_rate, Decimal(1.00))
        self.assertEqual(target_rate, Decimal(0.85))
        self.assertEqual(converted, Decimal(100) / source_rate * target_rate)

        _, ngn_rate, jpy_rate = convert_currency(1, "NGN", "JPY")
        self.assertEqual(ngn_rate, Decimal(750))
        self.assertEqual(jpy_rate, Decimal("110.5"))

    def test_unknown_currency_codes_raise(self):
        for source, target in [("XYZ", "USD"), ("USD", "ABC"), ("usd", "EUR"), ("", "USD")]:
            with self.subTest(source=source, target=target):
                with self.assertRaisesMessage(ValueError, "Invalid currency code."):
                    convert_currency(100, source, target)


class CreateDepositTransactionTests(TransactionsTestBase):
    def test_deposit_updates_balance_and_creates_transaction(self):
        response = self.post(CreateDepositTransaction, self.alice, {
            "account_number": str(self.alice_usd.number), "amount": 250, "description": "salary",
        })

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.alice_usd.refresh_from_db()
        self.assertEqual(self.alice_usd.balance, Decimal("1250.00"))

        transaction = Transaction.objects.get(identifier=response.data["identifier"])
        self.assertEqual(transaction.transaction_type, "DEPOSIT")
        self.assertEqual(transaction.account, self.alice_usd)
        self.assertEqual(transaction.payer, self.alice_usd)
        self.assertEqual(transaction.payee, self.alice_usd)
        self.assertEqual(transaction.amount_sent, Decimal("250.00"))
        self.assertEqual(transaction.amount_received, Decimal("250.00"))
        self.assertEqual(transaction.currency_sent, "USD")
        self.assertEqual(transaction.currency_received, "USD")
        self.assertEqual(transaction.rate, Decimal("1"))
        self.assertEqual(transaction.description, "salary")

        self.notify.assert_called_once()
        user, notification_type, _ = self.notify.call_args.args
        self.assertEqual(user, self.alice)
        self.assertEqual(notification_type, "transaction_notification")

    def test_unknown_account_returns_not_found(self):
        response = self.post(CreateDepositTransaction, self.alice, {
            "account_number": UNKNOWN_ACCOUNT, "amount": 250,
        })
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(str(response.data["detail"]), "Account not found")
        self.assertFalse(Transaction.objects.exists())
        self.notify.assert_not_called()

    def test_foreign_account_is_permission_denied(self):
        response = self.post(CreateDepositTransaction, self.alice, {
            "account_number": str(self.bob_ngn.number), "amount": 250,
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(str(response.data["detail"]), "Account does not belong to this user")
        self.bob_ngn.refresh_from_db()
        self.assertEqual(self.bob_ngn.balance, Decimal("500000.00"))
        self.assertFalse(Transaction.objects.exists())

    def test_negative_deposit_is_rejected(self):
        """BUG: `DepositSerializer.amount` has no min_value, so a negative deposit drains the balance."""
        response = self.post(CreateDepositTransaction, self.alice, {
            "account_number": str(self.alice_usd.number), "amount": -500,
        })
        self.alice_usd.refresh_from_db()
        self.assertEqual(self.alice_usd.balance, Decimal("1000.00"))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class CreateTransferTransactionTests(TransactionsTestBase):
    def transfer(self, user, payer, payee, amount):
        return self.post(CreateTransferTransaction, user, {
            "payer_account_number": str(payer), "payee_account_number": str(payee), "amount": amount,
        })

    def test_transfer_to_other_user_with_currency_conversion(self):
        response = self.transfer(self.alice, self.alice_usd.number, self.bob_ngn.number, 100)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.alice_usd.refresh_from_db()
        self.bob_ngn.refresh_from_db()
        self.assertEqual(self.alice_usd.balance, Decimal("900.00"))
        self.assertEqual(self.bob_ngn.balance, Decimal("575000.00"))

        transaction = Transaction.objects.get(identifier=response.data["identifier"])
        self.assertEqual(transaction.transaction_type, "TRANSFER")
        self.assertEqual(transaction.account, self.alice_usd)
        self.assertEqual(transaction.payer, self.alice_usd)
        self.assertEqual(transaction.payee, self.bob_ngn)
        self.assertEqual(transaction.amount_sent, Decimal("100.00"))
        self.assertEqual(transaction.amount_received, Decimal("75000.00"))
        self.assertEqual(transaction.currency_sent, "USD")
        self.assertEqual(transaction.currency_received, "NGN")
        self.assertEqual(transaction.rate, Decimal("0.001333"))

        self.assertEqual(self.notified_users(), [self.alice, self.bob])

    def test_transfer_between_own_accounts_with_conversion(self):
        response = self.transfer(self.alice, self.alice_usd.number, self.alice_eur.number, 100)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.alice_usd.refresh_from_db()
        self.alice_eur.refresh_from_db()
        self.assertEqual(self.alice_usd.balance, Decimal("900.00"))
        self.assertEqual(self.alice_eur.balance, Decimal("585.00"))

        transaction = Transaction.objects.get(identifier=response.data["identifier"])
        self.assertEqual(transaction.amount_received, Decimal("85.00"))
        self.assertEqual(transaction.rate, Decimal("1.176471"))
        self.assertEqual(self.notified_users(), [self.alice])

    def test_identical_payer_and_payee_rejected(self):
        response = self.transfer(self.alice, self.alice_usd.number, self.alice_usd.number, 100)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(str(response.data["detail"]), "Payer and Payee accounts cannot be identical.")
        self.assertFalse(Transaction.objects.exists())
        self.assertEqual(self.notified_users(), [self.alice])

    def test_unknown_payer_account(self):
        response = self.transfer(self.alice, UNKNOWN_ACCOUNT, self.bob_ngn.number, 100)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(str(response.data["detail"]), "Account not found")
        self.assertFalse(Transaction.objects.exists())

    def test_unknown_payee_account(self):
        response = self.transfer(self.alice, self.alice_usd.number, UNKNOWN_ACCOUNT, 100)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(str(response.data["detail"]), "Payee Account not found")
        self.alice_usd.refresh_from_db()
        self.assertEqual(self.alice_usd.balance, Decimal("1000.00"))
        self.assertEqual(self.notified_users(), [self.alice])

    def test_insufficient_funds(self):
        response = self.transfer(self.alice, self.alice_usd.number, self.bob_ngn.number, 1001)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(str(response.data["detail"]), "Insufficient funds")
        self.alice_usd.refresh_from_db()
        self.bob_ngn.refresh_from_db()
        self.assertEqual(self.alice_usd.balance, Decimal("1000.00"))
        self.assertEqual(self.bob_ngn.balance, Decimal("500000.00"))
        self.assertFalse(Transaction.objects.exists())

    def test_exact_balance_is_allowed(self):
        response = self.transfer(self.alice, self.alice_usd.number, self.bob_ngn.number, 1000)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.alice_usd.refresh_from_db()
        self.assertEqual(self.alice_usd.balance, Decimal("0.00"))

    def test_payer_account_owned_by_other_user(self):
        response = self.transfer(self.alice, self.bob_ngn.number, self.alice_usd.number, 100)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(str(response.data["detail"]), "Account does not belong to this user")
        self.bob_ngn.refresh_from_db()
        self.assertEqual(self.bob_ngn.balance, Decimal("500000.00"))
        user, notification_type, _ = self.notify.call_args.args
        self.assertEqual((user, notification_type), (self.bob, "security_notification"))

    def test_negative_transfer_is_rejected(self):
        """BUG: `TransferSerializer.amount` has no min_value, so a negative transfer pulls funds from the payee."""
        response = self.transfer(self.alice, self.alice_usd.number, self.bob_ngn.number, -100)
        self.bob_ngn.refresh_from_db()
        self.assertEqual(self.bob_ngn.balance, Decimal("500000.00"))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class CreateDebitCardTransactionTests(TransactionsTestBase):
    def pay(self, user=None, payee=None, card=CARD_NUMBER, cvv=CARD_CVV, expiry=CARD_EXPIRY, amount=1000):
        return self.post(CreateDebitCardTransaction, user or self.alice, {
            "payee_account_number": str(payee or self.alice_usd.number),
            "card_number": card, "cvv": cvv, "expiration_date": expiry, "amount": amount,
        })

    def assertRejected(self, response, status_code, detail):
        self.assertEqual(response.status_code, status_code)
        self.assertEqual(str(response.data["detail"]), detail)
        self.assertFalse(Transaction.objects.exists())

    def test_successful_debit_card_payment(self):
        response = self.pay()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.alice_usd.refresh_from_db()
        self.bob_ngn.refresh_from_db()
        self.assertEqual(self.bob_ngn.balance, Decimal("499000.00"))
        self.assertEqual(self.alice_usd.balance, Decimal("1001.33"))

        transaction = Transaction.objects.get(identifier=response.data["identifier"])
        self.assertEqual(transaction.transaction_type, "DEBIT_CARD")
        self.assertEqual(transaction.account, self.alice_usd)
        self.assertEqual(transaction.payer, self.bob_ngn)
        self.assertEqual(transaction.payee, self.alice_usd)
        self.assertEqual(transaction.amount_sent, Decimal("1000.00"))
        self.assertEqual(transaction.amount_received, Decimal("1.33"))
        self.assertEqual(transaction.currency_sent, "NGN")
        self.assertEqual(transaction.currency_received, "USD")
        self.assertEqual(transaction.rate, Decimal("750"))
        self.assertEqual(self.notified_users(), [self.bob, self.alice])

    def test_own_card_payment_into_own_account(self):
        alice_card = DebitCard.objects.create(
            account=self.alice_eur, card_number=int(UNREGISTERED_VALID_CARD), cvv="321",
            expiration_date=datetime(2030, 6, 15, tzinfo=dt_timezone.utc),
        )
        response = self.pay(card=str(alice_card.card_number), cvv="321", amount=85)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.alice_eur.refresh_from_db()
        self.alice_usd.refresh_from_db()
        self.assertEqual(self.alice_eur.balance, Decimal("415.00"))
        self.assertEqual(self.alice_usd.balance, Decimal("1100.00"))
        self.assertEqual(self.notified_users(), [self.alice])

    def test_invalid_expiration_dates(self):
        now = datetime.now()
        cases = [
            ("13/30", "Invalid month"),
            ("00/30", "Invalid month"),
            (f"{now.month:02d}/{(now.year - 1) % 100:02d}", "Card has expired"),
            ("06/100", "Invalid year"),
            ("abc", "Invalid expiry date"),
            ("0630", "Invalid expiry date"),
            ("ab/cd", "Invalid expiry date"),
            ("06/30/01", "Invalid expiry date"),
        ]
        for expiry, detail in cases:
            with self.subTest(expiry=expiry):
                self.assertRejected(self.pay(expiry=expiry), status.HTTP_403_FORBIDDEN, detail)

    def test_luhn_invalid_card_number(self):
        self.assertRejected(self.pay(card=LUHN_INVALID_CARD), status.HTTP_403_FORBIDDEN, "Invalid card number")

    def test_non_numeric_card_number_is_rejected(self):
        """BUG: `luhn_checksum` raises ValueError on non-digit input, which escapes the view as a 500."""
        try:
            response = self.pay(card="4111-1111-1111-1111")
        except ValueError as exc:
            self.fail(f"BUG: non-numeric card number crashes CreateDebitCardTransaction: {exc!r}")
        self.assertRejected(response, status.HTTP_403_FORBIDDEN, "Invalid card number")

    def test_card_not_found(self):
        for kwargs in ({"card": UNREGISTERED_VALID_CARD}, {"cvv": "000"}, {"expiry": "07/30"}):
            with self.subTest(**kwargs):
                self.assertRejected(self.pay(**kwargs), status.HTTP_403_FORBIDDEN, "Invalid card")

    def test_unknown_payee_account(self):
        self.assertRejected(self.pay(payee=UNKNOWN_ACCOUNT), status.HTTP_404_NOT_FOUND, "Account not found")

    def test_payee_account_owned_by_other_user(self):
        self.assertRejected(
            self.pay(payee=self.carol_gbp.number), status.HTTP_403_FORBIDDEN,
            "Account does not belong to this user",
        )

    def test_payee_equals_card_account(self):
        self.assertRejected(
            self.pay(user=self.bob, payee=self.bob_ngn.number), status.HTTP_403_FORBIDDEN,
            "Payee and Payer accounts cannot be the same",
        )
        self.assertEqual(self.notified_users(), [self.bob])

    def test_insufficient_funds(self):
        self.assertRejected(self.pay(amount=500001), status.HTTP_403_FORBIDDEN, "Insufficient funds")
        self.bob_ngn.refresh_from_db()
        self.alice_usd.refresh_from_db()
        self.assertEqual(self.bob_ngn.balance, Decimal("500000.00"))
        self.assertEqual(self.alice_usd.balance, Decimal("1000.00"))
        self.assertEqual(self.notified_users(), [self.alice, self.bob])


class HistoryTestBase(TransactionsTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.dep_alice = cls.make_transaction(cls.alice_usd, cls.alice_usd, cls.alice_usd, "DEPOSIT")
        cls.tr_a2b = cls.make_transaction(cls.alice_usd, cls.alice_usd, cls.bob_ngn, "TRANSFER")
        cls.tr_b2a = cls.make_transaction(cls.bob_ngn, cls.bob_ngn, cls.alice_eur, "TRANSFER")
        cls.dc_b2a = cls.make_transaction(cls.alice_usd, cls.bob_ngn, cls.alice_usd, "DEBIT_CARD")
        cls.dep_carol = cls.make_transaction(cls.carol_gbp, cls.carol_gbp, cls.carol_gbp, "DEPOSIT")
        cls.tr_c2b = cls.make_transaction(cls.carol_gbp, cls.carol_gbp, cls.bob_ngn, "TRANSFER")

    def history(self, view_cls, user, **params):
        response = self.get(view_cls, user, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return {r["identifier"] for r in response.data["results"]}

    @staticmethod
    def ids(*transactions):
        return {str(t.identifier) for t in transactions}


class TransactionHistoryTests(HistoryTestBase):
    def test_filters(self):
        cases = [
            ({}, [self.dep_alice, self.tr_a2b, self.tr_b2a, self.dc_b2a]),
            ({"role": "unknown"}, [self.dep_alice, self.tr_a2b, self.tr_b2a, self.dc_b2a]),
            ({"role": "payer"}, [self.dep_alice, self.tr_a2b]),
            ({"role": "payee"}, [self.dep_alice, self.tr_b2a, self.dc_b2a]),
            ({"role": "payer", "account_number": self.alice_usd.number}, [self.dep_alice, self.tr_a2b]),
            ({"role": "payer", "account_number": self.alice_eur.number}, []),
            ({"role": "payer", "account_number": self.bob_ngn.number}, []),
            ({"role": "payee", "account_number": self.alice_eur.number}, [self.tr_b2a]),
            ({"account_number": self.alice_eur.number}, [self.tr_b2a]),
            ({"account_number": self.bob_ngn.number}, [self.tr_a2b, self.tr_b2a, self.dc_b2a]),
            ({"account_number": self.carol_gbp.number}, []),
        ]
        for params, expected in cases:
            with self.subTest(**params):
                self.assertEqual(self.history(TransactionHistory, self.alice, **params), self.ids(*expected))

    def test_other_user_sees_only_their_transactions(self):
        self.assertEqual(self.history(TransactionHistory, self.carol), self.ids(self.dep_carol, self.tr_c2b))

    def test_requires_authentication(self):
        request = self.factory.get("/")
        response = TransactionHistory.as_view()(request)
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))


class DepositListTests(HistoryTestBase):
    def test_lists_only_own_deposits(self):
        self.assertEqual(self.history(DepositList, self.alice), self.ids(self.dep_alice))
        self.assertEqual(self.history(DepositList, self.carol), self.ids(self.dep_carol))
        self.assertEqual(self.history(DepositList, self.bob), set())


class TransferHistoryTests(HistoryTestBase):
    def test_filters(self):
        cases = [
            ({}, [self.tr_a2b, self.tr_b2a]),
            ({"role": "payer"}, [self.tr_a2b]),
            ({"role": "payee"}, [self.tr_b2a]),
            ({"role": "payer", "account_number": self.alice_usd.number}, [self.tr_a2b]),
            ({"role": "payee", "account_number": self.alice_usd.number}, []),
            ({"account_number": self.alice_eur.number}, [self.tr_b2a]),
            ({"account_number": self.carol_gbp.number}, []),
        ]
        for params, expected in cases:
            with self.subTest(**params):
                self.assertEqual(self.history(TransferHistory, self.alice, **params), self.ids(*expected))

    def test_bob_sees_incoming_transfer_from_carol(self):
        self.assertEqual(
            self.history(TransferHistory, self.bob, role="payee"), self.ids(self.tr_a2b, self.tr_c2b)
        )


class DebitCardHistoryTests(HistoryTestBase):
    def test_filters(self):
        cases = [
            ({}, [self.dc_b2a]),
            ({"role": "payer"}, []),
            ({"role": "payee"}, [self.dc_b2a]),
            ({"role": "payee", "account_number": self.alice_usd.number}, [self.dc_b2a]),
            ({"role": "payee", "account_number": self.alice_eur.number}, []),
            ({"account_number": self.bob_ngn.number}, [self.dc_b2a]),
        ]
        for params, expected in cases:
            with self.subTest(**params):
                self.assertEqual(self.history(DebitCardHistory, self.alice, **params), self.ids(*expected))

    def test_card_owner_sees_payment_as_payer(self):
        self.assertEqual(self.history(DebitCardHistory, self.bob, role="payer"), self.ids(self.dc_b2a))
        self.assertEqual(
            self.history(DebitCardHistory, self.bob, role="payer", account_number=self.bob_ngn.number),
            self.ids(self.dc_b2a),
        )

    def test_uninvolved_user_sees_nothing(self):
        self.assertEqual(self.history(DebitCardHistory, self.carol), set())


class DetailViewTests(HistoryTestBase):
    def detail(self, view_cls, user, transaction_id):
        return self.get(view_cls, user, identifier=str(transaction_id))

    def assertFound(self, view_cls, user, transaction):
        response = self.detail(view_cls, user, transaction.identifier)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["identifier"], str(transaction.identifier))

    def assertNotFound(self, view_cls, transaction_id):
        response = self.detail(view_cls, self.alice, transaction_id)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def foreign_view(self, view_cls, user, transaction):
        try:
            return self.detail(view_cls, user, transaction.identifier)
        except AttributeError as exc:
            self.fail(f"BUG: {view_cls.__name__} crashes when a non-owner views a transaction: {exc!r}")

    # TransactionDetail
    def test_transaction_detail_lookup(self):
        for transaction in (self.dep_alice, self.tr_a2b, self.dc_b2a):
            with self.subTest(type=transaction.transaction_type):
                self.assertFound(TransactionDetail, self.alice, transaction)
        self.assertNotFound(TransactionDetail, uuid.uuid4())

    def test_transaction_detail_foreign_view_notifies_account_owner(self):
        """Documents actual behavior: any authenticated user can read any transaction; the owner is notified."""
        response = self.foreign_view(TransactionDetail, self.carol, self.dep_alice)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user, notification_type, message = self.notify.call_args.args
        self.assertEqual((user, notification_type), (self.alice, "security_notification"))
        self.assertIn(str(self.dep_alice.identifier), message)
        self.assertIn("Carol Clark reviewed the transaction", message)

    def test_transaction_detail_owner_view_does_not_notify(self):
        """BUG: TransactionDetail compares Account objects to a User with `or`, so the owner is always notified."""
        self.assertFound(TransactionDetail, self.alice, self.dep_alice)
        self.notify.assert_not_called()

    # DepositDetail
    def test_deposit_detail_lookup(self):
        self.assertFound(DepositDetail, self.alice, self.dep_alice)
        self.notify.assert_not_called()
        self.assertNotFound(DepositDetail, uuid.uuid4())
        self.assertNotFound(DepositDetail, self.tr_a2b.identifier)

    def test_deposit_detail_foreign_view_notifies_owner(self):
        """BUG: DepositDetail reads `deposit.transaction.date`; Transaction has no `transaction` attribute."""
        response = self.foreign_view(DepositDetail, self.carol, self.dep_alice)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.notified_users(), [self.alice])

    # TransferDetails
    def test_transfer_detail_lookup(self):
        self.assertFound(TransferDetails, self.alice, self.tr_a2b)
        self.assertFound(TransferDetails, self.bob, self.tr_a2b)
        self.notify.assert_not_called()
        self.assertNotFound(TransferDetails, uuid.uuid4())
        self.assertNotFound(TransferDetails, self.dep_alice.identifier)
        self.assertNotFound(TransferDetails, self.dc_b2a.identifier)

    def test_transfer_detail_foreign_view_notifies_both_parties(self):
        """BUG: TransferDetails reads `transfer.transaction.date`; Transaction has no `transaction` attribute."""
        response = self.foreign_view(TransferDetails, self.carol, self.tr_a2b)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.notified_users(), [self.alice, self.bob])

    # DebitCardDetails
    def test_debit_card_detail_lookup(self):
        self.assertFound(DebitCardDetails, self.alice, self.dc_b2a)
        self.assertFound(DebitCardDetails, self.bob, self.dc_b2a)
        self.notify.assert_not_called()
        self.assertNotFound(DebitCardDetails, uuid.uuid4())
        self.assertNotFound(DebitCardDetails, self.tr_a2b.identifier)

    def test_debit_card_detail_foreign_view_notifies_both_parties(self):
        """BUG: DebitCardDetails reads `card.transaction.date`; Transaction has no `transaction` attribute."""
        response = self.foreign_view(DebitCardDetails, self.carol, self.dc_b2a)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.notified_users(), [self.bob, self.alice])


class TransactionPaginationTests(HistoryTestBase):
    def paginate(self, items, **params):
        request = Request(self.factory.get("/", params))
        return TransactionPagination().paginate_queryset(items, request)

    def test_configuration(self):
        self.assertEqual(TransactionPagination.page_size, 100)
        self.assertEqual(TransactionPagination.page_size_query_param, "size")
        self.assertEqual(TransactionPagination.page_query_param, "page")
        self.assertEqual(TransactionPagination.max_page_size, 1000)

    def test_default_page_size(self):
        self.assertEqual(self.paginate(list(range(250))), list(range(100)))

    def test_size_and_page_params(self):
        self.assertEqual(self.paginate(list(range(250)), size=5, page=2), [5, 6, 7, 8, 9])
        self.assertEqual(self.paginate(list(range(250)), page=3), list(range(200, 250)))

    def test_size_is_capped_at_max_page_size(self):
        self.assertEqual(len(self.paginate(list(range(1500)), size=5000)), 1000)

    def test_history_view_is_paginated(self):
        response = self.get(TransactionHistory, self.alice, {"size": 2})
        self.assertEqual(response.data["count"], 4)
        self.assertEqual(len(response.data["results"]), 2)
        self.assertIsNotNone(response.data["next"])
        self.assertIsNone(response.data["previous"])

        response = self.get(TransactionHistory, self.alice, {"size": 2, "page": 2})
        self.assertEqual(len(response.data["results"]), 2)
        self.assertIsNone(response.data["next"])


class TransactionModelTests(TransactionsTestBase):
    def test_str(self):
        transaction = self.make_transaction(
            self.alice_usd, self.alice_usd, self.alice_usd, "DEPOSIT", Decimal("100.00")
        )
        self.assertEqual(str(transaction), f"Transaction ID: {transaction.pk} - Type: Deposit - Amount: 100.00")

        card = self.make_transaction(self.alice_usd, self.bob_ngn, self.alice_usd, "DEBIT_CARD", Decimal("5.50"))
        self.assertEqual(str(card), f"Transaction ID: {card.pk} - Type: Debit Card - Amount: 5.50")

    def test_ordering_is_newest_first(self):
        self.assertEqual(Transaction._meta.ordering, ("-date",))
        oldest, middle, newest = (
            self.make_transaction(self.alice_usd, self.alice_usd, self.alice_usd, "DEPOSIT") for _ in range(3)
        )
        for transaction, day in ((oldest, 1), (middle, 2), (newest, 3)):
            Transaction.objects.filter(pk=transaction.pk).update(date=datetime(2024, 1, day, tzinfo=dt_timezone.utc))
        self.assertEqual(list(Transaction.objects.all()), [newest, middle, oldest])

    def test_defaults(self):
        transaction = self.make_transaction(self.alice_usd, self.alice_usd, self.alice_usd, "DEPOSIT")
        self.assertEqual(transaction.rate, 1)
        self.assertIsInstance(transaction.identifier, uuid.UUID)
        self.assertEqual(transaction.description, "")


class RedactionUtilityTests(SimpleTestCase):
    def test_mask_number(self):
        self.assertEqual(mask_number(100000000001), "********0001")
        self.assertEqual(mask_number(CARD_NUMBER), "************1111")
        self.assertEqual(mask_number("1234"), "****")
        self.assertEqual(mask_number(""), "")
        self.assertEqual(mask_number(None), REDACTED)

    def test_redact_scrubs_pii(self):
        cases = [
            (f"card {CARD_NUMBER} used", f"card {REDACTED} used"),
            ("account 100000000001", f"account {REDACTED}"),
            ("contact alice@example.com now", f"contact {REDACTED} now"),
            (f"cvv={CARD_CVV}", f"cvv={REDACTED}"),
            (f"'cvv': '{CARD_CVV}'", f"'cvv': {REDACTED}"),
            (f"expiration_date: {CARD_EXPIRY}", f"expiration_date: {REDACTED}"),
            ("password=hunter2", f"password={REDACTED}"),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(redact(raw), expected)

    def test_redact_preserves_safe_values(self):
        identifier = "12345678-1234-1234-1234-123456789012"
        safe = f"identifier={identifier} user_id=42 amount=100000 masked=********0001"
        self.assertEqual(redact(safe), safe)

    def test_filter_redacts_formatted_args(self):
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "card=%s email=%s", (CARD_NUMBER, "a@b.co"), None)
        self.assertTrue(RedactingFilter().filter(record))
        self.assertEqual(record.getMessage(), f"card={REDACTED} email={REDACTED}")
        self.assertIsNone(record.args)

    def test_get_redacted_logger_is_idempotent(self):
        logger = get_redacted_logger("transactions.tests.idempotent")
        get_redacted_logger("transactions.tests.idempotent")
        self.assertEqual(sum(isinstance(f, RedactingFilter) for f in logger.filters), 1)

    def test_views_logger_redacts_raw_pii(self):
        logger = logging.getLogger(VIEWS_LOGGER)
        self.assertTrue(any(isinstance(f, RedactingFilter) for f in logger.filters))
        with self.assertLogs(VIEWS_LOGGER, level="INFO") as cm:
            logger.info("raw account %s card %s email %s", 100000000001, CARD_NUMBER, "alice@example.com")
        self.assertEqual(cm.output, [f"INFO:{VIEWS_LOGGER}:raw account {REDACTED} card {REDACTED} email {REDACTED}"])

    def test_logging_settings_attach_redaction_filter(self):
        config = settings.LOGGING
        self.assertEqual(config["filters"]["redact_pii"]["()"], "virtual_bank.log_redaction.RedactingFilter")
        self.assertIn("redact_pii", config["handlers"]["console"]["filters"])
        self.assertIn("console", config["loggers"]["transactions"]["handlers"])
        handlers = logging.getLogger("transactions").handlers
        self.assertTrue(any(isinstance(f, RedactingFilter) for h in handlers for f in h.filters))


class TransactionLoggingTests(HistoryTestBase):
    def capture(self, call, level="INFO"):
        with self.assertLogs(VIEWS_LOGGER, level=level) as cm:
            try:
                call()
            except AttributeError:
                pass
        self.assert_no_pii(cm.output)
        return cm.output

    def assertLogged(self, output, level, *fragments):
        matching = [line for line in output if line.startswith(f"{level}:")]
        self.assertTrue(
            any(all(f in line for f in fragments) for line in matching),
            f"No {level} log containing {fragments!r} in {output!r}",
        )

    def deposit(self, account, amount=100):
        return lambda: self.post(CreateDepositTransaction, self.alice, {"account_number": str(account), "amount": amount})

    def transfer(self, payer, payee, amount=100):
        return lambda: self.post(CreateTransferTransaction, self.alice, {
            "payer_account_number": str(payer), "payee_account_number": str(payee), "amount": amount,
        })

    def card(self, payee=None, card=CARD_NUMBER, cvv=CARD_CVV, expiry=CARD_EXPIRY, amount=1000, user=None):
        return lambda: self.post(CreateDebitCardTransaction, user or self.alice, {
            "payee_account_number": str(payee or self.alice_usd.number),
            "card_number": card, "cvv": cvv, "expiration_date": expiry, "amount": amount,
        })

    def test_deposit_logs(self):
        output = self.capture(self.deposit(self.alice_usd.number))
        identifier = Transaction.objects.get(transaction_type="DEPOSIT", amount_sent=100).identifier
        self.assertLogged(
            output, "INFO", "Deposit created", f"identifier={identifier}", f"user_id={self.alice.id}",
            "account=********0001", "amount=100", "currency=USD",
        )
        self.assertLogged(
            self.capture(self.deposit(UNKNOWN_ACCOUNT)), "WARNING",
            "Deposit rejected: account not found", "account=********9999",
        )
        self.assertLogged(
            self.capture(self.deposit(self.bob_ngn.number)), "WARNING",
            "Deposit rejected: account ownership mismatch", "account=********0001",
        )

    def test_transfer_logs(self):
        output = self.capture(self.transfer(self.alice_usd.number, self.bob_ngn.number))
        self.assertLogged(
            output, "INFO", "Transfer created", "payer=********0001", "payee=********0001",
            "amount_sent=100", "currency_sent=USD", "amount_received=75000.00", "currency_received=NGN",
        )
        cases = [
            (self.transfer(self.alice_usd.number, self.alice_usd.number), "identical payer and payee"),
            (self.transfer(UNKNOWN_ACCOUNT, self.bob_ngn.number), "payer account not found"),
            (self.transfer(self.bob_ngn.number, self.alice_usd.number), "payer account ownership mismatch"),
            (self.transfer(self.alice_usd.number, UNKNOWN_ACCOUNT), "payee account not found"),
            (self.transfer(self.alice_usd.number, self.bob_ngn.number, 10**6), "insufficient funds"),
        ]
        for call, reason in cases:
            with self.subTest(reason=reason):
                self.assertLogged(
                    self.capture(call, "WARNING"), "WARNING",
                    f"Transfer rejected: {reason}", f"user_id={self.alice.id}",
                )

    def test_debit_card_logs(self):
        output = self.capture(self.card())
        self.assertLogged(
            output, "INFO", "Debit card payment created", "card=************1111",
            "payer=********0001", "payee=********0001", "amount_sent=1000", "currency_sent=NGN",
            "amount_received=1.33", "currency_received=USD",
        )
        cases = [
            (self.card(payee=UNKNOWN_ACCOUNT), "payee account not found"),
            (self.card(payee=self.carol_gbp.number), "payee account ownership mismatch"),
            (self.card(expiry="13/30"), "Invalid month"),
            (self.card(expiry="01/20"), "Card has expired"),
            (self.card(expiry="06/100"), "Invalid year"),
            (self.card(expiry="garbage"), "Invalid expiry date"),
            (self.card(card=LUHN_INVALID_CARD), "invalid card number"),
            (self.card(card=UNREGISTERED_VALID_CARD), "card not found"),
            (self.card(user=self.bob, payee=self.bob_ngn.number), "payee is the card's own account"),
            (self.card(amount=10**7), "insufficient funds"),
        ]
        for call, reason in cases:
            with self.subTest(reason=reason):
                self.assertLogged(self.capture(call, "WARNING"), "WARNING", f"Debit card payment rejected: {reason}")

    def test_foreign_detail_view_logs(self):
        cases = [
            (TransactionDetail, self.dep_alice),
            (DepositDetail, self.dep_alice),
            (TransferDetails, self.tr_a2b),
            (DebitCardDetails, self.dc_b2a),
        ]
        for view_cls, transaction in cases:
            with self.subTest(view=view_cls.__name__):
                output = self.capture(lambda: self.get(view_cls, self.carol, identifier=str(transaction.identifier)))
                self.assertLogged(
                    output, "INFO", "Transaction viewed by non-owner", f"view={view_cls.__name__}",
                    f"identifier={transaction.identifier}", f"viewer_id={self.carol.id}",
                )

    def test_owner_detail_views_do_not_log(self):
        cases = [
            (DepositDetail, self.dep_alice),
            (TransferDetails, self.tr_a2b),
            (DebitCardDetails, self.dc_b2a),
        ]
        logger = logging.getLogger(VIEWS_LOGGER)
        for view_cls, transaction in cases:
            with self.subTest(view=view_cls.__name__):
                with patch.object(logger, "info") as info:
                    self.get(view_cls, self.alice, identifier=str(transaction.identifier))
                info.assert_not_called()
