from .serializers import NotificationSerializer, AdminNotificationSerializer
from .models import Notification
from .utils import NOTIFICATION_TYPES
from rest_framework import generics, permissions, exceptions
from notifications.paginations import NotifiCationPagination
from rest_framework.exceptions import NotFound
from virtual_bank.logging import get_logger

logger = get_logger("notifications")
security_logger = get_logger("security")


def _reject(request, event, reason, exc_class, detail, security=False, **fields):
    log = security_logger if security else logger
    log.warning(
        "%s rejected: %s",
        event,
        reason,
        extra={"event": f"{event}.rejected", "reason": reason, "user_id": request.user.pk, **fields},
    )
    raise exc_class(detail)


class NotificationList(generics.ListCreateAPIView):
    queryset = Notification.objects.all()
    serializer_class = AdminNotificationSerializer
    permission_classes = [permissions.IsAdminUser]
    pagination_class = NotifiCationPagination

    def perform_create(self, serializer):
        notification = serializer.save()
        logger.info(
            "notification created by admin",
            extra={
                "event": "notification.admin_created",
                "user_id": self.request.user.pk,
                "notification_id": notification.pk,
                "notification_type": notification.notification_type,
            },
        )


class NotificationDetail(generics.RetrieveUpdateDestroyAPIView):
    queryset = Notification.objects.all()
    serializer_class = AdminNotificationSerializer
    permission_classes = [permissions.IsAdminUser]


class UserNotificationList(generics.ListAPIView):
    queryset = Notification.objects.all()
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = NotifiCationPagination

    def get_queryset(self):
        type = self.request.query_params.get("type", None)
        if type:
            notification_type = f"{type}_notification".lower()
            if notification_type not in NOTIFICATION_TYPES:
                _reject(
                    self.request,
                    "notification.list",
                    "unknown_type",
                    exceptions.ValidationError,
                    "Unknown notification type",
                )
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
        except Notification.DoesNotExist:
            _reject(
                self.request,
                "notification.read",
                "not_found",
                NotFound,
                "Notification not found.",
                notification_id=notification_number,
            )

        if notification.status != "READ":
            notification.status = "READ"
            notification.save()
            logger.info(
                "notification marked read",
                extra={
                    "event": "notification.read",
                    "user_id": self.request.user.pk,
                    "notification_id": notification.pk,
                    "notification_type": notification.notification_type,
                },
            )

        return notification
