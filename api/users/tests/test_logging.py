import re

from django.urls import reverse
from rest_framework_simplejwt.tokens import RefreshToken

from virtual_bank.logging import JsonFormatter, PIIRedactingFilter, build_logging_config

from .helpers import PASSWORD, UserAPITestCase, registration_payload

PII_PATTERNS = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "card": re.compile(r"\b\d{16}\b"),
    "account": re.compile(r"\b\d{10}\b"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.\w+"),
    "ip": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
}


class LoggingConfigTests(UserAPITestCase):
    """Tests that the shared logging config routes the users logger through the redacting console handler."""

    def test_users_logger_configured(self):
        cfg = build_logging_config(level="DEBUG")
        self.assertEqual(cfg["loggers"]["users"], {"handlers": ["console"], "level": "DEBUG", "propagate": False})
        self.assertIn("redact_pii", cfg["handlers"]["console"]["filters"])


class ViewLoggingTests(UserAPITestCase):
    """End-to-end: users view code paths emit structured events and never include names, emails, IPs or passwords."""

    def render(self, cm):
        formatter = JsonFormatter()
        lines = []
        for rec in cm.records:
            PIIRedactingFilter().filter(rec)
            lines.append(formatter.format(rec))
        return lines

    def assert_no_pii(self, cm, *forbidden):
        for line in self.render(cm):
            for name, pattern in PII_PATTERNS.items():
                self.assertIsNone(pattern.search(line), f"{name} leaked in {line}")
            for value in (PASSWORD, "Alice", "Anders", self.alice.email, *forbidden):
                self.assertNotIn(value, line)

    def test_registration_logs_user_id_only(self):
        self.client.force_authenticate(None)
        payload = registration_payload()
        with self.assertLogs("users", level="INFO") as cm:
            r = self.client.post(reverse("api:user_registration"), payload, REMOTE_ADDR="198.51.100.7")
        self.assertEqual(r.status_code, 201)
        rec = cm.records[-1]
        self.assertEqual((rec.event, rec.user_id), ("user.registered", r.data["id"]))
        self.assert_no_pii(cm, payload["username"], payload["email"], "198.51.100.7")

    def test_failed_login_is_security_event_without_credentials(self):
        self.client.force_authenticate(None)
        with self.assertLogs("security", level="WARNING") as cm:
            self.client.post(reverse("api:user_login"), {"username": self.alice.username, "password": "wrong"})
        rec = cm.records[-1]
        self.assertEqual((rec.event, rec.reason), ("login.rejected", "invalid_credentials"))
        self.assert_no_pii(cm, self.alice.username, "wrong")

    def test_successful_login_and_refresh_log_events(self):
        self.client.force_authenticate(None)
        with self.assertLogs("users", level="INFO") as cm:
            r = self.client.post(reverse("api:user_login"), {"username": self.alice.username, "password": PASSWORD})
            self.client.post(reverse("api:token_refresh"), {"refresh": r.data["refresh"]})
        self.assertEqual([rec.event for rec in cm.records], ["login.succeeded", "token.refreshed"])
        self.assert_no_pii(cm, r.data["access"], r.data["refresh"])

    def test_update_rejections_log_reason(self):
        url = reverse("api:user_update")
        with self.assertLogs("users", level="WARNING") as cm:
            self.client.patch(url, {"city": "Oslo"})
        self.assertEqual((cm.records[-1].event, cm.records[-1].reason), ("user.update.rejected", "password_required"))
        with self.assertLogs("security", level="WARNING") as cm:
            self.client.patch(url, {"password": "wrong", "city": "Oslo"})
        self.assertEqual((cm.records[-1].reason, cm.records[-1].user_id), ("invalid_password", self.alice.pk))
        self.assert_no_pii(cm)

    def test_update_success_logs_event(self):
        with self.assertLogs("users", level="INFO") as cm:
            self.client.patch(reverse("api:user_update"), {"password": PASSWORD, "city": "Oslo"})
        self.assertEqual((cm.records[-1].event, cm.records[-1].user_id), ("user.updated", self.alice.pk))
        self.assert_no_pii(cm, "Oslo")

    def test_logout_events(self):
        with self.assertLogs("security", level="WARNING") as cm:
            self.client.get(reverse("api:user_logout"))
        self.assertEqual(cm.records[-1].reason, "invalid_refresh_token")
        self.client.cookies["vb_rtoken"] = str(RefreshToken.for_user(self.alice))
        with self.assertLogs("users", level="INFO") as cm:
            self.client.get(reverse("api:user_logout"))
        self.assertEqual((cm.records[-1].event, cm.records[-1].user_id), ("logout.succeeded", self.alice.pk))
        self.assert_no_pii(cm)

    def test_admin_create_and_delete_events(self):
        self.client.force_authenticate(self.admin)
        with self.assertLogs("users", level="INFO") as cm:
            r = self.client.post(reverse("api:admin_user_list"), registration_payload())
            self.client.delete(reverse("api:admin_user_detail", kwargs={"pk": r.data["id"]}))
        self.assertEqual([(rec.event, rec.user_id) for rec in cm.records], [("user.created", r.data["id"]), ("user.deleted", r.data["id"])])
        self.assert_no_pii(cm)
