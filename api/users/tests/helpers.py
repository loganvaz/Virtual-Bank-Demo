import itertools

from django.test import override_settings
from rest_framework.test import APITestCase

from users.models import User

_counter = itertools.count(1)

PASSWORD = "pw12345!"

in_memory_channels = override_settings(
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
)


def make_user(first_name="Test", last_name="User", **kwargs):
    n = next(_counter)
    return User.objects.create_user(
        username=f"user{n}", email=f"user{n}@example.com", password=PASSWORD,
        first_name=first_name, last_name=last_name, **kwargs,
    )


def make_admin():
    return make_user("Root", "Admin", is_superuser=True, is_staff=True)


def registration_payload(**overrides):
    n = next(_counter)
    data = {
        "username": f"newuser{n}",
        "email": f"newuser{n}@example.com",
        "password": PASSWORD,
        "first_name": "New",
        "last_name": "User",
    }
    data.update(overrides)
    return data


@in_memory_channels
class UserAPITestCase(APITestCase):
    """Base test case: Alice (authenticated), Bob and one superuser admin."""

    def setUp(self):
        self.alice = make_user("Alice", "Anders")
        self.bob = make_user("Bob", "Brown")
        self.admin = make_admin()
        self.client.force_authenticate(self.alice)
