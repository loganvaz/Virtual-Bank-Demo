import logging

from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from users.models import User
from virtual_bank.logging_utils import REDACTED, PIIRedactionFilter, redact

PASSWORD = "S3cure-pass!"


class RedactTests(SimpleTestCase):
    def test_redacts_email(self):
        self.assertEqual(redact("user alice@example.com logged in"), f"user {REDACTED} logged in")

    def test_redacts_jwt(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyIjoxfQ.abc-DEF_123"
        self.assertEqual(redact(f"token {jwt}"), f"token {REDACTED}")

    def test_redacts_secret_key_values(self):
        for key in ("password", "token", "cvv", "Password"):
            with self.subTest(key):
                self.assertEqual(redact(f"{key}=hunter2 ok"), f"{key}={REDACTED} ok")
                self.assertEqual(redact(f"{key}: hunter2"), f"{key}: {REDACTED}")

    def test_redacts_card_and_phone_numbers(self):
        self.assertEqual(redact("card 4111111111111111"), f"card {REDACTED}")
        self.assertEqual(redact("call +1 (555) 123-4567 now"), f"call {REDACTED} now")

    def test_keeps_ids_and_ips(self):
        for line in (
            "auth.login.success user_id=42 ip=10.0.0.7 status=200",
            "HTTP POST /api/auth/login/ 404 [0.01, 172.18.0.1:60050]",
        ):
            self.assertEqual(redact(line), line)

    def test_filter_applies_to_record_with_args(self):
        record = logging.LogRecord(
            "t", logging.INFO, __file__, 1, "email=%s", ("bob@x.io",), None
        )
        self.assertTrue(PIIRedactionFilter().filter(record))
        self.assertEqual(record.getMessage(), f"email={REDACTED}")


class AuthAuditLogTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="alice", email="alice@example.com", password=PASSWORD
        )

    def assertNoPII(self, output, *tokens):
        for line in output:
            self.assertNotIn(PASSWORD, line)
            self.assertNotIn("alice@example.com", line)
            self.assertNotIn("alice", line)
            for token in tokens:
                self.assertNotIn(token, line)

    def test_login_success_is_audited_without_pii(self):
        with self.assertLogs("virtual_bank.auth", level="INFO") as logs:
            response = self.client.post(
                reverse("api:user_login"), {"username": "alice", "password": PASSWORD}
            )
        self.assertEqual(len(logs.records), 1)
        self.assertIn(f"auth.login.success user_id={self.user.id}", logs.output[0])
        self.assertNoPII(logs.output, response.cookies["vb_token"].value)

    def test_login_failure_is_audited_without_pii(self):
        with self.assertLogs("virtual_bank.auth", level="WARNING") as logs:
            self.client.post(
                reverse("api:user_login"), {"username": "alice", "password": "wrong"}
            )
        self.assertEqual(len(logs.records), 1)
        self.assertIn("auth.login.failed ip=127.0.0.1", logs.output[0])
        self.assertNoPII(logs.output, "wrong")

    def test_logout_is_audited(self):
        self.client.post(reverse("api:user_login"), {"username": "alice", "password": PASSWORD})
        with self.assertLogs("virtual_bank.auth", level="INFO") as logs:
            self.client.get(reverse("api:user_logout"))
        self.assertIn(f"auth.logout.success user_id={self.user.id}", logs.output[0])
        self.assertNoPII(logs.output)

    def test_console_handler_redacts(self):
        handler = logging.getLogger().handlers[0]
        self.assertTrue(any(isinstance(f, PIIRedactionFilter) for f in handler.filters))
