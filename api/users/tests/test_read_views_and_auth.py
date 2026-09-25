from django.urls import reverse
from rest_framework import permissions
from rest_framework.views import APIView
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken

from users import views
from users.models import User

from .helpers import PASSWORD, UserAPITestCase


class UserGetTests(UserAPITestCase):
    """Tests for /auth/verify/: returns the caller's own profile without secrets."""

    def test_returns_current_user(self):
        r = self.client.get(reverse("api:user_verification"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["id"], self.alice.pk)
        self.assertNotIn("password", r.data)

    def test_unauthenticated_rejected(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(reverse("api:user_verification")).status_code, (401, 403))


class AdminUserDetailTests(UserAPITestCase):
    """Authorization tests for /admin/users/<pk>/: admin only, IDOR regression for normal users."""

    def url(self, user):
        return reverse("api:admin_user_detail", kwargs={"pk": user.pk})

    def test_normal_user_cannot_read_other_or_self(self):
        self.assertEqual(self.client.get(self.url(self.bob)).status_code, 403)
        self.assertEqual(self.client.get(self.url(self.alice)).status_code, 403)

    def test_unauthenticated_rejected(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url(self.bob)).status_code, (401, 403))

    def test_admin_read_update_delete(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(self.url(self.bob)).status_code, 200)
        r = self.client.patch(self.url(self.bob), {"city": "Rome"})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["city"], "Rome")
        self.assertEqual(self.client.delete(self.url(self.bob)).status_code, 204)
        self.assertFalse(User.objects.filter(pk=self.bob.pk).exists())

    def test_admin_unknown_pk_404(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(reverse("api:admin_user_detail", kwargs={"pk": 999999})).status_code, 404)


class LoginTests(UserAPITestCase):
    """Tests for /auth/login/: tokens returned as JSON and httponly cookies, bad credentials rejected."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(None)
        self.url = reverse("api:user_login")

    def test_login_sets_cookies(self):
        r = self.client.post(self.url, {"username": self.alice.username, "password": PASSWORD})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIn("access", r.data)
        self.assertEqual(r.cookies["vb_token"].value, r.data["access"])
        self.assertEqual(r.cookies["vb_rtoken"].value, r.data["refresh"])
        self.assertTrue(r.cookies["vb_token"]["httponly"])

    def test_bad_password_401_without_cookies(self):
        r = self.client.post(self.url, {"username": self.alice.username, "password": "nope"})
        self.assertEqual(r.status_code, 401)
        self.assertNotIn("vb_token", r.cookies)

    def test_unknown_user_401(self):
        r = self.client.post(self.url, {"username": "ghost", "password": PASSWORD})
        self.assertEqual(r.status_code, 401)

    def test_missing_fields_400(self):
        r = self.client.post(self.url, {"username": self.alice.username})
        self.assertEqual(r.status_code, 400)


class RefreshTests(UserAPITestCase):
    """Tests for /auth/token/refresh/: rotates tokens into cookies and rejects invalid refresh tokens."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(None)
        self.url = reverse("api:token_refresh")

    def test_refresh_sets_new_cookies(self):
        refresh = RefreshToken.for_user(self.alice)
        r = self.client.post(self.url, {"refresh": str(refresh)})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.cookies["vb_token"].value, r.data["access"])
        self.assertIn("vb_rtoken", r.cookies)

    def test_garbage_refresh_401(self):
        r = self.client.post(self.url, {"refresh": "not.a.token"})
        self.assertEqual(r.status_code, 401)
        self.assertNotIn("vb_token", r.cookies)


class LogoutTests(UserAPITestCase):
    """Tests for /auth/logout/: blacklists the refresh cookie and clears both cookies."""

    def setUp(self):
        super().setUp()
        self.url = reverse("api:user_logout")

    def test_logout_blacklists_and_clears_cookies(self):
        refresh = RefreshToken.for_user(self.alice)
        self.client.cookies["vb_rtoken"] = str(refresh)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, {"details": "success"})
        self.assertTrue(BlacklistedToken.objects.filter(token__jti=refresh["jti"]).exists())
        self.assertEqual(r.cookies["vb_token"].value, "")
        self.assertEqual(r.cookies["vb_rtoken"].value, "")

    def test_logout_without_refresh_cookie_400(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data, {"details": "failed"})

    def test_logout_with_garbage_cookie_400(self):
        self.client.cookies["vb_rtoken"] = "garbage"
        self.assertEqual(self.client.get(self.url).status_code, 400)

    def test_unauthenticated_rejected(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url).status_code, (401, 403))


class AuthMechanismTests(UserAPITestCase):
    """Tests for JWT bearer, cookie-injected JWT, admin-only endpoints and explicit permission declarations."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(None)
        self.token = str(RefreshToken.for_user(self.alice).access_token)
        self.url = reverse("api:user_verification")

    def test_bearer_jwt_accepted(self):
        r = self.client.get(self.url, HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["id"], self.alice.pk)

    def test_garbage_jwt_rejected(self):
        self.assertEqual(self.client.get(self.url, HTTP_AUTHORIZATION="Bearer not.a.token").status_code, 401)

    def test_vb_token_cookie_is_promoted_to_bearer(self):
        self.client.cookies["vb_token"] = self.token
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_admin_list_requires_staff(self):
        url = reverse("api:admin_user_list")
        self.assertIn(self.client.get(url).status_code, (401, 403))
        self.client.force_authenticate(self.alice)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_every_user_view_declares_explicit_permissions(self):
        public = {views.UserCreate, views.Login, views.RefreshTokenView}
        for name in dir(views):
            obj = getattr(views, name)
            if not (isinstance(obj, type) and issubclass(obj, APIView) and obj.__module__ == views.__name__):
                continue
            if obj in public:
                continue
            with self.subTest(view=name):
                self.assertTrue(
                    set(obj.permission_classes) <= {permissions.IsAuthenticated, permissions.IsAdminUser},
                    f"{name} must restrict access explicitly",
                )
