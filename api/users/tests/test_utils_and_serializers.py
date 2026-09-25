from django.test import RequestFactory, SimpleTestCase, TestCase

from users.models import User
from users.serializers import UserSerializer
from users.utils import get_client_ip

from .helpers import PASSWORD, in_memory_channels, make_user, registration_payload


class GetClientIpTests(SimpleTestCase):
    """Tests that get_client_ip prefers the first X-Forwarded-For hop and falls back to REMOTE_ADDR."""

    def setUp(self):
        self.factory = RequestFactory()

    def test_forwarded_for_first_hop_wins(self):
        request = self.factory.get("/", HTTP_X_FORWARDED_FOR="203.0.113.5, 10.0.0.1", REMOTE_ADDR="10.0.0.2")
        self.assertEqual(get_client_ip(request), "203.0.113.5")

    def test_remote_addr_fallback(self):
        request = self.factory.get("/", REMOTE_ADDR="10.0.0.2")
        self.assertEqual(get_client_ip(request), "10.0.0.2")


@in_memory_channels
class UserModelTests(TestCase):
    """Tests for User.save username auto-generation and email uniqueness."""

    def test_blank_username_is_generated(self):
        user = User.objects.create(email="gen@example.com")
        self.assertTrue(user.username.startswith("users_"))

    def test_explicit_username_kept(self):
        user = make_user()
        self.assertTrue(user.username.startswith("user"))
        self.assertFalse(user.username.startswith("users_"))


@in_memory_channels
class UserSerializerTests(TestCase):
    """Tests that UserSerializer hashes passwords, hides them on output and treats ip_address as read-only."""

    def test_create_hashes_password_and_ignores_client_ip(self):
        s = UserSerializer(data={**registration_payload(), "ip_address": "1.2.3.4"})
        self.assertTrue(s.is_valid(), s.errors)
        user = s.save()
        self.assertNotEqual(user.password, PASSWORD)
        self.assertTrue(user.check_password(PASSWORD))
        self.assertIsNone(user.ip_address)

    def test_password_not_in_output(self):
        data = UserSerializer(make_user()).data
        self.assertNotIn("password", data)
        self.assertIn("username", data)

    def test_duplicate_email_and_username_rejected(self):
        existing = make_user()
        s = UserSerializer(data=registration_payload(email=existing.email, username=existing.username))
        self.assertFalse(s.is_valid())
        self.assertIn("email", s.errors)
        self.assertIn("username", s.errors)

    def test_invalid_email_rejected(self):
        s = UserSerializer(data=registration_payload(email="not-an-email"))
        self.assertFalse(s.is_valid())
        self.assertIn("email", s.errors)

    def test_update_rehashes_password(self):
        user = make_user()
        s = UserSerializer(user, data={"password": "newpass99!", "city": "Berlin"}, partial=True)
        self.assertTrue(s.is_valid(), s.errors)
        s.save()
        user.refresh_from_db()
        self.assertTrue(user.check_password("newpass99!"))
        self.assertEqual(user.city, "Berlin")

    def test_update_without_password_keeps_old_hash(self):
        user = make_user()
        s = UserSerializer(user, data={"city": "Paris"}, partial=True)
        self.assertTrue(s.is_valid(), s.errors)
        s.save()
        user.refresh_from_db()
        self.assertTrue(user.check_password(PASSWORD))
