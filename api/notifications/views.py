from django.shortcuts import render
from .serializers import NotificationSerializer
from .models import Notification
from rest_framework import generics, permissions, exceptions
from notifications.paginations import NotifiCationPagination
from rest_framework.exceptions import NotFound
from virtual_bank.log_redaction import get_redacted_logger

logger = get_redacted_logger(__name__)


class AdminAccessLoggingMixin:
    def permission_denied(self, request, message=None, code=None):
        user = request.user
        logger.warning(
            "notification admin access denied: view=%s user_id=%s is_staff=%s",
            self.__class__.__name__,
            user.pk,
            user.is_staff,
        )
        super().permission_denied(request, message=message, code=code)


class NotificationList(AdminAccessLoggingMixin, generics.ListCreateAPIView):
    queryset = Notification.objects.all()
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAdminUser]
    pagination_class = NotifiCationPagination

    def perform_create(self, serializer):
        notification = serializer.save()
        logger.info(
            "notification created by admin: notification_id=%s admin_id=%s type=%s status=%s",
            notification.pk,
            self.request.user.pk,
            notification.notification_type,
            notification.status,
        )


class NotificationDetail(AdminAccessLoggingMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Notification.objects.all()
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAdminUser]

    def perform_update(self, serializer):
        notification = serializer.save()
        logger.info(
            "notification updated by admin: notification_id=%s admin_id=%s user_id=%s status=%s",
            notification.pk,
            self.request.user.pk,
            notification.user_id,
            notification.status,
        )

    def perform_destroy(self, instance):
        logger.info(
            "notification deleted by admin: notification_id=%s admin_id=%s user_id=%s",
            instance.pk,
            self.request.user.pk,
            instance.user_id,
        )
        instance.delete()


class UserNotificationList(generics.ListAPIView):
    queryset = Notification.objects.all()
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = NotifiCationPagination

    def get_queryset(self):
        type = self.request.query_params.get("type", None)
        if type:
            notification_type = f"{type}_notification"
            if notification_type not in [
                "user_notification",
                "account_notification",
                "transaction_notification",
                "security_notification",
            ]:
                logger.warning(
                    "notification list rejected: user_id=%s reason=unknown_type type=%s",
                    self.request.user.pk,
                    type,
                )
                raise exceptions.PermissionDenied("Unknown notification type")
            return self.queryset.filter(
                user=self.request.user, notification_type=notification_type.upper()
            )
        return self.queryset.filter(user=self.request.user)


class UserNotificationDetail(generics.RetrieveAPIView):
    queryset = Notification.objects.all()
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "pk"

    def get_queryset(self):
        user = self.request.user
        return self.queryset.filter(user=user)

    def get_object(self):
        queryset = self.get_queryset()
        notification_number = self.kwargs.get("id")

        try:
            notification = queryset.get(id=notification_number)
            print(notification)
        except Notification.DoesNotExist:
            logger.warning(
                "notification lookup rejected: user_id=%s notification_id=%s reason=not_found",
                self.request.user.pk,
                notification_number,
            )
            raise NotFound("Notification not found.")

        notification.status = "READ"
        notification.save()
        logger.info(
            "notification marked read: notification_id=%s user_id=%s type=%s",
            notification.pk,
            notification.user_id,
            notification.notification_type,
        )

        return notification
