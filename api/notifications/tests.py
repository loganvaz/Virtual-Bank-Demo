import re

from django.test import SimpleTestCase, override_settings
from rest_framework import status
from rest_framework.test import APIRequestFactory, APITestCase, force_authenticate

from users.models import User
from virtual_bank.log_redaction import REDACTED, get_redacted_logger, mask_number, redact

from .models import Notification
from .paginations import NotifiCationPagination
from .serializers import NotificationSerializer
from .utils import (
    process_notifications,
    send_account_notification,
    send_security_notification,
    send_transaction_notification,
    send_user_notification,
)
from .views import (
    NotificationDetail,
    NotificationList,
    UserNotificationDetail,
    UserNotificationList,
)

IN_MEMORY_CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
VIEWS_LOGGER = "notifications.views"
UTILS_LOGGER = "notifications.utils"

VALID_TYPES = [
    "user_notification",
    "account_notification",
    "transaction_notification",
    "security_notification",
]
ACCOUNT_NUMBER = "100000000001"
CARD_NUMBER = "4111111111111111"
CARD_CVV = "987"
CARD_EXPIRY = "06/30"
# Notification content deliberately carries PII so that any log line echoing
# `content` is caught by assert_no_pii.
PII_CONTENT = (
    f"Alice Anderson <alice@example.com> received 100.00 USD on account {ACCOUNT_NUMBER} "
    f"with card {CARD_NUMBER} cvv={CARD_CVV} expiry={CARD_EXPIRY}"
)


@override_settings(CHANNEL_LAYERS=IN_MEMORY_CHANNEL_LAYERS)
class NotificationsTestBase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(
            username="alice", email="alice@example.com", password="pw",
            first_name="Alice", last_name="Anderson",
        )
        cls.bob = User.objects.create_user(
            username="bob", email="bob@example.com", password="pw",
            first_name="Bob", last_name="Brown",
        )
        cls.admin = User.objects.create_superuser(
            username="root", email="root@example.com", password="pw",
            first_name="Root", last_name="Rooter",
        )
        cls.staff = User.objects.create_user(
            username="staff", email="staff@example.com", password="pw",
            first_name="Stan", last_name="Staffer", is_staff=True,
        )

    def setUp(self):
        self.factory = APIRequestFactory()

    @staticmethod
    def make_notification(user, notification_type="USER_NOTIFICATION", content=PII_CONTENT, **kwargs):
        return Notification.objects.create(
            user=user, notification_type=notification_type, content=content, **kwargs
        )

    def request(self, method, view_cls, user, data=None, params=None, **kwargs):
        if method == "get":
            request = self.factory.get("/", params or {})
        else:
            request = getattr(self.factory, method)("/", data, format="json")
        if user is not None:
            force_authenticate(request, user=user)
        return view_cls.as_view()(request, **kwargs)

    def get(self, view_cls, user, params=None, **kwargs):
        return self.request("get", view_cls, user, params=params, **kwargs)

    def result_ids(self, response):
        return [row["id"] for row in response.data["results"]]

    def pii_values(self):
        values = [ACCOUNT_NUMBER, CARD_NUMBER, PII_CONTENT]
        for user in (self.alice, self.bob, self.admin, self.staff):
            values += [user.first_name, user.last_name, user.email]
        return values

    def assert_no_pii(self, output):
        text = "\n".join(output)
        for value in self.pii_values():
            self.assertNotIn(value, text)
        self.assertIsNone(re.search(rf"\b{CARD_CVV}\b", text), "CVV leaked into logs")
        self.assertNotIn(CARD_EXPIRY, text)

    def assertLogged(self, output, level, *fragments):
        for line in output:
            if line.startswith(f"{level}:") and all(f in line for f in fragments):
                return line
        self.fail(f"No {level} log line containing {fragments!r} in {output!r}")


class NotificationModelTests(NotificationsTestBase):
    def test_defaults_and_str(self):
        notification = self.make_notification(self.alice, "TRANSACTION_NOTIFICATION")
        self.assertEqual(notification.status, "UNREAD")
        self.assertIsNotNone(notification.created_date)
        self.assertEqual(
            str(notification),
            f"Notification ID: {notification.pk} - Type: Transaction Alert - User: alice",
        )

    def test_type_display_labels(self):
        expected = {
            "USER_NOTIFICATION": "User Notification",
            "TRANSACTION_NOTIFICATION": "Transaction Alert",
            "ACCOUNT_NOTIFICATION": "Account Alert",
            "SECURITY_NOTIFICATION": "Security Notification",
        }
        for value, label in expected.items():
            with self.subTest(value=value):
                notification = self.make_notification(self.alice, value)
                self.assertEqual(notification.get_notification_type_display(), label)

    def test_deleting_user_cascades(self):
        user = User.objects.create_user(username="temp", email="temp@example.com", password="pw")
        self.make_notification(user)
        user.delete()
        self.assertFalse(Notification.objects.filter(user__username="temp").exists())


class NotificationSerializerTests(NotificationsTestBase):
    def test_output_shape_and_nested_user_without_password(self):
        notification = self.make_notification(self.alice, "ACCOUNT_NOTIFICATION", content="hi")
        data = NotificationSerializer(notification).data
        self.assertEqual(
            set(data), {"id", "user", "notification_type", "content", "status", "created_date"}
        )
        self.assertEqual(data["notification_type"], "ACCOUNT_NOTIFICATION")
        self.assertEqual(data["content"], "hi")
        self.assertEqual(data["status"], "UNREAD")
        self.assertEqual(data["user"]["id"], self.alice.id)
        self.assertEqual(data["user"]["username"], "alice")
        self.assertNotIn("password", data["user"])

    def test_read_only_fields_are_ignored_on_input(self):
        notification = self.make_notification(self.alice, "ACCOUNT_NOTIFICATION", content="hi")
        serializer = NotificationSerializer(
            notification,
            data={
                "status": "READ",
                "content": "changed",
                "notification_type": "SECURITY_NOTIFICATION",
                "user": {"id": self.bob.id},
                "created_date": "2000-01-01T00:00:00Z",
                "id": 999,
            },
            partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data, {"status": "READ"})
        saved = serializer.save()
        saved.refresh_from_db()
        self.assertEqual(saved.pk, notification.pk)
        self.assertEqual(saved.status, "READ")
        self.assertEqual(saved.content, "hi")
        self.assertEqual(saved.notification_type, "ACCOUNT_NOTIFICATION")
        self.assertEqual(saved.user, self.alice)

    def test_status_choice_validation(self):
        for value in ("READ", "UNREAD"):
            with self.subTest(value=value):
                serializer = NotificationSerializer(data={"status": value})
                self.assertTrue(serializer.is_valid(), serializer.errors)
        for value in ("read", "ARCHIVED", "", None):
            with self.subTest(value=value):
                serializer = NotificationSerializer(data={"status": value})
                self.assertFalse(serializer.is_valid())
                self.assertIn("status", serializer.errors)

    def test_status_is_optional(self):
        serializer = NotificationSerializer(data={})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data, {})


class NotifiCationPaginationTests(SimpleTestCase):
    def test_configuration(self):
        self.assertEqual(NotifiCationPagination.page_size, 100)
        self.assertEqual(NotifiCationPagination.page_size_query_param, "size")
        self.assertEqual(NotifiCationPagination.page_query_param, "page")
        self.assertEqual(NotifiCationPagination.max_page_size, 1000)

    def test_page_size_from_query_param_is_capped(self):
        paginator = NotifiCationPagination()
        factory = APIRequestFactory()
        cases = [({}, 100), ({"size": "5"}, 5), ({"size": "5000"}, 1000), ({"size": "abc"}, 100), ({"size": "0"}, 100)]
        for params, expected in cases:
            with self.subTest(params=params):
                request = factory.get("/", params)
                request.query_params = request.GET
                self.assertEqual(paginator.get_page_size(request), expected)


class PaginatedListTests(NotificationsTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Notification.objects.bulk_create(
            Notification(user=cls.alice, notification_type="USER_NOTIFICATION", content=f"n{i}")
            for i in range(105)
        )

    def test_default_page_size_and_second_page(self):
        first = self.get(UserNotificationList, self.alice)
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data["count"], 105)
        self.assertEqual(len(first.data["results"]), 100)
        self.assertIsNotNone(first.data["next"])
        self.assertIsNone(first.data["previous"])

        second = self.get(UserNotificationList, self.alice, {"page": 2})
        self.assertEqual(len(second.data["results"]), 5)
        self.assertIsNone(second.data["next"])
        self.assertEqual(set(self.result_ids(first)).isdisjoint(self.result_ids(second)), True)

    def test_size_param_and_out_of_range_page(self):
        response = self.get(UserNotificationList, self.alice, {"size": 10, "page": 3})
        self.assertEqual(len(response.data["results"]), 10)
        self.assertEqual(response.data["count"], 105)

        missing = self.get(UserNotificationList, self.alice, {"page": 99})
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)

    def test_admin_list_is_paginated_too(self):
        response = self.get(NotificationList, self.admin, {"size": 7})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 105)
        self.assertEqual(len(response.data["results"]), 7)


class ProcessNotificationsTests(NotificationsTestBase):
    def test_each_type_creates_matching_notification(self):
        for notification_type in VALID_TYPES:
            with self.subTest(type=notification_type):
                process_notifications(self.alice, notification_type, "msg " + notification_type)
                notification = Notification.objects.get(
                    user=self.alice, notification_type=notification_type.upper()
                )
                self.assertEqual(notification.content, "msg " + notification_type)
                self.assertEqual(notification.status, "UNREAD")
        self.assertEqual(Notification.objects.filter(user=self.alice).count(), 4)
        self.assertFalse(Notification.objects.exclude(user=self.alice).exists())

    def test_admin_broadcast_targets_every_superuser_only(self):
        second_admin = User.objects.create_superuser(
            username="root2", email="root2@example.com", password="pw"
        )
        process_notifications("admin", "security_notification", "alert")

        recipients = set(Notification.objects.values_list("user_id", flat=True))
        self.assertEqual(recipients, {self.admin.id, second_admin.id})
        self.assertNotIn(self.staff.id, recipients)
        for notification in Notification.objects.all():
            self.assertEqual(notification.notification_type, "SECURITY_NOTIFICATION")
            self.assertEqual(notification.content, "alert")

    def test_admin_broadcast_without_superusers_creates_nothing(self):
        User.objects.filter(is_superuser=True).delete()
        process_notifications("admin", "user_notification", "alert")
        self.assertFalse(Notification.objects.exists())

    def test_unknown_type_raises_and_creates_nothing(self):
        for bad_type in ("email_notification", "USER_NOTIFICATION", "user", "", None):
            with self.subTest(type=bad_type):
                with self.assertRaisesMessage(Exception, "Unknown notification type"):
                    process_notifications(self.alice, bad_type, "msg")
        with self.assertRaisesMessage(Exception, "Unknown notification type"):
            process_notifications("admin", "bogus", "msg")
        self.assertFalse(Notification.objects.exists())

    def test_send_helpers(self):
        helpers = [
            (send_user_notification, "USER_NOTIFICATION"),
            (send_account_notification, "ACCOUNT_NOTIFICATION"),
            (send_transaction_notification, "TRANSACTION_NOTIFICATION"),
            (send_security_notification, "SECURITY_NOTIFICATION"),
        ]
        for helper, expected_type in helpers:
            with self.subTest(helper=helper.__name__):
                helper(self.bob, "content for " + expected_type)
                notification = Notification.objects.get(notification_type=expected_type)
                self.assertEqual(notification.user, self.bob)
                self.assertEqual(notification.content, "content for " + expected_type)
                self.assertEqual(notification.status, "UNREAD")

    def test_dispatch_logs_ids_only(self):
        with self.assertLogs(UTILS_LOGGER, level="INFO") as cm:
            process_notifications(self.alice, "transaction_notification", PII_CONTENT)
        self.assertLogged(
            cm.output, "INFO", "notification dispatched",
            f"user_id={self.alice.id}", "type=transaction_notification", "admin_broadcast=False",
        )
        self.assert_no_pii(cm.output)

    def test_admin_dispatch_logs_each_recipient(self):
        with self.assertLogs(UTILS_LOGGER, level="INFO") as cm:
            process_notifications("admin", "security_notification", PII_CONTENT)
        self.assertLogged(
            cm.output, "INFO", "notification dispatched", f"user_id={self.admin.id}",
            "type=security_notification", "admin_broadcast=True",
        )
        self.assertEqual(len(cm.output), 1)
        self.assert_no_pii(cm.output)

    def test_unknown_type_logs_warning(self):
        with self.assertLogs(UTILS_LOGGER, level="WARNING") as cm:
            with self.assertRaises(Exception):
                process_notifications(self.alice, "bogus", PII_CONTENT)
        self.assertLogged(
            cm.output, "WARNING", "notification rejected", "reason=unknown_type",
            "type=bogus", f"target={self.alice.id}",
        )
        self.assert_no_pii(cm.output)

        with self.assertLogs(UTILS_LOGGER, level="WARNING") as cm:
            with self.assertRaises(Exception):
                process_notifications("admin", "bogus", PII_CONTENT)
        self.assertLogged(cm.output, "WARNING", "notification rejected", "target=admin")
        self.assert_no_pii(cm.output)


class AdminNotificationListTests(NotificationsTestBase):
    def test_admin_sees_all_users_notifications(self):
        mine = self.make_notification(self.alice)
        theirs = self.make_notification(self.bob, "SECURITY_NOTIFICATION")
        response = self.get(NotificationList, self.admin)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(set(self.result_ids(response)), {mine.id, theirs.id})
        self.assertEqual(response.data["results"][0]["user"]["username"], "alice")

    def test_staff_flag_is_enough(self):
        self.make_notification(self.alice)
        response = self.get(NotificationList, self.staff)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)

    def test_regular_user_is_forbidden(self):
        self.make_notification(self.alice)
        response = self.get(NotificationList, self.alice)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertNotIn("results", response.data)

    def test_anonymous_is_rejected(self):
        response = self.get(NotificationList, None)
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_regular_user_cannot_post(self):
        response = self.request("post", NotificationList, self.alice, {"status": "READ"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Notification.objects.exists())

    def test_admin_post_does_not_crash(self):
        """BUG: NotificationList is a ListCreateAPIView, but NotificationSerializer marks
        `user`, `notification_type` and `content` read-only. A POST therefore reaches
        `serializer.save()` with only `status`, and `Notification.user_id` NOT NULL fails
        with an unhandled IntegrityError (HTTP 500) instead of a 400 validation error
        or a 201 with the created row."""
        response = self.request(
            "post", NotificationList, self.admin,
            {"status": "UNREAD", "notification_type": "USER_NOTIFICATION", "content": "x"},
        )
        self.assertIn(response.status_code, (status.HTTP_201_CREATED, status.HTTP_400_BAD_REQUEST))

    def test_denied_access_is_logged_without_pii(self):
        with self.assertLogs(VIEWS_LOGGER, level="WARNING") as cm:
            self.get(NotificationList, self.alice)
        self.assertLogged(
            cm.output, "WARNING", "notification admin access denied", "view=NotificationList",
            f"user_id={self.alice.id}", "is_staff=False",
        )
        self.assert_no_pii(cm.output)

        with self.assertLogs(VIEWS_LOGGER, level="WARNING") as cm:
            self.get(NotificationList, None)
        self.assertLogged(cm.output, "WARNING", "notification admin access denied", "user_id=None")
        self.assert_no_pii(cm.output)

    def test_granted_access_is_not_logged_as_denied(self):
        self.make_notification(self.alice)
        logger = get_redacted_logger(VIEWS_LOGGER)
        with self.assertNoLogs(logger, level="WARNING"):
            self.get(NotificationList, self.admin)


class AdminNotificationDetailTests(NotificationsTestBase):
    def setUp(self):
        super().setUp()
        self.notification = self.make_notification(self.alice, "ACCOUNT_NOTIFICATION")

    def test_admin_can_retrieve_any_users_notification(self):
        response = self.get(NotificationDetail, self.admin, pk=self.notification.pk)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.notification.pk)
        self.assertEqual(response.data["user"]["id"], self.alice.id)
        self.notification.refresh_from_db()
        self.assertEqual(self.notification.status, "UNREAD")

    def test_missing_pk_is_not_found(self):
        response = self.get(NotificationDetail, self.admin, pk=self.notification.pk + 1000)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_admin_patch_status(self):
        response = self.request(
            "patch", NotificationDetail, self.admin, {"status": "READ"}, pk=self.notification.pk
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "READ")
        self.notification.refresh_from_db()
        self.assertEqual(self.notification.status, "READ")

    def test_admin_put_status(self):
        response = self.request(
            "put", NotificationDetail, self.admin, {"status": "READ"}, pk=self.notification.pk
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.notification.refresh_from_db()
        self.assertEqual(self.notification.status, "READ")

    def test_admin_patch_invalid_status(self):
        response = self.request(
            "patch", NotificationDetail, self.admin, {"status": "ARCHIVED"}, pk=self.notification.pk
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("status", response.data)
        self.notification.refresh_from_db()
        self.assertEqual(self.notification.status, "UNREAD")

    def test_admin_patch_cannot_rewrite_content_or_owner(self):
        response = self.request(
            "patch", NotificationDetail, self.admin,
            {"content": "tampered", "notification_type": "SECURITY_NOTIFICATION", "user": self.bob.id},
            pk=self.notification.pk,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.notification.refresh_from_db()
        self.assertEqual(self.notification.content, PII_CONTENT)
        self.assertEqual(self.notification.notification_type, "ACCOUNT_NOTIFICATION")
        self.assertEqual(self.notification.user, self.alice)

    def test_admin_delete(self):
        response = self.request("delete", NotificationDetail, self.admin, pk=self.notification.pk)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Notification.objects.filter(pk=self.notification.pk).exists())

    def test_regular_user_is_forbidden_for_every_method(self):
        for method, data in (("get", None), ("patch", {"status": "READ"}), ("put", {"status": "READ"}), ("delete", None)):
            with self.subTest(method=method):
                response = self.request(method, NotificationDetail, self.alice, data, pk=self.notification.pk)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.notification.refresh_from_db()
        self.assertEqual(self.notification.status, "UNREAD")

    def test_update_and_delete_are_logged_without_pii(self):
        with self.assertLogs(VIEWS_LOGGER, level="INFO") as cm:
            self.request("patch", NotificationDetail, self.admin, {"status": "READ"}, pk=self.notification.pk)
        self.assertLogged(
            cm.output, "INFO", "notification updated by admin",
            f"notification_id={self.notification.pk}", f"admin_id={self.admin.id}",
            f"user_id={self.alice.id}", "status=READ",
        )
        self.assert_no_pii(cm.output)

        with self.assertLogs(VIEWS_LOGGER, level="INFO") as cm:
            self.request("delete", NotificationDetail, self.admin, pk=self.notification.pk)
        self.assertLogged(
            cm.output, "INFO", "notification deleted by admin",
            f"notification_id={self.notification.pk}", f"admin_id={self.admin.id}",
            f"user_id={self.alice.id}",
        )
        self.assert_no_pii(cm.output)

    def test_denied_access_is_logged(self):
        with self.assertLogs(VIEWS_LOGGER, level="WARNING") as cm:
            self.request("delete", NotificationDetail, self.bob, pk=self.notification.pk)
        self.assertLogged(
            cm.output, "WARNING", "notification admin access denied",
            "view=NotificationDetail", f"user_id={self.bob.id}",
        )
        self.assert_no_pii(cm.output)


class UserNotificationListTests(NotificationsTestBase):
    def setUp(self):
        super().setUp()
        self.alice_user = self.make_notification(self.alice, "USER_NOTIFICATION")
        self.alice_account = self.make_notification(self.alice, "ACCOUNT_NOTIFICATION")
        self.alice_transaction = self.make_notification(self.alice, "TRANSACTION_NOTIFICATION")
        self.alice_security = self.make_notification(self.alice, "SECURITY_NOTIFICATION")
        self.bob_user = self.make_notification(self.bob, "USER_NOTIFICATION")

    def test_lists_only_own_notifications(self):
        response = self.get(UserNotificationList, self.alice)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 4)
        self.assertNotIn(self.bob_user.id, self.result_ids(response))

        response = self.get(UserNotificationList, self.bob)
        self.assertEqual(self.result_ids(response), [self.bob_user.id])

    def test_user_with_no_notifications_gets_empty_page(self):
        response = self.get(UserNotificationList, self.staff)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 0)
        self.assertEqual(response.data["results"], [])

    def test_type_filter(self):
        expected = {
            "user": self.alice_user,
            "account": self.alice_account,
            "transaction": self.alice_transaction,
            "security": self.alice_security,
        }
        for type_param, notification in expected.items():
            with self.subTest(type=type_param):
                response = self.get(UserNotificationList, self.alice, {"type": type_param})
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(self.result_ids(response), [notification.id])

    def test_type_filter_still_scopes_to_current_user(self):
        response = self.get(UserNotificationList, self.bob, {"type": "user"})
        self.assertEqual(self.result_ids(response), [self.bob_user.id])
        response = self.get(UserNotificationList, self.bob, {"type": "security"})
        self.assertEqual(response.data["results"], [])

    def test_empty_type_param_is_ignored(self):
        response = self.get(UserNotificationList, self.alice, {"type": ""})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 4)

    def test_unknown_type_is_rejected(self):
        for bad in ("email", "USER", "user_notification", "%20"):
            with self.subTest(type=bad):
                response = self.get(UserNotificationList, self.alice, {"type": bad})
                self.assertGreaterEqual(response.status_code, 400)
                self.assertEqual(str(response.data["detail"]), "Unknown notification type")

    def test_unknown_type_is_a_bad_request_not_forbidden(self):
        """BUG: an unrecognised `?type=` value is a malformed client filter, but
        `UserNotificationList.get_queryset` raises `PermissionDenied`, so the API
        answers 403 Forbidden to an authenticated user who is fully allowed to read
        their own notifications. It should be 400 Bad Request (ValidationError)."""
        response = self.get(UserNotificationList, self.alice, {"type": "email"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_anonymous_is_rejected(self):
        response = self.get(UserNotificationList, None)
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_unknown_type_is_logged_without_pii(self):
        with self.assertLogs(VIEWS_LOGGER, level="WARNING") as cm:
            self.get(UserNotificationList, self.alice, {"type": "alice@example.com"})
        line = self.assertLogged(
            cm.output, "WARNING", "notification list rejected",
            f"user_id={self.alice.id}", "reason=unknown_type",
        )
        self.assertIn(f"type={REDACTED}", line)
        self.assert_no_pii(cm.output)

    def test_valid_list_emits_no_warning(self):
        logger = get_redacted_logger(VIEWS_LOGGER)
        with self.assertNoLogs(logger, level="WARNING"):
            self.get(UserNotificationList, self.alice, {"type": "user"})


class UserNotificationDetailTests(NotificationsTestBase):
    def setUp(self):
        super().setUp()
        self.mine = self.make_notification(self.alice, "TRANSACTION_NOTIFICATION")
        self.theirs = self.make_notification(self.bob)

    def test_retrieving_marks_as_read(self):
        response = self.get(UserNotificationDetail, self.alice, id=self.mine.pk)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.mine.pk)
        self.assertEqual(response.data["status"], "READ")
        self.assertEqual(response.data["content"], PII_CONTENT)
        self.mine.refresh_from_db()
        self.assertEqual(self.mine.status, "READ")

    def test_retrieving_an_already_read_notification_is_idempotent(self):
        self.mine.status = "READ"
        self.mine.save()
        response = self.get(UserNotificationDetail, self.alice, id=self.mine.pk)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "READ")

    def test_other_users_notification_is_not_found(self):
        response = self.get(UserNotificationDetail, self.alice, id=self.theirs.pk)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(str(response.data["detail"]), "Notification not found.")
        self.theirs.refresh_from_db()
        self.assertEqual(self.theirs.status, "UNREAD")

    def test_nonexistent_notification_is_not_found(self):
        response = self.get(UserNotificationDetail, self.alice, id=self.theirs.pk + 1000)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(str(response.data["detail"]), "Notification not found.")

    def test_lookup_by_declared_lookup_field(self):
        """BUG: the view declares `lookup_field = "pk"`, so a URL like
        `<int:pk>/` hands the view `kwargs["pk"]`, but `get_object` reads
        `self.kwargs.get("id")`. With the declared kwarg the lookup becomes
        `queryset.get(id=None)`, which always raises DoesNotExist -> 404."""
        response = self.get(UserNotificationDetail, self.alice, pk=self.mine.pk)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.mine.pk)

    def test_anonymous_is_rejected(self):
        response = self.get(UserNotificationDetail, None, id=self.mine.pk)
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
        self.mine.refresh_from_db()
        self.assertEqual(self.mine.status, "UNREAD")

    def test_mark_read_is_logged_without_pii(self):
        with self.assertLogs(VIEWS_LOGGER, level="INFO") as cm:
            self.get(UserNotificationDetail, self.alice, id=self.mine.pk)
        self.assertLogged(
            cm.output, "INFO", "notification marked read", f"notification_id={self.mine.pk}",
            f"user_id={self.alice.id}", "type=TRANSACTION_NOTIFICATION",
        )
        self.assert_no_pii(cm.output)

    def test_not_found_is_logged_without_pii(self):
        with self.assertLogs(VIEWS_LOGGER, level="WARNING") as cm:
            self.get(UserNotificationDetail, self.alice, id=self.theirs.pk)
        self.assertLogged(
            cm.output, "WARNING", "notification lookup rejected", f"user_id={self.alice.id}",
            f"notification_id={self.theirs.pk}", "reason=not_found",
        )
        self.assert_no_pii(cm.output)


class RedactionHelperTests(SimpleTestCase):
    def test_mask_number(self):
        self.assertEqual(mask_number(ACCOUNT_NUMBER), "********0001")
        self.assertEqual(mask_number(None), REDACTED)

    def test_redact_scrubs_notification_content(self):
        scrubbed = redact(PII_CONTENT)
        for leaked in (ACCOUNT_NUMBER, CARD_NUMBER, "alice@example.com", "cvv=987", "expiry=06/30"):
            self.assertNotIn(leaked, scrubbed)
        self.assertIn("100.00 USD", scrubbed)

    def test_notifications_loggers_carry_redaction_filter(self):
        for name in (VIEWS_LOGGER, UTILS_LOGGER):
            with self.subTest(logger=name):
                logger = get_redacted_logger(name)
                with self.assertLogs(name, level="INFO") as cm:
                    logger.info("content=%s", PII_CONTENT)
                self.assertNotIn(ACCOUNT_NUMBER, cm.output[0])
                self.assertNotIn("alice@example.com", cm.output[0])
                self.assertIn(REDACTED, cm.output[0])
