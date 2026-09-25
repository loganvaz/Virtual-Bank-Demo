import json
import logging
import re

from django.test import SimpleTestCase
from django.urls import reverse

from virtual_bank.logging import JsonFormatter, PIIRedactingFilter, redact

from .helpers import TransactionAPITestCase, card_expiry, make_card

PII_PATTERNS = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "card": re.compile(r"\b\d{16}\b"),
    "account": re.compile(r"\b\d{10}\b"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.\w+"),
}


class RedactTests(SimpleTestCase):
    """Positive and negative tests for each PII pattern handled by redact()."""

    def test_ssn(self):
        self.assertEqual(redact("ssn 123-45-6789 here"), "ssn [SSN-REDACTED] here")

    def test_luhn_valid_card_redacted(self):
        self.assertEqual(redact("card 4111111111111111"), "card [CARD-REDACTED]")
        self.assertEqual(redact("card 4111 1111 1111 1111"), "card [CARD-REDACTED]")

    def test_luhn_invalid_16_digits_not_treated_as_card(self):
        self.assertNotIn("CARD", redact("ref 4111111111111112"))

    def test_account_number(self):
        self.assertEqual(redact("acct 1000000042"), "acct [ACCT-REDACTED]")

    def test_cvv_and_expiry(self):
        self.assertEqual(redact("cvv=123 exp 12/29"), "cvv=[REDACTED] exp [EXP-REDACTED]")

    def test_email_phone_ip(self):
        self.assertEqual(redact("a.b+c@example.com"), "[EMAIL-REDACTED]")
        self.assertEqual(redact("call +1 (555) 123-4567"), "call [PHONE-REDACTED]")
        self.assertEqual(redact("from 10.0.0.1"), "from [IP-REDACTED]")

    def test_bearer_token(self):
        self.assertEqual(redact("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc"), "Authorization: Bearer [REDACTED]")

    def test_safe_values_untouched(self):
        for value in ("7f3c6f3e-0a3a-4b5e-9c1d-2b6f1a8e9d10", "amount 12345.67", "user_id 42", "rate 0.850000"):
            self.assertEqual(redact(value), value)


class FilterAndFormatterTests(SimpleTestCase):
    """Tests that the handler-level filter redacts msg, args and extra fields and that JSON output is well-formed."""

    def record(self, msg, args=(), **extra):
        rec = logging.LogRecord("x", logging.INFO, __file__, 1, msg, args, None)
        for k, v in extra.items():
            setattr(rec, k, v)
        return rec

    def test_filter_redacts_message_args_and_extra(self):
        rec = self.record("user %s paid", ("bob@example.com",), reason="acct 1000000001", event="x")
        PIIRedactingFilter().filter(rec)
        self.assertEqual(rec.getMessage(), "user [EMAIL-REDACTED] paid")
        self.assertEqual(rec.reason, "acct [ACCT-REDACTED]")

    def test_json_formatter_includes_structured_fields(self):
        rec = self.record("hi", event="deposit.created", user_id=7, txn_id="abc")
        payload = json.loads(JsonFormatter().format(rec))
        self.assertEqual(payload["event"], "deposit.created")
        self.assertEqual(payload["user_id"], 7)
        self.assertEqual(payload["logger"], "x")
        self.assertNotIn("password", payload)


class ViewLoggingTests(TransactionAPITestCase):
    """End-to-end: view code paths emit structured events and never include PII."""

    def assert_no_pii(self, cm):
        formatter = JsonFormatter()
        for rec in cm.records:
            PIIRedactingFilter().filter(rec)
            line = formatter.format(rec)
            for name, pattern in PII_PATTERNS.items():
                self.assertIsNone(pattern.search(line), f"{name} leaked in {line}")
            for forbidden in ("Alice", "Bob", str(self.alice_acct.number), str(self.bob_acct.number)):
                self.assertNotIn(forbidden, line)

    def test_deposit_created_event(self):
        with self.assertLogs("transactions", level="INFO") as cm:
            r = self.client.post(reverse("api:deposit_creation"), {"account_number": self.alice_acct.number, "amount": "5"})
        self.assertEqual(r.status_code, 201)
        rec = cm.records[-1]
        self.assertEqual(rec.event, "deposit.created")
        self.assertEqual((rec.user_id, rec.account_id, rec.amount), (self.alice.pk, self.alice_acct.pk, "5.00"))
        self.assert_no_pii(cm)

    def test_not_owner_goes_to_security_logger(self):
        with self.assertLogs("security", level="WARNING") as cm:
            self.client.post(reverse("api:deposit_creation"), {"account_number": self.bob_acct.number, "amount": "5"})
        self.assertEqual(cm.records[-1].reason, "not_owner")
        self.assert_no_pii(cm)

    def test_transfer_rejections_log_reason(self):
        url = reverse("api:transfer_creation")
        with self.assertLogs("transactions", level="WARNING") as cm:
            self.client.post(url, {"payer_account_number": self.alice_acct.number, "payee_account_number": self.bob_acct.number, "amount": "500"})
            self.client.post(url, {"payer_account_number": self.alice_acct.number, "payee_account_number": self.alice_acct.number, "amount": "5"})
        self.assertEqual([r.reason for r in cm.records], ["insufficient_funds", "same_account"])
        self.assert_no_pii(cm)

    def test_invalid_card_attempts_are_security_events_without_card_data(self):
        card = make_card(self.bob_acct)
        with self.assertLogs("security", level="WARNING") as cm:
            self.client.post(reverse("api:debit_card_payment"), {
                "payee_account_number": self.alice_acct.number, "card_number": str(card.card_number),
                "cvv": "000", "expiration_date": card_expiry(card), "amount": "1",
            })
        self.assertEqual(cm.records[-1].reason, "card_not_found")
        self.assertNotIn(str(card.card_number), " ".join(JsonFormatter().format(r) for r in cm.records))
        self.assert_no_pii(cm)

    def test_idor_attempt_logged(self):
        from .test_read_views_and_auth import make_txn
        txn = make_txn(self.bob_acct, self.bob_acct, "DEPOSIT")
        with self.assertLogs("security", level="WARNING") as cm:
            r = self.client.get(reverse("api:deposit_detail", kwargs={"identifier": txn.identifier}))
        self.assertEqual(r.status_code, 403)
        self.assertEqual((cm.records[-1].reason, cm.records[-1].txn_id), ("not_participant", str(txn.identifier)))
