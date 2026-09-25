from django.urls import reverse
from rest_framework import permissions
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from notifications import views

from .helpers import NotificationAPITestCase, make_notification, make_user


class UserNotificationListTests(NotificationAPITestCase):
    """GET /notifications/: caller sees only their own rows; type filter is case-insensitive and validated."""

    url = reverse("api:notification_list")

    def ids(self, response):
        return {n["id"] for n in response.data["results"]}

    def test_only_own_notifications(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.ids(r), {self.alice_note.pk})
        self.assertNotIn("Bob private message", str(r.data))

    def test_type_filter(self):
        acct = make_notification(self.alice, "ACCOUNT_NOTIFICATION")
        for value in ("account", "ACCOUNT", "Account"):
            with self.subTest(value):
                r = self.client.get(self.url, {"type": value})
                self.assertEqual(r.status_code, 200)
                self.assertEqual(self.ids(r), {acct.pk})
        r = self.client.get(self.url, {"type": "security"})
        self.assertEqual(self.ids(r), set())

    def test_unknown_type_is_400_and_logged(self):
        with self.assertLogs("notifications", "WARNING") as cm:
            r = self.client.get(self.url, {"type": "promo"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual((cm.records[-1].reason, cm.records[-1].user_id), ("unknown_type", self.alice.pk))

    def test_empty_type_param_ignored(self):
        r = self.client.get(self.url, {"type": ""})
        self.assertEqual(self.ids(r), {self.alice_note.pk})

    def test_pagination_size_param_and_cap(self):
        for _ in range(3):
            make_notification(self.alice)
        r = self.client.get(self.url, {"size": 2})
        self.assertEqual(len(r.data["results"]), 2)
        self.assertEqual(r.data["count"], 4)
        self.assertIsNotNone(r.data["next"])
        r = self.client.get(self.url, {"size": 2, "page": 2})
        self.assertEqual(len(r.data["results"]), 2)
        self.assertEqual(views.NotifiCationPagination.max_page_size, 1000)

    def test_anonymous_rejected(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url).status_code, (401, 403))


class UserNotificationDetailTests(NotificationAPITestCase):
    """GET /notifications/<id>/: owner read marks READ once; other users' rows are 404 (no IDOR)."""

    def url(self, note):
        return reverse("api:notification_detail_single", kwargs={"id": note.pk})

    def test_owner_read_marks_read_and_logs_once(self):
        with self.assertLogs("notifications", "INFO") as cm:
            r = self.client.get(self.url(self.alice_note))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["status"], "READ")
        self.alice_note.refresh_from_db()
        self.assertEqual(self.alice_note.status, "READ")
        self.assertEqual([rec.event for rec in cm.records], ["notification.read"])
        self.assertEqual(cm.records[0].notification_id, self.alice_note.pk)

    def test_second_read_does_not_log_again(self):
        self.client.get(self.url(self.alice_note))
        with self.assertNoLogs("notifications", "INFO"):
            r = self.client.get(self.url(self.alice_note))
        self.assertEqual(r.data["status"], "READ")

    def test_other_users_notification_is_404_and_untouched(self):
        with self.assertLogs("notifications", "WARNING") as cm:
            r = self.client.get(self.url(self.bob_note))
        self.assertEqual(r.status_code, 404)
        self.assertNotIn("Bob", str(r.data))
        self.assertEqual((cm.records[-1].reason, cm.records[-1].notification_id), ("not_found", self.bob_note.pk))
        self.bob_note.refresh_from_db()
        self.assertEqual(self.bob_note.status, "UNREAD")

    def test_admin_does_not_bypass_ownership_on_user_endpoint(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(self.url(self.alice_note)).status_code, 404)

    def test_missing_id_404(self):
        r = self.client.get(reverse("api:notification_detail_single", kwargs={"id": 999999}))
        self.assertEqual(r.status_code, 404)

    def test_anonymous_rejected(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url(self.alice_note)).status_code, (401, 403))


class AdminReadTests(NotificationAPITestCase):
    """Admin list/detail return every user's notifications; staff flag (not superuser) is what IsAdminUser checks."""

    def test_admin_list_all_and_detail(self):
        self.client.force_authenticate(self.admin)
        r = self.client.get(reverse("api:admin_notification_list"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual({n["id"] for n in r.data["results"]}, {self.alice_note.pk, self.bob_note.pk})
        r = self.client.get(reverse("api:admin_notification_detail", kwargs={"pk": self.bob_note.pk}))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["user"]["id"], self.bob.pk)
        self.bob_note.refresh_from_db()
        self.assertEqual(self.bob_note.status, "UNREAD")

    def test_admin_detail_unknown_pk_404(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(reverse("api:admin_notification_detail", kwargs={"pk": 999999})).status_code, 404)

    def test_regular_user_and_non_staff_superuser_forbidden(self):
        superuser_not_staff = make_user("Su", "NoStaff", is_superuser=True, is_staff=False)
        for user in (self.alice, superuser_not_staff):
            with self.subTest(user.username):
                self.client.force_authenticate(user)
                self.assertEqual(self.client.get(reverse("api:admin_notification_list")).status_code, 403)
                self.assertEqual(
                    self.client.get(reverse("api:admin_notification_detail", kwargs={"pk": self.alice_note.pk})).status_code, 403
                )


class AuthenticationTests(NotificationAPITestCase):
    """JWT bearer and vb_token cookie are accepted, invalid tokens rejected, and every view declares permissions."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(None)
        self.token = str(RefreshToken.for_user(self.alice).access_token)
        self.url = reverse("api:notification_list")

    def test_bearer_token_accepted(self):
        r = self.client.get(self.url, HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["count"], 1)

    def test_invalid_bearer_rejected(self):
        r = self.client.get(self.url, HTTP_AUTHORIZATION="Bearer not.a.token")
        self.assertEqual(r.status_code, 401)

    def test_vb_token_cookie_accepted(self):
        self.client.cookies["vb_token"] = self.token
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)

    def test_every_view_declares_explicit_permissions(self):
        view_classes = [
            obj for obj in vars(views).values()
            if isinstance(obj, type) and issubclass(obj, APIView) and obj.__module__ == views.__name__
        ]
        self.assertEqual(len(view_classes), 4)
        for view in view_classes:
            with self.subTest(view.__name__):
                self.assertTrue(view.permission_classes)
                self.assertNotIn(permissions.AllowAny, view.permission_classes)
