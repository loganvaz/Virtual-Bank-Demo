import logging
from unittest.mock import patch

from django.conf import settings
from django.db import IntegrityError
from django.test import SimpleTestCase, override_settings
from rest_framework import status
from rest_framework.test import APIRequestFactory, APITestCase, force_authenticate
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken
from virtual_bank.log_redaction import REDACTED, RedactingFilter, get_redacted_logger

from .forms import UserRegisterForm
from .models import User
from .serializers import UserSerializer
from .utils import get_client_ip
from .views import (
    Login,
    Logout,
    RefreshTokenView,
    UserCreate,
    UserDetail,
    UserGet,
    UserList,
    UserUpdate,
    _user_id_from_access_token,
)

IN_MEMORY_CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
VIEWS_LOGGER = "users.views"

PASSWORD = "S3cret-pass-word"
WRONG_PASSWORD = "not-the-password"
CLIENT_IP = "203.0.113.7"
FORWARDED_IP = "198.51.100.42"
PHONE = 15551234567


def registration_payload(**overrides):
    data = {
        "username": "dave",
        "first_name": "Dave",
        "last_name": "Dawson",
        "email": "dave@example.com",
        "password": PASSWORD,
        "address": "1 Main St",
        "city": "Springfield",
        "state": "IL",
        "country": "US",
        "phone_number": PHONE,
    }
    data.update(overrides)
    return data


@override_settings(CHANNEL_LAYERS=IN_MEMORY_CHANNEL_LAYERS)
class UsersTestBase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            username="admin", email="admin@example.com", password=PASSWORD,
            first_name="Ada", last_name="Admin",
        )
        cls.alice = User.objects.create_user(
            username="alice", email="alice@example.com", password=PASSWORD,
            first_name="Alice", last_name="Anderson", phone_number=PHONE,
            address="42 Elm St", city="Metropolis", state="NY", country="US",
        )
        cls.bob = User.objects.create_user(
            username="bob", email="bob@example.com", password=PASSWORD,
            first_name="Bob", last_name="Brown",
        )

    def setUp(self):
        self.factory = APIRequestFactory()
        patcher = patch("users.views.process_notifications")
        self.notify = patcher.start()
        self.addCleanup(patcher.stop)

    def request(self, method, view_cls, user=None, data=None, fmt="json", cookies=None, **kwargs):
        request = getattr(self.factory, method)("/", data, format=fmt, REMOTE_ADDR=CLIENT_IP)
        if cookies:
            request.COOKIES.update(cookies)
        if user is not None:
            force_authenticate(request, user=user)
        return view_cls.as_view()(request, **kwargs)

    def pii_values(self):
        values = [PASSWORD, WRONG_PASSWORD, CLIENT_IP, FORWARDED_IP, str(PHONE)]
        for user in (self.admin, self.alice, self.bob):
            values += [user.first_name, user.last_name, user.email]
        payload = registration_payload()
        values += [payload["first_name"], payload["last_name"], payload["email"], payload["address"]]
        return values

    def assert_no_pii(self, output, *extra):
        text = "\n".join(output)
        for value in list(self.pii_values()) + list(extra):
            self.assertNotIn(value, text, f"PII {value!r} leaked into logs")

    def capture(self, call, level="INFO"):
        with self.assertLogs(VIEWS_LOGGER, level=level) as cm:
            result = call()
        self.assert_no_pii(cm.output)
        return result, cm.output

    def assertLogged(self, output, level, *fragments):
        matching = [line for line in output if line.startswith(f"{level}:{VIEWS_LOGGER}:")]
        self.assertTrue(
            any(all(fragment in line for fragment in fragments) for line in matching),
            f"no {level} log line containing {fragments}; got {output}",
        )


# --------------------------------------------------------------------------- models


class UserModelTests(UsersTestBase):
    def test_explicit_username_is_kept(self):
        user = User.objects.create(username="explicit", email="explicit@example.com")
        self.assertEqual(user.username, "explicit")

    def test_blank_username_is_generated_from_user_count(self):
        count = User.objects.count()
        user = User.objects.create(email="generated@example.com")
        self.assertEqual(user.username, f"users_{count + 1}")

    def test_generated_username_is_not_regenerated_on_resave(self):
        user = User.objects.create(email="stable@example.com")
        generated = user.username
        user.city = "Gotham"
        user.save()
        user.refresh_from_db()
        self.assertEqual(user.username, generated)

    def test_email_must_be_unique(self):
        with self.assertRaises(IntegrityError):
            User.objects.create(username="alice2", email=self.alice.email)

    def test_defaults_for_optional_fields(self):
        self.assertEqual(self.bob.profile_picture.name, "default.png")
        self.assertIsNone(self.bob.phone_number)
        self.assertIsNone(self.bob.ip_address)
        self.assertIsNone(self.bob.date_of_birth)

    def test_generated_username_survives_user_deletion(self):
        """BUG: User.save() derives the fallback username from User.objects.count() + 1.
        Once any user is deleted the count shrinks and the next generated username collides
        with an existing row, so creating a user without a username raises IntegrityError
        (unique username). A monotonically increasing source (e.g. max id) is needed."""
        first = User.objects.create(email="gen1@example.com")
        User.objects.create(email="gen2@example.com")
        first.delete()
        third = User.objects.create(email="gen3@example.com")
        self.assertTrue(User.objects.filter(pk=third.pk).exists())


# --------------------------------------------------------------------------- utils


class GetClientIpTests(SimpleTestCase):
    @staticmethod
    def build(**meta):
        return APIRequestFactory().get("/", **meta)

    def test_uses_first_forwarded_address(self):
        request = self.build(HTTP_X_FORWARDED_FOR=f"{FORWARDED_IP},10.0.0.1", REMOTE_ADDR=CLIENT_IP)
        self.assertEqual(get_client_ip(request), FORWARDED_IP)

    def test_single_forwarded_address(self):
        request = self.build(HTTP_X_FORWARDED_FOR=FORWARDED_IP, REMOTE_ADDR=CLIENT_IP)
        self.assertEqual(get_client_ip(request), FORWARDED_IP)

    def test_falls_back_to_remote_addr(self):
        request = self.build(REMOTE_ADDR=CLIENT_IP)
        self.assertEqual(get_client_ip(request), CLIENT_IP)

    def test_empty_forwarded_header_falls_back_to_remote_addr(self):
        request = self.build(HTTP_X_FORWARDED_FOR="", REMOTE_ADDR=CLIENT_IP)
        self.assertEqual(get_client_ip(request), CLIENT_IP)

    def test_no_address_returns_none(self):
        request = self.build()
        del request.META["REMOTE_ADDR"]
        self.assertIsNone(get_client_ip(request))


# --------------------------------------------------------------------------- forms


class UserRegisterFormTests(UsersTestBase):
    def form_data(self, **overrides):
        data = registration_payload()
        data.pop("password")
        data.update(password1=PASSWORD, password2=PASSWORD, date_of_birth="1990-05-17 00:00")
        data.update(overrides)
        return data

    def test_valid_form_creates_user_with_hashed_password(self):
        form = UserRegisterForm(data=self.form_data())
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertEqual(user.phone_number, PHONE)
        self.assertTrue(user.check_password(PASSWORD))
        self.assertNotEqual(user.password, PASSWORD)

    def test_required_fields(self):
        form = UserRegisterForm(
            data=self.form_data(first_name="", last_name="", phone_number="", email="", date_of_birth="")
        )
        self.assertFalse(form.is_valid())
        self.assertEqual(
            set(form.errors), {"first_name", "last_name", "phone_number", "email", "date_of_birth"}
        )

    def test_password_mismatch_and_non_numeric_phone(self):
        form = UserRegisterForm(data=self.form_data(password2="different", phone_number="abc"))
        self.assertFalse(form.is_valid())
        self.assertIn("password2", form.errors)
        self.assertIn("phone_number", form.errors)

    def test_duplicate_email_rejected(self):
        form = UserRegisterForm(data=self.form_data(email=self.alice.email))
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)


# --------------------------------------------------------------------------- serializer


class UserSerializerTests(UsersTestBase):
    def test_create_hashes_password_and_stores_fields(self):
        serializer = UserSerializer(data=registration_payload())
        self.assertTrue(serializer.is_valid(), serializer.errors)
        user = serializer.save()
        self.assertTrue(user.check_password(PASSWORD))
        self.assertNotEqual(user.password, PASSWORD)
        self.assertEqual(user.email, "dave@example.com")
        self.assertEqual(user.phone_number, PHONE)
        self.assertEqual(user.profile_picture.name, "default.png")

    def test_password_is_write_only(self):
        data = UserSerializer(self.alice).data
        self.assertNotIn("password", data)
        self.assertEqual(data["username"], "alice")
        self.assertEqual(data["email"], "alice@example.com")
        self.assertEqual(data["phone_number"], PHONE)
        self.assertIn("date_joined", data)

    def test_ip_address_is_read_only(self):
        serializer = UserSerializer(data=registration_payload(ip_address=CLIENT_IP))
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertNotIn("ip_address", serializer.validated_data)
        user = serializer.save()
        self.assertIsNone(user.ip_address)

    def test_ip_address_can_be_passed_to_save(self):
        serializer = UserSerializer(data=registration_payload())
        self.assertTrue(serializer.is_valid(), serializer.errors)
        user = serializer.save(ip_address=CLIENT_IP)
        self.assertEqual(user.ip_address, CLIENT_IP)

    def test_required_fields_reported(self):
        serializer = UserSerializer(data={})
        self.assertFalse(serializer.is_valid())
        self.assertEqual(set(serializer.errors), {"username", "email", "password"})

    def test_invalid_email_rejected(self):
        serializer = UserSerializer(data=registration_payload(email="not-an-email"))
        self.assertFalse(serializer.is_valid())
        self.assertIn("email", serializer.errors)

    def test_duplicate_username_and_email_rejected(self):
        serializer = UserSerializer(data=registration_payload(username="alice", email="alice@example.com"))
        self.assertFalse(serializer.is_valid())
        self.assertEqual(set(serializer.errors), {"username", "email"})

    def test_non_integer_phone_number_rejected(self):
        serializer = UserSerializer(data=registration_payload(phone_number="555-1234"))
        self.assertFalse(serializer.is_valid())
        self.assertIn("phone_number", serializer.errors)

    def test_partial_update_without_password_keeps_password(self):
        serializer = UserSerializer(self.bob, data={"city": "Gotham"}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        user = serializer.save()
        self.assertEqual(user.city, "Gotham")
        self.assertTrue(user.check_password(PASSWORD))

    def test_update_with_password_rehashes(self):
        serializer = UserSerializer(self.bob, data={"password": "new-pass-123"}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        user = serializer.save()
        self.assertTrue(user.check_password("new-pass-123"))
        self.assertFalse(user.check_password(PASSWORD))
        self.assertNotEqual(user.password, "new-pass-123")

    def test_full_update_requires_password(self):
        serializer = UserSerializer(self.bob, data={"username": "bob", "email": "bob@example.com"})
        self.assertFalse(serializer.is_valid())
        self.assertEqual(set(serializer.errors), {"password"})


# --------------------------------------------------------------------------- UserList


class UserListTests(UsersTestBase):
    def test_anonymous_is_unauthenticated(self):
        response = self.request("get", UserList)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_non_admin_is_forbidden(self):
        response = self.request("get", UserList, user=self.alice)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        response = self.request("post", UserList, user=self.alice, data=registration_payload())
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_lists_all_users_without_passwords(self):
        response = self.request("get", UserList, user=self.admin)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual({u["username"] for u in response.data}, {"admin", "alice", "bob"})
        self.assertTrue(all("password" not in u for u in response.data))

    def test_admin_creates_user_with_hashed_password_and_logs(self):
        response, output = self.capture(
            lambda: self.request("post", UserList, user=self.admin, data=registration_payload())
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username="dave")
        self.assertTrue(user.check_password(PASSWORD))
        self.assertNotIn("password", response.data)
        self.assertLogged(output, "INFO", "User created by admin", f"user_id={user.id}", f"admin_id={self.admin.id}")

    def test_admin_create_invalid_payload_returns_400_and_does_not_log(self):
        with self.assertNoLogs(VIEWS_LOGGER, level="INFO"):
            response = self.request("post", UserList, user=self.admin, data=registration_payload(email="bad"))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(username="dave").exists())

    def test_admin_create_stores_client_ip(self):
        """BUG: UserList.create() injects request.data["ip_address"], but the serializer
        declares ip_address read_only, so the value is silently dropped and the created user
        has ip_address=None. UserCreate passes it via serializer.save(ip_address=...) instead."""
        response = self.request("post", UserList, user=self.admin, data=registration_payload())
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(User.objects.get(username="dave").ip_address, CLIENT_IP)

    def test_admin_create_accepts_form_encoded_data(self):
        """BUG: for form/multipart bodies request.data is an immutable QueryDict, so the
        assignment request.data["ip_address"] = ... in UserList.create() raises
        AttributeError ("This QueryDict instance is immutable") -> HTTP 500."""
        response = self.request("post", UserList, user=self.admin, data=registration_payload(), fmt="multipart")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


# --------------------------------------------------------------------------- UserDetail


class UserDetailTests(UsersTestBase):
    def test_non_admin_is_forbidden(self):
        response = self.request("get", UserDetail, user=self.alice, pk=self.alice.pk)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_retrieves_user(self):
        response = self.request("get", UserDetail, user=self.admin, pk=self.alice.pk)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["username"], "alice")
        self.assertNotIn("password", response.data)

    def test_unknown_user_is_404(self):
        response = self.request("get", UserDetail, user=self.admin, pk=999999)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_admin_partial_update(self):
        response = self.request("patch", UserDetail, user=self.admin, data={"city": "Gotham"}, pk=self.bob.pk)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.bob.refresh_from_db()
        self.assertEqual(self.bob.city, "Gotham")
        self.assertTrue(self.bob.check_password(PASSWORD))

    def test_admin_full_update_resets_password(self):
        data = {"username": "bob", "email": "bob@example.com", "password": "brand-new-pw"}
        response = self.request("put", UserDetail, user=self.admin, data=data, pk=self.bob.pk)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.bob.refresh_from_db()
        self.assertTrue(self.bob.check_password("brand-new-pw"))

    def test_admin_deletes_user(self):
        response = self.request("delete", UserDetail, user=self.admin, pk=self.bob.pk)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(User.objects.filter(pk=self.bob.pk).exists())


# --------------------------------------------------------------------------- UserGet


class UserGetTests(UsersTestBase):
    def test_anonymous_is_unauthenticated(self):
        response = self.request("get", UserGet)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_returns_requesting_user_only(self):
        response = self.request("get", UserGet, user=self.alice)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.alice.id)
        self.assertEqual(response.data["email"], "alice@example.com")
        self.assertNotIn("password", response.data)

        response = self.request("get", UserGet, user=self.bob)
        self.assertEqual(response.data["id"], self.bob.id)


# --------------------------------------------------------------------------- UserUpdate


class UserUpdateTests(UsersTestBase):
    """UserUpdate has no queryset/get_object, so any request that reaches super().update()
    crashes (see test_authenticated_update_reaches_serializer). Tests of the post-password
    behaviour patch get_object to return the requesting user so the rest of the view is
    still exercised."""

    def setUp(self):
        super().setUp()
        self.get_object = patch.object(UserUpdate, "get_object", autospec=True)

    def patch_get_object(self, user):
        mocked = self.get_object.start()
        self.addCleanup(self.get_object.stop)
        mocked.side_effect = lambda view: user
        return mocked

    def test_anonymous_is_unauthenticated(self):
        response = self.request("patch", UserUpdate, data={"password": PASSWORD, "city": "X"})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_missing_password_is_rejected_and_logged(self):
        response, output = self.capture(
            lambda: self.request("patch", UserUpdate, user=self.alice, data={"city": "Gotham"}),
            level="WARNING",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data, {"error": "password required"})
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.city, "Metropolis")
        self.notify.assert_not_called()
        self.assertLogged(output, "WARNING", "password missing", f"user_id={self.alice.id}")

    def test_wrong_password_is_rejected_and_logged(self):
        response, output = self.capture(
            lambda: self.request(
                "patch", UserUpdate, user=self.alice, data={"password": WRONG_PASSWORD, "city": "Gotham"}
            ),
            level="WARNING",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.city, "Metropolis")
        self.notify.assert_not_called()
        self.assertLogged(output, "WARNING", "invalid password", f"user_id={self.alice.id}")

    def test_partial_update_changes_profile_notifies_and_logs(self):
        self.patch_get_object(self.alice)
        data = {"password": PASSWORD, "city": "Gotham", "address": "1007 Mountain Drive"}
        response, output = self.capture(lambda: self.request("patch", UserUpdate, user=self.alice, data=data))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.city, "Gotham")
        self.assertEqual(self.alice.address, "1007 Mountain Drive")
        self.assertTrue(self.alice.check_password(PASSWORD))
        self.assertNotIn("password", response.data)
        self.notify.assert_called_once()
        self.assertEqual(self.notify.call_args.args[:2], ("admin", "user_notification"))
        self.assertLogged(output, "INFO", "Profile updated", f"user_id={self.alice.id}", "fields=['address', 'city', 'password']")
        self.assert_no_pii(output, "1007 Mountain Drive", "Gotham")

    def test_full_update_requires_all_required_fields(self):
        self.patch_get_object(self.alice)
        with self.assertNoLogs(VIEWS_LOGGER, level="INFO"):
            response = self.request(
                "put", UserUpdate, user=self.alice, data={"password": PASSWORD, "city": "Gotham"}
            )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(set(response.data), {"username", "email"})
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.city, "Metropolis")

    def test_invalid_payload_does_not_notify(self):
        """BUG: UserUpdate.update() calls process_notifications() *before* delegating to
        super().update(), so a "profile updated" notification is sent even when serializer
        validation fails and nothing was saved."""
        self.patch_get_object(self.alice)
        response = self.request(
            "patch", UserUpdate, user=self.alice, data={"password": PASSWORD, "email": "not-an-email"}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.notify.assert_not_called()

    def test_authenticated_update_reaches_serializer(self):
        """BUG: UserUpdate declares neither `queryset` nor `get_object()` (unlike UserGet), so
        GenericAPIView.get_object() inside super().update() fails with an AssertionError for
        every request that passes the password check -> HTTP 500. No profile can be updated
        through this view."""
        response = self.request("patch", UserUpdate, user=self.alice, data={"password": PASSWORD, "city": "Gotham"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.city, "Gotham")


# --------------------------------------------------------------------------- UserCreate


class UserCreateTests(UsersTestBase):
    def test_registration_stores_ip_hashes_password_notifies_and_logs(self):
        response, output = self.capture(lambda: self.request("post", UserCreate, data=registration_payload()))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username="dave")
        self.assertEqual(user.ip_address, CLIENT_IP)
        self.assertTrue(user.check_password(PASSWORD))
        self.assertNotIn("password", response.data)
        self.assertEqual(response.data["id"], user.id)
        self.notify.assert_called_once_with("admin", "user_notification", "Dave Dawson has joined the system")
        self.assertLogged(output, "INFO", "User registered", f"user_id={user.id}")

    def test_registration_prefers_forwarded_ip(self):
        request = self.factory.post(
            "/", registration_payload(), format="json",
            REMOTE_ADDR=CLIENT_IP, HTTP_X_FORWARDED_FOR=f"{FORWARDED_IP}, 10.0.0.1",
        )
        response = UserCreate.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(User.objects.get(username="dave").ip_address, FORWARDED_IP)

    def test_client_supplied_ip_is_ignored(self):
        response = self.request("post", UserCreate, data=registration_payload(ip_address="1.1.1.1"))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(User.objects.get(username="dave").ip_address, CLIENT_IP)

    def test_registration_works_with_form_encoded_data(self):
        response = self.request("post", UserCreate, data=registration_payload(), fmt="multipart")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_duplicate_email_rejected_without_notification_or_log(self):
        with self.assertNoLogs(VIEWS_LOGGER, level="INFO"):
            response = self.request("post", UserCreate, data=registration_payload(email=self.alice.email))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)
        self.notify.assert_not_called()
        self.assertFalse(User.objects.filter(username="dave").exists())

    def test_missing_required_fields_rejected(self):
        response = self.request("post", UserCreate, data={"first_name": "Dave"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(set(response.data), {"username", "email", "password"})

    def test_registration_without_name_succeeds(self):
        """BUG: UserCreate.perform_create() reads validated_data['first_name'] and
        ['last_name'] to build the notification text, but neither field is required
        (AbstractUser declares them blank=True), so a valid payload without them raises
        KeyError after the user row has already been saved -> HTTP 500."""
        payload = registration_payload()
        payload.pop("first_name")
        payload.pop("last_name")
        response = self.request("post", UserCreate, data=payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


# --------------------------------------------------------------------------- Login


class LoginTests(UsersTestBase):
    def login(self, username="alice", password=PASSWORD):
        return self.request("post", Login, data={"username": username, "password": password})

    def test_valid_credentials_return_tokens_and_cookies_and_log(self):
        response, output = self.capture(self.login)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.cookies["vb_token"].value, response.data["access"])
        self.assertEqual(response.cookies["vb_rtoken"].value, response.data["refresh"])
        self.assertTrue(response.cookies["vb_token"]["httponly"])
        self.assertTrue(response.cookies["vb_rtoken"]["httponly"])
        self.assertEqual(AccessToken(response.data["access"])["user_id"], self.alice.id)
        self.assertLogged(output, "INFO", "Login succeeded", f"user_id={self.alice.id}")
        self.assert_no_pii(output, response.data["access"], response.data["refresh"])

    def test_wrong_password_is_401_without_cookies_and_logs(self):
        response, output = self.capture(lambda: self.login(password=WRONG_PASSWORD), level="WARNING")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertNotIn("vb_token", response.cookies)
        self.assertNotIn("vb_rtoken", response.cookies)
        self.assertLogged(output, "WARNING", "Login rejected")
        self.assertNotIn("alice", "\n".join(output))

    def test_unknown_user_is_401(self):
        response = self.login(username="nobody")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_inactive_user_is_401(self):
        User.objects.filter(pk=self.bob.pk).update(is_active=False)
        response = self.login(username="bob")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_missing_fields_is_400(self):
        response = self.request("post", Login, data={"username": "alice"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn("vb_token", response.cookies)


# --------------------------------------------------------------------------- RefreshToken


class RefreshTokenViewTests(UsersTestBase):
    def refresh(self, token):
        return self.request("post", RefreshTokenView, data={"refresh": str(token)})

    def test_valid_refresh_rotates_tokens_sets_cookies_and_logs(self):
        old = RefreshToken.for_user(self.alice)
        response, output = self.capture(lambda: self.refresh(old))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(AccessToken(response.data["access"])["user_id"], self.alice.id)
        self.assertNotEqual(response.data["refresh"], str(old))
        self.assertEqual(response.cookies["vb_token"].value, response.data["access"])
        self.assertEqual(response.cookies["vb_rtoken"].value, response.data["refresh"])
        self.assertTrue(response.cookies["vb_token"]["httponly"])
        self.assertTrue(BlacklistedToken.objects.filter(token__jti=old["jti"]).exists())
        self.assertLogged(output, "INFO", "Token refreshed", f"user_id={self.alice.id}")
        self.assert_no_pii(output, str(old), response.data["access"], response.data["refresh"])

    def test_reusing_rotated_refresh_token_is_rejected_and_logged(self):
        old = RefreshToken.for_user(self.alice)
        self.refresh(old)
        response, output = self.capture(lambda: self.refresh(old), level="WARNING")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertNotIn("vb_token", response.cookies)
        self.assertLogged(output, "WARNING", "Token refresh rejected")

    def test_garbage_token_is_401(self):
        response = self.refresh("not-a-token")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_missing_token_is_400(self):
        response = self.request("post", RefreshTokenView, data={})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# --------------------------------------------------------------------------- Logout


class LogoutTests(UsersTestBase):
    def logout(self, user, token=None):
        cookies = {"vb_rtoken": str(token)} if token is not None else None
        return self.request("get", Logout, user=user, cookies=cookies)

    def test_anonymous_is_unauthenticated(self):
        response = self.request("get", Logout)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_logout_blacklists_token_clears_cookies_and_logs(self):
        token = RefreshToken.for_user(self.alice)
        response, output = self.capture(lambda: self.logout(self.alice, token))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"details": "success"})
        self.assertTrue(BlacklistedToken.objects.filter(token__jti=token["jti"]).exists())
        self.assertEqual(response.cookies["vb_token"]["max-age"], 0)
        self.assertEqual(response.cookies["vb_rtoken"]["max-age"], 0)
        self.assertLogged(output, "INFO", "Logout succeeded", f"user_id={self.alice.id}")
        self.assert_no_pii(output, str(token))

    def test_blacklisted_token_cannot_be_refreshed(self):
        token = RefreshToken.for_user(self.alice)
        self.logout(self.alice, token)
        response = self.request("post", RefreshTokenView, data={"refresh": str(token)})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_invalid_cookie_reports_failure_and_logs(self):
        response, output = self.capture(lambda: self.logout(self.alice, "garbage"), level="WARNING")
        self.assertEqual(response.data, {"details": "failed"})
        self.assertNotIn("vb_token", response.cookies)
        self.assertFalse(BlacklistedToken.objects.exists())
        self.assertLogged(output, "WARNING", "Logout failed", f"user_id={self.alice.id}", "reason=TokenError")
        self.assert_no_pii(output, "garbage")

    def test_missing_cookie_reports_failure(self):
        """BUG: when the vb_rtoken cookie is absent, RefreshToken(None) does not raise; it
        mints a brand-new refresh token, which Logout.get() then blacklists and reports
        {"details": "success"}. Nothing belonging to the user was revoked."""
        response = self.logout(self.alice)
        self.assertEqual(response.data, {"details": "failed"})
        self.assertFalse(BlacklistedToken.objects.exists())

    def test_failed_logout_uses_error_status(self):
        """BUG: Logout.get() swallows every exception and returns {"details": "failed"} with
        the default HTTP 200, so clients cannot distinguish a successful logout from an
        invalid refresh token without parsing the body."""
        response = self.logout(self.alice, "garbage")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# --------------------------------------------------------------------------- logging plumbing


class UsersLoggingSetupTests(SimpleTestCase):
    def test_views_logger_has_redacting_filter(self):
        logger = logging.getLogger(VIEWS_LOGGER)
        self.assertTrue(any(isinstance(f, RedactingFilter) for f in logger.filters))
        self.assertIs(get_redacted_logger(VIEWS_LOGGER), logger)
        self.assertEqual(sum(isinstance(f, RedactingFilter) for f in logger.filters), 1)

    def test_views_logger_redacts_raw_pii(self):
        logger = logging.getLogger(VIEWS_LOGGER)
        with self.assertLogs(VIEWS_LOGGER, level="INFO") as cm:
            logger.info("email %s phone %s password=%s token='abc.def'", "alice@example.com", PHONE, PASSWORD)
        self.assertEqual(
            cm.output,
            [f"INFO:{VIEWS_LOGGER}:email {REDACTED} phone {REDACTED} password={REDACTED} token={REDACTED}"],
        )

    def test_user_id_from_access_token(self):
        self.assertIsNone(_user_id_from_access_token("not-a-token"))

    def test_logging_settings_route_users_logger_through_redacting_handler(self):
        config = settings.LOGGING
        self.assertIn("redact_pii", config["handlers"]["console"]["filters"])
        self.assertIn("console", config["loggers"]["users"]["handlers"])
