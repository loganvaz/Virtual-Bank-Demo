from django.urls import reverse

from notifications.models import Notification
from users.models import User

from .helpers import PASSWORD, UserAPITestCase, registration_payload


class RegistrationTests(UserAPITestCase):
    """Tests for the public /auth/register/ endpoint: happy path, IP capture, validation and notifications."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(None)
        self.url = reverse("api:user_registration")

    def test_register_creates_user_with_ip_and_notifies_admin(self):
        payload = registration_payload()
        r = self.client.post(self.url, payload, REMOTE_ADDR="198.51.100.7")
        self.assertEqual(r.status_code, 201, r.data)
        user = User.objects.get(username=payload["username"])
        self.assertEqual(user.ip_address, "198.51.100.7")
        self.assertTrue(user.check_password(PASSWORD))
        self.assertNotIn("password", r.data)
        self.assertEqual(r.data["ip_address"], "198.51.100.7")
        self.assertTrue(Notification.objects.filter(user=self.admin, content__contains="has joined").exists())

    def test_register_uses_forwarded_for_header(self):
        r = self.client.post(self.url, registration_payload(), HTTP_X_FORWARDED_FOR="203.0.113.9, 10.0.0.1")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["ip_address"], "203.0.113.9")

    def test_register_without_names_does_not_crash(self):
        payload = registration_payload()
        del payload["first_name"], payload["last_name"]
        r = self.client.post(self.url, payload)
        self.assertEqual(r.status_code, 201, r.data)

    def test_register_duplicate_email_400(self):
        r = self.client.post(self.url, registration_payload(email=self.alice.email))
        self.assertEqual(r.status_code, 400)
        self.assertIn("email", r.data)

    def test_register_missing_password_400(self):
        payload = registration_payload()
        del payload["password"]
        r = self.client.post(self.url, payload)
        self.assertEqual(r.status_code, 400)
        self.assertIn("password", r.data)

    def test_register_ignores_client_supplied_ip(self):
        r = self.client.post(self.url, {**registration_payload(), "ip_address": "1.1.1.1"}, REMOTE_ADDR="198.51.100.8")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["ip_address"], "198.51.100.8")


class AdminUserListCreateTests(UserAPITestCase):
    """Tests for the admin-only /admin/users/ list + create endpoint."""

    def setUp(self):
        super().setUp()
        self.url = reverse("api:admin_user_list")
        self.client.force_authenticate(self.admin)

    def test_admin_can_list(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data), User.objects.count())

    def test_admin_create_form_encoded_captures_ip(self):
        payload = registration_payload()
        r = self.client.post(self.url, payload, REMOTE_ADDR="198.51.100.20")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(User.objects.get(username=payload["username"]).ip_address, "198.51.100.20")

    def test_admin_create_json(self):
        r = self.client.post(self.url, registration_payload(), format="json", REMOTE_ADDR="198.51.100.21")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["ip_address"], "198.51.100.21")

    def test_admin_create_invalid_400(self):
        r = self.client.post(self.url, registration_payload(email="bad"))
        self.assertEqual(r.status_code, 400)


class UserUpdateTests(UserAPITestCase):
    """Tests for /auth/update/: password confirmation, field updates and notification side effects."""

    def setUp(self):
        super().setUp()
        self.url = reverse("api:user_update")

    def test_patch_with_correct_password_updates_profile(self):
        r = self.client.patch(self.url, {"password": PASSWORD, "city": "Oslo"})
        self.assertEqual(r.status_code, 200, r.data)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.city, "Oslo")
        self.assertTrue(self.alice.check_password(PASSWORD))
        self.assertTrue(Notification.objects.filter(user=self.admin, content__contains="successfully updated").exists())

    def test_put_with_correct_password(self):
        r = self.client.put(self.url, {"password": PASSWORD, "username": self.alice.username, "email": self.alice.email, "first_name": "Alicia"})
        self.assertEqual(r.status_code, 200, r.data)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.first_name, "Alicia")

    def test_missing_password_400(self):
        r = self.client.patch(self.url, {"city": "Oslo"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data, {"error": "password required"})

    def test_wrong_password_401_and_no_notification(self):
        r = self.client.patch(self.url, {"password": "wrong", "city": "Oslo"})
        self.assertEqual(r.status_code, 401)
        self.alice.refresh_from_db()
        self.assertIsNone(self.alice.city)
        self.assertFalse(Notification.objects.exists())

    def test_invalid_field_400_and_no_notification(self):
        r = self.client.patch(self.url, {"password": PASSWORD, "email": self.bob.email})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Notification.objects.exists())

    def test_cannot_update_other_user(self):
        r = self.client.patch(self.url, {"password": PASSWORD, "id": self.bob.pk, "city": "Oslo"})
        self.assertEqual(r.status_code, 200)
        self.bob.refresh_from_db()
        self.assertIsNone(self.bob.city)
