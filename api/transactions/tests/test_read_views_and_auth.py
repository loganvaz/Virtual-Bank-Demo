from django.urls import reverse
from rest_framework import permissions
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from notifications.models import Notification
from transactions import views
from transactions.models import Transaction

from .helpers import TransactionAPITestCase, make_account, make_user


def make_txn(payer, payee, txn_type="TRANSFER", amount="10"):
    return Transaction.objects.create(
        account=payer, payer=payer, payee=payee, transaction_type=txn_type,
        amount_sent=amount, amount_received=amount, currency_sent=payer.currency, currency_received=payee.currency,
    )


class DetailViewAuthorizationTests(TransactionAPITestCase):
    """IDOR regression tests: only participants (or admins) may read a transaction by UUID."""

    def setUp(self):
        super().setUp()
        self.carol = make_user("Carol", "Cole")
        make_account(self.carol)
        self.transfer = make_txn(self.alice_acct, self.bob_acct)
        self.deposit = make_txn(self.alice_acct, self.alice_acct, "DEPOSIT")
        self.card_txn = make_txn(self.bob_acct, self.alice_acct, "DEBIT_CARD")

    def cases(self):
        return [
            ("api:transaction_detail", self.transfer),
            ("api:transfer_detail", self.transfer),
            ("api:deposit_detail", self.deposit),
            ("api:debit_card_transaction_detail", self.card_txn),
        ]

    def test_owner_can_read_without_triggering_security_notification(self):
        for name, txn in self.cases():
            with self.subTest(name):
                r = self.client.get(reverse(name, kwargs={"identifier": txn.identifier}))
                self.assertEqual(r.status_code, 200)
        self.assertFalse(Notification.objects.filter(notification_type="SECURITY_NOTIFICATION").exists())

    def test_counterparty_can_read(self):
        self.client.force_authenticate(self.bob)
        r = self.client.get(reverse("api:transfer_detail", kwargs={"identifier": self.transfer.identifier}))
        self.assertEqual(r.status_code, 200)

    def test_third_party_gets_403(self):
        self.client.force_authenticate(self.carol)
        for name, txn in self.cases():
            with self.subTest(name):
                r = self.client.get(reverse(name, kwargs={"identifier": txn.identifier}))
                self.assertEqual(r.status_code, 403)
                self.assertNotIn("number", str(r.data))

    def test_admin_read_notifies_owner_and_does_not_crash(self):
        admin = make_user("Root", "Admin", is_superuser=True, is_staff=True)
        self.client.force_authenticate(admin)
        for name, txn in self.cases():
            with self.subTest(name):
                r = self.client.get(reverse(name, kwargs={"identifier": txn.identifier}))
                self.assertEqual(r.status_code, 200)
        notes = Notification.objects.filter(notification_type="SECURITY_NOTIFICATION")
        self.assertTrue(notes.exists())
        self.assertTrue(all("administrator" in n.content for n in notes))

    def test_unknown_identifier_404(self):
        r = self.client.get(reverse("api:transaction_detail", kwargs={"identifier": "00000000-0000-0000-0000-000000000000"}))
        self.assertEqual(r.status_code, 404)


class HistoryViewTests(TransactionAPITestCase):
    """Tests that history endpoints only return the caller's transactions and honour role/account filters."""

    def setUp(self):
        super().setUp()
        self.carol = make_user("Carol", "Cole")
        self.carol_acct = make_account(self.carol)
        self.t_ab = make_txn(self.alice_acct, self.bob_acct)
        self.t_ba = make_txn(self.bob_acct, self.alice_acct)
        self.t_bc = make_txn(self.bob_acct, self.carol_acct)

    def ids(self, r):
        return {row["identifier"] for row in r.data["results"]}

    def test_default_returns_only_own_transactions(self):
        r = self.client.get(reverse("api:transaction_history"))
        self.assertEqual(self.ids(r), {str(self.t_ab.identifier), str(self.t_ba.identifier)})

    def test_role_filters(self):
        r = self.client.get(reverse("api:transfer_history"), {"role": "payer"})
        self.assertEqual(self.ids(r), {str(self.t_ab.identifier)})
        r = self.client.get(reverse("api:transfer_history"), {"role": "payee"})
        self.assertEqual(self.ids(r), {str(self.t_ba.identifier)})

    def test_account_number_filter_for_someone_elses_account_is_empty(self):
        r = self.client.get(reverse("api:transaction_history"), {"role": "payer", "account_number": self.bob_acct.number})
        self.assertEqual(self.ids(r), set())

    def test_unauthenticated_rejected(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(reverse("api:transaction_history")).status_code, (401, 403))


class AuthMechanismTests(TransactionAPITestCase):
    """Tests for JWT bearer, cookie-injected JWT and admin-only endpoints."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(None)
        self.token = str(RefreshToken.for_user(self.alice).access_token)

    def test_bearer_jwt_accepted(self):
        r = self.client.get(reverse("api:deposit_list"), HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(r.status_code, 200)

    def test_garbage_jwt_rejected(self):
        r = self.client.get(reverse("api:deposit_list"), HTTP_AUTHORIZATION="Bearer not.a.token")
        self.assertEqual(r.status_code, 401)

    def test_vb_token_cookie_is_promoted_to_bearer(self):
        self.client.cookies["vb_token"] = self.token
        r = self.client.get(reverse("api:deposit_list"))
        self.assertEqual(r.status_code, 200)

    def test_admin_endpoints_require_staff(self):
        url = reverse("api:admin_transaction_list") if _has_name("admin_transaction_list") else "/api/v1/admin/transactions/"
        self.assertIn(self.client.get(url).status_code, (401, 403))
        self.client.force_authenticate(self.alice)
        self.assertEqual(self.client.get(url).status_code, 403)
        admin = make_user("Root", "Admin", is_superuser=True, is_staff=True)
        self.client.force_authenticate(admin)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_every_transaction_view_declares_non_anonymous_permissions(self):
        allowed = {permissions.IsAuthenticated, permissions.IsAdminUser}
        for name in dir(views):
            obj = getattr(views, name)
            if isinstance(obj, type) and issubclass(obj, APIView) and obj.__module__ == views.__name__:
                with self.subTest(name):
                    self.assertTrue(set(obj.permission_classes) & allowed, f"{name} is anonymously readable")


def _has_name(name):
    try:
        reverse(f"api:{name}")
        return True
    except Exception:
        return False
