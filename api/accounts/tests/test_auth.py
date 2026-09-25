"""Authentication / authorization matrix for every accounts endpoint."""

import pytest

pytestmark = [pytest.mark.django_db]


@pytest.mark.authn
def test_anonymous_is_rejected_on_list(api_client, url):
    assert api_client.get(url("account_list")).status_code == 401


@pytest.mark.authn
def test_anonymous_is_rejected_on_create(api_client, url):
    assert api_client.post(url("account_creation"), {"name": "Savings"}).status_code == 401


@pytest.mark.authn
def test_anonymous_is_rejected_on_detail(api_client, account, url):
    assert api_client.get(url("account_detail", number=account.number)).status_code == 401


@pytest.mark.authn
def test_anonymous_is_rejected_on_admin(api_client, account, url):
    assert api_client.get(url("admin_account_list")).status_code == 401
    assert api_client.get(url("admin_account_detail", pk=account.pk)).status_code == 401


@pytest.mark.authn
@pytest.mark.parametrize("client_fixture", ["auth_client", "cookie_client", "basic_client", "session_client"])
def test_every_supported_auth_scheme_is_accepted(request, url, client_fixture):
    client = request.getfixturevalue(client_fixture)
    assert client.get(url("account_list")).status_code == 200


@pytest.mark.authz
def test_admin_endpoints_forbid_regular_users(auth_client, account, url):
    assert auth_client.get(url("admin_account_list")).status_code == 403
    assert auth_client.get(url("admin_account_detail", pk=account.pk)).status_code == 403


@pytest.mark.authz
def test_admin_endpoints_allow_admin(admin_client, account, url):
    resp = admin_client.get(url("admin_account_list"))
    assert resp.status_code == 200
    data = resp.json()
    items = data["results"] if isinstance(data, dict) else data
    assert account.pk in {a["id"] for a in items}
    assert admin_client.get(url("admin_account_detail", pk=account.pk)).status_code == 200


@pytest.mark.authz
@pytest.mark.parametrize("method", ["get", "put", "delete"])
def test_other_users_account_detail_is_not_found(other_client, account, url, method):
    resp = getattr(other_client, method)(url("account_detail", number=account.number))
    assert resp.status_code == 404
    from accounts.models import Account
    assert Account.objects.filter(pk=account.pk).exists()


@pytest.mark.authz
def test_list_excludes_other_users_accounts(other_client, account, other_account, url):
    resp = other_client.get(url("account_list"))
    assert resp.status_code == 200
    data = resp.json()
    items = data["results"] if isinstance(data, dict) else data
    numbers = {a["number"] for a in items}
    assert str(account.number) not in numbers
    assert str(other_account.number) in numbers
