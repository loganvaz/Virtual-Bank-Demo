from django.urls import reverse
from rest_framework import permissions
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from debit_cards import views

from .helpers import DebitCardAPITestCase, make_admin, make_card


class UserDebitCardListTests(DebitCardAPITestCase):
    """Tests GET /debit-cards/ returns only the caller's cards."""

    def test_only_own_cards(self):
        make_card(self.alice_acct)
        r = self.client.get(reverse("api:debit_card_list"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data), 2)
        self.assertTrue(all(c["account"]["id"] == self.alice_acct.pk for c in r.data))
        self.assertNotIn(str(self.bob_card.card_number), str(r.data))

    def test_user_without_cards_gets_empty_list(self):
        self.client.force_authenticate(make_admin())
        r = self.client.get(reverse("api:debit_card_list"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, [])

    def test_anonymous_rejected(self):
        self.client.force_authenticate(None)
        r = self.client.get(reverse("api:debit_card_list"))
        self.assertIn(r.status_code, (401, 403))


class UserDebitCardDetailTests(DebitCardAPITestCase):
    """Tests GET /debit-cards/<number>/: owner 200, other user's card and unknown number 404 (no IDOR)."""

    def url(self, number):
        return reverse("api:debit_card_detail", kwargs={"number": number})

    def test_owner_reads_card(self):
        r = self.client.get(self.url(self.alice_card.card_number))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["id"], self.alice_card.pk)
        self.assertEqual(r.data["expiration_date"], self.alice_card.expiration_date.strftime("%m/%y"))

    def test_other_users_card_is_404(self):
        r = self.client.get(self.url(self.bob_card.card_number))
        self.assertEqual(r.status_code, 404)
        self.assertNotIn(str(self.bob_acct.number), str(r.data))

    def test_unknown_number_is_404(self):
        r = self.client.get(self.url(5000000000000009))
        self.assertEqual(r.status_code, 404)

    def test_admin_does_not_see_other_users_card_via_public_endpoint(self):
        self.client.force_authenticate(make_admin())
        r = self.client.get(self.url(self.alice_card.card_number))
        self.assertEqual(r.status_code, 404)


class AdminDebitCardReadTests(DebitCardAPITestCase):
    """Tests admin list/detail endpoints: admins see every card, regular users get 403."""

    def setUp(self):
        super().setUp()
        self.admin = make_admin()

    def test_admin_lists_all_cards(self):
        self.client.force_authenticate(self.admin)
        r = self.client.get(reverse("api:admin_debit_card_list"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual({c["id"] for c in r.data}, {self.alice_card.pk, self.bob_card.pk})

    def test_admin_reads_any_card(self):
        self.client.force_authenticate(self.admin)
        r = self.client.get(reverse("api:admin_debit_card_detail", kwargs={"pk": self.bob_card.pk}))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["id"], self.bob_card.pk)

    def test_admin_unknown_pk_404(self):
        self.client.force_authenticate(self.admin)
        r = self.client.get(reverse("api:admin_debit_card_detail", kwargs={"pk": 999999}))
        self.assertEqual(r.status_code, 404)

    def test_staff_without_superuser_is_admin(self):
        self.client.force_authenticate(make_admin().__class__.objects.create_user(
            username="staffonly", password="pw12345!", is_staff=True))
        r = self.client.get(reverse("api:admin_debit_card_list"))
        self.assertEqual(r.status_code, 200)

    def test_regular_user_forbidden(self):
        for url in (reverse("api:admin_debit_card_list"),
                    reverse("api:admin_debit_card_detail", kwargs={"pk": self.alice_card.pk})):
            with self.subTest(url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_anonymous_forbidden(self):
        self.client.force_authenticate(None)
        r = self.client.get(reverse("api:admin_debit_card_list"))
        self.assertIn(r.status_code, (401, 403))


class JWTAuthTests(DebitCardAPITestCase):
    """Tests that bearer JWTs and the vb_token cookie authenticate, and invalid tokens are rejected."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(None)
        self.token = str(RefreshToken.for_user(self.alice).access_token)
        self.url = reverse("api:debit_card_list")

    def test_bearer_jwt_accepted(self):
        r = self.client.get(self.url, HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data[0]["id"], self.alice_card.pk)

    def test_garbage_jwt_rejected(self):
        r = self.client.get(self.url, HTTP_AUTHORIZATION="Bearer not.a.token")
        self.assertEqual(r.status_code, 401)

    def test_vb_token_cookie_accepted(self):
        self.client.cookies["vb_token"] = self.token
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)


class PermissionDeclarationTests(DebitCardAPITestCase):
    """Meta-test: every debit_cards view declares explicit, non-anonymous permission_classes."""

    def test_all_views_declare_permissions(self):
        allowed = {permissions.IsAuthenticated, permissions.IsAdminUser}
        found = 0
        for name in dir(views):
            obj = getattr(views, name)
            if isinstance(obj, type) and issubclass(obj, APIView) and obj.__module__ == views.__name__:
                found += 1
                self.assertIn("permission_classes", vars(obj), f"{name} relies on defaults")
                self.assertTrue(set(obj.permission_classes) & allowed, f"{name} is anonymously readable")
        self.assertEqual(found, 4)
