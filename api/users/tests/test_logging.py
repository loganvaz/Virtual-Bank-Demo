"""Audit events emitted by the users app contain surrogate IDs only — never usernames, emails, phones or IPs."""

import logging
import re

import pytest

from conftest import PASSWORD

pytestmark = [pytest.mark.audit, pytest.mark.pii, pytest.mark.django_db]

EMAIL_RX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
IP_RX = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def _messages(caplog, needle):
    return [r.getMessage() for r in caplog.records if needle in r.getMessage()]


def _assert_no_pii(message, user):
    assert user.username not in message
    assert user.email not in message
    assert user.first_name not in message
    assert str(user.phone_number) not in message
    assert not EMAIL_RX.search(message)
    assert not IP_RX.search(message)


def test_register_logs_user_id_only(api_client, url, caplog):
    payload = {
        "username": "logme",
        "first_name": "Log",
        "last_name": "Me",
        "email": "logme@example.com",
        "password": PASSWORD,
        "phone_number": 5550003333,
    }
    with caplog.at_level(logging.INFO, logger="users"):
        resp = api_client.post(url("user_registration"), payload, REMOTE_ADDR="198.51.100.9")
    assert resp.status_code == 201
    (message,) = _messages(caplog, "user.registered")
    assert f"user_id={resp.data['id']}" in message
    assert "logme" not in message and "198.51.100.9" not in message
    assert not EMAIL_RX.search(message)


def test_update_rejections_go_to_users_and_security_loggers(auth_client, user, url, caplog):
    with caplog.at_level(logging.WARNING):
        auth_client.patch(url("user_update"), {"city": "X"})
        auth_client.patch(url("user_update"), {"password": "wrong", "city": "X"})
    (missing,) = [r for r in caplog.records if "reason='password_required'" in r.getMessage()]
    (invalid,) = [r for r in caplog.records if "reason='invalid_password'" in r.getMessage()]
    assert missing.name == "users.views"
    assert invalid.name == "security"
    for record in (missing, invalid):
        assert f"actor_id={user.pk}" in record.getMessage()
        _assert_no_pii(record.getMessage(), user)
    assert "wrong" not in invalid.getMessage()


def test_update_success_logs_field_names_not_values(auth_client, user, url, caplog):
    with caplog.at_level(logging.INFO, logger="users"):
        auth_client.patch(url("user_update"), {"password": PASSWORD, "city": "Secret City", "email": "new@example.com"})
    (message,) = _messages(caplog, "user.updated")
    assert f"actor_id={user.pk}" in message
    assert "city" in message and "email" in message
    assert "Secret City" not in message and "new@example.com" not in message and PASSWORD not in message


def test_login_outcomes_logged_to_security_without_credentials(api_client, user, url, caplog):
    with caplog.at_level(logging.INFO, logger="security"):
        api_client.post(url("user_login"), {"username": user.username, "password": "bad"})
        api_client.post(url("user_login"), {"username": user.username, "password": PASSWORD})
    (failed,) = _messages(caplog, "auth.login.failed")
    (ok,) = _messages(caplog, "auth.login.succeeded")
    assert "reason='invalid_credentials'" in failed
    assert f"user_id={user.pk}" in ok
    for message in (failed, ok):
        _assert_no_pii(message, user)
        assert PASSWORD not in message and "bad" not in message


def test_refresh_and_logout_events(api_client, auth_client, user, url, caplog):
    from rest_framework_simplejwt.tokens import RefreshToken

    refresh = RefreshToken.for_user(user)
    with caplog.at_level(logging.INFO, logger="security"):
        api_client.post(url("token_refresh"), {"refresh": "junk"})
        api_client.post(url("token_refresh"), {"refresh": str(refresh)})
        auth_client.cookies["vb_rtoken"] = "junk"
        auth_client.get(url("user_logout"))
        auth_client.cookies["vb_rtoken"] = str(RefreshToken.for_user(user))
        auth_client.get(url("user_logout"))
    assert _messages(caplog, "auth.refresh.failed reason='invalid_token'")
    assert _messages(caplog, "auth.refresh.succeeded")
    (failed,) = _messages(caplog, "auth.logout.failed")
    (ok,) = _messages(caplog, "auth.logout.succeeded")
    assert f"user_id={user.pk}" in failed and "reason=TokenError" in failed
    assert f"user_id={user.pk}" in ok
    for record in caplog.records:
        assert str(refresh) not in record.getMessage()
        _assert_no_pii(record.getMessage(), user)


def test_admin_create_logs_actor_and_new_id(admin_client, admin_user, url, caplog):
    payload = {"username": "viaadmin", "email": "viaadmin@example.com", "password": PASSWORD}
    with caplog.at_level(logging.INFO, logger="users"):
        resp = admin_client.post(url("admin_user_list"), payload, format="json")
    (message,) = _messages(caplog, "admin.user.created")
    assert f"actor_id={admin_user.pk}" in message and f"user_id={resp.data['id']}" in message
    assert "viaadmin" not in message
