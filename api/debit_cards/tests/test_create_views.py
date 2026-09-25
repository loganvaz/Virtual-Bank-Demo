from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from debit_cards.models import DebitCard
from debit_cards.utils import luhn_checksum

from .helpers import DebitCardAPITestCase, future, make_admin


class AdminDebitCardCreateTests(DebitCardAPITestCase):
    """Tests POST /admin/debit-cards/: happy path, validation errors and non-admin rejection."""

    def setUp(self):
        super().setUp()
        self.admin = make_admin()
        self.url = reverse("api:admin_debit_card_list")

    def payload(self, **overrides):
        data = {"account_id": self.bob_acct.pk, "expires_at": future().isoformat()}
        data.update(overrides)
        return data

    def test_admin_creates_card(self):
        self.client.force_authenticate(self.admin)
        r = self.client.post(self.url, self.payload())
        self.assertEqual(r.status_code, 201, r.data)
        card = DebitCard.objects.get(pk=r.data["id"])
        self.assertEqual(card.account, self.bob_acct)
        self.assertEqual(luhn_checksum(str(card.card_number)), 0)
        self.assertEqual(r.data["account"]["id"], self.bob_acct.pk)
        self.assertEqual(r.data["expiration_date"], card.expiration_date.strftime("%m/%y"))

    def test_missing_account_is_400(self):
        self.client.force_authenticate(self.admin)
        r = self.client.post(self.url, {"expires_at": future().isoformat()})
        self.assertEqual(r.status_code, 400)
        self.assertIn("account_id", r.data)

    def test_past_expiry_is_400(self):
        self.client.force_authenticate(self.admin)
        r = self.client.post(self.url, self.payload(expires_at=(timezone.now() - timedelta(days=1)).isoformat()))
        self.assertEqual(r.status_code, 400)
        self.assertIn("expires_at", r.data)

    def test_garbage_expiry_is_400(self):
        self.client.force_authenticate(self.admin)
        r = self.client.post(self.url, self.payload(expires_at="12/29"))
        self.assertEqual(r.status_code, 400)

    def test_regular_user_cannot_create(self):
        before = DebitCard.objects.count()
        r = self.client.post(self.url, self.payload(account_id=self.alice_acct.pk))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(DebitCard.objects.count(), before)

    def test_anonymous_cannot_create(self):
        self.client.force_authenticate(None)
        r = self.client.post(self.url, self.payload())
        self.assertIn(r.status_code, (401, 403))


class AdminDebitCardUpdateDeleteTests(DebitCardAPITestCase):
    """Tests PUT/PATCH/DELETE /admin/debit-cards/<pk>/ by admins and rejection for regular users."""

    def setUp(self):
        super().setUp()
        self.admin = make_admin()
        self.url = reverse("api:admin_debit_card_detail", kwargs={"pk": self.alice_card.pk})

    def test_admin_patch_moves_card_to_other_account(self):
        self.client.force_authenticate(self.admin)
        r = self.client.patch(self.url, {"account_id": self.bob_acct.pk})
        self.assertEqual(r.status_code, 200, r.data)
        self.alice_card.refresh_from_db()
        self.assertEqual(self.alice_card.account, self.bob_acct)

    def test_admin_put_updates_expiry(self):
        self.client.force_authenticate(self.admin)
        new_exp = future(48)
        r = self.client.put(self.url, {"account_id": self.alice_acct.pk, "expires_at": new_exp.isoformat()})
        self.assertEqual(r.status_code, 200, r.data)
        self.alice_card.refresh_from_db()
        self.assertEqual(self.alice_card.expiration_date.strftime("%m/%y"), new_exp.strftime("%m/%y"))

    def test_admin_delete(self):
        self.client.force_authenticate(self.admin)
        r = self.client.delete(self.url)
        self.assertEqual(r.status_code, 204)
        self.assertFalse(DebitCard.objects.filter(pk=self.alice_card.pk).exists())

    def test_owner_cannot_modify_or_delete_own_card(self):
        for method in ("patch", "put", "delete"):
            with self.subTest(method):
                r = getattr(self.client, method)(self.url, {"account_id": self.bob_acct.pk})
                self.assertEqual(r.status_code, 403)
        self.assertTrue(DebitCard.objects.filter(pk=self.alice_card.pk, account=self.alice_acct).exists())
