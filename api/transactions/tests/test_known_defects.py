"""
Defects surfaced while writing the suite. Marked xfail(strict=True) so they
document current behaviour and start failing loudly once fixed.
"""

import pytest

from conftest import TransactionFactory

pytestmark = [pytest.mark.txn, pytest.mark.authz, pytest.mark.django_db]


@pytest.mark.xfail(strict=True, reason="detail views reference `txn.transaction.date`; Transaction has no `transaction` attr")
@pytest.mark.parametrize(
    "endpoint,ttype",
    [("deposit_detail", "DEPOSIT"), ("transfer_detail", "TRANSFER"), ("debit_card_transaction_detail", "DEBIT_CARD")],
)
def test_non_party_viewing_detail_gets_403_not_500(other_client, account, url, endpoint, ttype):
    txn = TransactionFactory(account=account, transaction_type=ttype)
    resp = other_client.get(url(endpoint, identifier=txn.identifier))
    assert resp.status_code in (403, 404)


@pytest.mark.xfail(strict=True, reason="TransactionDetail compares Account objects to the User, so every owner view is flagged as a security event")
def test_owner_viewing_own_transaction_is_not_flagged(auth_client, account, url):
    from notifications.models import Notification

    txn = TransactionFactory(account=account)
    auth_client.get(url("transaction_detail", identifier=txn.identifier))
    assert not Notification.objects.filter(notification_type="SECURITY_NOTIFICATION").exists()


@pytest.mark.xfail(strict=True, reason="TransactionDetail does not enforce ownership; any authenticated user can read any transaction by UUID")
def test_transaction_detail_hidden_from_unrelated_user(other_client, account, url):
    txn = TransactionFactory(account=account)
    assert other_client.get(url("transaction_detail", identifier=txn.identifier)).status_code in (403, 404)
