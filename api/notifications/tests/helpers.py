import itertools

from django.test import override_settings
from rest_framework.test import APITestCase

from notifications.models import Notification
from users.models import User

_counter = itertools.count(1)

in_memory_channels = override_settings(
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
)


def make_user(first_name="Test", last_name="User", **kwargs):
    n = next(_counter)
    return User.objects.create_user(
        username=f"user{n}", email=f"user{n}@example.com", password="pw12345!",
        first_name=first_name, last_name=last_name, **kwargs,
    )


def make_admin(first_name="Root", last_name="Admin"):
    return make_user(first_name, last_name, is_staff=True, is_superuser=True)


def make_notification(user, notification_type="USER_NOTIFICATION", content="hello", status="UNREAD"):
    return Notification.objects.create(
        user=user, notification_type=notification_type, content=content, status=status,
    )


@in_memory_channels
class NotificationAPITestCase(APITestCase):
    """Base test case: Alice (authenticated) with one unread notification, Bob with one, and an admin user."""

    def setUp(self):
        self.alice = make_user("Alice", "Anders")
        self.bob = make_user("Bob", "Brown")
        self.admin = make_admin()
        self.alice_note = make_notification(self.alice, content="Alice private message")
        self.bob_note = make_notification(self.bob, "ACCOUNT_NOTIFICATION", content="Bob private message")
        self.client.force_authenticate(self.alice)
