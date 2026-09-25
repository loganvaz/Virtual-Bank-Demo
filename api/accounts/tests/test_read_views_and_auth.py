from decimal import Decimal

from django.urls import reverse
from rest_framework import permissions
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from accounts import views
from accounts.models import Account
from notifications.models import Notification

from .helpers import AccountAPITestCase, make_account, make_admin

LIST_URL = reverse("api:account_list")
ADMIN_LIST_URL = reverse("api:admin_account_list")


def detail_url(account):
    return reverse("api:account_detail", kwargs={"number": account.number})


def admin_detail_url(account):
    return reverse("api:admin_account_detail", kwargs={"pk": account.pk})


class UserAccountListTests(AccountAPITestCase):
    """Tests that GET /accounts/ returns only the caller's accounts."""

    def test_returns_only_own_accounts(self):
        make_account(self.alice, name="Second")
        r = self.client.get(LIST_URL)
        self.assertEqual(r.status_code, 200)
        self.assertEqual({a["name"] for a in r.data}, {"Alice Savings", "Second"})
        self.assertNotIn(str(self.bob_acct.number), str(r.data))

    def test_unauthenticated_rejected(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(LIST_URL).status_code, (401, 403))


class UserAccountDetailTests(AccountAPITestCase):
    """Tests for GET/PUT/PATCH/DELETE /accounts/<number>/ including IDOR, rename rules and immutable fields."""

    def test_owner_can_read(self):
        r = self.client.get(detail_url(self.alice_acct))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["number"], str(self.alice_acct.number))
        self.assertEqual(r.data["balance"], "100.00")

    def test_other_users_account_is_404_not_leaked(self):
        r = self.client.get(detail_url(self.bob_acct))
        self.assertEqual(r.status_code, 404)
        self.assertNotIn("Bob", str(r.data))

    def test_unknown_number_is_404(self):
        r = self.client.get(reverse("api:account_detail", kwargs={"number": 999999999999}))
        self.assertEqual(r.status_code, 404)

    def test_rename_via_patch(self):
        r = self.client.patch(detail_url(self.alice_acct), {"name": "Holiday fund"})
        self.assertEqual(r.status_code, 200, r.data)
        self.alice_acct.refresh_from_db()
        self.assertEqual(self.alice_acct.name, "Holiday fund")
        self.assertTrue(Notification.objects.filter(user=self.alice, content="Account updated successfully").exists())

    def test_put_with_unchanged_name_is_allowed(self):
        r = self.client.put(detail_url(self.alice_acct), {"name": "Alice Savings", "account_type": "SAVINGS", "currency": "USD"})
        self.assertEqual(r.status_code, 200, r.data)

    def test_rename_to_existing_own_account_name_rejected(self):
        make_account(self.alice, name="Second")
        r = self.client.patch(detail_url(self.alice_acct), {"name": "Second"})
        self.assertEqual(r.status_code, 403)
        self.alice_acct.refresh_from_db()
        self.assertEqual(self.alice_acct.name, "Alice Savings")

    def test_balance_type_currency_and_owner_are_immutable(self):
        original_number = self.alice_acct.number
        r = self.client.patch(detail_url(self.alice_acct), {
            "balance": "999999.00", "account_type": "CURRENT", "currency": "EUR", "user_id": self.bob.pk, "number": "1",
        })
        self.assertEqual(r.status_code, 200, r.data)
        self.alice_acct.refresh_from_db()
        self.assertEqual(self.alice_acct.balance, Decimal("100.00"))
        self.assertEqual((self.alice_acct.account_type, self.alice_acct.currency), ("SAVINGS", "USD"))
        self.assertEqual(self.alice_acct.user, self.alice)
        self.assertEqual(self.alice_acct.number, original_number)

    def test_cannot_update_or_delete_someone_elses_account(self):
        self.assertEqual(self.client.patch(detail_url(self.bob_acct), {"name": "hijack"}).status_code, 404)
        self.assertEqual(self.client.delete(detail_url(self.bob_acct)).status_code, 404)
        self.bob_acct.refresh_from_db()
        self.assertEqual(self.bob_acct.name, "Bob Savings")

    def test_owner_can_delete(self):
        r = self.client.delete(detail_url(self.alice_acct))
        self.assertEqual(r.status_code, 204)
        self.assertFalse(Account.objects.filter(pk=self.alice_acct.pk).exists())
        self.assertTrue(Notification.objects.filter(user=self.alice, content="Account deleted successfully").exists())

    def test_invalid_patch_payload(self):
        r = self.client.patch(detail_url(self.alice_acct), {"name": "n" * 51})
        self.assertEqual(r.status_code, 400)


class AdminAccountViewTests(AccountAPITestCase):
    """Tests that /admin/accounts/ endpoints list/read/update/delete any account for staff only."""

    def setUp(self):
        super().setUp()
        self.admin = make_admin()

    def test_non_admin_forbidden(self):
        for url in (ADMIN_LIST_URL, admin_detail_url(self.bob_acct)):
            with self.subTest(url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_anonymous_rejected(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(ADMIN_LIST_URL).status_code, (401, 403))

    def test_admin_lists_all_accounts(self):
        self.client.force_authenticate(self.admin)
        r = self.client.get(ADMIN_LIST_URL)
        self.assertEqual(r.status_code, 200)
        self.assertEqual({a["id"] for a in r.data}, {self.alice_acct.pk, self.bob_acct.pk})

    def test_admin_reads_updates_and_deletes_any_account(self):
        self.client.force_authenticate(self.admin)
        url = admin_detail_url(self.bob_acct)
        self.assertEqual(self.client.get(url).status_code, 200)
        r = self.client.patch(url, {"balance": "12.34"})
        self.assertEqual(r.status_code, 200, r.data)
        self.bob_acct.refresh_from_db()
        self.assertEqual(self.bob_acct.balance, Decimal("12.34"))
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertFalse(Account.objects.filter(pk=self.bob_acct.pk).exists())

    def test_admin_unknown_pk_404(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(reverse("api:admin_account_detail", kwargs={"pk": 424242})).status_code, 404)


class AuthMechanismTests(AccountAPITestCase):
    """Tests for JWT bearer, vb_token cookie, invalid tokens and the explicit-permissions meta check."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(None)
        self.token = str(RefreshToken.for_user(self.alice).access_token)

    def test_bearer_jwt_accepted(self):
        r = self.client.get(LIST_URL, HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data), 1)

    def test_garbage_jwt_rejected(self):
        self.assertEqual(self.client.get(LIST_URL, HTTP_AUTHORIZATION="Bearer not.a.token").status_code, 401)

    def test_vb_token_cookie_accepted(self):
        self.client.cookies["vb_token"] = self.token
        self.assertEqual(self.client.get(LIST_URL).status_code, 200)

    def test_every_account_view_declares_non_anonymous_permissions(self):
        allowed = {permissions.IsAuthenticated, permissions.IsAdminUser}
        for name in dir(views):
            obj = getattr(views, name)
            if isinstance(obj, type) and issubclass(obj, APIView) and obj.__module__ == views.__name__:
                with self.subTest(name):
                    self.assertTrue(set(obj.permission_classes) & allowed, f"{name} is anonymously readable")
