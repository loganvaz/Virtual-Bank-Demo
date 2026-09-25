from notifications.models import Notification
from notifications.serializers import AdminNotificationSerializer, NotificationSerializer
from notifications.utils import (
    NOTIFICATION_TYPES,
    process_notifications,
    send_account_notification,
    send_security_notification,
    send_transaction_notification,
    send_user_notification,
)

from .helpers import NotificationAPITestCase, make_admin, make_user


class ProcessNotificationsTests(NotificationAPITestCase):
    """Covers every type branch of process_notifications, the 'admin' fan-out and the unknown-type rejection."""

    def test_each_type_creates_matching_notification(self):
        for type_ in NOTIFICATION_TYPES:
            with self.subTest(type_):
                process_notifications(self.alice, type_, "msg")
                self.assertTrue(
                    Notification.objects.filter(user=self.alice, notification_type=type_.upper(), content="msg").exists()
                )

    def test_admin_keyword_notifies_every_superuser_only(self):
        second_admin = make_admin("Other", "Admin")
        process_notifications("admin", "security_notification", "alert")
        recipients = set(Notification.objects.filter(content="alert").values_list("user_id", flat=True))
        self.assertEqual(recipients, {self.admin.pk, second_admin.pk})

    def test_unknown_type_raises_and_logs(self):
        with self.assertLogs("notifications", "WARNING") as cm, self.assertRaises(ValueError):
            process_notifications(self.alice, "bogus_notification", "msg")
        self.assertEqual((cm.records[-1].reason, cm.records[-1].user_id), ("unknown_type", self.alice.pk))
        self.assertFalse(Notification.objects.filter(content="msg").exists())

    def test_unknown_type_for_admin_logs_without_user(self):
        with self.assertLogs("notifications", "WARNING") as cm, self.assertRaises(ValueError):
            process_notifications("admin", "USER_NOTIFICATION", "msg")
        self.assertIsNone(cm.records[-1].user_id)

    def test_send_helpers_return_created_notification(self):
        for fn, type_ in (
            (send_user_notification, "USER_NOTIFICATION"),
            (send_account_notification, "ACCOUNT_NOTIFICATION"),
            (send_transaction_notification, "TRANSACTION_NOTIFICATION"),
            (send_security_notification, "SECURITY_NOTIFICATION"),
        ):
            with self.subTest(type_):
                note = fn(self.bob, "x")
                self.assertEqual((note.user, note.notification_type, note.status), (self.bob, type_, "UNREAD"))


class NotificationModelTests(NotificationAPITestCase):
    """Model defaults and __str__ rendering."""

    def test_defaults_and_str(self):
        self.assertEqual(self.alice_note.status, "UNREAD")
        self.assertIsNotNone(self.alice_note.created_date)
        self.assertEqual(
            str(self.alice_note),
            f"Notification ID: {self.alice_note.pk} - Type: User Notification - User: {self.alice.username}",
        )


class NotificationSerializerTests(NotificationAPITestCase):
    """User-facing serializer: nested user, read-only type/content, status validated against choices."""

    def test_output_shape(self):
        data = NotificationSerializer(self.alice_note).data
        self.assertEqual(set(data), {"id", "user", "notification_type", "content", "status", "created_date"})
        self.assertEqual(data["user"]["id"], self.alice.pk)

    def test_type_and_content_are_read_only(self):
        s = NotificationSerializer(self.alice_note, data={"notification_type": "SECURITY_NOTIFICATION", "content": "x", "status": "READ"}, partial=True)
        self.assertTrue(s.is_valid(), s.errors)
        note = s.save()
        self.assertEqual((note.notification_type, note.content, note.status), ("USER_NOTIFICATION", "Alice private message", "READ"))

    def test_invalid_status_rejected(self):
        s = NotificationSerializer(self.alice_note, data={"status": "ARCHIVED"}, partial=True)
        self.assertFalse(s.is_valid())
        self.assertIn("status", s.errors)


class AdminNotificationSerializerTests(NotificationAPITestCase):
    """Admin serializer accepts user_id/type/content on write and keeps the nested user on read."""

    def test_create_requires_user_id_type_and_content(self):
        s = AdminNotificationSerializer(data={})
        self.assertFalse(s.is_valid())
        self.assertEqual(set(s.errors), {"user_id", "notification_type", "content"})

    def test_create_and_representation(self):
        s = AdminNotificationSerializer(data={"user_id": self.bob.pk, "notification_type": "ACCOUNT_NOTIFICATION", "content": "c"})
        self.assertTrue(s.is_valid(), s.errors)
        note = s.save()
        self.assertEqual(note.user, self.bob)
        self.assertNotIn("user_id", s.data)
        self.assertEqual(s.data["user"]["id"], self.bob.pk)

    def test_unknown_user_and_type_rejected(self):
        s = AdminNotificationSerializer(data={"user_id": 999999, "notification_type": "NOPE", "content": "c"})
        self.assertFalse(s.is_valid())
        self.assertEqual(set(s.errors), {"user_id", "notification_type"})
