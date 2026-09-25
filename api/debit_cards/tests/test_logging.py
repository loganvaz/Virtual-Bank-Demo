import re

from django.urls import reverse

from virtual_bank.logging import JsonFormatter, PIIRedactingFilter, build_logging_config

from .helpers import DebitCardAPITestCase, future, make_admin

PII_PATTERNS = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "card": re.compile(r"\b\d{14,16}\b"),
    "account": re.compile(r"\b\d{10}\b"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.\w+"),
    "expiry": re.compile(r"\b(0[1-9]|1[0-2])/\d{2}\b"),
}


class LoggingConfigTests(DebitCardAPITestCase):
    """Tests that the shared logging config routes the debit_cards logger through the redacting JSON handler."""

    def test_debit_cards_logger_configured(self):
        cfg = build_logging_config()
        self.assertEqual(cfg["loggers"]["debit_cards"]["handlers"], ["console"])
        self.assertIn("redact_pii", cfg["handlers"]["console"]["filters"])


class ViewLoggingTests(DebitCardAPITestCase):
    """End-to-end: debit_cards views emit structured events carrying only IDs and never card data or PII."""

    def assert_no_pii(self, cm):
        formatter = JsonFormatter()
        for rec in cm.records:
            PIIRedactingFilter().filter(rec)
            line = formatter.format(rec)
            for name, pattern in PII_PATTERNS.items():
                self.assertIsNone(pattern.search(line), f"{name} leaked in {line}")
            for forbidden in ("Alice", "Bob", str(self.alice_acct.number), str(self.bob_acct.number),
                              str(self.alice_card.card_number), str(self.bob_card.card_number), "123"):
                self.assertNotIn(forbidden, line)

    def test_card_created_event(self):
        self.client.force_authenticate(make_admin())
        with self.assertLogs("debit_cards", level="INFO") as cm:
            r = self.client.post(reverse("api:admin_debit_card_list"),
                                 {"account_id": self.bob_acct.pk, "expires_at": future().isoformat()})
        self.assertEqual(r.status_code, 201)
        rec = cm.records[-1]
        self.assertEqual(rec.event, "card.created")
        self.assertEqual((rec.card_id, rec.account_id, rec.user_id), (r.data["id"], self.bob_acct.pk, self.bob.pk))
        self.assert_no_pii(cm)

    def test_card_updated_and_deleted_events(self):
        self.client.force_authenticate(make_admin())
        url = reverse("api:admin_debit_card_detail", kwargs={"pk": self.alice_card.pk})
        with self.assertLogs("debit_cards", level="INFO") as cm:
            self.client.patch(url, {"account_id": self.bob_acct.pk})
            self.client.delete(url)
        self.assertEqual([r.event for r in cm.records], ["card.updated", "card.deleted"])
        self.assertEqual([r.card_id for r in cm.records], [self.alice_card.pk] * 2)
        self.assertEqual(cm.records[-1].account_id, self.bob_acct.pk)
        self.assert_no_pii(cm)

    def test_probing_another_users_card_is_a_security_event_without_card_number(self):
        with self.assertLogs("security", level="WARNING") as cm:
            r = self.client.get(reverse("api:debit_card_detail", kwargs={"number": self.bob_card.card_number}))
        self.assertEqual(r.status_code, 404)
        rec = cm.records[-1]
        self.assertEqual((rec.event, rec.reason, rec.user_id), ("card_lookup.rejected", "card_not_found", self.alice.pk))
        self.assert_no_pii(cm)

    def test_successful_reads_are_silent(self):
        with self.assertNoLogs("debit_cards"), self.assertNoLogs("security"):
            self.assertEqual(self.client.get(reverse("api:debit_card_list")).status_code, 200)
            self.assertEqual(self.client.get(
                reverse("api:debit_card_detail", kwargs={"number": self.alice_card.card_number})).status_code, 200)
