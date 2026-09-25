from decimal import Decimal

from django.urls import reverse

from accounts.models import Account
from debit_cards.models import DebitCard
from notifications.models import Notification

from .helpers import AccountAPITestCase, make_admin

CREATE_URL = reverse("api:account_creation")
ADMIN_LIST_URL = reverse("api:admin_account_list")


class AccountCreateTests(AccountAPITestCase):
    """Tests for POST /accounts/create/: savings vs current, notifications, duplicate names and input validation."""

    def test_create_savings_account(self):
        r = self.client.post(CREATE_URL, {"name": "Rainy day", "account_type": "SAVINGS", "currency": "GBP"})
        self.assertEqual(r.status_code, 201, r.data)
        acct = Account.objects.get(pk=r.data["id"])
        self.assertEqual((acct.user, acct.name, acct.currency, acct.balance), (self.alice, "Rainy day", "GBP", 0))
        self.assertEqual(len(str(acct.number)), 12)
        self.assertEqual(r.data["user"]["username"], self.alice.username)
        self.assertFalse(DebitCard.objects.filter(account=acct).exists())
        self.assertEqual(Notification.objects.filter(user=self.alice, notification_type="ACCOUNT_NOTIFICATION").count(), 1)

    def test_create_current_account_issues_debit_card(self):
        r = self.client.post(CREATE_URL, {"name": "Spending", "account_type": "CURRENT"})
        self.assertEqual(r.status_code, 201, r.data)
        card = DebitCard.objects.get(account_id=r.data["id"])
        self.assertTrue(str(card.card_number).startswith("5"))
        self.assertEqual(len(card.cvv), 3)
        self.assertGreater(card.expiration_date.year, card.created_date.year)
        notes = Notification.objects.filter(user=self.alice, notification_type="ACCOUNT_NOTIFICATION")
        self.assertEqual(notes.count(), 2)
        self.assertTrue(any("debit card" in n.content for n in notes))

    def test_defaults_when_only_name_given(self):
        r = self.client.post(CREATE_URL, {"name": "Default"})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data["account_type"], r.data["currency"]), ("SAVINGS", "NGN"))

    def test_client_cannot_set_opening_balance(self):
        r = self.client.post(CREATE_URL, {"name": "Rich", "balance": "1000000.00"})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(Account.objects.get(pk=r.data["id"]).balance, Decimal("0"))

    def test_duplicate_name_for_same_user_rejected(self):
        r = self.client.post(CREATE_URL, {"name": "Alice Savings"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Account.objects.filter(user=self.alice).count(), 1)

    def test_same_name_as_another_users_account_is_allowed(self):
        r = self.client.post(CREATE_URL, {"name": "Bob Savings"})
        self.assertEqual(r.status_code, 201, r.data)

    def test_invalid_account_type_and_currency(self):
        r = self.client.post(CREATE_URL, {"name": "x", "account_type": "GOLD"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("account_type", r.data)
        r = self.client.post(CREATE_URL, {"name": "x", "currency": "XXX"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("currency", r.data)
        self.assertEqual(Account.objects.filter(user=self.alice).count(), 1)

    def test_missing_name(self):
        r = self.client.post(CREATE_URL, {"account_type": "SAVINGS"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("name", r.data)

    def test_unauthenticated_cannot_create(self):
        self.client.force_authenticate(None)
        r = self.client.post(CREATE_URL, {"name": "anon"})
        self.assertIn(r.status_code, (401, 403))
        self.assertFalse(Account.objects.filter(name="anon").exists())


class AdminAccountCreateTests(AccountAPITestCase):
    """Tests for POST /admin/accounts/: admins create accounts for a given user_id; missing user is a 400 not a 500."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(make_admin())

    def test_admin_creates_account_for_user(self):
        r = self.client.post(ADMIN_LIST_URL, {"name": "Admin made", "user_id": self.bob.pk, "balance": "25.50"})
        self.assertEqual(r.status_code, 201, r.data)
        acct = Account.objects.get(pk=r.data["id"])
        self.assertEqual((acct.user, acct.balance), (self.bob, Decimal("25.50")))

    def test_admin_create_without_user_is_400(self):
        r = self.client.post(ADMIN_LIST_URL, {"name": "Orphan"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("user_id", r.data)

    def test_admin_create_with_unknown_user_is_400(self):
        r = self.client.post(ADMIN_LIST_URL, {"name": "x", "user_id": 987654})
        self.assertEqual(r.status_code, 400)

    def test_regular_user_cannot_use_admin_create(self):
        self.client.force_authenticate(self.alice)
        r = self.client.post(ADMIN_LIST_URL, {"name": "x", "user_id": self.alice.pk})
        self.assertEqual(r.status_code, 403)
