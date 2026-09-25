"""Transaction logs must exist for every money-movement path and must contain no PII."""
import logging
import sys
from pathlib import Path

import pytest
from django.urls import reverse

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from scan_logs_for_pii import scan_text  # noqa: E402

from transactions.logging_utils import mask_account

pytestmark = pytest.mark.django_db


def test_mask_account_hides_all_but_last_four():
    assert mask_account(1234567890) == "****7890"
    assert mask_account("") == "****"


def test_scanner_detects_pii_samples():
    kinds = {k for _, k, _ in scan_text(
        "ssn 123-45-6789 mail bob@example.com card 4111 1111 1111 1111 acct 1234567890 phone 555-123-4567"
    )}
    assert kinds == {"ssn", "email", "card_number", "account_number", "phone"}


def test_scanner_ignores_masked_values_and_timestamps():
    assert scan_text("2026-09-25 12:00:00,123 INFO transactions deposit_ok user_id=7 account=****7890 amount=250") == []


def test_all_transaction_log_paths_are_pii_free(auth_api, account, other_account, caplog):
    caplog.set_level(logging.INFO, logger="transactions")
    deposit, transfer = reverse("api:deposit_creation"), reverse("api:transfer_creation")

    auth_api.post(deposit, {"account_number": account.number, "amount": 50})
    auth_api.post(deposit, {"account_number": other_account.number, "amount": 50})
    auth_api.post(deposit, {"account_number": 1, "amount": 50})
    for payer, payee, amount in [
        (account.number, other_account.number, 10),
        (account.number, other_account.number, 10_000),
        (account.number, account.number, 10),
        (other_account.number, account.number, 10),
        (1, account.number, 10),
        (account.number, 1, 10),
    ]:
        auth_api.post(transfer, {"payer_account_number": payer, "payee_account_number": payee, "amount": amount})

    events = [r.getMessage() for r in caplog.records if r.name == "transactions"]
    assert len(events) == 9
    assert {e.split()[0] for e in events} == {"deposit_ok", "deposit_rejected", "transfer_ok", "transfer_rejected"}

    text = "\n".join(events)
    assert scan_text(text) == []
    for secret in [str(account.number), str(other_account.number), "alice", "bob", "example.com", "Test User"]:
        assert secret not in text
