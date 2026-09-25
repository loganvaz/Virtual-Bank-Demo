"""Authentication and object-ownership tests for the user debit-card endpoints."""

import pytest

from conftest import DebitCardFactory

pytestmark = [pytest.mark.authn, pytest.mark.authz, pytest.mark.django_db]


@pytest.mark.authn
def test_anonymous_is_rejected_on_list(api_client, url):
    assert api_client.get(url("debit_card_list")).status_code == 401


@pytest.mark.authn
def test_anonymous_is_rejected_on_detail(api_client, debit_card, url):
    resp = api_client.get(url("debit_card_detail", number=debit_card.card_number))
    assert resp.status_code == 401


@pytest.mark.authn
@pytest.mark.parametrize(
    "client_fixture",
    ["auth_client", "cookie_client", "basic_client", "session_client"],
)
def test_every_supported_auth_scheme_is_accepted(request, url, client_fixture):
    client = request.getfixturevalue(client_fixture)
    assert client.get(url("debit_card_list")).status_code == 200


@pytest.mark.authz
def test_list_shows_only_own_cards(auth_client, account, other_account, url):
    own = [DebitCardFactory(account=account), DebitCardFactory(account=account)]
    DebitCardFactory(account=other_account)
    body = auth_client.get(url("debit_card_list")).json()
    assert {card["id"] for card in body} == {card.pk for card in own}


@pytest.mark.authz
def test_owner_can_view_own_card_detail(auth_client, account, url):
    card = DebitCardFactory(account=account)
    resp = auth_client.get(url("debit_card_detail", number=card.card_number))
    assert resp.status_code == 200
    assert resp.json()["id"] == card.pk


@pytest.mark.authz
def test_other_users_card_is_not_found(auth_client, debit_card, url):
    """IDOR check: an authenticated non-owner gets 404, not the card."""
    resp = auth_client.get(url("debit_card_detail", number=debit_card.card_number))
    assert resp.status_code == 404


@pytest.mark.authz
def test_unknown_card_number_is_not_found(auth_client, account, url):
    assert auth_client.get(url("debit_card_detail", number=12345678901234)).status_code == 404
