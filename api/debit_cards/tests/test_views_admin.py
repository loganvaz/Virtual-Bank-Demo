"""Authorization tests for the admin debit-card endpoints."""

import pytest

from debit_cards.models import DebitCard

from conftest import DebitCardFactory

pytestmark = [pytest.mark.authz, pytest.mark.django_db]


def test_admin_list_rejects_anonymous(api_client, url):
    assert api_client.get(url("admin_debit_card_list")).status_code == 401


def test_admin_list_forbids_regular_users(auth_client, url):
    assert auth_client.get(url("admin_debit_card_list")).status_code == 403


def test_admin_list_returns_all_cards(admin_client, account, other_account, url):
    cards = [
        DebitCardFactory(account=account),
        DebitCardFactory(account=other_account),
    ]
    body = admin_client.get(url("admin_debit_card_list")).json()
    assert {card["id"] for card in body} == {card.pk for card in cards}


def test_admin_detail_forbids_regular_users(auth_client, debit_card, url):
    resp = auth_client.get(url("admin_debit_card_detail", pk=debit_card.pk))
    assert resp.status_code == 403


def test_admin_detail_returns_card(admin_client, debit_card, url):
    resp = admin_client.get(url("admin_debit_card_detail", pk=debit_card.pk))
    assert resp.status_code == 200
    assert resp.json()["id"] == debit_card.pk


def test_admin_detail_unknown_pk_is_not_found(admin_client, url):
    assert admin_client.get(url("admin_debit_card_detail", pk=999999)).status_code == 404


def test_admin_delete_removes_card(admin_client, debit_card, url):
    resp = admin_client.delete(url("admin_debit_card_detail", pk=debit_card.pk))
    assert resp.status_code == 204
    assert not DebitCard.objects.filter(pk=debit_card.pk).exists()
