"""Settings for the in-process test suite: SQLite + in-memory channel layer, no external services."""
import os

os.environ.setdefault("SECRET_KEY", "test-secret-key")

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

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

LOGGING["handlers"]["transactions_file"]["filename"] = os.path.join(  # noqa: F405
    BASE_DIR, "logs", "transactions.test.log"  # noqa: F405
)
LOGGING["loggers"]["transactions"]["propagate"] = True  # noqa: F405  (lets pytest caplog see records)
