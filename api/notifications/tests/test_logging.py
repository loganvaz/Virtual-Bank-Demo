import logging
import re

from django.test import SimpleTestCase
from django.urls import reverse

from notifications.utils import process_notifications
from virtual_bank.logging import STRUCTURED_FIELDS, JsonFormatter, PIIRedactingFilter, build_logging_config

from .helpers import NotificationAPITestCase

PII_PATTERNS = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "card": re.compile(r"\b\d{16}\b"),
    "account": re.compile(r"\b\d{10}\b"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.\w+"),
}


class LoggingConfigTests(SimpleTestCase):
    """The shared config routes the notifications logger through the redacting JSON handler."""

    def test_notifications_logger_configured(self):
        cfg = build_logging_config()
        self.assertEqual(cfg["loggers"]["notifications"], {"handlers": ["console"], "level": "INFO", "propagate": False})
        self.assertIn("redact_pii", cfg["handlers"]["console"]["filters"])
        self.assertIn("notification_id", STRUCTURED_FIELDS)
        self.assertIn("notification_type", STRUCTURED_FIELDS)

    def test_formatter_emits_notification_fields(self):
        rec = logging.LogRecord("notifications", logging.INFO, __file__, 1, "x", (), None)
        rec.event, rec.notification_id, rec.notification_type = "notification.created", 5, "USER_NOTIFICATION"
        PIIRedactingFilter().filter(rec)
        line = JsonFormatter().format(rec)
        self.assertIn('"notification_id": 5', line)
        self.assertIn('"notification_type": "USER_NOTIFICATION"', line)


class ViewLoggingTests(NotificationAPITestCase):
    """End-to-end: notification code paths emit structured events and never include content, names or emails."""

    SECRET = "SSN 123-45-6789 card 4111111111111111 acct 1000000042 bob@example.com"

    def assert_no_pii(self, cm):
        formatter = JsonFormatter()
        for rec in cm.records:
            PIIRedactingFilter().filter(rec)
            line = formatter.format(rec)
            for name, pattern in PII_PATTERNS.items():
                self.assertIsNone(pattern.search(line), f"{name} leaked in {line}")
            for forbidden in ("Alice", "Bob", "private message", self.SECRET, "SSN"):
                self.assertNotIn(forbidden, line)

    def test_created_event_has_ids_only(self):
        with self.assertLogs("notifications", "INFO") as cm:
            process_notifications(self.alice, "security_notification", self.SECRET)
        rec = cm.records[-1]
        self.assertEqual((rec.event, rec.user_id, rec.notification_type), ("notification.created", self.alice.pk, "SECURITY_NOTIFICATION"))
        self.assertIsInstance(rec.notification_id, int)
        self.assert_no_pii(cm)

    def test_admin_create_event(self):
        self.client.force_authenticate(self.admin)
        with self.assertLogs("notifications", "INFO") as cm:
            r = self.client.post(
                reverse("api:admin_notification_list"),
                {"user_id": self.bob.pk, "notification_type": "USER_NOTIFICATION", "content": self.SECRET},
            )
        self.assertEqual(r.status_code, 201)
        rec = cm.records[-1]
        self.assertEqual((rec.event, rec.user_id, rec.notification_id), ("notification.admin_created", self.admin.pk, r.data["id"]))
        self.assert_no_pii(cm)

    def test_read_and_rejection_events(self):
        self.alice_note.content = self.SECRET
        self.alice_note.save()
        with self.assertLogs("notifications", "INFO") as cm:
            self.client.get(reverse("api:notification_detail_single", kwargs={"id": self.alice_note.pk}))
            self.client.get(reverse("api:notification_detail_single", kwargs={"id": self.bob_note.pk}))
            self.client.get(reverse("api:notification_list"), {"type": self.SECRET})
        events = [(r.levelname, r.event) for r in cm.records]
        self.assertEqual(events, [
            ("INFO", "notification.read"),
            ("WARNING", "notification.read.rejected"),
            ("WARNING", "notification.list.rejected"),
        ])
        self.assert_no_pii(cm)
