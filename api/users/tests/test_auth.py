from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import Account
from users.models import User

PASSWORD = "S3cure-pass!"


class AuthTestMixin:
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="alice", email="alice@example.com", password=PASSWORD
        )
        cls.other = User.objects.create_user(
            username="bob", email="bob@example.com", password=PASSWORD
        )
        cls.admin = User.objects.create_superuser(
            username="root", email="root@example.com", password=PASSWORD
        )
        cls.account = Account.objects.create(
            user=cls.user, name="Main", account_type="SAVINGS", balance=100, number=1234567890
        )

    def login(self, username="alice"):
        return self.client.post(
            reverse("api:user_login"), {"username": username, "password": PASSWORD}
        )

    def auth(self, user):
        token = str(RefreshToken.for_user(user).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")


class LoginTests(AuthTestMixin, APITestCase):
    def test_login_sets_httponly_cookies(self):
        response = self.login()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for name in ("vb_token", "vb_rtoken"):
            cookie = response.cookies[name]
            self.assertTrue(cookie["httponly"])
            self.assertEqual(cookie["samesite"], "Lax")
            self.assertFalse(cookie["secure"])

    @override_settings(AUTH_COOKIE_SECURE=True)
    def test_login_cookies_secure_outside_debug(self):
        response = self.login()
        self.assertTrue(response.cookies["vb_token"]["secure"])

    def test_login_wrong_password_sets_no_cookies(self):
        response = self.client.post(
            reverse("api:user_login"), {"username": "alice", "password": "nope"}
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertNotIn("vb_token", response.cookies)
        self.assertNotIn("password", response.content.decode())

    def test_login_response_omits_password(self):
        self.assertNotIn("password", self.login().json())


class MiddlewareTests(AuthTestMixin, APITestCase):
    def test_cookie_token_authenticates_request(self):
        self.login()
        response = self.client.get(reverse("api:user_verification"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["username"], "alice")

    def test_invalid_cookie_token_rejected(self):
        self.client.cookies["vb_token"] = "not.a.jwt"
        response = self.client.get(reverse("api:user_verification"))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_explicit_header_wins_over_cookie(self):
        self.client.cookies["vb_token"] = "garbage"
        self.auth(self.other)
        response = self.client.get(reverse("api:user_verification"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["username"], "bob")


class PermissionTests(AuthTestMixin, APITestCase):
    PROTECTED = [
        ("api:user_verification", {}),
        ("api:account_list", {}),
        ("api:debit_card_list", {}),
        ("api:notification_list", {}),
        ("api:transaction_history", {}),
        ("api:transfer_history", {}),
        ("api:deposit_list", {}),
        ("api:user_logout", {}),
    ]
    ADMIN_ONLY = [
        "api:admin_user_list",
        "api:admin_account_list",
        "api:admin_debit_card_list",
        "api:admin_notification_list",
        "api:admin_transaction_list",
    ]

    def test_anonymous_gets_401_on_protected_endpoints(self):
        for name, kwargs in self.PROTECTED:
            with self.subTest(name):
                response = self.client.get(reverse(name, kwargs=kwargs))
                self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_regular_user_gets_403_on_admin_endpoints(self):
        self.auth(self.user)
        for name in self.ADMIN_ONLY:
            with self.subTest(name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_access_admin_endpoints(self):
        self.auth(self.admin)
        for name in self.ADMIN_ONLY:
            with self.subTest(name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_register_is_public(self):
        response = self.client.post(
            reverse("api:user_registration"),
            {
                "username": "carol",
                "email": "carol@example.com",
                "password": PASSWORD,
                "first_name": "Carol",
                "last_name": "C",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotIn("password", response.json())

    def test_user_cannot_read_another_users_account(self):
        self.auth(self.other)
        response = self.client.get(
            reverse("api:account_detail", kwargs={"number": self.account.number})
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_owner_can_read_own_account(self):
        self.auth(self.user)
        response = self.client.get(
            reverse("api:account_detail", kwargs={"number": self.account.number})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class TokenLifecycleTests(AuthTestMixin, APITestCase):
    def test_refresh_rotates_and_blacklists_old_token(self):
        old_refresh = self.login().cookies["vb_rtoken"].value
        response = self.client.post(reverse("api:token_refresh"), {"refresh": old_refresh})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotEqual(response.cookies["vb_rtoken"].value, old_refresh)
        self.assertTrue(response.cookies["vb_token"]["httponly"])

        replay = self.client.post(reverse("api:token_refresh"), {"refresh": old_refresh})
        self.assertEqual(replay.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_logout_blacklists_refresh_and_clears_cookies(self):
        refresh = self.login().cookies["vb_rtoken"].value
        response = self.client.get(reverse("api:user_logout"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.cookies["vb_token"].value, "")
        self.assertEqual(response.cookies["vb_rtoken"].value, "")
        jti = RefreshToken(refresh, verify=False)["jti"]
        self.assertTrue(BlacklistedToken.objects.filter(token__jti=jti).exists())

    def test_logout_without_refresh_cookie_fails(self):
        self.auth(self.user)
        response = self.client.get(reverse("api:user_logout"))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_verify_endpoint_accepts_valid_and_rejects_invalid(self):
        token = self.login().cookies["vb_token"].value
        ok = self.client.post(reverse("api:token_verification"), {"token": token})
        bad = self.client.post(reverse("api:token_verification"), {"token": token[:-3] + "xyz"})
        self.assertEqual(ok.status_code, status.HTTP_200_OK)
        self.assertEqual(bad.status_code, status.HTTP_401_UNAUTHORIZED)


class ProfileUpdateTests(AuthTestMixin, APITestCase):
    def test_update_requires_current_password(self):
        self.auth(self.user)
        url = reverse("api:user_update")
        self.assertEqual(
            self.client.patch(url, {"first_name": "A"}).status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            self.client.patch(url, {"first_name": "A", "password": "wrong"}).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )
        response = self.client.patch(url, {"first_name": "A", "password": PASSWORD})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["first_name"], "A")
        self.assertNotIn("password", response.json())
