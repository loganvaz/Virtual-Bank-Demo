"""
Defects surfaced while writing the suite. Marked xfail(strict=True) so they
document current behaviour and start failing loudly once fixed.
"""

import pytest

from conftest import DebitCardFactory

pytestmark = [pytest.mark.authz, pytest.mark.validation, pytest.mark.django_db]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "admin create passes a nested `account` dict to DebitCard.objects.create "
        "and reads the read-only SerializerMethodField `expiration_date` from "
        "validated_data -> KeyError/500 instead of 201/400"
    ),
)
def test_admin_create_debit_card_returns_201_or_400_not_500(admin_client, account, url):
    admin_client.raise_request_exception = False
    resp = admin_client.post(
        url("admin_debit_card_list"),
        {
            "account": {
                "name": "Main",
                "account_type": "CURRENT",
                "balance": "100.00",
                "currency": "USD",
            },
            "expiration_date": "12/30",
        },
        format="json",
    )
    assert resp.status_code in (201, 400)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "admin update hits DRF's writable-nested-field assertion: "
        "`.update() does not support writable nested fields` -> 500 instead of 200/400"
    ),
)
def test_admin_update_with_nested_account_returns_200_or_400_not_500(admin_client, debit_card, url):
    admin_client.raise_request_exception = False
    resp = admin_client.patch(
        url("admin_debit_card_detail", pk=debit_card.pk),
        {"account": {"name": "x"}},
        format="json",
    )
    assert resp.status_code in (200, 400)
