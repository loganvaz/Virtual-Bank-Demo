"""
Settings used by the automated test suite (pytest).

No external services are required: SQLite in-memory replaces PostgreSQL and
the in-memory channel layer replaces Redis.
"""

import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")

from .settings import *  # noqa: E402,F401,F403

DEBUG = False

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

CHANNEL_LAYERS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
}

SIMPLE_JWT = {**SIMPLE_JWT, "SIGNING_KEY": os.environ["SECRET_KEY"]}  # noqa: F405

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

LOGGING["handlers"]["file"]["filename"] = os.path.join(  # noqa: F405
    BASE_DIR, "logs", "test.log"  # noqa: F405
)
