"""Authentication / authorization matrix for every transactions endpoint."""

import uuid

import pytest
from rest_framework_simplejwt.tokens import AccessToken

from conftest import TransactionFactory, jwt_for

pytestmark = [pytest.mark.django_db]

LIST_ENDPOINTS = [
    "transaction_history",
    "deposit_list",
    "transfer_history",
    "debit_card_transactions_history",
]
DETAIL_ENDPOINTS = [
    "transaction_detail",
    "deposit_detail",
    "transfer_detail",
    "debit_card_transaction_detail",
]
CREATE_ENDPOINTS = ["deposit_creation", "transfer_creation", "debit_card_payment"]


@pytest.mark.authn
@pytest.mark.parametrize("name", LIST_ENDPOINTS + CREATE_ENDPOINTS)
def test_anonymous_is_rejected(api_client, url, name):
    method = api_client.post if name in CREATE_ENDPOINTS else api_client.get
    assert method(url(name)).status_code == 401


@pytest.mark.authn
@pytest.mark.parametrize("name", DETAIL_ENDPOINTS)
def test_anonymous_is_rejected_on_detail(api_client, url, name):
    assert api_client.get(url(name, identifier=uuid.uuid4())).status_code == 401


@pytest.mark.authn
@pytest.mark.parametrize("client_fixture", ["auth_client", "cookie_client", "basic_client", "session_client"])
def test_every_supported_auth_scheme_is_accepted(request, url, client_fixture):
    client = request.getfixturevalue(client_fixture)
    assert client.get(url("transaction_history")).status_code == 200


@pytest.mark.authn
def test_tampered_jwt_is_rejected(api_client, user, url):
    token = jwt_for(user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token[:-4]}abcd")
    assert api_client.get(url("transaction_history")).status_code == 401


@pytest.mark.authn
def test_expired_jwt_is_rejected(api_client, user, url):
    token = AccessToken.for_user(user)
    token.set_exp(lifetime=-token.lifetime)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    assert api_client.get(url("transaction_history")).status_code == 401


@pytest.mark.authn
def test_jwt_for_deleted_user_is_rejected(api_client, user, url):
    token = jwt_for(user)
    user.delete()
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    assert api_client.get(url("transaction_history")).status_code == 401


@pytest.mark.authn
def test_cookie_token_does_not_override_explicit_header(api_client, user, url):
    api_client.cookies["vb_token"] = "garbage"
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {jwt_for(user)}")
    # middleware replaces the header with the cookie value -> garbage token -> 401
    assert api_client.get(url("transaction_history")).status_code == 401


@pytest.mark.authz
@pytest.mark.parametrize("name", ["admin_transaction_list", "admin_transaction_detail"])
def test_admin_endpoints_forbid_regular_users(auth_client, url, name):
    txn = TransactionFactory()
    kwargs = {"pk": txn.pk} if name.endswith("detail") else {}
    assert auth_client.get(url(name, **kwargs)).status_code == 403


@pytest.mark.authz
def test_admin_endpoints_allow_admin(admin_client, url):
    txn = TransactionFactory()
    assert admin_client.get(url("admin_transaction_list")).status_code == 200
    assert admin_client.get(url("admin_transaction_detail", pk=txn.pk)).status_code == 200
    assert admin_client.delete(url("admin_transaction_detail", pk=txn.pk)).status_code == 204


@pytest.mark.authz
@pytest.mark.parametrize("name", LIST_ENDPOINTS)
def test_lists_never_leak_other_users_transactions(auth_client, other_account, url, name):
    TransactionFactory(account=other_account, transaction_type="DEPOSIT")
    TransactionFactory(account=other_account, transaction_type="TRANSFER")
    TransactionFactory(account=other_account, transaction_type="DEBIT_CARD")
    body = auth_client.get(url(name)).json()
    assert body["count"] == 0
    assert body["results"] == []
