import re

from django.test import SimpleTestCase
from django.urls import reverse

from virtual_bank.logging import JsonFormatter, PIIRedactingFilter, build_logging_config

from .helpers import AccountAPITestCase, make_account

PII_PATTERNS = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "card": re.compile(r"\b\d{16}\b"),
    "account": re.compile(r"\b\d{10}\b"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.\w+"),
}


class LoggingConfigTests(SimpleTestCase):
    """Tests that the shared logging config routes the accounts logger through the redacting JSON handler."""

    def test_accounts_logger_configured(self):
        cfg = build_logging_config(level="DEBUG")
        self.assertEqual(cfg["loggers"]["accounts"], {"handlers": ["console"], "level": "DEBUG", "propagate": False})
        self.assertIn("redact_pii", cfg["handlers"]["console"]["filters"])


class AccountViewLoggingTests(AccountAPITestCase):
    """End-to-end: account views emit structured events with IDs only and never include PII."""

    def assert_no_pii(self, cm):
        formatter = JsonFormatter()
        for rec in cm.records:
            PIIRedactingFilter().filter(rec)
            line = formatter.format(rec)
            for name, pattern in PII_PATTERNS.items():
                self.assertIsNone(pattern.search(line), f"{name} leaked in {line}")
            for forbidden in ("Alice", "Bob", "Savings", str(self.alice_acct.number), str(self.bob_acct.number)):
                self.assertNotIn(forbidden, line)

    def test_savings_create_logs_created_event(self):
        with self.assertLogs("accounts", level="INFO") as cm:
            r = self.client.post(reverse("api:account_creation"), {"name": "Alice Holiday", "currency": "EUR"})
        self.assertEqual(r.status_code, 201)
        events = [rec.event for rec in cm.records]
        self.assertEqual(events, ["account.created"])
        rec = cm.records[0]
        self.assertEqual((rec.user_id, rec.account_id, rec.currency), (self.alice.pk, r.data["id"], "EUR"))
        self.assert_no_pii(cm)

    def test_current_create_logs_card_issued_without_card_data(self):
        with self.assertLogs("accounts", level="INFO") as cm:
            r = self.client.post(reverse("api:account_creation"), {"name": "Alice Current", "account_type": "CURRENT"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual([rec.event for rec in cm.records], ["account.created", "account.debit_card_issued"])
        self.assertEqual(cm.records[1].account_id, r.data["id"])
        self.assert_no_pii(cm)

    def test_duplicate_name_rejection_logged(self):
        with self.assertLogs("accounts", level="WARNING") as cm:
            r = self.client.post(reverse("api:account_creation"), {"name": "Alice Savings"})
        self.assertEqual(r.status_code, 403)
        rec = cm.records[-1]
        self.assertEqual((rec.event, rec.reason, rec.user_id), ("account.create.rejected", "duplicate_name", self.alice.pk))
        self.assert_no_pii(cm)

    def test_foreign_account_access_is_security_event(self):
        with self.assertLogs("security", level="WARNING") as cm:
            r = self.client.get(reverse("api:account_detail", kwargs={"number": self.bob_acct.number}))
        self.assertEqual(r.status_code, 404)
        rec = cm.records[-1]
        self.assertEqual((rec.event, rec.reason, rec.user_id), ("account.access.rejected", "not_found_or_not_owner", self.alice.pk))
        self.assertFalse(hasattr(rec, "account_id"))
        self.assert_no_pii(cm)

    def test_update_and_delete_events(self):
        url = reverse("api:account_detail", kwargs={"number": self.alice_acct.number})
        with self.assertLogs("accounts", level="INFO") as cm:
            self.assertEqual(self.client.patch(url, {"name": "Renamed"}).status_code, 200)
            self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertEqual([r.event for r in cm.records], ["account.updated", "account.deleted"])
        self.assertTrue(all(r.account_id == self.alice_acct.pk for r in cm.records))
        self.assert_no_pii(cm)

    def test_rename_collision_logged_with_account_id(self):
        make_account(self.alice, name="Second")
        url = reverse("api:account_detail", kwargs={"number": self.alice_acct.number})
        with self.assertLogs("accounts", level="WARNING") as cm:
            self.client.patch(url, {"name": "Second"})
        rec = cm.records[-1]
        self.assertEqual((rec.event, rec.reason, rec.account_id), ("account.update.rejected", "duplicate_name", self.alice_acct.pk))
        self.assert_no_pii(cm)
