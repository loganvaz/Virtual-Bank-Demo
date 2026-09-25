import re
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, override_settings
from rest_framework import status
from rest_framework.test import APIRequestFactory, APITestCase, force_authenticate

from debit_cards.models import DebitCard
from users.models import User
from virtual_bank.log_redaction import REDACTED, get_redacted_logger, mask_number

from . import urls as accounts_urls
from .models import Account
from .serializers import AccountCreateSerializer, AccountSerializer
from .utils import currency_to_unicode, generate_account_number
from .views import (
    AccountCreate,
    AccountDetail,
    AccountList,
    UserAccountDetail,
    UserAccountList,
)

IN_MEMORY_CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
VIEWS_LOGGER = "accounts.views"

CARD_NUMBER = 4111111111111111
UNKNOWN_ACCOUNT = 999999999999
ACCOUNT_FIELDS = {"id", "user", "name", "account_type", "balance", "number", "currency", "created_date"}


@override_settings(CHANNEL_LAYERS=IN_MEMORY_CHANNEL_LAYERS)
class AccountsTestBase(APITestCase):
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
        cls.admin = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="pw",
            first_name="Ada", last_name="Admin",
        )
        cls.alice_usd = Account.objects.create(
            user=cls.alice, name="Alice USD", currency="USD",
            balance=Decimal("1000.00"), number=100000000001,
        )
        cls.alice_eur = Account.objects.create(
            user=cls.alice, name="Alice EUR", currency="EUR", account_type="CURRENT",
            balance=Decimal("500.00"), number=100000000002,
        )
        cls.bob_ngn = Account.objects.create(
            user=cls.bob, name="Bob NGN", currency="NGN",
            balance=Decimal("500000.00"), number=200000000001,
        )

    def setUp(self):
        self.factory = APIRequestFactory()
        patcher = patch("accounts.views.process_notifications")
        self.notify = patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, view_cls, method, user, data=None, **kwargs):
        request = getattr(self.factory, method)("/", data or {}, format="json")
        if user is not None:
            force_authenticate(request, user=user)
        return view_cls.as_view()(request, **kwargs)

    def notified_messages(self):
        return [c.args[2] for c in self.notify.call_args_list]

    def pii_values(self):
        values = [str(CARD_NUMBER), str(UNKNOWN_ACCOUNT)]
        for user in (self.alice, self.bob, self.admin):
            values += [user.first_name, user.last_name, user.email]
        for account in (self.alice_usd, self.alice_eur, self.bob_ngn):
            values += [str(account.number), account.name]
        return values

    def assert_no_pii(self, output):
        text = "\n".join(output)
        for value in self.pii_values():
            self.assertNotIn(value, text)
        self.assertIsNone(re.search(r"\b\d{3,4}\b\s*(cvv|expir)", text, re.I), "card secret leaked")


class UtilsTests(SimpleTestCase):
    def test_generate_account_number_is_12_digits(self):
        for _ in range(50):
            number = generate_account_number()
            self.assertIsInstance(number, str)
            self.assertRegex(number, r"^\d{12}$")

    def test_generate_account_number_uses_random_digits(self):
        with patch("accounts.utils.random.randint", side_effect=lambda a, b: 7):
            self.assertEqual(generate_account_number(), "777777777777")

    def test_generate_account_number_never_starts_with_zero(self):
        """BUG: generate_account_number() joins 12 independent random digits, so it
        can start with '0'. Account.number is a BigIntegerField, so the leading zero
        is dropped on save and the persisted account number has fewer than 12 digits
        (and in the extreme case is 0)."""
        with patch("accounts.utils.random.randint", side_effect=lambda a, b: 0):
            number = generate_account_number()
        self.assertNotEqual(number[0], "0")
        self.assertEqual(len(str(int(number))), 12)

    def test_currency_to_unicode_known_codes(self):
        expected = {"USD": "$", "EUR": "€", "GBP": "£", "NGN": "₦", "JPY": "¥"}
        for code, symbol in expected.items():
            self.assertEqual(currency_to_unicode(code), symbol)

    def test_currency_to_unicode_unknown_code_is_empty(self):
        self.assertEqual(currency_to_unicode("XXX"), "")
        self.assertEqual(currency_to_unicode(None), "")
        self.assertEqual(currency_to_unicode("usd"), "")


class AccountModelTests(AccountsTestBase):
    def test_str_contains_type_display_number_and_owner(self):
        self.assertEqual(str(self.alice_usd), "Savings - 100000000001 - User: Alice Anderson")
        self.assertEqual(str(self.alice_eur), "Current - 100000000002 - User: Alice Anderson")

    def test_defaults(self):
        account = Account.objects.create(user=self.bob, name="Defaults", number=300000000001)
        self.assertEqual(account.account_type, "SAVINGS")
        self.assertEqual(account.currency, "NGN")
        self.assertEqual(account.balance, 0)
        self.assertIsNotNone(account.created_date)

    def test_number_is_unique(self):
        from django.db import IntegrityError, transaction

        with transaction.atomic(), self.assertRaises(IntegrityError):
            Account.objects.create(user=self.bob, name="Dup", number=self.alice_usd.number)

    def test_same_name_allowed_across_users(self):
        account = Account.objects.create(user=self.bob, name="Alice USD", number=300000000002)
        self.assertEqual(Account.objects.filter(name="Alice USD").count(), 2)
        account.delete()

    def test_deleting_user_cascades_to_accounts(self):
        user = User.objects.create_user(username="tmp", email="tmp@example.com", password="pw")
        Account.objects.create(user=user, name="Tmp", number=300000000003)
        user.delete()
        self.assertFalse(Account.objects.filter(number=300000000003).exists())


class SerializerTests(AccountsTestBase):
    def test_account_serializer_output_fields_and_nested_user(self):
        data = AccountSerializer(self.alice_usd).data
        self.assertEqual(set(data), ACCOUNT_FIELDS)
        self.assertEqual(data["number"], "100000000001")
        self.assertEqual(data["balance"], "1000.00")
        self.assertEqual(data["user"]["username"], "alice")
        self.assertNotIn("password", data["user"])

    def test_read_only_fields_are_ignored_on_input(self):
        for cls in (AccountSerializer, AccountCreateSerializer):
            serializer = cls(data={"name": "New", "number": "123", "created_date": "2020-01-01T00:00:00Z", "user": 5})
            self.assertTrue(serializer.is_valid(), serializer.errors)
            self.assertEqual(set(serializer.validated_data), {"name"})

    def test_name_is_required_and_max_50(self):
        for cls in (AccountSerializer, AccountCreateSerializer):
            serializer = cls(data={})
            self.assertFalse(serializer.is_valid())
            self.assertIn("name", serializer.errors)
            self.assertFalse(cls(data={"name": "x" * 51}).is_valid())
            self.assertTrue(cls(data={"name": "x" * 50}).is_valid())

    def test_invalid_choices_rejected(self):
        serializer = AccountCreateSerializer(data={"name": "A", "account_type": "CHECKING", "currency": "XXX"})
        self.assertFalse(serializer.is_valid())
        self.assertEqual(set(serializer.errors), {"account_type", "currency"})

    def test_valid_choices_accepted(self):
        serializer = AccountCreateSerializer(data={"name": "A", "account_type": "CURRENT", "currency": "JPY"})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["account_type"], "CURRENT")
        self.assertEqual(serializer.validated_data["currency"], "JPY")

    def test_balance_validation(self):
        self.assertFalse(AccountCreateSerializer(data={"name": "A", "balance": "abc"}).is_valid())
        self.assertFalse(AccountCreateSerializer(data={"name": "A", "balance": "1.234"}).is_valid())
        self.assertFalse(AccountCreateSerializer(data={"name": "A", "balance": "1" * 14 + ".00"}).is_valid())
        serializer = AccountCreateSerializer(data={"name": "A", "balance": "12.50"})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["balance"], Decimal("12.50"))

    def test_create_generates_account_number(self):
        for offset, cls in enumerate((AccountSerializer, AccountCreateSerializer)):
            generated = f"12345678901{offset}"
            with patch("accounts.serializers.generate_account_number", return_value=generated):
                account = cls().create({"name": f"Gen {cls.__name__}", "user": self.bob})
            account.refresh_from_db()
            self.assertEqual(account.number, int(generated))
            self.assertEqual(account.user, self.bob)

    def test_get_user_helpers(self):
        self.assertEqual(AccountSerializer().get_user(self.alice_usd), "Alice Anderson")
        self.assertEqual(AccountCreateSerializer().get_user(self.alice_usd), "alice")
        unowned = SimpleNamespace(user=None)
        self.assertIsNone(AccountSerializer().get_user(unowned))
        self.assertIsNone(AccountCreateSerializer().get_user(unowned))


class AccountCreateTests(AccountsTestBase):
    def create(self, as_user, **data):
        return self.call(AccountCreate, "post", as_user, {"name": "Holiday", **data})

    def test_requires_authentication(self):
        response = self.create(None)
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
        self.assertEqual(Account.objects.count(), 3)

    def test_creates_savings_account_for_request_user(self):
        response = self.create(self.bob, currency="GBP")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        account = Account.objects.get(id=response.data["id"])
        self.assertEqual(account.user, self.bob)
        self.assertEqual(account.account_type, "SAVINGS")
        self.assertEqual(account.currency, "GBP")
        self.assertEqual(account.balance, Decimal("0"))
        self.assertRegex(str(account.number), r"^\d{1,12}$")
        self.assertEqual(response.data["user"]["username"], "bob")
        self.assertFalse(DebitCard.objects.filter(account=account).exists())
        self.notify.assert_called_once_with(self.bob, "account_notification", "A new Account has been successfully created.")

    def test_current_account_issues_debit_card(self):
        with patch("accounts.views.generate_valid_credit_card_number", return_value=CARD_NUMBER):
            response = self.create(self.bob, account_type="CURRENT")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        account = Account.objects.get(id=response.data["id"])
        card = DebitCard.objects.get(account=account)
        self.assertEqual(card.card_number, CARD_NUMBER)
        self.assertRegex(card.cvv, r"^\d{3,4}$")
        self.assertGreater(card.expiration_date.year, account.created_date.year + 1)
        self.assertEqual(self.notify.call_count, 2)
        self.assertEqual(
            self.notified_messages()[1],
            f"A debit card has been successfully created for your account ({account.number}).",
        )

    def test_savings_type_never_issues_card(self):
        response = self.create(self.bob, account_type="SAVINGS")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(DebitCard.objects.count(), 0)
        self.assertEqual(self.notify.call_count, 1)

    def test_duplicate_name_for_same_user_rejected(self):
        response = self.create(self.alice, name="Alice USD")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["detail"], "Account with this name already exists")
        self.assertEqual(Account.objects.filter(user=self.alice).count(), 2)
        self.notify.assert_not_called()

    def test_duplicate_name_check_is_per_user(self):
        response = self.create(self.bob, name="Alice USD")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_duplicate_name_check_is_case_sensitive(self):
        response = self.create(self.alice, name="alice usd")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_invalid_payload_returns_400(self):
        response = self.call(AccountCreate, "post", self.bob, {"account_type": "CURRENT"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("name", response.data)
        response = self.create(self.bob, account_type="current")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("account_type", response.data)
        self.assertEqual(Account.objects.count(), 3)
        self.notify.assert_not_called()

    def test_client_cannot_set_user_or_number(self):
        response = self.create(self.bob, user=self.alice.id, number="123456789012")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        account = Account.objects.get(id=response.data["id"])
        self.assertEqual(account.user, self.bob)
        self.assertNotEqual(account.number, 123456789012)

    def test_client_cannot_set_opening_balance(self):
        """BUG: AccountCreateSerializer leaves `balance` writable, so any user can
        open an account with an arbitrary positive balance (free money)."""
        response = self.create(self.bob, balance="1000000.00")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        account = Account.objects.get(id=response.data["id"])
        self.assertEqual(account.balance, Decimal("0.00"))


class UserAccountListTests(AccountsTestBase):
    def test_requires_authentication(self):
        response = self.call(UserAccountList, "get", None)
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_lists_only_own_accounts(self):
        response = self.call(UserAccountList, "get", self.alice)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual({a["number"] for a in response.data}, {"100000000001", "100000000002"})
        self.assertEqual(set(response.data[0]), ACCOUNT_FIELDS)

    def test_user_without_accounts_gets_empty_list(self):
        response = self.call(UserAccountList, "get", self.admin)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_post_not_allowed(self):
        response = self.call(UserAccountList, "post", self.alice, {"name": "x"})
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class UserAccountDetailTests(AccountsTestBase):
    def detail(self, method, user, number, data=None):
        return self.call(UserAccountDetail, method, user, data, number=number)

    def test_requires_authentication(self):
        response = self.detail("get", None, self.alice_usd.number)
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_retrieve_own_account(self):
        response = self.detail("get", self.alice, self.alice_usd.number)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.alice_usd.id)
        self.assertEqual(response.data["balance"], "1000.00")

    def test_other_users_account_is_hidden_as_404(self):
        response = self.detail("get", self.bob, self.alice_usd.number)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["detail"], "Not found")

    def test_unknown_number_is_404(self):
        response = self.detail("get", self.alice, UNKNOWN_ACCOUNT)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_patch_renames_account(self):
        response = self.detail("patch", self.alice, self.alice_usd.number, {"name": "Rainy day"})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.alice_usd.refresh_from_db()
        self.assertEqual(self.alice_usd.name, "Rainy day")
        self.notify.assert_called_once_with(self.alice, "account_notification", "Account updated successfully")

    def test_patch_ignores_protected_fields(self):
        response = self.detail(
            "patch", self.alice, self.alice_usd.number,
            {"balance": "999999.00", "currency": "JPY", "account_type": "CURRENT"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.alice_usd.refresh_from_db()
        self.assertEqual(self.alice_usd.balance, Decimal("1000.00"))
        self.assertEqual(self.alice_usd.currency, "USD")
        self.assertEqual(self.alice_usd.account_type, "SAVINGS")
        self.assertEqual(self.alice_usd.user, self.alice)

    def test_put_ignores_protected_fields(self):
        response = self.detail(
            "put", self.alice, self.alice_usd.number,
            {"name": "Renamed", "balance": "0.00", "currency": "GBP"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.alice_usd.refresh_from_db()
        self.assertEqual(self.alice_usd.name, "Renamed")
        self.assertEqual(self.alice_usd.balance, Decimal("1000.00"))
        self.assertEqual(self.alice_usd.currency, "USD")

    def test_put_requires_name(self):
        response = self.detail("put", self.alice, self.alice_usd.number, {"currency": "GBP"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("name", response.data)

    def test_rename_to_another_own_account_name_rejected(self):
        response = self.detail("patch", self.alice, self.alice_usd.number, {"name": "Alice EUR"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["detail"], "Account with this name already exists for the user")
        self.alice_usd.refresh_from_db()
        self.assertEqual(self.alice_usd.name, "Alice USD")
        self.notify.assert_not_called()

    def test_rename_to_other_users_account_name_allowed(self):
        response = self.detail("patch", self.alice, self.alice_usd.number, {"name": "Bob NGN"})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_update_keeping_own_name_succeeds(self):
        """BUG: perform_update's duplicate-name check does not exclude the account being
        updated, so a PUT/PATCH that keeps the account's current name is rejected
        with 403 as a "duplicate" of itself."""
        response = self.detail("put", self.alice, self.alice_usd.number, {"name": "Alice USD"})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_cannot_update_other_users_account(self):
        response = self.detail("patch", self.bob, self.alice_usd.number, {"name": "Hijacked"})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.alice_usd.refresh_from_db()
        self.assertEqual(self.alice_usd.name, "Alice USD")

    def test_delete_own_account(self):
        account = Account.objects.create(user=self.bob, name="Temp", number=300000000009)
        response = self.detail("delete", self.bob, account.number)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Account.objects.filter(id=account.id).exists())
        self.notify.assert_called_once_with(self.bob, "account_notification", "Account deleted successfully")

    def test_cannot_delete_other_users_account(self):
        response = self.detail("delete", self.bob, self.alice_usd.number)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(Account.objects.filter(id=self.alice_usd.id).exists())
        self.notify.assert_not_called()


class AdminAccountViewTests(AccountsTestBase):
    def test_list_requires_admin(self):
        for user, expected in ((None, (401, 403)), (self.alice, (403,))):
            response = self.call(AccountList, "get", user)
            self.assertIn(response.status_code, expected)

    def test_detail_requires_admin(self):
        for method in ("get", "patch", "delete"):
            response = self.call(AccountDetail, method, self.alice, {"name": "x"}, pk=self.alice_usd.id)
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.alice_usd.refresh_from_db()
        self.assertEqual(self.alice_usd.name, "Alice USD")

    def test_admin_lists_all_accounts(self):
        response = self.call(AccountList, "get", self.admin)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 3)
        self.assertEqual({a["user"]["username"] for a in response.data}, {"alice", "bob"})

    def test_admin_retrieves_any_account_by_pk(self):
        response = self.call(AccountDetail, "get", self.admin, pk=self.bob_ngn.id)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["number"], "200000000001")
        response = self.call(AccountDetail, "get", self.admin, pk=99999)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_admin_updates_any_account(self):
        response = self.call(AccountDetail, "patch", self.admin, {"balance": "1.00", "name": "Adj"}, pk=self.bob_ngn.id)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.bob_ngn.refresh_from_db()
        self.assertEqual(self.bob_ngn.balance, Decimal("1.00"))
        self.assertEqual(self.bob_ngn.name, "Adj")
        self.assertEqual(self.bob_ngn.user, self.bob)

    def test_admin_deletes_any_account(self):
        account = Account.objects.create(user=self.bob, name="Gone", number=300000000010)
        response = self.call(AccountDetail, "delete", self.admin, pk=account.id)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Account.objects.filter(id=account.id).exists())

    def test_admin_create_without_owner_is_rejected_cleanly(self):
        """BUG: AccountList uses AccountSerializer whose `user` is read-only and whose
        create() never assigns one, so an admin POST hits a NOT NULL IntegrityError
        (HTTP 500) instead of a 400 validation error or a created account."""
        from django.db import IntegrityError, transaction

        try:
            with transaction.atomic():
                response = self.call(AccountList, "post", self.admin, {"name": "Orphan"})
        except IntegrityError as exc:
            self.fail(f"admin account creation crashed: {exc}")
        self.assertIn(response.status_code, (status.HTTP_201_CREATED, status.HTTP_400_BAD_REQUEST))


class UrlsTests(SimpleTestCase):
    def test_app_namespace_and_empty_patterns(self):
        self.assertEqual(accounts_urls.app_name, "accounts")
        self.assertEqual(accounts_urls.urlpatterns, [])


class LoggingTests(AccountsTestBase):
    def assertLogged(self, output, level, *fragments):
        matches = [line for line in output if line.startswith(f"{level}:{VIEWS_LOGGER}:")]
        self.assertTrue(matches, output)
        joined = "\n".join(matches)
        for fragment in fragments:
            self.assertIn(fragment, joined)

    def test_logger_has_redaction_filter_and_settings_route(self):
        logger = get_redacted_logger(VIEWS_LOGGER)
        with self.assertLogs(VIEWS_LOGGER, level="INFO") as cm:
            logger.info("raw account %s email %s", 100000000001, "alice@example.com")
        self.assertEqual(cm.output, [f"INFO:{VIEWS_LOGGER}:raw account {REDACTED} email {REDACTED}"])
        self.assertIn("redact_pii", settings.LOGGING["handlers"]["console"]["filters"])
        self.assertIn("console", settings.LOGGING["loggers"]["accounts"]["handlers"])

    def test_account_created_logs_masked_number(self):
        with self.assertLogs(VIEWS_LOGGER, level="INFO") as cm:
            response = self.call(AccountCreate, "post", self.bob, {"name": "Log me", "currency": "GBP"})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        account = Account.objects.get(id=response.data["id"])
        self.assertLogged(
            cm.output, "INFO", "Account created", f"account_id={account.id}",
            f"user_id={self.bob.id}", f"account={mask_number(account.number)}",
            "account_type=SAVINGS", "currency=GBP",
        )
        self.assertNotIn(str(account.number), "\n".join(cm.output))
        self.assertNotIn("Log me", "\n".join(cm.output))
        self.assert_no_pii(cm.output)

    def test_debit_card_issued_logs_masked_card(self):
        with patch("accounts.views.generate_valid_credit_card_number", return_value=CARD_NUMBER):
            with self.assertLogs(VIEWS_LOGGER, level="INFO") as cm:
                response = self.call(AccountCreate, "post", self.bob, {"name": "Card", "account_type": "CURRENT"})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        card = DebitCard.objects.get(account_id=response.data["id"])
        self.assertLogged(
            cm.output, "INFO", "Debit card issued", f"card_id={card.id}",
            f"account_id={card.account_id}", f"card={mask_number(CARD_NUMBER)}",
        )
        text = "\n".join(cm.output)
        self.assertNotIn(card.cvv, text.replace(f"card_id={card.id}", "").replace(f"account_id={card.account_id}", ""))
        self.assertNotIn(card.expiration_date.strftime("%m/%y"), text)
        self.assert_no_pii(cm.output)

    def test_duplicate_create_logs_warning(self):
        with self.assertLogs(VIEWS_LOGGER, level="WARNING") as cm:
            response = self.call(AccountCreate, "post", self.alice, {"name": "Alice USD"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertLogged(cm.output, "WARNING", "Account creation rejected: duplicate name", f"user_id={self.alice.id}")
        self.assert_no_pii(cm.output)

    def test_lookup_of_foreign_account_logs_warning(self):
        with self.assertLogs(VIEWS_LOGGER, level="WARNING") as cm:
            response = self.call(UserAccountDetail, "get", self.bob, number=self.alice_usd.number)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertLogged(
            cm.output, "WARNING", "Account lookup rejected", f"user_id={self.bob.id}",
            f"account={mask_number(self.alice_usd.number)}",
        )
        self.assert_no_pii(cm.output)

    def test_update_logs_info(self):
        with self.assertLogs(VIEWS_LOGGER, level="INFO") as cm:
            response = self.call(UserAccountDetail, "patch", self.alice, {"name": "Renamed"}, number=self.alice_usd.number)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertLogged(
            cm.output, "INFO", "Account updated", f"account_id={self.alice_usd.id}",
            f"user_id={self.alice.id}", f"account={mask_number(self.alice_usd.number)}",
        )
        self.assertNotIn("Renamed", "\n".join(cm.output))
        self.assert_no_pii(cm.output)

    def test_duplicate_update_logs_warning(self):
        with self.assertLogs(VIEWS_LOGGER, level="WARNING") as cm:
            response = self.call(UserAccountDetail, "patch", self.alice, {"name": "Alice EUR"}, number=self.alice_usd.number)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertLogged(cm.output, "WARNING", "Account update rejected: duplicate name", f"account_id={self.alice_usd.id}")
        self.assert_no_pii(cm.output)

    def test_delete_logs_info(self):
        account = Account.objects.create(user=self.bob, name="Bye", number=300000000011)
        with self.assertLogs(VIEWS_LOGGER, level="INFO") as cm:
            response = self.call(UserAccountDetail, "delete", self.bob, number=account.number)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertLogged(cm.output, "INFO", "Account deleted", f"account_id={account.id}", "account=********0011")
        self.assertNotIn("300000000011", "\n".join(cm.output))
        self.assert_no_pii(cm.output)
