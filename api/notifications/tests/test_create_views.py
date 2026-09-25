from django.urls import reverse

from notifications.models import Notification

from .helpers import NotificationAPITestCase


class AdminNotificationCreateTests(NotificationAPITestCase):
    """POST /admin/notifications/: admin-only creation, validation errors, and the former NULL-user crash."""

    url = reverse("api:admin_notification_list")

    def payload(self, **overrides):
        data = {"user_id": self.bob.pk, "notification_type": "SECURITY_NOTIFICATION", "content": "Login from new device"}
        data.update(overrides)
        return data

    def test_admin_creates_for_target_user(self):
        self.client.force_authenticate(self.admin)
        r = self.client.post(self.url, self.payload())
        self.assertEqual(r.status_code, 201, r.data)
        note = Notification.objects.get(pk=r.data["id"])
        self.assertEqual((note.user, note.notification_type, note.status), (self.bob, "SECURITY_NOTIFICATION", "UNREAD"))
        self.assertEqual(r.data["user"]["id"], self.bob.pk)

    def test_missing_user_is_400_not_500(self):
        self.client.force_authenticate(self.admin)
        r = self.client.post(self.url, {"notification_type": "USER_NOTIFICATION", "content": "x"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("user_id", r.data)

    def test_invalid_type_and_status_400(self):
        self.client.force_authenticate(self.admin)
        r = self.client.post(self.url, self.payload(notification_type="PROMO", status="ARCHIVED"))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(set(r.data), {"notification_type", "status"})

    def test_regular_user_cannot_create(self):
        r = self.client.post(self.url, self.payload())
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Notification.objects.count(), 2)

    def test_anonymous_cannot_create(self):
        self.client.force_authenticate(None)
        r = self.client.post(self.url, self.payload())
        self.assertIn(r.status_code, (401, 403))


class AdminNotificationUpdateDeleteTests(NotificationAPITestCase):
    """PATCH/PUT/DELETE /admin/notifications/<pk>/ for admins; forbidden for everyone else."""

    def url(self, note):
        return reverse("api:admin_notification_detail", kwargs={"pk": note.pk})

    def test_admin_patch_status_and_reassign_user(self):
        self.client.force_authenticate(self.admin)
        r = self.client.patch(self.url(self.bob_note), {"status": "READ", "user_id": self.alice.pk})
        self.assertEqual(r.status_code, 200, r.data)
        self.bob_note.refresh_from_db()
        self.assertEqual((self.bob_note.status, self.bob_note.user), ("READ", self.alice))

    def test_admin_put_requires_full_payload(self):
        self.client.force_authenticate(self.admin)
        r = self.client.put(self.url(self.bob_note), {"status": "READ"})
        self.assertEqual(r.status_code, 400)

    def test_admin_delete(self):
        self.client.force_authenticate(self.admin)
        r = self.client.delete(self.url(self.bob_note))
        self.assertEqual(r.status_code, 204)
        self.assertFalse(Notification.objects.filter(pk=self.bob_note.pk).exists())

    def test_owner_cannot_modify_via_admin_endpoint(self):
        for method in ("patch", "put", "delete"):
            with self.subTest(method):
                r = getattr(self.client, method)(self.url(self.alice_note), {"status": "READ"})
                self.assertEqual(r.status_code, 403)
        self.alice_note.refresh_from_db()
        self.assertEqual(self.alice_note.status, "UNREAD")
